"""Managed process lifecycle and the runtime instance registry."""

from __future__ import annotations

import asyncio
import contextlib
import getpass
import hashlib
import json
import logging
import os
import signal
from collections.abc import Awaitable, Callable, Coroutine
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote
from uuid import uuid4

import psutil

from ..errors import AppError
from ..profiles.formatting import substitute_placeholders
from ..profiles.ports import resolve_effective_port
from ..schemas.instances import (
    Instance,
    InstanceList,
    LogsData,
    Metric,
    MetricList,
    ProcessOperation,
)
from ..schemas.profiles import ProfileData
from .discovery import ExternalObservation, discover_external, query_models
from .external_logs import external_logs
from .launcher import ObservedProcess, SystemdLauncher, process_start_ns
from .registry import ModelRegistry
from .runs import RunStore

LOG_LIMIT = 8 * 1024 * 1024
START_PROBE_INTERVAL = 2.0
logger = logging.getLogger(__name__)


@dataclass(slots=True)
class _Managed:
    instance: Instance
    process: Any
    log_path: Path
    command: str
    installation_id: str
    unit_name: str | None = None


def _identity_start_ns(pid: int) -> int | None:
    try:
        value = int(float(psutil.Process(pid).create_time()) * 1_000_000_000)
        return value if value > 0 else None
    except (psutil.Error, OSError, TypeError, ValueError):
        return None


def _safe_profile_dir(value: str) -> str:
    readable = "".join(char if char.isalnum() or char in "._-" else "_" for char in value)
    # Profile names may contain spaces and other punctuation. Keep logs
    # readable while appending a digest so names such as ``a b`` and ``a_b``
    # cannot share an instance directory.
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]
    return f"{readable or 'profile'}-{digest}"


class ProcessManager:
    """Own Neural Deck launches and combine them with point-in-time discovery."""

    def __init__(
        self,
        *,
        log_root: Path | None = None,
        username: str | None = None,
        http_client: Any | None = None,
        registry: ModelRegistry | None = None,
        run_store: RunStore | None = None,
        launcher: SystemdLauncher | None = None,
        external_discovery: Callable[..., Awaitable[list[ExternalObservation]]] | None = None,
    ) -> None:
        self.log_root = log_root or Path.cwd() / "logs" / "profiles"
        self.log_root.mkdir(parents=True, exist_ok=True)
        self.username = username or getpass.getuser()
        self.http_client = http_client
        self.external_discovery = external_discovery or discover_external
        self.registry = registry or ModelRegistry()
        self.run_store = run_store
        self.launcher = launcher
        self._managed: dict[str, _Managed] = {}
        self._external: dict[str, ExternalObservation] = {}
        self._operations: dict[str, ProcessOperation] = {}
        self._guard = asyncio.Lock()
        self._tasks: set[asyncio.Task[Any]] = set()
        self.discovery_error: str | None = None

    def _track_task(self, coroutine: Coroutine[Any, Any, Any]) -> asyncio.Task[Any]:
        task: asyncio.Task[Any] = asyncio.create_task(coroutine)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    async def close(self) -> None:
        """Stop watcher tasks while leaving already started model processes alive."""

        tasks = list(self._tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._tasks.clear()
        close = getattr(self.http_client, "aclose", None)
        if close is not None:
            result = close()
            if asyncio.iscoroutine(result):
                await result

    def _snapshot(self, item: _Managed) -> dict[str, Any]:
        return {
            "instance": item.instance.model_dump(mode="json"),
            "log_path": str(item.log_path),
            "command": item.command,
            "installation_id": item.installation_id,
            "unit_name": item.unit_name,
        }

    async def _restore(self) -> None:
        if self.run_store is None:
            return
        snapshots = await self.run_store.list()
        if self.launcher is not None:
            for path in self.launcher.pending_dir.glob("*.json"):
                data = json.loads(path.read_text(encoding="utf-8"))
                unit = data["unit_name"]
                pid = await self.launcher.pid(unit)
                start_ns = process_start_ns(pid) if pid else None
                if start_ns is not None:
                    data["instance"] = Instance(
                        id=f"managed/{quote(data.get('profile_id') or data['profile_name'], safe='')}/{pid}-{start_ns}",
                        source="managed",
                        profile_name=data["profile_name"],
                        profile_id=data.get("profile_id"),
                        engine_id=data["engine_id"],
                        pid=pid,
                        process_start_time_ns=start_ns,
                        identity_verified=True,
                        can_stop=True,
                        port=data["port"],
                        model_name=data["model_name"],
                        state="starting",
                        observed_at=datetime.now(UTC),
                    ).model_dump(mode="json")
                    await self.run_store.save(data)
                    snapshots.append(data)
                path.unlink()
                path.with_suffix(".env").unlink(missing_ok=True)
        for data in snapshots:
            instance = Instance.model_validate(data["instance"])
            actual = process_start_ns(instance.pid)
            if actual is None or actual != instance.process_start_time_ns:
                await self.run_store.delete(instance.pid, instance.process_start_time_ns)
                continue
            if instance.id in self._managed:
                continue
            if psutil.Process(instance.pid).username() != self.username:
                raise AppError("instance_identity_changed", "Владелец процесса изменился")
            unit = data.get("unit_name")
            if unit and (self.launcher is None or await self.launcher.pid(unit) != instance.pid):
                raise AppError(
                    "instance_identity_changed", "Сервис модели не соответствует процессу"
                )
            instance.state = "starting"
            item = _Managed(
                instance,
                ObservedProcess(instance.pid, actual),
                Path(data["log_path"]),
                data["command"],
                data["installation_id"],
                unit,
            )
            self._managed[instance.id] = item
            self.registry.register_instance(instance)
            self._track_task(self._wait_process(instance.id))
            self._track_task(self._watch_start(instance.id))

    async def start_profile(self, profile: ProfileData, engine_path: str) -> Instance:
        # Refresh once before evaluating model and port conflicts so a manual
        # launch cannot be shadowed by stale in-memory state.
        await self.refresh()
        async with self._guard:
            active = self._active_managed()
            active_external = [item.instance for item in self._external.values()]
            if any(
                item.instance.profile_id == profile.profile_id
                or (item.instance.profile_id is None and item.instance.profile_name == profile.name)
                for item in active
            ):
                raise AppError(
                    "profile_already_running",
                    "Профиль уже запущен",
                    status_code=409,
                    details={"profile_name": profile.name},
                )
            # Several managed profiles may intentionally publish the same
            # public model name.  TASK-004 groups them into one ordered route;
            # port conflicts remain a separate safety check below.
            command = substitute_placeholders(
                profile.command,
                engine=engine_path,
                model=str(profile.model.path),
                model_name=profile.model_name,
                model_draft=str(profile.model.draft_path) if profile.model.draft_path else None,
            )
            port = resolve_effective_port(profile.engine.id, command).port
            if port is not None and (
                any(item.instance.port == port for item in active)
                or any(item.port == port for item in active_external)
            ):
                raise AppError(
                    "port_conflict",
                    "Порт уже используется активным экземпляром",
                    status_code=409,
                    details={"port": port},
                )
            profile_dir = self.log_root / _safe_profile_dir(profile.profile_id or profile.name)
            profile_dir.mkdir(parents=True, exist_ok=True)
            with contextlib.suppress(OSError):
                (profile_dir / ".profile-name").write_text(profile.name, encoding="utf-8")
            temporary_log = profile_dir / f"starting-{uuid4().hex}.log"
            env = os.environ.copy()
            env.update(dict(profile.environment))
            process: Any
            unit_name = None
            pending_path = None
            if self.launcher is not None:
                unit_name = f"neural-deck-model-{uuid4().hex}.service"
                pending_path = self.launcher.pending(
                    unit_name,
                    {
                        "unit_name": unit_name,
                        "profile_name": profile.name,
                        "profile_id": profile.profile_id,
                        "engine_id": profile.engine.id,
                        "model_name": profile.model_name,
                        "port": port,
                        "command": command,
                        "installation_id": profile.engine.installation_id,
                        "log_path": str(temporary_log),
                    },
                )
                pid = await self.launcher.start(unit_name, command, env, temporary_log)
                start_ns = process_start_ns(pid) if pid else None
                if start_ns is None:
                    await self.launcher.stop(unit_name)
                    pending_path.unlink(missing_ok=True)
                    raise AppError(
                        "instance_identity_unavailable", "Процесс модели завершился при запуске"
                    )
                process = ObservedProcess(pid, start_ns)
            else:
                try:
                    log_handle = temporary_log.open("ab")
                    process = await asyncio.create_subprocess_exec(
                        "/bin/bash",
                        "-c",
                        command,
                        env=env,
                        stdin=asyncio.subprocess.DEVNULL,
                        stdout=log_handle,
                        stderr=asyncio.subprocess.STDOUT,
                        start_new_session=True,
                    )
                except (OSError, ValueError) as exc:
                    with contextlib.suppress(OSError):
                        log_handle.close()
                    raise AppError("instance_start_failed", str(exc), status_code=500) from exc
                finally:
                    with contextlib.suppress(UnboundLocalError, OSError):
                        log_handle.close()
            pid = int(process.pid)
            # A launch timestamp is not a process identity.  If psutil cannot
            # confirm the child create time, do not publish an instance which
            # could later point at a reused PID.  Terminate the just-created
            # process before returning the diagnostic error so it is not left
            # running outside the registry.
            start_ns = process.start_ns if unit_name else _identity_start_ns(pid)
            if start_ns is None:
                # Keep the subprocess handle's normal termination hook for
                # embedders that provide one, then terminate the entire
                # session below.  The group signal is the safety guarantee;
                # this hook also makes cleanup observable in lightweight test
                # doubles.
                try:
                    cleanup_pgid = os.getpgid(pid)
                except OSError:
                    cleanup_pgid = pid
                with contextlib.suppress(AttributeError, OSError, ProcessLookupError):
                    process.terminate()
                await _terminate_process_group_and_wait(process, pgid=cleanup_pgid)
                raise AppError(
                    "instance_identity_unavailable",
                    "Не удалось подтвердить время запуска процесса; экземпляр не зарегистрирован",
                    status_code=503,
                    retryable=True,
                )
            instance_id = f"managed/{quote(profile.profile_id or profile.name, safe='')}/{pid}-{start_ns}"
            log_path = profile_dir / f"{pid}-{start_ns}.log"
            if unit_name is not None:
                log_path = temporary_log
            else:
                with contextlib.suppress(OSError):
                    temporary_log.replace(log_path)
            instance = Instance(
                id=instance_id,
                source="managed",
                profile_name=profile.name,
                profile_id=profile.profile_id,
                engine_id=profile.engine.id,
                pid=pid,
                process_start_time_ns=start_ns,
                identity_verified=True,
                can_stop=True,
                port=port,
                model_name=profile.model_name,
                state="starting",
                observed_at=datetime.now(UTC),
            )
            managed = _Managed(
                instance=instance,
                process=process,
                log_path=log_path,
                command=command,
                installation_id=profile.engine.installation_id,
                unit_name=unit_name,
            )
            if self.run_store is not None:
                try:
                    await self.run_store.save(self._snapshot(managed))
                except Exception:
                    if unit_name and self.launcher is not None:
                        await self.launcher.stop(unit_name)
                    else:
                        await _terminate_process_group_and_wait(process)
                    raise
            if pending_path is not None:
                pending_path.unlink(missing_ok=True)
            self._managed[instance.id] = managed
            self._track_task(self._wait_process(instance.id))
            self.registry.register_instance(instance)
            self._track_task(self._watch_start(instance.id))
            logger.info(
                "Процесс профиля запущен: profile=%s instance_id=%s pid=%s log=%s",
                profile.name,
                instance.id,
                pid,
                log_path,
            )
            return instance

    def _active_managed(self) -> list[_Managed]:
        result: list[_Managed] = []
        for item in self._managed.values():
            if item.instance.state not in {"exited", "error"} and item.process.returncode is None:
                result.append(item)
        return result

    def is_installation_running(self, installation_id: str) -> bool:
        """Return whether a managed model currently uses an installation."""

        return any(
            item.installation_id == installation_id
            and item.instance.state not in {"exited", "error"}
            and item.process.returncode is None
            for item in self._managed.values()
        )

    def _process_done(self, instance_id: str) -> None:
        item = self._managed.get(instance_id)
        if item is None:
            return
        # A process can be observed as finished by both its wait task and a
        # later explicit refresh.  Finalize it only once so read endpoints do
        # not repeat state changes or log the same failure on every poll.
        if item.instance.state in {"exited", "error"}:
            return
        if item.instance.state == "stopping" or isinstance(item.process, ObservedProcess):
            # An adopted process has no waitpid exit status in this backend.
            # Report completion without inventing an engine failure reason.
            item.instance.state = "exited"
        elif item.process.returncode == 0:
            item.instance.state = "exited"
        else:
            item.instance.state = "error"
            logger.error(
                "Процесс профиля завершился с ошибкой: profile=%s instance_id=%s "
                "pid=%s returncode=%s log=%s",
                item.instance.profile_name,
                instance_id,
                item.instance.pid,
                item.process.returncode,
                item.log_path,
            )
        item.instance.observed_at = datetime.now(UTC)
        self.registry.clear_instance(instance_id)

    async def _wait_process(self, instance_id: str) -> None:
        item = self._managed.get(instance_id)
        if item is None:
            return
        await item.process.wait()
        self._process_done(instance_id)
        if self.run_store is not None:
            await self.run_store.delete(item.instance.pid, item.instance.process_start_time_ns)

    async def _watch_start(self, instance_id: str) -> None:
        item = self._managed.get(instance_id)
        if item is None:
            return
        # Loading large models can take minutes. Process liveness alone is
        # not readiness; keep probing until the engine publishes its models.
        while item.instance.state == "starting":
            if item.process.returncode is not None:
                self._process_done(instance_id)
                return
            models = await query_models(item.instance.port, client=self.http_client)
            # A stop or exit can happen while the HTTP request is in flight.
            if item.process.returncode is not None:
                self._process_done(instance_id)
                return
            if item.instance.state != "starting":
                return
            if models:
                item.instance.state = "ready"
                item.instance.observed_at = datetime.now(UTC)
                self.registry.register_instance(item.instance, upstream_models=models)
                return
            await asyncio.sleep(START_PROBE_INTERVAL)

    async def refresh(self, *, probe_models: bool = True) -> InstanceList:
        try:
            return await self._refresh(probe_models=probe_models)
        except Exception as exc:
            self.discovery_error = str(exc)
            raise

    async def _refresh(self, *, probe_models: bool = True) -> InstanceList:
        """Reconcile managed and externally discovered processes.

        This is an active observation operation: it may finalize child
        processes, update the model registry, and run external discovery. It
        must only be called by explicit process/system refresh paths, never by
        a liveness or health probe.
        """
        discovery_error: str | None = None
        async with self._guard:
            await self._restore()
        for item in list(self._managed.values()):
            if item.process.returncode is not None:
                self._process_done(item.instance.id)
                if self.run_store is not None:
                    await self.run_store.delete(
                        item.instance.pid, item.instance.process_start_time_ns
                    )
            item.instance.observed_at = datetime.now(UTC)
        excluded = {
            (item.instance.pid, item.instance.process_start_time_ns)
            for item in self._managed.values()
            if item.process.returncode is None
        }
        for item in self._active_managed():
            try:
                children = psutil.Process(item.instance.pid).children(recursive=True)
                excluded.update(
                    (child.pid, int(child.create_time() * 1_000_000_000)) for child in children
                )
            except psutil.NoSuchProcess:
                pass
        try:
            found = await self.external_discovery(
                excluded_keys=excluded,
                username=self.username,
                http_client=self.http_client,
                probe_models=probe_models,
            )
        except Exception as exc:  # noqa: BLE001 - external process boundary
            discovery_error = str(exc)
            found = []
        self._external = {item.instance.id: item for item in found}
        for observation in found:
            # An observation without process identity is still visible to an
            # operator, but must not become a routable model target.
            if probe_models and observation.instance.identity_verified:
                self.registry.register_instance(
                    observation.instance,
                    upstream_models=observation.upstream_models,
                )
        active_ids = {
            item.instance.id for item in self._managed.values() if item.process.returncode is None
        }
        active_ids.update(self._external)
        self.registry.remove_exited(active_ids)
        all_instances = [item.instance for item in self._managed.values()] + [
            item.instance for item in self._external.values()
        ]
        self.discovery_error = discovery_error
        return InstanceList(items=all_instances)

    async def get(self, instance_id: str) -> Instance:
        await self.refresh()
        item = self._managed.get(instance_id)
        if item is not None:
            return item.instance
        external = self._external.get(instance_id)
        if external is not None:
            return external.instance
        raise AppError("instance_not_found", "Экземпляр не найден", status_code=404)

    async def stop(self, instance_id: str) -> ProcessOperation:
        await self.refresh()
        managed = self._managed.get(instance_id)
        if managed is not None:
            instance_value = managed.instance
        else:
            external = self._external.get(instance_id)
            if external is None:
                raise AppError("instance_not_found", "Экземпляр не найден", status_code=404)
            instance_value = external.instance
        if not isinstance(instance_value, Instance):
            raise AppError("instance_not_found", "Экземпляр не найден", status_code=404)
        if not instance_value.can_stop:
            raise AppError(
                "instance_stop_not_allowed",
                "Остановка этого процесса не разрешена: идентичность или владелец не подтверждены",
                status_code=409,
                details={
                    "instance_id": instance_id,
                    "identity_verified": instance_value.identity_verified,
                },
            )
        self._check_identity(instance_value, check_group=not (managed and managed.unit_name))
        operation = ProcessOperation(
            id=f"op_{uuid4().hex}",
            kind="stop_instance",
            state="running",
            target_id=instance_id,
            created_at=datetime.now(UTC),
            started_at=datetime.now(UTC),
        )
        self._operations[operation.id] = operation
        instance_value.state = "stopping"
        try:
            if managed is not None and managed.unit_name:
                if (
                    self.launcher is None
                    or await self.launcher.pid(managed.unit_name) != instance_value.pid
                ):
                    raise AppError(
                        "instance_identity_changed", "Сервис модели изменился", status_code=409
                    )
                self._check_identity(instance_value, check_group=False)
                await self.launcher.stop(managed.unit_name)
            else:
                pgid = self._check_identity(instance_value)
                os.killpg(pgid, signal.SIGTERM)
                await self._wait_exit(instance_value.pid, duration=10.0)
                if _pid_exists(instance_value.pid):
                    # Identity must still match before escalating.  A PID reused
                    # by a different process is a conflict, not a kill target.
                    kill_pgid = self._check_identity(instance_value)
                    os.killpg(kill_pgid, signal.SIGKILL)
                    await self._wait_exit(instance_value.pid, duration=1.0)
            if managed is not None and managed.process.returncode is None:
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(managed.process.wait(), timeout=1.0)
            if managed is not None and managed.process.returncode is not None:
                self._process_done(instance_id)
                if self.run_store is not None:
                    await self.run_store.delete(
                        instance_value.pid, instance_value.process_start_time_ns
                    )
            operation.exit_code = 0
            operation.state = "succeeded"
        except AppError as exc:
            instance_value.state = "unknown"
            operation.state = "failed"
            operation.error = exc.message
            operation.exit_code = 1
            raise
        except (OSError, psutil.Error) as exc:
            instance_value.state = "unknown"
            operation.state = "failed"
            operation.error = str(exc)
            operation.exit_code = 1
        finally:
            instance_value.observed_at = datetime.now(UTC)
            operation.finished_at = datetime.now(UTC)
        return operation

    def _check_identity(self, expected: Instance, *, check_group: bool = True) -> int:
        try:
            process = psutil.Process(expected.pid)
            actual_ns = _identity_start_ns(expected.pid)
            current_user = process.username()
        except (psutil.Error, OSError) as exc:
            raise AppError(
                "instance_identity_changed", "Процесс больше не существует", status_code=409
            ) from exc
        if (
            expected.process_start_time_ns is None
            or actual_ns != expected.process_start_time_ns
            or (self.username is not None and current_user != self.username)
        ):
            raise AppError(
                "instance_identity_changed",
                "Идентичность процесса изменилась",
                status_code=409,
                details={"pid": expected.pid},
            )
        if not check_group:
            return expected.pid
        try:
            pgid = os.getpgid(expected.pid)
            group_leader = psutil.Process(pgid)
            group_user = group_leader.username()
            neural_deck_pgid = os.getpgrp()
        except (OSError, psutil.Error) as exc:
            raise AppError(
                "instance_identity_changed",
                "Не удалось подтвердить группу процесса",
                status_code=409,
                details={"pid": expected.pid},
            ) from exc
        if pgid <= 0 or pgid == neural_deck_pgid or group_user != self.username:
            raise AppError(
                "instance_identity_changed",
                "Группа процесса не принадлежит подтверждённому экземпляру",
                status_code=409,
                details={"pid": expected.pid, "pgid": pgid},
            )
        return pgid

    async def _wait_exit(self, pid: int, *, duration: float) -> None:
        deadline = asyncio.get_running_loop().time() + duration
        while _pid_exists(pid) and asyncio.get_running_loop().time() < deadline:  # noqa: ASYNC110
            await asyncio.sleep(0.05)

    async def logs(self, instance_id: str, tail: int = 200) -> LogsData:
        if tail < 1 or tail > 10_000:
            raise AppError(
                "invalid_tail", "Параметр tail должен быть от 1 до 10000", status_code=422
            )
        instance = await self.get(instance_id)
        item = self._managed.get(instance_id)
        if item is None:
            return await external_logs(instance, tail)
        if not item.log_path.exists():
            raise AppError(
                "logs_unavailable",
                "Лог запуска ещё недоступен",
                status_code=503,
                details={"source": "managed"},
            )
        try:
            data = item.log_path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            raise AppError("logs_unavailable", str(exc), status_code=503) from exc
        truncated = len(data) > LOG_LIMIT
        if truncated:
            data = data[-LOG_LIMIT:]
        lines = data.splitlines()
        if len(lines) > tail:
            lines = lines[-tail:]
            truncated = True
        return LogsData(instance_id=instance_id, lines=lines, truncated=truncated)

    async def metrics(self, instance_id: str) -> MetricList:
        instance = await self.get(instance_id)
        now = datetime.now(UTC)
        return MetricList(
            items=[
                Metric(
                    name="process_state",
                    value=instance.state,
                    unit="",
                    source="process_table",
                    observed_at=now,
                    state="ok" if instance.state in {"running", "ready", "starting"} else "stale",
                ),
                Metric(
                    name="api_latency_ms",
                    value=None,
                    unit="ms",
                    source="openai_compatible_api",
                    observed_at=now,
                    state="unavailable",
                ),
            ]
        )

    def model_registry(self) -> ModelRegistry:
        return self.registry

    def get_operation(self, operation_id_value: str) -> ProcessOperation:
        operation = self._operations.get(operation_id_value)
        if operation is None:
            raise AppError("operation_not_found", "Операция не найдена", status_code=404)
        return operation


def _pid_exists(pid: int) -> bool:
    try:
        return psutil.pid_exists(pid)
    except (OSError, TypeError, ValueError):
        return False


async def _terminate_process_group_and_wait(process: Any, *, pgid: int | None = None) -> None:
    """Clean up a newly managed process whose identity could not be read."""

    pid = int(process.pid)
    if pgid is None:
        try:
            pgid = os.getpgid(pid)
        except OSError:
            pgid = pid
    with contextlib.suppress(OSError, ProcessLookupError):
        os.killpg(pgid, signal.SIGTERM)
    with contextlib.suppress(asyncio.TimeoutError, OSError, ProcessLookupError):
        await asyncio.wait_for(process.wait(), timeout=1.0)
    # The session leader may have exited while one of its children remains;
    # signal the captured process group regardless of the leader return code.
    with contextlib.suppress(OSError, ProcessLookupError):
        os.killpg(pgid, signal.SIGKILL)
    if getattr(process, "returncode", None) is None:
        with contextlib.suppress(asyncio.TimeoutError, OSError, ProcessLookupError):
            await asyncio.wait_for(process.wait(), timeout=1.0)


__all__ = ["ProcessManager"]
