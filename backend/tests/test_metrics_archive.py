"""Daily rollup preserves distributions and survives pruning and retries."""

from datetime import UTC, datetime, timedelta

import pytest
from app.db import Base, create_engine, create_session_factory
from app.metrics.archive import capture_distributions, historical_statistics, percentile
from app.metrics.entities import MetricArchiveCheckpoint, MetricDaily, MetricSample
from app.metrics.providers import _parse_prometheus
from app.metrics.storage import MetricsStorage
from app.schemas.instances import Instance, Metric, MetricList
from sqlalchemy import func, select


@pytest.fixture
async def database(tmp_path):
    engine = create_engine(tmp_path / "archive.db")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    yield create_session_factory(engine)
    await engine.dispose()


def histogram(at, count):
    body = f"""
# TYPE vllm:time_to_first_token_seconds histogram
vllm:time_to_first_token_seconds_bucket{{le="1"}} {count}
vllm:time_to_first_token_seconds_bucket{{le="2"}} {count * 2}
vllm:time_to_first_token_seconds_bucket{{le="+Inf"}} {count * 2}
vllm:time_to_first_token_seconds_count {count * 2}
vllm:time_to_first_token_seconds_sum {count * 2}
"""
    return capture_distributions(_parse_prometheus(body, None, at, "vllm"))


async def write(storage, at, speed=90, requests=2, count=0, pid=10, model="model"):
    instance = Instance(
        id=f"managed/profile/{pid}", profile_name="profile", source="managed",
        engine_id="vllm", pid=pid, model_name=model, state="ready", observed_at=at,
    )
    items = [Metric(name=name, value=value, observed_at=at, source="test", state="ok")
             for name, value in (("decode_tps", speed), ("active_requests", requests))]
    await storage.write([(instance, MetricList(
        items=items, distributions=histogram(at, count),
    ))], {})


async def read(sessions, curve=False):
    async with sessions() as session:
        return await historical_statistics(session, profile_name="profile", days=7, curve=curve)


def test_native_histogram_quantile_and_capture():
    distributions = histogram(datetime.now(UTC), 5)
    assert len(distributions) == 1
    native = next(iter(distributions.values()))
    assert native["count"] == 10 and native["sum"] == 10
    assert native["buckets"]["+Inf"] == 10
    assert percentile(native["buckets"], .5, cumulative=True) == 1
    assert percentile(native["buckets"], .95, cumulative=True) == pytest.approx(1.9)
    assert percentile({}, .95, cumulative=True) is None


async def test_archive_pruning_idempotence_histograms_and_new_sample_ids(database):
    storage = MetricsStorage(database)
    at = datetime.now(UTC) - timedelta(hours=25)
    await write(storage, at, count=0)
    await write(storage, at + timedelta(seconds=5), count=5)
    await write(storage, at + timedelta(seconds=10), speed=0, count=5)
    before = await read(database)
    await storage.cleanup(set())
    await storage.cleanup(set())
    after = await read(database)
    assert after == before
    stats = after["items"][0]["statistics"]["decode_tps"]
    assert stats["avg"] == 90 and stats["count"] == 2
    native = after["items"][0]["request_distributions"][0]
    assert native["count"] == 10 and native["p95"] == pytest.approx(1.9)
    curve = await read(database, curve=True)
    assert curve["items"][0]["total_avg"] == 90
    assert curve["items"][0]["per_request_avg"] == 45
    assert curve["items"][0]["seconds"] == 5
    async with database() as session:
        assert await session.scalar(select(func.count()).select_from(MetricSample)) == 0
        checkpoint = await session.get(MetricArchiveCheckpoint, 1)
        old_cursor = checkpoint.last_sample_id
    await write(storage, datetime.now(UTC), speed=50, requests=1, pid=11)
    async with database() as session:
        row = await session.scalar(select(MetricSample))
        assert row.id > old_cursor
    await storage.cleanup(set())
    result = await read(database)
    assert sum(item["statistics"]["decode_tps"]["count"] for item in result["items"]) == 3


async def test_archive_failure_keeps_raw_samples_and_checkpoint(database, monkeypatch):
    import app.metrics.storage as module

    storage = MetricsStorage(database)
    await write(storage, datetime.now(UTC) - timedelta(hours=25))

    async def fail(session):
        session.add(MetricDaily(
            day="2026-01-01", profile_name="p", model_name="m", engine_id="vllm",
            statistics={}, distributions={},
        ))
        await session.flush()
        raise RuntimeError("rollup failed")

    monkeypatch.setattr(module, "archive_before_cleanup", fail)
    with pytest.raises(RuntimeError, match="rollup failed"):
        await storage.cleanup(set())
    async with database() as session:
        assert await session.scalar(select(func.count()).select_from(MetricSample)) == 1
        assert await session.scalar(select(func.count()).select_from(MetricDaily)) == 0
        assert await session.get(MetricArchiveCheckpoint, 1) is None


async def test_curve_separates_models_and_rejects_transitions_gaps_and_resets(database):
    storage = MetricsStorage(database)
    at = datetime.now(UTC) - timedelta(minutes=2)
    await write(storage, at, speed=50, requests=1)
    await write(storage, at + timedelta(seconds=5), speed=50, requests=1, count=1)
    await write(storage, at + timedelta(seconds=10), speed=120, requests=3, count=2)
    await write(storage, at + timedelta(seconds=15), speed=150, requests=3, count=3)
    await write(storage, at + timedelta(seconds=60), speed=9999, requests=3, count=0)
    await write(storage, at + timedelta(seconds=65), speed=200, requests=2, model="other")
    await write(storage, at + timedelta(seconds=70), speed=200, requests=2, model="other", count=1)
    result = await read(database, curve=True)
    points = {(r["model_name"], r["requests"]): r for r in result["items"]}
    assert len(points) == 3
    assert points[("model", 1)]["total_avg"] == 50
    assert points[("model", 3)]["total_avg"] == 150
    assert points[("model", 3)]["per_request_avg"] == 50
    assert points[("other", 2)]["total_avg"] == 200
    await storage.cleanup(set())
    assert await read(database, curve=True) == result


async def test_daily_weights_and_repeated_measurements(database):
    storage = MetricsStorage(database)
    at = datetime.now(UTC)
    model = Instance(id="p/10", profile_name="profile", source="managed", engine_id="vllm",
                     pid=10, state="ready", observed_at=at)
    for value, weight, seconds in ((1000, 1, 0), (2000, 3, 5), (2000, 3, 5)):
        metric = Metric(name="prefill_tps", value=value, source="test", state="ok",
                        observed_at=at + timedelta(seconds=seconds),
                        metadata={"denominator": weight})
        await storage.write([(model, MetricList(items=[metric]))], {})
    result = await read(database)
    stats = result["items"][0]["statistics"]["prefill_tps"]
    assert stats["avg"] == 1750 and stats["max"] == 2000 and stats["count"] == 2
    assert 950 <= stats["p10"] <= 1050 and 1900 <= stats["p95"] <= 2100


async def test_migration_keeps_samples_and_prevents_id_reuse(tmp_path):
    import asyncio
    from pathlib import Path

    from alembic import command
    from alembic.config import Config
    from app.config import Settings
    from app.db import migrate_database
    from app.paths import build_paths, prepare_data_dirs
    from sqlalchemy import text

    settings = Settings(data_root=tmp_path / "data", host="127.0.0.1", port=3000, _env_file=None)
    paths = build_paths(settings)
    prepare_data_dirs(paths)
    config = Config(str(Path(__file__).parents[1] / "alembic.ini"))
    config.attributes["llm_studio_settings"] = settings
    config.attributes["configure_logging"] = False
    await asyncio.to_thread(command.upgrade, config, "0006_model_runs")
    engine = create_engine(paths.sqlite_path)
    old = datetime.now(UTC) - timedelta(hours=25)
    async with engine.begin() as connection:
        await connection.execute(text(
            "INSERT INTO metric_samples "
            "(profile_name,pid,engine_id,observed_at,recorded_at,metric_meta,decode_tps) "
            "VALUES ('profile',10,'vllm',:at,:at,'{}',12)"
        ), {"at": old.replace(tzinfo=None)})
    await engine.dispose()
    await migrate_database(settings, paths)
    await migrate_database(settings, paths)
    engine = create_engine(paths.sqlite_path)
    sessions = create_session_factory(engine)
    async with sessions() as session:
        original = await session.scalar(select(MetricSample))
        assert original.decode_tps == 12 and original.distributions is None
        old_id = original.id
    storage = MetricsStorage(sessions)
    await storage.cleanup(set())
    await write(storage, datetime.now(UTC))
    async with sessions() as session:
        current = await session.scalar(select(MetricSample))
        assert current.id > old_id
    await engine.dispose()
