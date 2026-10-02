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
from app.metrics.service import MetricsService
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
    config.attributes["llm_studio_settings"] = settings
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
        assert "studio_model_decode_tps" in service.export()
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
        Manager(), storage=storage, prometheus_url="http://prom", studio_id="studio"
    )
    now = datetime.now(UTC).timestamp()
    stamp = now - 2
    labels = {"studio_id": "studio", "profile_name": "profile", "pid": "10", "engine_id": "vllm"}

    def handle(request):
        return httpx.Response(
            200,
            json={
                "status": "success",
                "data": {
                    "result": [
                        {
                            "metric": {**labels, "__name__": "studio_model_decode_tps"},
                            "value": [now, "99"],
                        },
                        {
                            "metric": {
                                **labels,
                                "__name__": "studio_model_observed_timestamp_seconds",
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
        assert (
            next(item for item in payload["current"]["items"] if item["name"] == "decode_tps")[
                "value"
            ]
            == 0
        )
        assert client.get("/metrics").status_code == 200
        assert client.get("/api/v1/metrics?mode=usage").json()["data"]["items"] == []
        assert client.get("/api/v1/metrics?mode=history&limit=0").status_code == 422
