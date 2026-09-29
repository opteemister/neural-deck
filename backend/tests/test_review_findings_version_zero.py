"""Focused regression tests for the final Version Zero review findings."""

from __future__ import annotations

import asyncio
import json
import signal
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
from app.db import create_engine, create_session_factory
from app.mcp.server import MCPDependencies, create_mcp_server
from app.metrics.providers import NInferMetricsProvider, VllmMetricsProvider
from app.models import entities
from app.process.discovery import ExternalObservation, discover_external
from app.process.manager import ProcessManager, _Managed
from app.profiles.repository import ProfileRepository
from app.profiles.service import ProfileService
from app.proxy.service import ProxyService
from app.schemas.instances import Instance
from app.schemas.profiles import ProfileData
from app.system import collect_status
from app.token_stats import RequestJournalService


def _profile() -> ProfileData:
    return ProfileData.model_validate(
        {
            "version": 1,
            "name": "demo",
            "model_name": "demo-model",
            "engine": {"id": "vllm", "installation_id": "inst"},
            "model": {"path": "/models/demo"},
            "command": "{{engine}} --model {{model}} --port 1919",
        }
    )


@pytest.mark.version_zero
def test_managed_start_does_not_invent_identity_when_create_time_is_unknown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class Process:
        pid = 901
        returncode = None
        terminated = False

        def terminate(self) -> None:
            self.terminated = True

        async def wait(self) -> int:
            self.returncode = -15
            return self.returncode

        def kill(self) -> None:
            self.returncode = -9

    process = Process()

    async def spawn(*args: object, **kwargs: object) -> Process:
        del args, kwargs
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    monkeypatch.setattr("app.process.manager._identity_start_ns", lambda pid: None)
    manager = ProcessManager(log_root=tmp_path, username="studio")
    manager.refresh = _empty_refresh.__get__(manager, ProcessManager)  # type: ignore[method-assign]

    async def run() -> None:
        with pytest.raises(Exception) as error:
            await manager.start_profile(_profile(), "/bin/echo")
        assert getattr(error.value, "code", None) == "instance_identity_unavailable"
        assert process.terminated is True
        assert manager._managed == {}

    asyncio.run(run())


@pytest.mark.version_zero
def test_external_discovery_keeps_process_without_real_create_time_read_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Process:
        pid = 902

        def cmdline(self) -> list[str]:
            return ["python", "-m", "vllm.entrypoints.openai.api_server"]

        def name(self) -> str:
            return "python"

        def create_time(self) -> float:
            raise PermissionError("hidden")

    monkeypatch.setattr("app.process.discovery.psutil.process_iter", lambda *a, **k: [Process()])

    async def run() -> None:
        found = await discover_external(username="studio")
        assert len(found) == 1
        assert found[0].instance.process_start_time_ns is None
        assert found[0].instance.identity_verified is False
        assert found[0].instance.can_stop is False
        assert found[0].instance.id == "external/vllm/902-unknown"

    asyncio.run(run())


@pytest.mark.version_zero
def test_external_identity_exposes_same_user_stop_and_other_user_read_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Process:
        def __init__(self, pid: int, user: str) -> None:
            self.pid = pid
            self.user = user

        def cmdline(self) -> list[str]:
            return ["python", "-m", "vllm.entrypoints.openai.api_server"]

        def name(self) -> str:
            return "python"

        def create_time(self) -> float:
            return float(self.pid)

        def username(self) -> str:
            return self.user

    monkeypatch.setattr(
        "app.process.discovery.psutil.process_iter",
        lambda *args, **kwargs: [Process(903, "studio"), Process(904, "other")],
    )

    async def run() -> None:
        found = await discover_external(username="studio")
        by_pid = {item.instance.pid: item.instance for item in found}
        assert by_pid[903].identity_verified is True
        assert by_pid[903].can_stop is True
        assert by_pid[904].identity_verified is True
        assert by_pid[904].can_stop is False

    asyncio.run(run())


@pytest.mark.version_zero
def test_external_stop_rejects_unverified_capability(tmp_path: Path) -> None:
    instance = Instance(
        id="external/vllm/905-unknown",
        source="external",
        profile_name=None,
        engine_id="vllm",
        pid=905,
        process_start_time_ns=None,
        identity_verified=False,
        can_stop=False,
        port=1919,
        model_name="m",
        state="ready",
        observed_at=datetime.now(UTC),
    )
    manager = ProcessManager(log_root=tmp_path)
    manager._external[instance.id] = ExternalObservation(instance=instance, upstream_models=["m"])
    manager.refresh = _empty_refresh.__get__(manager, ProcessManager)  # type: ignore[method-assign]

    async def run() -> None:
        with pytest.raises(Exception) as error:
            await manager.stop(instance.id)
        assert getattr(error.value, "code", None) == "instance_stop_not_allowed"

    asyncio.run(run())


@pytest.mark.version_zero
def test_profile_yaml_read_is_off_event_loop(tmp_path: Path) -> None:
    repository = ProfileRepository(tmp_path / "profiles")
    repository.create(_profile())
    main_thread = threading.get_ident()
    seen: list[int] = []
    original = repository.get

    def get(name: str) -> ProfileData:
        seen.append(threading.get_ident())
        return original(name)

    repository.get = get  # type: ignore[method-assign]

    class Manager:
        async def start_profile(self, profile: ProfileData, engine_path: str) -> ProfileData:
            return profile

    service = ProfileService(
        tmp_path / "profiles",
        repository=repository,
        process_manager=Manager(),
        installation_resolver=lambda engine, installation: "/bin/echo",
    )

    async def run() -> None:
        await service.start("demo")

    asyncio.run(run())
    assert seen and seen[0] != main_thread


@pytest.mark.version_zero
def test_duplicate_request_ids_are_retained_as_separate_journal_rows(tmp_path: Path) -> None:
    async def run() -> None:
        engine = create_engine(tmp_path / "journal.db")
        factory = create_session_factory(engine)
        async with engine.begin() as connection:
            await connection.run_sync(entities.Base.metadata.create_all)
        journal = RequestJournalService(session_factory=factory)
        values = dict(
            request_id="same",
            route="/v1/chat/completions",
            model_name="m",
            profile_name=None,
            instance_id=None,
            engine_id=None,
            status_code=200,
            latency_ms=1,
            input_tokens=None,
            output_tokens=None,
            created_at=datetime.now(UTC),
        )
        await journal.record(**values)
        await journal.record(**values)
        stats = await journal.stats()
        assert stats.items[0].requests == 2
        await engine.dispose()

    asyncio.run(run())


@pytest.mark.version_zero
def test_journal_failure_is_visible_without_breaking_proxy_success() -> None:
    class Journal:
        last_error: str | None = None

        async def record_safely(self, **kwargs: Any) -> bool:
            del kwargs
            raise RuntimeError("sqlite locked")

    async def run() -> None:
        from app.process.registry import ModelRegistry

        instance = Instance(
            id="managed/p/1-2",
            source="managed",
            profile_name="p",
            engine_id="vllm",
            pid=1,
            process_start_time_ns=2,
            port=1919,
            model_name="public",
            state="ready",
            observed_at=datetime.now(UTC),
        )

        class Manager:
            registry = ModelRegistry()

            def __init__(self) -> None:
                self.registry.register_instance(instance, upstream_models=["upstream"])

            async def refresh(self) -> None:
                return None

            def model_registry(self) -> ModelRegistry:
                return self.registry

            async def get(self, instance_id: str) -> Instance:
                assert instance_id == instance.id
                return instance

        def handler(request: httpx.Request) -> httpx.Response:
            del request
            return httpx.Response(200, json={"ok": True})

        service = ProxyService(
            Manager(),
            journal=Journal(),  # type: ignore[arg-type]
            client_factory=lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        )
        response = await service.forward(
            route="/v1/chat/completions",
            payload={"model": "public", "messages": []},
            headers={},
            request_id="same",
        )
        assert response.status_code == 200

    asyncio.run(run())


@pytest.mark.version_zero
def test_metrics_endpoint_mapping_and_stale_after() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if len(calls) == 1:
            return httpx.Response(200, text="vllm_metric 1\n")
        return httpx.Response(503, text="down")

    instance = Instance(
        id="managed/p/1-2",
        source="managed",
        profile_name="p",
        engine_id="vllm",
        pid=1,
        process_start_time_ns=2,
        port=1919,
        model_name="m",
        state="ready",
        observed_at=datetime.now(UTC),
    )

    async def run() -> None:
        provider = VllmMetricsProvider(
            client=httpx.AsyncClient(transport=httpx.MockTransport(handler)), stale_after=1
        )
        assert (await provider.collect(instance))[0].state == "ok"
        provider._cache[instance.id] = [
            provider._cache[instance.id][0].model_copy(
                update={"observed_at": datetime.now(UTC) - timedelta(seconds=2)}
            )
        ]
        assert (await provider.collect(instance))[0].state == "stale"
        await provider.client.aclose()  # type: ignore[union-attr]

        unknown = NInferMetricsProvider()
        assert (await unknown.collect(instance.model_copy(update={"engine_id": "ninfer"})))[
            0
        ].state == "unavailable"
        assert calls == ["/metrics", "/metrics"]

    asyncio.run(run())


@pytest.mark.version_zero
def test_status_requires_route_and_model_and_marks_unavailable_proxy_as_error(
    tmp_path: Path,
) -> None:
    async def run() -> None:
        engine = create_engine(tmp_path / "status.db")
        empty = await collect_status(engine, openai_api_available=True, openai_api_models=0)
        assert (
            next(item for item in empty.components if item.name == "openai_api").state == "degraded"
        )
        unavailable = await collect_status(engine, openai_api_available=False)
        assert (
            next(item for item in unavailable.components if item.name == "openai_api").state
            == "error"
        )
        await engine.dispose()

    asyncio.run(run())


@pytest.mark.version_zero
def test_stop_rechecks_identity_and_signals_only_the_selected_process(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    starts = {101: 1.0, 202: 2.0}
    groups = {101: 301, 202: 302}
    signals: list[tuple[int, int]] = []

    class Proc:
        def __init__(self, pid: int) -> None:
            self.pid = pid

        def create_time(self) -> float:
            return starts[self.pid]

        def username(self) -> str:
            return "studio"

    class Child:
        def __init__(self) -> None:
            self.returncode = 0

        async def wait(self) -> int:
            return self.returncode

    def fake_killpg(pgid: int, signum: int) -> None:
        signals.append((pgid, signum))

    monkeypatch.setattr("app.process.manager.psutil.Process", Proc)
    monkeypatch.setattr("app.process.manager.os.getpgid", lambda pid: groups[pid])
    monkeypatch.setattr("app.process.manager.os.getpgrp", lambda: 999)
    monkeypatch.setattr("app.process.manager.os.killpg", fake_killpg)
    monkeypatch.setattr("app.process.manager._pid_exists", lambda pid: True)
    monkeypatch.setattr(ProcessManager, "_wait_exit", lambda *args, **kwargs: asyncio.sleep(0))

    manager = ProcessManager(log_root=tmp_path, username="studio")
    manager.refresh = _empty_refresh.__get__(manager, ProcessManager)  # type: ignore[method-assign]
    children: dict[int, Child] = {}
    for pid, start in ((101, 1), (202, 2)):
        instance = Instance(
            id=f"managed/p/{pid}-{start}",
            source="managed",
            profile_name="p",
            engine_id="vllm",
            pid=pid,
            process_start_time_ns=int(start * 1_000_000_000),
            identity_verified=True,
            can_stop=True,
            port=1900 + pid,
            model_name=f"m{pid}",
            state="ready",
            observed_at=datetime.now(UTC),
        )
        children[pid] = Child()
        manager._managed[instance.id] = _Managed(
            instance=instance,
            process=children[pid],
            log_path=tmp_path / f"{pid}.log",
            command="",
            installation_id="inst",
        )

    async def run() -> None:
        first = await manager.stop("managed/p/101-1")
        assert first.state == "succeeded"
        second = await manager.stop("managed/p/202-2")
        assert second.state == "succeeded"

    asyncio.run(run())
    assert signals == [
        (301, signal.SIGTERM),
        (301, signal.SIGKILL),
        (302, signal.SIGTERM),
        (302, signal.SIGKILL),
    ]


@pytest.mark.version_zero
def test_stop_refuses_pid_reuse_before_sending_signal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class Proc:
        def create_time(self) -> float:
            return 9.0

        def username(self) -> str:
            return "studio"

    signals: list[tuple[int, int]] = []
    monkeypatch.setattr("app.process.manager.psutil.Process", lambda pid: Proc())
    monkeypatch.setattr("app.process.manager.os.killpg", lambda *args: signals.append(args))
    manager = ProcessManager(log_root=tmp_path, username="studio")
    manager.refresh = _empty_refresh.__get__(manager, ProcessManager)  # type: ignore[method-assign]
    instance = Instance(
        id="managed/p/101-1",
        source="managed",
        profile_name="p",
        engine_id="vllm",
        pid=101,
        process_start_time_ns=1,
        identity_verified=True,
        can_stop=True,
        port=1919,
        model_name="m",
        state="ready",
        observed_at=datetime.now(UTC),
    )
    manager._managed[instance.id] = _Managed(
        instance=instance,
        process=type("Child", (), {"returncode": 0})(),
        log_path=tmp_path / "101.log",
        command="",
        installation_id="inst",
    )

    async def run() -> None:
        with pytest.raises(Exception) as error:
            await manager.stop(instance.id)
        assert getattr(error.value, "code", None) == "instance_identity_changed"

    asyncio.run(run())
    assert signals == []


@pytest.mark.version_zero
def test_external_logs_report_unavailable_source_instead_of_empty_success(
    tmp_path: Path,
) -> None:
    instance = Instance(
        id="external/vllm/101-1",
        source="external",
        profile_name=None,
        engine_id="vllm",
        pid=101,
        process_start_time_ns=1,
        port=1919,
        model_name="m",
        state="ready",
        observed_at=datetime.now(UTC),
    )
    manager = ProcessManager(log_root=tmp_path)
    manager._external[instance.id] = ExternalObservation(instance=instance, upstream_models=["m"])
    manager.refresh = _empty_refresh.__get__(manager, ProcessManager)  # type: ignore[method-assign]

    async def run() -> None:
        with pytest.raises(Exception) as error:
            await manager.logs(instance.id)
        assert getattr(error.value, "code", None) == "logs_unavailable"

    asyncio.run(run())


@pytest.mark.version_zero
def test_proxy_covers_embeddings_model_missing_upstream_error_and_journal(tmp_path: Path) -> None:
    async def run() -> None:
        from app.errors import AppError
        from app.process.registry import ModelRegistry

        engine = create_engine(tmp_path / "proxy-review.db")
        factory = create_session_factory(engine)
        async with engine.begin() as connection:
            await connection.run_sync(entities.Base.metadata.create_all)

        instance = Instance(
            id="managed/p/1-2",
            source="managed",
            profile_name="p",
            engine_id="vllm",
            pid=1,
            process_start_time_ns=2,
            port=1919,
            model_name="public",
            state="ready",
            observed_at=datetime.now(UTC),
        )

        class Manager:
            def __init__(self) -> None:
                self.registry = ModelRegistry()
                self.registry.register_instance(instance, upstream_models=["actual"])

            async def refresh(self) -> None:
                return None

            def model_registry(self) -> ModelRegistry:
                return self.registry

            async def get(self, instance_id: str) -> Instance:
                assert instance_id == instance.id
                return instance

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/v1/embeddings":
                return httpx.Response(400, json={"error": "bad input"})
            raise httpx.ConnectError("upstream down", request=request)

        service = ProxyService(
            Manager(),
            journal=RequestJournalService(session_factory=factory),
            client_factory=lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        )
        with pytest.raises(AppError) as missing:
            await service.forward(
                route="/v1/chat/completions",
                payload={"model": "missing", "messages": []},
                headers={},
                request_id="missing-id",
            )
        assert missing.value.code == "model_not_found"

        embeddings = await service.forward(
            route="/v1/embeddings",
            payload={"model": "public", "input": "x"},
            headers={},
            request_id="same-id",
        )
        assert embeddings.status_code == 400

        with pytest.raises(AppError) as unavailable:
            await service.forward(
                route="/v1/chat/completions",
                payload={"model": "public", "messages": []},
                headers={},
                request_id="same-id",
            )
        assert unavailable.value.code == "upstream_unavailable"
        stats = await service.journal.stats()  # type: ignore[union-attr]
        assert sum(item.requests for item in stats.items) == 3
        await engine.dispose()

    asyncio.run(run())


@pytest.mark.version_zero
def test_mcp_all_five_tools_keep_success_and_error_shapes() -> None:
    async def success(*args: Any) -> dict[str, Any]:
        del args
        return {"observed_at": datetime.now(UTC), "ok": True}

    async def failure(*args: Any) -> Any:
        del args
        from app.errors import AppError

        raise AppError("not_found", "missing", status_code=404)

    async def run() -> None:
        server = create_mcp_server(MCPDependencies(success, success, success, success, success))
        names = [
            "get_system_state",
            "list_models",
            "list_profiles",
            "start_profile",
            "stop_instance",
        ]
        for name in names:
            args = (
                {"profile_name": "p"}
                if name == "start_profile"
                else ({"instance_id": "i"} if name == "stop_instance" else {})
            )
            result = await server._tool_manager.call_tool(name, args)
            assert result.isError is False
            envelope = result.structuredContent
            assert json.loads(result.content[0].text) == envelope

        failing = create_mcp_server(MCPDependencies(failure, failure, failure, failure, failure))
        result = await failing._tool_manager.call_tool("list_models", {})
        assert result.isError is True
        assert json.loads(result.content[0].text) == result.structuredContent

    asyncio.run(run())


async def _empty_refresh(self: ProcessManager):
    from app.schemas.instances import InstanceList

    return InstanceList(items=[])
