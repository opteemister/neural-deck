from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from app.profiles.ports import resolve_effective_port
from app.profiles.summary import build_profile_summary, utc_day_window
from app.schemas.instances import Instance, InstanceList, Metric, MetricList
from app.schemas.profiles import ProfileData


def profile(name: str, command: str = "{{engine}} serve {{model}}") -> ProfileData:
    return ProfileData(
        version=1,
        name=name,
        model_name=name,
        engine={"id": "vllm", "installation_id": "vllm-test"},
        model={"path": f"/models/{name}.gguf"},
        command=command,
    )


@pytest.mark.parametrize(
    ("engine", "command", "port", "source"),
    [
        ("vllm", "serve --port 9100", 9100, "command"),
        ("llama.cpp", "llama-server", 8080, "engine_default"),
        ("sglang", "serve", 30000, "engine_default"),
        ("freetoken", "ft serve --port=1920", 1920, "command"),
    ],
)
def test_effective_port_prefers_command_and_has_engine_default(
    engine: str, command: str, port: int, source: str
):
    result = resolve_effective_port(engine, command)

    assert result.port == port
    assert result.state == "ok"
    assert result.source == source


def test_effective_port_does_not_execute_dynamic_command_port():
    result = resolve_effective_port("vllm", "serve --port $PORT")

    assert result.port is None
    assert result.state == "unknown"
    assert result.reason == "dynamic_port"


def test_utc_day_window_uses_utc_calendar_date():
    start, end = utc_day_window(datetime(2026, 10, 4, 1, 30, tzinfo=UTC))

    assert start == datetime(2026, 10, 4, tzinfo=UTC)
    assert end == datetime(2026, 10, 4, 1, 30, tzinfo=UTC)


@pytest.mark.asyncio
async def test_profile_summary_uses_disk_size_metrics_limit_and_default_port():
    first = profile("first", "{{engine}} serve --port 9100")
    second = profile("second")
    third = profile("third")
    observed_at = datetime(2026, 10, 4, 12, tzinfo=UTC)
    instance = Instance(
        id="managed/first/101",
        source="managed",
        profile_name="first",
        engine_id="vllm",
        pid=101,
        process_start_time_ns=1,
        state="running",
        observed_at=observed_at,
    )

    class Storage:
        async def aggregates(self, *, profile_name, **_kwargs):
            return {
                "first": {
                    "prefill_tps": {"avg": 10.0, "max": 12.0},
                    "decode_tps": {"avg": 20.0, "max": 22.0},
                    "active_requests": {"avg": 2.0, "max": 4.0},
                    "vram_bytes": {"avg": 1_073_741_824.0, "max": 2_147_483_648.0},
                }
            }.get(profile_name, {})

    class ProcessManager:
        async def refresh(self, *, probe_models):
            assert probe_models is False
            return InstanceList(items=[instance])

    class Metrics:
        process_manager = ProcessManager()
        storage = Storage()

        async def local(self, instance_id):
            assert instance_id == instance.id
            return MetricList(
                items=[
                    Metric(
                        name="vram_bytes",
                        value=1_500_000_000,
                        unit="bytes",
                        source="nvidia-smi:process",
                        observed_at=observed_at,
                        state="ok",
                    )
                ]
            )

    catalogue = SimpleNamespace(
        items=lambda: [
            SimpleNamespace(path="/models/first.gguf", size_bytes=4_000_000_000)
        ]
    )
    start = datetime(2026, 10, 4, tzinfo=UTC)
    end = datetime(2026, 10, 4, 13, tzinfo=UTC)

    result = await build_profile_summary(
        [first, second, third],
        metrics=Metrics(),
        catalogue=catalogue,
        start=start,
        end=end,
    )

    first_summary, second_summary, third_summary = result.items
    assert first_summary.performance.prefill_tps.value == 10
    assert first_summary.memory.disk_bytes.value == 4_000_000_000
    assert first_summary.memory.vram_current_bytes.value == 1_500_000_000
    assert first_summary.concurrency.limit.value == 4
    assert first_summary.concurrency.optimal.state == "not_measured"
    assert first_summary.endpoint.port == 9100
    assert first_summary.endpoint.conflict is False
    assert first_summary.endpoint.conflict_with == []
    assert second_summary.endpoint.port == 8000
    assert second_summary.endpoint.conflict is True
    assert second_summary.endpoint.conflict_with == ["third"]
    assert third_summary.endpoint.conflict_with == ["second"]
    assert second_summary.memory.disk_bytes.state == "unavailable"
    assert second_summary.memory.vram_current_bytes.state == "unavailable"
    assert result.period.model_dump(mode="json", by_alias=True)["from"] == "2026-10-04T00:00:00Z"
