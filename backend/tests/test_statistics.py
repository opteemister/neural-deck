from datetime import UTC, datetime, timedelta

import pytest
from app.db import Base, create_engine, create_session_factory
from app.journal import RequestJournalRecord
from app.profiles.repository import ProfileRepository
from app.profiles.service import ProfileService
from app.schemas.profiles import ProfileData, ProfileEngine, ProfileModel
from app.statistics import StatisticsService, _cost, _load
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
    async with database() as session:
        session.add_all([
            row(
                "one", start, start + timedelta(minutes=10), status_code=200,
                input_tokens=100, cached_input_tokens=20, output_tokens=50, client_id=1,
            ),
            row(
                "two", start + timedelta(minutes=5), start + timedelta(minutes=15),
                status_code=500, error_code="upstream_error", input_tokens=100,
                cached_input_tokens=10, output_tokens=20, client_id=1,
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
    assert result["clients"][0]["label"] == "Клиент #1"
    assert item["load"]["max_concurrency"] == 2
    assert item["load"]["active_seconds"] == 1200.0
    assert item["load"]["percent"] == pytest.approx(1.389, abs=0.001)
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


def test_invalid_cached_subset_does_not_become_a_negative_bill():
    at = datetime(2026, 10, 1, tzinfo=UTC)
    request = row(
        "one", at, at + timedelta(seconds=1), status_code=200,
        input_tokens=100, cached_input_tokens=101, output_tokens=20,
    )
    cost = _cost([request], profile())
    assert cost["state"] == "unknown"
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
