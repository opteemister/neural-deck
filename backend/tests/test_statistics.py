from datetime import UTC, datetime, timedelta

import pytest
from app.db import Base, create_engine, create_session_factory
from app.journal import RequestJournalRecord
from app.profiles.repository import ProfileRepository
from app.profiles.service import ProfileService
from app.schemas.profiles import ProfileData, ProfileEngine, ProfileModel
from app.statistics import StatisticsService, _cost, _cost_by_profile, _load
from app.token_stats import RequestJournalService


@pytest.fixture
async def database(tmp_path):
    engine = create_engine(tmp_path / "statistics.db")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    yield create_session_factory(engine)
    await engine.dispose()


def profile() -> ProfileData:
    return ProfileData(
        version=1,
        name="qwen",
        model_name="qwen3",
        engine=ProfileEngine(id="vllm", installation_id="vllm-test"),
        model=ProfileModel(path="/models/qwen"),
        command="{{engine}} serve --model {{model}}",
        input_price_usd_per_million_tokens=1.0,
        cached_input_price_usd_per_million_tokens=0.5,
        output_price_usd_per_million_tokens=2.0,
    )


def row(index: str, start: datetime, finish: datetime | None, **values):
    return RequestJournalRecord(
        id=index,
        request_id=index,
        created_at=start,
        route="/v1/chat/completions",
        model_name="qwen3",
        profile_name="qwen",
        latency_ms=(finish - start).total_seconds() * 1000 if finish else 0,
        streaming=0,
        started_at=start,
        finished_at=finish,
        **values,
    )


async def test_statistics_uses_utc_daily_buckets_and_time_weighted_load(database, tmp_path):
    start = datetime(2026, 10, 1, 10, tzinfo=UTC)
    journal = RequestJournalService(session_factory=database)
    client_id = await journal.identify_client("Bearer test-client", "127.0.0.1")
    async with database() as session:
        session.add_all([
            row(
                "one", start, start + timedelta(minutes=10), status_code=200,
                input_tokens=100, cached_input_tokens=20, output_tokens=50, client_id=client_id,
            ),
            row(
                "two", start + timedelta(minutes=5), start + timedelta(minutes=15),
                status_code=500, error_code="upstream_error", input_tokens=100,
                cached_input_tokens=10, output_tokens=20, client_id=client_id,
            ),
        ])
        await session.commit()

    repository = ProfileRepository(tmp_path / "profiles")
    repository.create(profile())
    service = StatisticsService(
        RequestJournalService(session_factory=database),
        ProfileService(tmp_path / "profiles", repository=repository, process_manager=object()),
    )
    result = await service.read(
        period="custom",
        from_=start.replace(hour=0),
        to=start.replace(hour=12),
    )
    item = result["profiles"][0]
    assert result["period"]["timezone"] == "UTC"
    assert result["period"]["bucket"] == "day"
    assert result["totals"]["requests"]["failed"] == 1
    assert result["clients"][0]["label"] == "test-client"
    # Journal concurrency must not masquerade as engine measurements.
    assert item["load"]["max_concurrency"] == 0
    assert item["load"]["state"] == "unknown"
    assert result["activity"][0]["requests"]["total"] == 2
    assert result["summary"]["active_days"] == 1
    assert result["summary"]["success_rate"] == 50
    assert result["summary"]["sessions"] is None
    assert result["totals"]["performance"]["latency_p95_ms"] == 600_000


def test_cost_subtracts_cached_input_from_regular_input_price():
    at = datetime(2026, 10, 1, tzinfo=UTC)
    profile_data = profile()
    request = row(
        "one", at, at + timedelta(seconds=1), status_code=200,
        input_tokens=1_000_000, cached_input_tokens=250_000, output_tokens=500_000,
    )
    cost = _cost([request], profile_data)
    assert cost["state"] == "ok"
    assert cost["usd"] == pytest.approx(1.875)


@pytest.mark.parametrize("error_code", ["interrupted", "client_disconnected"])
@pytest.mark.parametrize("tokens", [None, 1000])
async def test_cancelled_requests_are_excluded_everywhere(database, tmp_path, error_code, tokens):
    start = datetime(2026, 10, 1, 10, tzinfo=UTC)
    journal = RequestJournalService(session_factory=database)
    client_id = await journal.identify_client("Bearer test-client", "127.0.0.1")
    async with database() as session:
        session.add_all([
            row("success", start, start + timedelta(seconds=10), status_code=200,
                input_tokens=100, cached_input_tokens=20, output_tokens=50, client_id=client_id),
            row("cancelled", start, start + timedelta(seconds=20), status_code=200,
                error_code=error_code, input_tokens=tokens, cached_input_tokens=tokens,
                output_tokens=tokens, client_id=client_id),
            row("cancelled-day", start - timedelta(days=1), start - timedelta(days=1) + timedelta(seconds=20),
                error_code=error_code, input_tokens=tokens, output_tokens=tokens, client_id=99),
        ])
        await session.commit()
    repository = ProfileRepository(tmp_path / "profiles")
    repository.create(profile())
    result = await StatisticsService(journal, ProfileService(
        tmp_path / "profiles", repository=repository, process_manager=object(),
    )).read(period="custom", from_=start - timedelta(days=2), to=start + timedelta(hours=1))
    assert len(result["clients"]) == 1
    for aggregate in [result["totals"], result["profiles"][0], result["clients"][0], result["activity"][-1]]:
        assert aggregate["requests"] == {"total": 1, "success": 1, "failed": 0, "interrupted": 0, "unknown": 0}
        assert aggregate["tokens"]["input"] == {"known_total": 100, "unknown_requests": 0}
        assert aggregate["tokens"]["output"] == {"known_total": 50, "unknown_requests": 0}
        assert aggregate["tokens"]["cached_input"] == {"known_total": 20, "unknown_requests": 0}
    assert result["totals"]["cost"]["state"] == "ok"
    assert result["totals"]["cost"]["usd"] == pytest.approx(0.00019)
    assert result["summary"]["active_days"] == 1
    assert result["summary"]["success_rate"] == 100
    assert result["totals"]["performance"]["latency_ms"] == 10_000
    assert result["profiles"][0]["load"]["state"] == "unknown"


async def test_client_labels_use_token_or_anonymous_ip(database):
    journal = RequestJournalService(session_factory=database)
    token_id = await journal.identify_client("Bearer test-client", "127.0.0.1")
    anonymous_id = await journal.identify_client(None, "127.0.0.2")
    assert await journal.client_labels() == {token_id: "test-client", anonymous_id: "127.0.0.2"}


async def test_profile_performance_and_load_use_engine_metrics(database, tmp_path):
    from app.metrics.storage import MetricsStorage
    from app.schemas.instances import Instance, Metric, MetricList

    start = datetime(2026, 10, 1, tzinfo=UTC)
    storage = MetricsStorage(database)
    instance = Instance(id="managed/qwen/10", profile_name="qwen", source="managed",
                        engine_id="vllm", pid=10, state="ready", observed_at=start)
    for seconds, prefill, decode, concurrency in [(0, 1000, 100, 2), (5, 2000, 200, 2), (10, 0, 0, 0)]:
        at = start + timedelta(seconds=seconds)
        await storage.write([(instance, MetricList(items=[
            Metric(name=name, value=value, observed_at=at, source="test", state="ok")
            for name, value in [("prefill_tps", prefill), ("decode_tps", decode), ("active_requests", concurrency)]
        ]))], {})
    repository = ProfileRepository(tmp_path / "profiles")
    repository.create(profile())
    result = await StatisticsService(RequestJournalService(session_factory=database),
        ProfileService(tmp_path / "profiles", repository=repository, process_manager=object()),
        storage).read(period="custom", from_=start, to=start + timedelta(days=1))
    item = result["profiles"][0]
    assert item["performance"]["prefill_tps"] == 1500
    assert item["performance"]["decode_tps"] == 150
    assert item["load"]["max_concurrency"] == 2
    assert item["load"]["active_seconds"] == 20
    assert item["load"]["percent"] == pytest.approx(20 / 86400 / 2 * 100)
    assert item["load"]["state"] == "ok"
    assert item["requests"]["total"] == 0


def test_invalid_cached_subset_does_not_become_a_negative_bill():
    at = datetime(2026, 10, 1, tzinfo=UTC)
    request = row(
        "one", at, at + timedelta(seconds=1), status_code=200,
        input_tokens=100, cached_input_tokens=101, output_tokens=20,
    )
    cost = _cost([request], profile())
    assert cost["state"] == "unknown"
    assert cost["unknown_requests"] == 1


@pytest.mark.parametrize("missing_field", ["input_tokens", "cached_input_tokens", "output_tokens"])
def test_partial_cost_preserves_known_total_without_assuming_missing_tokens_are_zero(missing_field):
    at = datetime(2026, 10, 1, tzinfo=UTC)
    values = {"input_tokens": 1_000_000, "cached_input_tokens": 250_000, "output_tokens": 500_000}
    known = row("known", at, at + timedelta(seconds=1), status_code=200, **values)
    unknown = row("unknown", at, at + timedelta(seconds=1), status_code=200,
                  **{**values, missing_field: None})
    for cost in (_cost([known, unknown], profile()),
                 _cost_by_profile([known, unknown], {"qwen": profile()})):
        assert cost["usd"] == pytest.approx(1.875)
        assert cost["state"] == "partial"
        assert cost["unknown_requests"] == 1
    assert _cost([unknown], profile())["usd"] is None


def test_mixed_profile_cost_skips_missing_prices_without_hiding_known_cost():
    at = datetime(2026, 10, 1, tzinfo=UTC)
    known = row("known", at, at + timedelta(seconds=1), status_code=200,
                input_tokens=1_000_000, cached_input_tokens=250_000, output_tokens=500_000)
    unpriced = row("unpriced", at, at + timedelta(seconds=1), status_code=200,
                   input_tokens=100, cached_input_tokens=0, output_tokens=50)
    unpriced.profile_name = "external"
    for rows in ([known, unpriced], [unpriced, known]):
        cost = _cost_by_profile(rows, {"qwen": profile()})
        assert cost["usd"] == pytest.approx(1.875)
        assert cost["state"] == "partial"
        assert cost["unknown_requests"] == 1
    assert _cost_by_profile([unpriced], {})["state"] == "unavailable"
    assert _cost_by_profile([unpriced], {})["usd"] is None


def test_zero_priced_cost_is_still_known_when_other_requests_are_unknown():
    at = datetime(2026, 10, 1, tzinfo=UTC)
    known = row("known", at, at + timedelta(seconds=1), status_code=200,
                input_tokens=0, cached_input_tokens=0, output_tokens=0)
    unknown = row("unknown", at, at + timedelta(seconds=1), status_code=500)
    cost = _cost([known, unknown], profile())
    assert cost["usd"] == 0
    assert cost["state"] == "partial"
    assert cost["unknown_requests"] == 1


def test_load_is_zero_when_profile_has_no_active_request():
    start = datetime(2026, 10, 1, tzinfo=UTC)
    assert _load([], "qwen", start, start + timedelta(hours=1), 0)["percent"] == 0


@pytest.mark.parametrize("period,expected", [
    ("week", "2026-09-28"), ("month", "2026-09-05"),
    ("quarter", "2026-07-04"), ("half_year", "2026-04-04"),
    ("year", "2025-09-29"),
])
async def test_named_periods_and_empty_summary(database, tmp_path, monkeypatch, period, expected):
    import app.statistics as statistics

    class FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 10, 4, 12, tzinfo=UTC)

    monkeypatch.setattr(statistics, "datetime", FixedDatetime)
    service = StatisticsService(
        RequestJournalService(session_factory=database),
        ProfileService(tmp_path / "profiles", process_manager=object()),
    )
    result = await service.read(period=period)
    assert result["period"]["from"].date().isoformat() == expected
    assert result["summary"]["active_days"] == 0
    assert result["summary"]["success_rate"] is None
    assert result["summary"]["streak_days"] == 0
    assert result["totals"]["tokens"]["input"]["known_total"] == 0
    assert result["totals"]["cost"]["usd"] == 0
    assert result["totals"]["performance"]["latency_p95_ms"] is None
    if period == "year":
        assert len(result["activity"]) == 371
        assert result["period"]["from"].weekday() == 0
