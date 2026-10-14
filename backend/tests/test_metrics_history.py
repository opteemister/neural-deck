"""Contracts of the approved metrics/history specification."""

import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from alembic import command
from alembic.config import Config
from app.config import Settings
from app.db import Base, create_engine, create_session_factory, migrate_database
from app.journal import ApiClientRecord, RequestJournalRecord
from app.metrics.performance import PerformanceNormalizer
from app.metrics.providers import _parse_prometheus
from app.metrics.service import MetricsService, aggregate_samples
from app.metrics.storage import MetricsStorage
from app.paths import build_paths, prepare_data_dirs
from app.proxy.service import ProxyService
from app.schemas.instances import Instance, InstanceList, Metric, MetricList
from app.token_stats import RequestJournalService
from sqlalchemy import func, select, text

from tests.test_proxy_metrics_version_zero import _Manager


def instance(pid=10, profile="profile", engine="vllm"):
    return Instance(
        id=f"managed/{profile}/{pid}",
        source="managed",
        profile_name=profile,
        pid=pid,
        engine_id=engine,
        state="ready",
        port=8001,
        observed_at=datetime.now(UTC),
    )


def metric(name, value, at=None, state="ok"):
    return Metric(
        name=name, value=value, source="test", observed_at=at or datetime.now(UTC), state=state
    )


@pytest.fixture
async def database(tmp_path):
    engine = create_engine(tmp_path / "metrics.db")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    yield create_session_factory(engine)
    await engine.dispose()


async def test_migration_preserves_existing_usage_and_is_repeatable(tmp_path):
    settings = Settings(data_root=tmp_path / "data", host="127.0.0.1", port=3000, _env_file=None)
    paths = build_paths(settings)
    prepare_data_dirs(paths)
    config = Config(str(Path(__file__).parents[1] / "alembic.ini"))
    config.attributes["neural_deck_settings"] = settings
    config.attributes["configure_logging"] = False
    await asyncio.to_thread(command.upgrade, config, "0004_request_journal_correlation_id")
    engine = create_engine(paths.sqlite_path)
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "INSERT INTO request_journal "
                "(id,request_id,created_at,route,model_name,latency_ms,streaming,input_tokens) "
                "VALUES ('old','req','2026-10-01','/v1/embeddings','m',10,0,42)"
            )
        )
    await migrate_database(settings, paths)
    await migrate_database(settings, paths)
    async with create_session_factory(engine)() as session:
        old = await session.get(RequestJournalRecord, "old")
        assert old.input_tokens == 42 and old.pid is None and old.client_id is None
        assert await session.scalar(select(func.count()).select_from(ApiClientRecord)) == 0
    await engine.dispose()


async def test_clients_atomic_incremental_and_ip_history(database):
    journal = RequestJournalService(session_factory=database)
    ids = await asyncio.gather(
        *(journal.identify_client("Bearer P-Work", f"10.0.0.{i}") for i in range(1, 9))
    )
    assert len(set(ids)) == 1
    second = await journal.identify_client("Bearer other", "10.0.0.1")
    anonymous = await journal.identify_client(None, "10.0.0.1")
    assert ids[0] < second < anonymous
    assert await journal.identify_client(None, "10.0.0.1") == anonymous
    async with database() as session:
        client = await session.get(ApiClientRecord, ids[0])
        assert client.api_key == "P-Work"
        assert len(client.ip_addresses) == 8


async def test_daily_hourly_token_buckets_keep_unknowns_and_filter_profile_pid(
    database, monkeypatch
):
    import app.token_stats as module

    journal = RequestJournalService(session_factory=database)
    now = datetime.now(UTC).replace(hour=10, minute=30, second=0, microsecond=0)

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return now

    monkeypatch.setattr(module, "datetime", Clock)

    def record(index, *, profile="profile", pid=10, age=10, done=True, known=True):
        at = now - timedelta(minutes=age)
        return RequestJournalRecord(
            id=str(index), request_id=str(index), created_at=at, route="/v1/chat/completions",
            model_name="model", profile_name=profile, pid=pid, latency_ms=1, streaming=0,
            finished_at=at if done else None, usage_received_at=at if done else None,
            input_tokens=100 if known else None, output_tokens=50 if known else None,
            cached_input_tokens=20 if known else None,
        )
    async with database() as session:
        session.add_all([record(i) for i in range(125)])
        session.add_all([
            record(126, known=False), record(127, profile="other"), record(128, pid=11),
            record(129, age=1500), record(130, done=False), record(131, age=1080),
        ])
        await session.commit()
    result = await journal.token_history(profile_name="profile", pid=10)
    assert len(result["items"]) == 24 and result["bucket_minutes"] == 60
    assert result["to"] - result["from"] == timedelta(hours=24)
    assert result["to"].hour == 11 and result["to"].minute == 0
    assert all(item["at"].minute == 0 and item["at"].second == 0 for item in result["items"])
    assert result["items"][1]["at"] - result["items"][0]["at"] == timedelta(hours=1)
    assert sum(item["requests"] for item in result["items"]) == 127
    assert sum(item["requests"] for item in result["items"][:-1]) == 1
    latest = result["items"][-1]
    assert latest["input_tokens"] == 12500 and latest["output_tokens"] == 6250
    assert latest["cached_tokens"] == 2500 and latest["cached_tokens_unknown"] == 1
    other = await journal.token_history(profile_name="other")
    assert sum(item["requests"] for item in other["items"]) == 1
    all_rows = await journal.token_history()
    assert sum(item["requests"] for item in all_rows["items"]) == 129


async def test_token_hour_boundaries_stay_fixed_until_the_next_hour(database, monkeypatch):
    import app.token_stats as module

    now = datetime.now(UTC).replace(hour=10, minute=10, second=0, microsecond=0)
    base = now.replace(minute=0)

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return now

    monkeypatch.setattr(module, "datetime", Clock)
    async with database() as session:
        for index, at in enumerate((base + timedelta(minutes=5), base + timedelta(hours=1))):
            session.add(RequestJournalRecord(
                id=f"hour-{index}", request_id=f"hour-{index}", created_at=at,
                route="/v1/chat/completions", model_name="m", profile_name="profile",
                latency_ms=1, streaming=0, finished_at=at, input_tokens=100, output_tokens=20,
            ))
        await session.commit()
    journal = RequestJournalService(session_factory=database)
    first = await journal.token_history(profile_name="profile")
    now = base + timedelta(minutes=59)
    second = await journal.token_history(profile_name="profile")
    assert first == second
    assert second["items"][-1]["at"] == base
    assert second["items"][-1]["requests"] == 1
    now = base + timedelta(hours=1, minutes=1)
    third = await journal.token_history(profile_name="profile")
    assert third["from"] == first["from"] + timedelta(hours=1)
    assert third["items"][-2]["at"] == base
    assert third["items"][-2]["requests"] == 1
    assert third["items"][-1]["at"] == base + timedelta(hours=1)
    assert third["items"][-1]["requests"] == 1


async def test_stream_request_single_row_usage_ttft_identity(database):
    manager = _Manager()
    manager.instance.engine_id = "llama.cpp"
    manager.registry.register_instance(manager.instance, upstream_models=["actual"])
    payload = (
        b'data: {"choices":[{"delta":{"role":"assistant"}}]}\n\n'
        b'data: {"choices":[{"delta":{"reasoning_content":"thinking"}}]}\n\n'
        b'data: {"usage":{"prompt_tokens":20,"completion_tokens":10,'
        b'"prompt_tokens_details":{"cached_tokens":5}},"timings":{'
        b'"prompt_ms":100,"prompt_n":15,"predicted_ms":500,"predicted_n":10,'
        b'"draft_n":20,"draft_n_accepted":10}}\n\ndata: [DONE]\n\n'
    )
    seen = []

    def handle(request):
        seen.append(request)
        return httpx.Response(200, content=payload, headers={"content-type": "text/event-stream"})

    journal = RequestJournalService(session_factory=database)
    proxy = ProxyService(
        manager,
        journal=journal,
        client_factory=lambda: httpx.AsyncClient(transport=httpx.MockTransport(handle)),
    )
    response = await proxy.forward(
        route="/v1/chat/completions",
        payload={"model": "public", "stream": True},
        headers={"Authorization": "Bearer P-Work"},
        request_id="same",
        client_ip="10.0.0.1",
    )
    # Model changes while the stream is consumed must not move the request to a new PID.
    manager.instance.pid = 99
    assert b"".join([chunk async for chunk in response.body_iterator]) == payload
    rows = (await journal.usage())["items"]
    assert len(rows) == 1
    row = rows[0]
    assert row["profile_name"] == "profile" and row["pid"] == 10
    assert (
        row["input_tokens"] == 20 and row["output_tokens"] == 10 and row["cached_input_tokens"] == 5
    )
    assert row["ttft_ms"] is not None and row["prefill_tps"] == 150 and row["decode_tps"] == 20
    assert row["prefix_cache_hit_percent"] == 25 and row["spec_acceptance_percent"] == 50
    assert row["finished_at"] is not None and row["client_id"] is not None
    assert "authorization" not in seen[0].headers
    assert "P-Work" not in json.dumps(rows, default=str)


async def test_interrupted_requests_and_long_lived_usage_not_cleaned(database):
    journal = RequestJournalService(session_factory=database)
    old = datetime.now(UTC) - timedelta(days=100)
    await journal.record(
        request_id="req",
        route="/v1/embeddings",
        model_name="m",
        profile_name="p",
        instance_id="i",
        engine_id="vllm",
        status_code=None,
        latency_ms=0,
        input_tokens=None,
        output_tokens=None,
        created_at=old,
        started_at=old,
    )
    await journal.interrupt_pending()
    storage = MetricsStorage(database)
    await storage.write([(instance(), MetricList(items=[metric("decode_tps", 50, old)]))], {})
    await storage.cleanup(set())
    assert await storage.history() == []
    rows = (await journal.usage(from_=old - timedelta(days=1)))["items"]
    assert len(rows) == 1 and rows[0]["error_code"] == "interrupted"
    assert rows[0]["input_tokens"] is None


def test_normalization_weighted_cache_queue_spec_and_reset():
    normalizer = PerformanceNormalizer()
    model = instance()
    at = datetime.now(UTC)

    def raw(stamp, hits, queries, accepted, drafted, queue_sum, queue_count):
        body = f"""# HELP vllm:prefix_cache_hits cache hits
# TYPE vllm:prefix_cache_hits counter
vllm:prefix_cache_hits{{model_name="m"}} {hits}
vllm:prefix_cache_queries{{model_name="m"}} {queries}
vllm:spec_decode_num_accepted_tokens {accepted}
vllm:spec_decode_num_draft_tokens {drafted}
vllm:request_queue_time_seconds_sum {queue_sum}
vllm:request_queue_time_seconds_count {queue_count}
"""
        return _parse_prometheus(body, None, stamp, "vllm")

    first = raw(at, 10, 100, 5, 10, 1, 2)
    assert first[0].metadata["labels"]["model_name"] == "m"
    normalizer.collect(model, first)
    second = normalizer.collect(model, raw(at + timedelta(seconds=5), 40, 150, 15, 30, 2, 4))
    values = {item.name: item.value for item in second}
    assert values["prefix_cache_hit_percent"] == 60
    assert values["prefix_cache_hit_mean_percent"] == pytest.approx(40 / 150 * 100)
    assert values["queue_time_mean_ms"] == 500 and values["spec_acceptance_percent"] == 50
    reset = normalizer.collect(model, raw(at + timedelta(seconds=10), 0, 0, 0, 0, 0, 0))
    assert next(item for item in reset if item.name == "spec_acceptance_percent").value is None


@pytest.mark.parametrize("poll_seconds", [2, 5, 30])
def test_vllm_prefill_excludes_cache_and_uses_request_phase_time(poll_seconds):
    normalizer = PerformanceNormalizer()
    model = instance()
    at = datetime.now(UTC)

    def raw(stamp, tokens, seconds, count, prompt, output):
        return _parse_prometheus(f"""
vllm:request_prefill_kv_computed_tokens_sum{{engine="0"}} {tokens}
vllm:request_prefill_kv_computed_tokens_count{{engine="0"}} {count}
vllm:request_prefill_time_seconds_sum{{engine="0"}} {seconds}
vllm:request_prefill_time_seconds_count{{engine="0"}} {count}
vllm:prompt_tokens_total {prompt}
vllm:prompt_tokens_cached_total {prompt - tokens}
vllm:generation_tokens_total {output}
""", None, stamp, "vllm")

    baseline = raw(at, 1000, 1, 1, 10000, 10)
    first = {m.name: m for m in normalizer.collect(model, baseline)}
    assert first["prefill_tps"].value is None
    # 100k input tokens, only 2k computed, over 2 seconds of prefill.
    latest = raw(at + timedelta(seconds=poll_seconds), 3000, 3, 3, 110000, 30)
    items = {m.name: m for m in normalizer.collect(model, latest)}
    assert items["prefill_tps"].value == 1000
    assert items["prefill_tps"].metadata["denominator"] == 2
    assert items["prompt_tokens_total"].value == 110000
    assert items["cached_prompt_tokens_total"].value == 107000
    assert items["decode_tps"].value == 20 / poll_seconds
    idle = raw(at + timedelta(seconds=2 * poll_seconds), 3000, 3, 3, 110000, 30)
    assert next(m for m in normalizer.collect(model, idle) if m.name == "prefill_tps").value is None
    reset = raw(at + timedelta(seconds=3 * poll_seconds), 0, 0, 0, 0, 0)
    reset_values = {m.name: m for m in normalizer.collect(model, reset)}
    assert reset_values["prefill_tps"].value is None
    resumed = raw(at + timedelta(seconds=4 * poll_seconds), 1000, 2, 1, 1000, 10)
    resumed_values = {m.name: m for m in normalizer.collect(model, resumed)}
    assert resumed_values["prefill_tps"].value == 500


@pytest.mark.parametrize("missing", ["tokens", "seconds", "mismatch", "labels", "zero_time"])
def test_vllm_prefill_does_not_invent_speed_from_incomplete_histograms(missing):
    normalizer = PerformanceNormalizer()
    model = instance()
    at = datetime.now(UTC)
    for index in (0, 1):
        body = f"""
vllm:prompt_tokens_total {index * 100000}
vllm:request_prefill_kv_computed_tokens_sum {index * 1000}
vllm:request_prefill_kv_computed_tokens_count {index}
vllm:request_prefill_time_seconds_sum {index}
vllm:request_prefill_time_seconds_count {index}
"""
        if missing == "tokens":
            body = "\n".join(line for line in body.splitlines() if "kv_computed" not in line)
        elif missing == "seconds":
            body = "\n".join(line for line in body.splitlines() if "time_seconds" not in line)
        elif missing == "mismatch":
            body = body.replace(f"time_seconds_count {index}", f"time_seconds_count {index * 2}")
        elif missing == "labels":
            body = body.replace("time_seconds_sum ", 'time_seconds_sum{engine="other"} ')
        elif missing == "zero_time":
            body = body.replace(f"time_seconds_sum {index}", "time_seconds_sum 0")
        values = normalizer.collect(
            model, _parse_prometheus(body, None, at + timedelta(seconds=index * 2), "vllm")
        )
        assert next(m for m in values if m.name == "prefill_tps").value is None


def test_vllm_prefill_combines_parallel_engines_by_phase_duration():
    normalizer = PerformanceNormalizer()
    at = datetime.now(UTC)
    model = instance()
    for index in (0, 1):
        lines = []
        for engine, tokens, seconds in ((0, 1000, 1), (1, 6000, 3)):
            for name, value in (
                ("request_prefill_kv_computed_tokens_sum", tokens),
                ("request_prefill_kv_computed_tokens_count", 1),
                ("request_prefill_time_seconds_sum", seconds),
                ("request_prefill_time_seconds_count", 1),
            ):
                lines.append(f'vllm:{name}{{engine="{engine}"}} {value * index}')
        result = normalizer.collect(model, _parse_prometheus(
            "\n".join(lines), None, at + timedelta(seconds=index * 5), "vllm"
        ))
    prefill = next(m for m in result if m.name == "prefill_tps")
    assert prefill.value == 1750  # 7000 computed tokens / 4 request-seconds.
    assert prefill.metadata["denominator"] == 4


def test_cumulative_means_survive_intervals_without_new_requests():
    normalizer = PerformanceNormalizer()
    at = datetime.now(UTC)
    body = """vllm:time_to_first_token_seconds_sum 2
vllm:time_to_first_token_seconds_count 4
vllm:spec_decode_num_accepted_tokens 3
vllm:spec_decode_num_draft_tokens 10
"""
    normalizer.collect(instance(), _parse_prometheus(body, None, at, "vllm"))
    unchanged = normalizer.collect(
        instance(), _parse_prometheus(body, None, at + timedelta(seconds=5), "vllm")
    )
    items = {item.name: item for item in unchanged}
    assert items["ttft_mean_ms"].value is None
    assert items["ttft_mean_ms"].metadata["mean"] == 500
    assert items["spec_acceptance_percent"].value is None
    assert items["spec_acceptance_percent"].metadata["mean"] == 30
    other = metric("ttft_mean_ms", None)
    other.metadata = {"mean": 100, "mean_weight": 12}
    combined = aggregate_samples([
        {"items": unchanged}, {"items": [other]},
    ])
    ttft = next(item for item in combined["items"] if item.name == "ttft_mean_ms")
    assert ttft.metadata["mean"] == 200
    assert ttft.value is None


async def test_background_without_browser_and_restore_peaks(database, monkeypatch):
    import app.metrics.service as module

    monkeypatch.setattr(module, "process_vram", lambda _: None)
    model = instance()

    class Manager:
        async def refresh(self, **kwargs):
            return InstanceList(items=[model])

        async def get(self, _):
            return model

    class Provider:
        async def collect(self, _):
            return [metric("decode_tps", 42)]

    storage = MetricsStorage(database)
    service = MetricsService(Manager(), providers={"vllm": Provider()}, storage=storage)
    await service.start()
    try:
        for _ in range(50):
            if await storage.history():
                break
            await asyncio.sleep(0.02)
        assert len(await storage.history()) == 1
        assert "neural_deck_model_decode_tps" in service.export()
    finally:
        await service.close()
    restarted = MetricsService(Manager(), providers={"vllm": Provider()}, storage=storage)
    try:
        data = await restarted.read(mode="bootstrap", profile_name="profile", pid=10)
        assert len(data["history"]) == 1 and data["peaks"]["decode_tps"] == 42
    finally:
        await restarted.close()


async def test_prometheus_fresh_observed_time_and_stale_fallback(database):
    model = instance()
    storage = MetricsStorage(database)
    await storage.write([(model, MetricList(items=[metric("decode_tps", 10)]))], {})

    class Manager:
        async def get(self, _):
            return model

    service = MetricsService(
        Manager(), storage=storage, prometheus_url="http://prom", neural_deck_id="neural-deck"
    )
    now = datetime.now(UTC).timestamp()
    stamp = now - 2
    labels = {
        "neural_deck_id": "neural-deck",
        "profile_name": "profile",
        "pid": "10",
        "engine_id": "vllm",
    }

    def handle(request):
        return httpx.Response(
            200,
            json={
                "status": "success",
                "data": {
                    "result": [
                        {
                            "metric": {**labels, "__name__": "neural_deck_model_decode_tps"},
                            "value": [now, "99"],
                        },
                        {
                            "metric": {
                                **labels,
                                "__name__": "neural_deck_model_observed_timestamp_seconds",
                                "field": "decode_tps",
                            },
                            "value": [now, str(stamp)],
                        },
                    ]
                },
            },
        )

    await service.prometheus.client.aclose()
    service.prometheus.client = httpx.AsyncClient(transport=httpx.MockTransport(handle))
    try:
        data = await service.collect(model.id)
        assert data.source == "prometheus" and data.items[0].value == 99
        assert data.items[0].observed_at.timestamp() == stamp
        stamp = now - 100
        data = await service.collect(model.id)
        assert data.source == "sqlite"
        assert next(item for item in data.items if item.name == "decode_tps").value == 10
    finally:
        await service.close()


async def test_collector_continues_every_five_seconds_for_two_profiles(database, monkeypatch):
    import app.metrics.service as module

    monkeypatch.setattr(module, "process_vram", lambda _: None)
    models = [instance(10, "first"), instance(11, "second")]

    class Manager:
        async def refresh(self, **kwargs):
            return InstanceList(items=models)

        async def get(self, identity):
            return next(model for model in models if model.id == identity)

    class Provider:
        async def collect(self, model):
            return [metric("decode_tps", model.pid)]

    storage = MetricsStorage(database)
    service = MetricsService(Manager(), providers={"vllm": Provider()}, storage=storage)
    await service.start()
    try:
        for _ in range(130):
            rows = await storage.history(limit=10)
            if len(rows) >= 4:
                break
            await asyncio.sleep(0.05)
        assert len(rows) >= 4
        for profile in ("first", "second"):
            points = [row for row in rows if row["profile_name"] == profile]
            assert len(points) >= 2
            assert 4.5 <= (points[1]["observed_at"] - points[0]["observed_at"]).total_seconds() <= 6
    finally:
        await service.close()


def test_application_start_migrates_and_exposes_history(tmp_path):
    from app.main import create_app
    from fastapi.testclient import TestClient

    settings = Settings(
        data_root=tmp_path / "runtime",
        host="127.0.0.1",
        port=3000,
        openai_port=8080,
        _env_file=None,
    )
    app = create_app(settings)
    with TestClient(app) as client:
        response = client.get("/api/v1/metrics?mode=bootstrap&scope=all")
        assert response.status_code == 200
        payload = response.json()["data"]
        assert payload["history"] == []
        daily = client.get("/api/v1/metrics?mode=aggregates&scope=all")
        assert daily.status_code == 200
        assert "aggregates" in daily.json()["data"]
        assert (
            next(item for item in payload["current"]["items"] if item["name"] == "decode_tps")[
                "value"
            ]
            == 0
        )
        assert client.get("/metrics").status_code == 200
        assert client.get("/api/v1/metrics?mode=usage").json()["data"]["items"] == []
        tokens = client.get("/api/v1/metrics?mode=token-history&scope=all")
        assert tokens.status_code == 200
        assert len(tokens.json()["data"]["items"]) == 24
        assert tokens.json()["data"]["source"] == "api-journal"
        assert client.get("/api/v1/metrics?mode=history&limit=0").status_code == 422
        for route in ("daily", "concurrency"):
            archived = client.get(f"/api/v1/metrics/{route}?days=7")
            assert archived.status_code == 200
            assert archived.json()["data"]["items"] == []
            assert client.get(f"/api/v1/metrics/{route}?days=0").status_code == 422


async def test_daily_aggregates_cover_all_snapshots_and_runs_and_expire_old_values(database):
    from app.metrics.entities import MetricSample

    now = datetime.now(UTC)
    start = now - timedelta(hours=24)
    at = now - timedelta(hours=12)

    def row(profile, pid, stamp, rate, *, ttft=None, weight=1):
        meta = {
            "decode_tps": {"state": "ok", "observed_at": stamp.isoformat()},
            "ttft_mean_ms": {"state": "ok", "observed_at": stamp.isoformat(),
                             "metadata": {"count": weight, "mean": 9999}},
        }
        return MetricSample(profile_name=profile, pid=pid, engine_id="vllm",
                            observed_at=stamp, recorded_at=stamp, metric_meta=meta,
                            decode_tps=rate, ttft_mean_ms=ttft)

    old = row("profile", 10, at, 1000, ttft=100, weight=3)
    duplicate = row("profile", 10, at + timedelta(seconds=5), 1000)
    duplicate.metric_meta["decode_tps"]["observed_at"] = at.isoformat()
    async with database() as session:
        session.add_all([old, duplicate, row("other", 99, at, 500),
                         row("profile", 10, now - timedelta(hours=25), 9000),
                         row("profile", 11, now - timedelta(hours=1), 0),
                         row("profile", 11, now - timedelta(minutes=30), None)])
        session.add_all([row("profile", 11, at + timedelta(minutes=10, seconds=i * 5), 10,
                            ttft=300 if i == 0 else None) for i in range(130)])
        await session.commit()
    storage = MetricsStorage(database)
    aggregates = await storage.aggregates(start=start, end=now, profile_name="profile")
    assert aggregates["decode_tps"]["max"] == 1000
    assert aggregates["decode_tps"]["avg"] == pytest.approx(2300 / 131)
    assert aggregates["ttft_mean_ms"] == {"avg": 150, "max": 300}
    all_profiles = await storage.aggregates(start=start, end=now, all_profiles=True)
    assert all_profiles["decode_tps"]["max"] == 1500
    recent = await storage.aggregates(start=now - timedelta(hours=2), end=now,
                                      profile_name="profile")
    assert recent == {}
    service = MetricsService(object(), storage=storage)
    try:
        daily = await service.read(mode="aggregates", profile_name="profile")
        assert daily["aggregates"]["decode_tps"]["max"] == 1000
        assert daily["to"] - daily["from"] == timedelta(hours=24)
    finally:
        await service.close()


async def test_daily_run_list_does_not_load_or_compute_metric_history(database):
    from app.metrics.entities import MetricSample

    now = datetime.now(UTC)
    async with database() as session:
        session.add_all([MetricSample(
            profile_name=name, model_name="model", engine_id="llama.cpp", pid=pid,
            observed_at=stamp, recorded_at=stamp, metric_meta={}, decode_tps=rate,
        ) for name, pid, stamp, rate in [
            ("today", 10, now, 25), ("today", 10, now, 30),
            ("idle", 11, now, 0), ("unavailable", 12, now, None),
            ("yesterday", 13, now - timedelta(days=1), 50),
        ]])
        await session.commit()
    result = await MetricsStorage(database).daily_runs()
    assert {row["profile_name"] for row in result} == {"today", "idle"}
    assert len(result) == 2
    assert all(set(row) == {"profile_name", "model_name", "engine_id"} for row in result)


async def test_decode_window_uses_same_statistics_for_sqlite_and_remote(database):
    from app.metrics.entities import MetricSample
    from app.metrics.service import summarize_window
    from app.metrics.storage import sample_data

    end = datetime.now(UTC)
    start = end - timedelta(hours=24)
    at = end - timedelta(minutes=10)
    rows = []
    for index, (rate, requests, weight) in enumerate([(100, 2, 1), (90, 3, 3), (0, 0, 1)]):
        stamp = at + timedelta(seconds=index * 5)
        rows.append(MetricSample(
            profile_name="profile", pid=10, engine_id="llama.cpp",
            observed_at=stamp, recorded_at=stamp, decode_tps=rate, active_requests=requests,
            metric_meta={
                "decode_tps": {"state": "ok", "observed_at": stamp.isoformat(),
                               "metadata": {"denominator": weight}},
                "active_requests": {"state": "ok", "observed_at": stamp.isoformat()},
            },
        ))
    remote = summarize_window([sample_data(row) for row in rows], start, end, False)
    async with database() as session:
        session.add_all(rows)
        await session.commit()
    local = await MetricsStorage(database).aggregates(
        start=start, end=end, profile_name="profile",
    )
    assert local == remote
    assert local["decode_tps"] == {"max": 100, "avg": 92.5}
    assert local["per_request_tps"] == {"max": 50, "avg": 35}


def test_all_decode_per_request_divides_combined_rate_by_combined_requests():
    from app.metrics.service import summarize_window

    end = datetime.now(UTC)
    at = end - timedelta(minutes=1)
    rows = [{
        "profile_name": name, "pid": pid, "observed_at": at,
        "items": [Metric(name=field, value=value, state="ok", unit="", source="test",
                         observed_at=at)
                  for field, value in [("decode_tps", rate), ("active_requests", requests)]],
    } for name, pid, rate, requests in [("a", 10, 100, 2), ("b", 11, 90, 3)]]
    result = summarize_window(rows, end - timedelta(hours=24), end, True)
    assert result["decode_tps"] == {"avg": 190, "max": 190}
    assert result["per_request_tps"] == {"avg": 38, "max": 38}


def test_remote_daily_summary_ignores_cumulative_means_and_duplicate_measurements():
    from app.metrics.service import summarize_window

    end = datetime.now(UTC)
    at = end - timedelta(hours=12)
    def row(pid, value, count):
        return {"profile_name": "profile", "pid": pid, "observed_at": at,
                "items": [Metric(name="ttft_mean_ms", value=value, unit="ms",
                                 source="prometheus", state="ok", observed_at=at,
                                 metadata={"count": count, "mean": 9999})]}
    samples = [row(10, 100, 3), row(10, 100, 3), row(11, 300, 1), row(12, 0, 100)]
    result = summarize_window(samples, end - timedelta(hours=24), end, False)
    assert result["ttft_mean_ms"] == {"avg": 150, "max": 300}


async def test_last_vram_updates_only_on_change_and_survives_cleanup(database):
    from app.metrics.entities import ProfileStatistics

    storage = MetricsStorage(database)
    at = datetime.now(UTC) - timedelta(days=30)
    assert await storage.last_vram("profile") is None
    await storage.remember_vram("profile", metric("vram_bytes", 8000, at))
    await storage.remember_vram("profile", metric("vram_bytes", 8000, at + timedelta(seconds=5)))
    async with database() as session:
        row = await session.get(ProfileStatistics, "profile")
        assert row.vram_observed_at.replace(tzinfo=UTC) == at
    await storage.remember_vram("profile", metric("vram_bytes", 6000, at + timedelta(seconds=10)))
    await storage.remember_vram("profile", metric("vram_bytes", 9000, at))
    await storage.remember_vram("profile", metric("vram_bytes", 0))
    await storage.remember_vram("profile", metric("vram_bytes", 10000, state="stale"))
    await storage.cleanup(set())
    assert await MetricsStorage(database).last_vram("profile") == 6000
    assert await storage.last_vram("another") is None


async def test_last_vram_migration_restores_latest_valid_sample(tmp_path):
    from app.metrics.entities import ProfileStatistics

    settings = Settings(data_root=tmp_path / "data", host="127.0.0.1", port=3000, _env_file=None)
    paths = build_paths(settings)
    prepare_data_dirs(paths)
    config = Config(str(Path(__file__).parents[1] / "alembic.ini"))
    config.attributes["neural_deck_settings"] = settings
    config.attributes["configure_logging"] = False
    await asyncio.to_thread(command.upgrade, config, "0007_daily_metrics")
    engine = create_engine(paths.sqlite_path)
    sessions = create_session_factory(engine)
    storage = MetricsStorage(sessions)
    old = datetime.now(UTC) - timedelta(days=2)
    await storage.write([(instance(), MetricList(items=[metric("vram_bytes", 8000, old)]))], {})
    await storage.write([(instance(pid=20), MetricList(items=[metric(
        "vram_bytes", 6000, old + timedelta(seconds=5),
    )]))], {})
    await storage.write([(instance(pid=20), MetricList(items=[metric(
        "vram_bytes", 9000, old + timedelta(seconds=10), state="stale",
    )]))], {})
    try:
        await migrate_database(settings, paths)
        await migrate_database(settings, paths)
        async with sessions() as session:
            assert (await session.get(ProfileStatistics, "profile")).vram_bytes == 6000
    finally:
        await engine.dispose()


async def test_local_metric_read_persists_vram_before_background_sample(database, monkeypatch):
    manager = _Manager()
    storage = MetricsStorage(database)
    service = MetricsService(manager, storage=storage)

    async def collect(_instance_id):
        return MetricList(items=[metric("vram_bytes", 4096)])

    monkeypatch.setattr(service, "_collect_local", collect)
    try:
        await service.local(manager.instance.id)
        assert await MetricsStorage(database).last_vram("profile") == 4096
    finally:
        await service.close()


async def test_profile_statistics_rename_preserves_existing_vram(tmp_path):
    settings = Settings(data_root=tmp_path / "data", host="127.0.0.1", port=3000, _env_file=None)
    paths = build_paths(settings)
    prepare_data_dirs(paths)
    config = Config(str(Path(__file__).parents[1] / "alembic.ini"))
    config.attributes["neural_deck_settings"] = settings
    config.attributes["configure_logging"] = False
    await asyncio.to_thread(command.upgrade, config, "0008_profile_last_vram")
    engine = create_engine(paths.sqlite_path)
    try:
        async with engine.begin() as connection:
            await connection.execute(text(
                "INSERT INTO profile_last_vram (profile_name, value_bytes, observed_at) "
                "VALUES ('profile', 4096, '2026-10-04 13:00:00')"
            ))
        await migrate_database(settings, paths)
        await migrate_database(settings, paths)
        storage = MetricsStorage(create_session_factory(engine))
        assert await storage.last_vram("profile") == 4096
        async with engine.connect() as connection:
            assert await connection.scalar(text(
                "SELECT count(*) FROM sqlite_master WHERE name='profile_last_vram'"
            )) == 0
            assert await connection.scalar(text(
                "SELECT vram_observed_at FROM profile_statistics WHERE profile_name='profile'"
            )) == "2026-10-04 13:00:00"
    finally:
        await engine.dispose()
