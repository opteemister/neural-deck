"""Перезапуск backend сохраняет принадлежность модели и возможность остановки."""

import asyncio
import os
import signal

import psutil
import pytest
from app.db import create_engine
from app.errors import AppError
from app.process.launcher import SystemdLauncher, process_start_ns
from app.process.manager import ProcessManager
from app.process.runs import RunStore
from app.profiles.repository import ProfileRepository
from app.profiles.service import ProfileService
from app.schemas.profiles import ProfileData
from sqlalchemy import text


async def no_external(**kwargs):
    return []


@pytest.fixture
async def store(tmp_path):
    engine = create_engine(tmp_path / "runs.sqlite")
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "CREATE TABLE model_runs (pid INTEGER PRIMARY KEY, "
                "process_start_time_ns INTEGER NOT NULL, snapshot TEXT NOT NULL)"
            )
        )
    yield RunStore(engine)
    await engine.dispose()


def profile(autostart=False):
    return ProfileData.model_validate(
        {
            "version": 1,
            "name": "demo",
            "model_name": "demo-model",
            "engine": {"id": "vllm", "installation_id": "installation"},
            "model": {"path": "/models/x"},
            "command": "sleep 60",
            "autostart": autostart,
        }
    )


def manager(tmp_path, store, launcher=None):
    return ProcessManager(
        log_root=tmp_path / "logs",
        run_store=store,
        launcher=launcher,
        external_discovery=no_external,
    )


@pytest.mark.asyncio
async def test_new_backend_restores_and_stops_previous_child(tmp_path, store):
    first = manager(tmp_path, store)
    second = manager(tmp_path, store)
    instance = await first.start_profile(profile(), "/bin/echo")
    try:
        await first.close()
        assert process_start_ns(instance.pid) == instance.process_start_time_ns
        restored = await second.refresh(probe_models=False)
        assert len(restored.items) == 1
        assert restored.items[0].id == instance.id
        assert restored.items[0].source == "managed"
        assert restored.items[0].state == "starting"
        assert second.is_installation_running("installation")
        assert (await second.logs(instance.id)).instance_id == instance.id
        operation = await second.stop(instance.id)
        assert operation.state == "succeeded"
        assert await store.list() == []
    finally:
        if process_start_ns(instance.pid) == instance.process_start_time_ns:
            os.killpg(instance.pid, signal.SIGKILL)
        await first.close()
        await second.close()


@pytest.mark.asyncio
async def test_dead_and_reused_pid_rows_are_deleted(tmp_path, store):
    first = manager(tmp_path, store)
    instance = await first.start_profile(profile(), "/bin/echo")
    try:
        data = (await store.list())[0]
        data["instance"]["process_start_time_ns"] += 1
        await store.save(data)
        second = manager(tmp_path, store)
        assert (await second.refresh()).items == []
        assert await store.list() == []
        assert process_start_ns(instance.pid) == instance.process_start_time_ns
        await second.close()
        data["instance"]["pid"] = 2147483647
        await store.save(data)
        await first.refresh()
        assert await store.list() == []
    finally:
        await first.stop(instance.id)
        await first.close()


class FakeUnits(SystemdLauncher):
    """Real Linux processes, simulated user systemd transport."""

    def __init__(self, runtime_dir):
        super().__init__(runtime_dir)
        self.units = {}
        self.stopped = []

    async def start(self, unit, command, env, log):
        with log.open("ab") as output:
            child = await asyncio.create_subprocess_exec(
                "/bin/bash",
                "-c",
                command,
                env=env,
                start_new_session=True,
                stdout=output,
                stderr=output,
            )
        self.units[unit] = child
        return child.pid

    async def pid(self, unit):
        child = self.units.get(unit)
        return child.pid if child and child.returncode is None else 0

    async def stop(self, unit):
        self.stopped.append(unit)
        child = self.units[unit]
        os.killpg(child.pid, signal.SIGTERM)
        await child.wait()


@pytest.mark.asyncio
async def test_restored_unit_stop_uses_systemd_not_process_group(tmp_path, store, monkeypatch):
    launcher = FakeUnits(tmp_path / "runtime")
    first = manager(tmp_path, store, launcher)
    second = manager(tmp_path, store, launcher)
    instance = await first.start_profile(profile(), "/bin/echo")
    try:
        unit = (await store.list())[0]["unit_name"]
        assert list(launcher.pending_dir.glob("*.json")) == []
        await first.close()
        await second.refresh()
        # Systemd MainPID need not lead an isolated process group.
        monkeypatch.setattr("app.process.manager.os.getpgid", lambda pid: os.getpgrp())
        assert (await second.stop(instance.id)).state == "succeeded"
        assert launcher.stopped == [unit]
        assert await store.list() == []
    finally:
        for child in launcher.units.values():
            if child.returncode is None:
                os.killpg(child.pid, signal.SIGKILL)
                await child.wait()
        await first.close()
        await second.close()


@pytest.mark.asyncio
async def test_crash_between_unit_start_and_database_commit_recovers(tmp_path, store):
    launcher = FakeUnits(tmp_path / "runtime")
    unit = "neural-deck-model-pending.service"
    log = tmp_path / "pending.log"
    pending = launcher.pending(
        unit,
        {
            "unit_name": unit,
            "profile_name": "demo",
            "engine_id": "vllm",
            "model_name": "demo-model",
            "port": None,
            "command": "sleep 60",
            "installation_id": "installation",
            "log_path": str(log),
        },
    )
    pid = await launcher.start(unit, "sleep 60", os.environ.copy(), log)
    recovered = manager(tmp_path, store, launcher)
    try:
        assert await store.list() == []
        instances = await recovered.refresh()
        assert instances.items[0].pid == pid
        assert instances.items[0].profile_name == "demo"
        assert not pending.exists()
        assert len(await store.list()) == 1
        await recovered.stop(instances.items[0].id)
    finally:
        child = launcher.units[unit]
        if child.returncode is None:
            await launcher.stop(unit)
        await recovered.close()


@pytest.mark.asyncio
async def test_autostart_skips_recovered_profile(tmp_path, store):
    repository = ProfileRepository(tmp_path / "profiles")
    repository.create(profile(autostart=True))
    first = manager(tmp_path, store)
    instance = await first.start_profile(profile(), "/bin/echo")
    second = manager(tmp_path, store)
    service = ProfileService(tmp_path / "profiles", repository=repository, process_manager=second)
    try:
        await first.close()
        assert await service.autostart_once() == []
        assert service.autostart_issues == []
        assert len(await store.list()) == 1
    finally:
        await second.stop(instance.id)
        await second.close()


@pytest.mark.asyncio
async def test_systemd_command_is_transient_preserves_environment_and_cleans_file(tmp_path):
    launcher = SystemdLauncher(tmp_path)
    calls = []

    async def call(*args, **kwargs):
        calls.append(args)
        if args[0] == "systemd-run":
            env_path = tmp_path / "model-launches" / "neural-deck-model-test.service.env"
            value = env_path.read_text()
            assert 'CUDA_HOME="/usr/local/cuda"' in value
            assert 'VALUE="a\\$b\\"c"' in value
            assert env_path.stat().st_mode & 0o777 == 0o600
            return ""
        return "LoadState=loaded\nMainPID=123"

    launcher._call = call
    pid = await launcher.start(
        "neural-deck-model-test.service",
        'echo "$VALUE"',
        {"CUDA_HOME": "/usr/local/cuda", "VALUE": 'a$b"c'},
        tmp_path / "log",
    )
    assert pid == 123
    assert "--user" in calls[0]
    assert "--collect" in calls[0]
    assert "--property=Restart=no" in calls[0]
    assert "--property=KillMode=control-group" in calls[0]
    assert calls[0][-1] == 'echo "$$VALUE"'
    assert not list((tmp_path / "model-launches").glob("*.env"))


@pytest.mark.asyncio
async def test_permissions_error_does_not_delete_snapshot(tmp_path, store, monkeypatch):
    first = manager(tmp_path, store)
    instance = await first.start_profile(profile(), "/bin/echo")
    second = manager(tmp_path, store)
    try:
        with monkeypatch.context() as context:

            def denied(pid):
                raise psutil.AccessDenied(pid)

            context.setattr("app.process.manager.process_start_ns", denied)
            with pytest.raises(psutil.AccessDenied):
                await second.refresh()
        assert len(await store.list()) == 1
    finally:
        await first.stop(instance.id)
        await first.close()
        await second.close()


@pytest.mark.asyncio
async def test_old_observer_cannot_delete_new_identity_with_same_pid(tmp_path, store):
    first = manager(tmp_path, store)
    instance = await first.start_profile(profile(), "/bin/echo")
    try:
        snapshot = (await store.list())[0]
        snapshot["instance"]["process_start_time_ns"] += 1
        await store.save(snapshot)
        await store.delete(instance.pid, instance.process_start_time_ns)
        assert len(await store.list()) == 1
    finally:
        await first.stop(instance.id)
        await first.close()


@pytest.mark.asyncio
async def test_pending_unit_is_not_restarted_after_machine_reboot(tmp_path, store):
    launcher = FakeUnits(tmp_path / "runtime")
    path = launcher.pending(
        "neural-deck-model-gone.service",
        {
            "unit_name": "neural-deck-model-gone.service",
        },
    )
    recovered = manager(tmp_path, store, launcher)
    try:
        assert (await recovered.refresh()).items == []
        assert await store.list() == []
        assert launcher.units == {}
        assert not path.exists()
    finally:
        await recovered.close()


@pytest.mark.asyncio
async def test_loading_model_without_api_stays_owned(tmp_path, store):
    first = manager(tmp_path, store)
    instance = await first.start_profile(profile(), "/bin/echo")
    try:
        await asyncio.sleep(0.1)
        assert (await first.get(instance.id)).source == "managed"
        assert (await first.get(instance.id)).state == "starting"
        assert len(await store.list()) == 1
    finally:
        await first.stop(instance.id)
        await first.close()


@pytest.mark.asyncio
async def test_loading_status_survives_refresh_until_models_are_available(
    tmp_path, store, monkeypatch
):
    available = asyncio.Event()
    probed = asyncio.Event()
    ready_response = asyncio.Event()
    calls = 0

    async def models(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls >= 3:
            probed.set()
        if available.is_set():
            ready_response.set()
            return ["actual-model"]
        return []

    monkeypatch.setattr("app.process.manager.query_models", models)
    monkeypatch.setattr("app.process.manager.START_PROBE_INTERVAL", 0.01)
    managed = manager(tmp_path, store)
    instance = await managed.start_profile(profile(), "/bin/echo")
    try:
        async with asyncio.timeout(2):
            await probed.wait()
        assert (await managed.refresh()).items[0].state == "starting"
        assert (await managed.refresh(probe_models=False)).items[0].state == "starting"
        available.set()
        async with asyncio.timeout(2):
            await ready_response.wait()
        assert (await managed.refresh()).items[0].state == "ready"
        assert managed.registry.get("demo-model").upstream_model == "actual-model"
    finally:
        await managed.stop(instance.id)
        await managed.close()


@pytest.mark.asyncio
async def test_readiness_response_cannot_overwrite_stop(tmp_path, store, monkeypatch):
    probing = asyncio.Event()
    release = asyncio.Event()

    async def models(*args, **kwargs):
        probing.set()
        await release.wait()
        return ["actual-model"]

    monkeypatch.setattr("app.process.manager.query_models", models)
    managed = manager(tmp_path, store)
    instance = await managed.start_profile(profile(), "/bin/echo")
    try:
        async with asyncio.timeout(2):
            await probing.wait()
        await managed.stop(instance.id)
        release.set()
        await asyncio.sleep(0.01)
        assert instance.state == "exited"
        assert managed.registry.get("demo-model") is None
    finally:
        release.set()
        await managed.close()


@pytest.mark.asyncio
async def test_systemd_error_never_falls_back_to_backend_child(tmp_path, store, monkeypatch):
    launcher = SystemdLauncher(tmp_path / "runtime")

    async def denied(*args, **kwargs):
        raise AppError("model_unit_failed", "user manager unavailable", status_code=503)

    async def forbidden_child(*args, **kwargs):
        raise AssertionError("Must not spawn model in backend cgroup")

    monkeypatch.setattr(launcher, "_call", denied)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", forbidden_child)
    managed = manager(tmp_path, store, launcher)
    try:
        with pytest.raises(AppError, match="user manager unavailable"):
            await managed.start_profile(profile(), "/bin/echo")
        assert await store.list() == []
        assert not list(launcher.pending_dir.glob("*.env"))
    finally:
        await managed.close()
