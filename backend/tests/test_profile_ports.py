from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import Mock

import psutil
import pytest
from app.config import Settings
from app.db import create_engine, create_session_factory, migrate_database
from app.errors import AppError
from app.metrics.storage import MetricsStorage
from app.paths import build_paths, prepare_data_dirs
from app.profiles import runtime_ports
from app.profiles.formatting import substitute_placeholders
from app.profiles.runtime_ports import ProfilePorts, configured_profile_ports, listening_ports
from app.profiles.summary import build_profile_summary
from app.schemas.instances import Instance, InstanceList, MetricList
from app.schemas.profiles import ProfileData


def instance(**updates):
    return Instance.model_validate({
        "id": "managed/demo/123", "profile_id": "profile-1", "profile_name": "demo",
        "source": "managed", "engine_id": "vllm", "pid": 123,
        "process_start_time_ns": 2_000_000_000, "port": 8000, "state": "running",
        "observed_at": datetime.now(UTC), **updates,
    })


def profile(**updates):
    return ProfileData.model_validate({
        "version": 1, "profile_id": "a0000000-0000-4000-8000-000000000001",
        "name": "demo", "model_name": "demo",
        "engine": {"id": "vllm", "installation_id": "inst-1"},
        "command": "serve --port 8000", **updates,
    })


def listener(port, status=psutil.CONN_LISTEN):
    return SimpleNamespace(status=status, laddr=SimpleNamespace(port=port))


def process(ports):
    return Mock(
        create_time=Mock(return_value=2.0), is_running=Mock(return_value=True),
        children=Mock(return_value=[]), net_connections=Mock(return_value=ports),
    )


def test_listeners_include_children_deduplicate_and_ignore_non_listeners(monkeypatch):
    root = process([listener(9100), listener(9100), listener(9200, psutil.CONN_ESTABLISHED)])
    child = process([listener(8100)])
    denied = process([])
    denied.net_connections.side_effect = psutil.AccessDenied(456)
    root.children.return_value = [child, denied]
    monkeypatch.setattr(runtime_ports.psutil, "Process", lambda pid: root)

    assert listening_ports(instance()) == [8100, 9100]
    root.net_connections.assert_called_once_with(kind="tcp")
    root.children.assert_called_once_with(recursive=True)


@pytest.mark.parametrize("start_ns", [None, 3_000_000_000])
def test_does_not_scan_unverified_or_reused_pid(monkeypatch, start_ns):
    root = process([listener(9100)])
    monkeypatch.setattr(runtime_ports.psutil, "Process", lambda pid: root)
    assert listening_ports(instance(process_start_time_ns=start_ns)) == []
    root.net_connections.assert_not_called()


def test_discards_ports_when_root_exits_during_scan(monkeypatch):
    root = process([listener(9100)])
    root.is_running.side_effect = [True, False, False]
    monkeypatch.setattr(runtime_ports.psutil, "Process", lambda pid: root)
    assert listening_ports(instance()) == []


def test_discards_ports_from_exited_child(monkeypatch):
    root = process([listener(8100)])
    child = process([listener(9100)])
    child.is_running.side_effect = [True, False]
    root.children.return_value = [child]
    monkeypatch.setattr(runtime_ports.psutil, "Process", lambda pid: root)
    assert listening_ports(instance()) == [8100]


def test_uses_launch_configuration_if_socket_access_denied(monkeypatch):
    monkeypatch.setattr(runtime_ports.psutil, "Process", Mock(side_effect=psutil.AccessDenied(123)))
    assert runtime_ports.observe_instance_ports(instance()) == ProfilePorts(
        [8000], "launch_configuration"
    )


@pytest.mark.parametrize(("updates", "ports", "source"), [
    ({"port": 9000}, [9000], "profile_config"),
    ({"command": "serve", "port": 9000}, [9000], "profile_config"),
    ({"command": "serve"}, [8000], "engine_default"),
    ({"command": "serve --port $PORT"}, [], "command"),
    ({"command": "serve --port {{port}}", "port": 8013}, [8013], "profile_config"),
])
def test_configured_port_sources(updates, ports, source):
    result = configured_profile_ports(profile(**updates))
    assert result.ports == ports
    assert result.source == source


@pytest.fixture
async def database(tmp_path):
    settings = Settings(
        data_root=tmp_path / "data", host="127.0.0.1", port=3000, _env_file=None
    )
    paths = build_paths(settings)
    prepare_data_dirs(paths)
    await migrate_database(settings, paths)
    engine = create_engine(paths.sqlite_path)
    yield engine, paths.sqlite_path
    await engine.dispose()


async def summary(profiles, instances, storage):
    async def refresh(**kwargs):
        return InstanceList(items=instances)

    async def local(instance_id):
        return MetricList(items=[])

    return await build_profile_summary(
        profiles, metrics=SimpleNamespace(
            process_manager=SimpleNamespace(refresh=refresh), storage=storage, local=local,
        ), catalogue=SimpleNamespace(items=lambda: []),
        start=datetime(2026, 10, 28, tzinfo=UTC), end=datetime.now(UTC),
    )


@pytest.mark.parametrize("running", [False, True])
async def test_explicit_port_has_priority_without_scanning_process(database, monkeypatch, running):
    engine, _ = database
    storage = MetricsStorage(create_session_factory(engine))
    selected = profile(port=8013)
    scan = Mock(side_effect=AssertionError("Explicit profile port needs no socket scan"))
    monkeypatch.setattr(runtime_ports, "listening_ports", scan)
    instances = [instance(profile_id=selected.profile_id, port=9000)] if running else []
    result = await summary([selected], instances, storage)
    assert result.items[0].endpoint.ports == [8013]
    assert result.items[0].endpoint.source == "profile_config"
    scan.assert_not_called()


async def test_live_ports_override_command_when_profile_port_is_empty(database, monkeypatch):
    engine, _ = database
    selected = profile()
    monkeypatch.setattr(runtime_ports, "listening_ports", lambda item: [8100, 9100])
    result = await summary(
        [selected], [instance(profile_id=selected.profile_id)],
        MetricsStorage(create_session_factory(engine)),
    )
    assert result.items[0].endpoint.ports == [8100, 9100]
    assert result.items[0].endpoint.port == 8100
    assert result.items[0].endpoint.source == "process_sockets"


async def test_missing_sockets_use_launch_configuration(database, monkeypatch):
    engine, _ = database
    selected = profile()
    monkeypatch.setattr(runtime_ports, "listening_ports", lambda item: [])
    result = await summary(
        [selected], [instance(profile_id=selected.profile_id, port=9000)],
        MetricsStorage(create_session_factory(engine)),
    )
    assert result.items[0].endpoint.ports == [9000]
    assert result.items[0].endpoint.source == "launch_configuration"


async def test_stopped_profile_uses_current_command_without_cache(database, monkeypatch):
    engine, _ = database
    storage = MetricsStorage(create_session_factory(engine))
    monkeypatch.setattr(runtime_ports, "listening_ports", Mock(side_effect=AssertionError))
    assert (await summary([profile()], [], storage)).items[0].endpoint.ports == [8000]
    changed = profile(command="serve --port 9999")
    result = await summary([changed], [], MetricsStorage(create_session_factory(engine)))
    assert result.items[0].endpoint.ports == [9999]
    assert result.items[0].endpoint.source == "command"


@pytest.mark.parametrize("running", [False, True])
async def test_sglang_template_uses_explicit_profile_port(database, monkeypatch, running):
    engine, _ = database
    selected = profile(
        name="uocr2", model_name="uocr2",
        engine={"id": "sglang", "installation_id": "inst-1"},
        command="{{engine}} serve --host 0.0.0.0 --port {{port}}", port=8013,
    )
    monkeypatch.setattr(runtime_ports, "listening_ports", Mock(side_effect=AssertionError))
    instances = [instance(profile_id=selected.profile_id)] if running else []
    result = await summary([selected], instances, MetricsStorage(create_session_factory(engine)))
    assert result.items[0].endpoint.ports == [8013]
    assert result.items[0].endpoint.source == "profile_config"


async def test_conflict_in_secondary_port_is_reported(database, monkeypatch):
    engine, _ = database
    first = profile()
    second = profile(
        name="second", profile_id="b0000000-0000-4000-8000-000000000001",
        command="serve --port 9100",
    )
    monkeypatch.setattr(runtime_ports, "listening_ports", lambda item: [8100, 9100])
    result = await summary(
        [first, second], [instance(profile_id=first.profile_id)],
        MetricsStorage(create_session_factory(engine)),
    )
    assert result.items[0].endpoint.conflict_with == ["second"]
    assert result.items[1].endpoint.conflict_with == ["demo"]


def test_empty_comfyui_port_is_not_silently_filled():
    selected = profile(
        engine={"id": "comfyui", "installation_id": "inst-1"},
        command="{{engine}} --listen 127.0.0.1 --port 8188",
    )
    assert selected.port is None
    assert ProfileData.model_validate(selected.model_dump()).port is None


def test_port_placeholder_requires_explicit_value_without_8188_fallback():
    selected = profile(
        engine={"id": "comfyui", "installation_id": "inst-1"},
        command="{{engine}} --port {{port}}",
    )
    with pytest.raises(AppError) as error:
        substitute_placeholders(
            selected.command, engine="/bin/comfyui", model=None, port=selected.port
        )
    assert error.value.code == "port_missing"
