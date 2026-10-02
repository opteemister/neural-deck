"""Managed process lifecycle and the runtime instance registry."""

from __future__ import annotations

import asyncio
import contextlib
import getpass
import hashlib
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
from .registry import ModelRegistry

LOG_LIMIT = 8 * 1024 * 1024
logger = logging.getLogger(__name__)


@dataclass(slots=True)
class _Managed:
    instance: Instance
    process: asyncio.subprocess.Process
    log_path: Path
    command: str
    installation_id: str


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
    """Own Studio launches and combine them with point-in-time discovery."""

    def __init__(
        self,
        *,
        log_root: Path | None = None,
        username: str | None = None,
        http_client: Any | None = None,
        registry: ModelRegistry | None = None,
        external_discovery: Callable[..., Awaitable[list[ExternalObservation]]] | None = None,
    ) -> None:
        self.log_root = log_root or Path.cwd() / "logs" / "profiles"
        self.log_root.mkdir(parents=True, exist_ok=True)
        self.username = username or getpass.getuser()
        self.http_client = http_client
        self.external_discovery = external_discovery or discover_external
        self.registry = registry or ModelRegistry()
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

    async def start_profile(self, profile: ProfileData, engine_path: str) -> Instance:
        # Refresh once before evaluating model and port conflicts so a manual
        # launch cannot be shadowed by stale in-memory state.
        await self.refresh()
        async with self._guard:
            active = self._active_managed()
            active_external = [item.instance for item in self._external.values()]
            if any(item.instance.profile_name == profile.name for item in active):
                raise AppError(
                    "profile_already_running",
                    "Профиль уже запущен",
                    status_code=409,
                    details={"profile_name": profile.name},
                )
            registry_model = self.registry.get(profile.model_name)
            if any(item.instance.model_name == profile.model_name for item in active) or any(
                item.model_name == profile.model_name for item in active_external
            ):
                raise AppError(
                    "model_name_conflict",
                    "Имя модели уже используется активным экземпляром",
                    status_code=409,
                    details={"model_name": profile.model_name},
                )
            if registry_model is not None and registry_model.instance_id not in {
                item.instance.id for item in active
            }:
                raise AppError(
                    "model_name_conflict",
                    "Имя модели уже зарегистрировано активным экземпляром",
                    status_code=409,
                    details={"model_name": profile.model_name},
                )
            command = substitute_placeholders(
                profile.command,
                engine=engine_path,
                model=str(profile.model.path),
                model_name=profile.model_name,
                model_draft=str(profile.model.draft_path) if profile.model.draft_path else None,
            )
            port = _extract_port(command)
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
            profile_dir = self.log_root / _safe_profile_dir(profile.name)
            profile_dir.mkdir(parents=True, exist_ok=True)
            with contextlib.suppress(OSError):
                (profile_dir / ".profile-name").write_text(profile.name, encoding="utf-8")
            temporary_log = profile_dir / f"starting-{uuid4().hex}.log"
            env = os.environ.copy()
            env.update(dict(profile.environment))
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
            start_ns = _identity_start_ns(pid)
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
            instance_id = f"managed/{quote(profile.name, safe='')}/{pid}-{start_ns}"
            log_path = profile_dir / f"{pid}-{start_ns}.log"
            with contextlib.suppress(OSError):
                temporary_log.replace(log_path)
            instance = Instance(
                id=instance_id,
                source="managed",
                profile_name=profile.name,
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
            )
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
        if item.instance.state == "stopping":
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

    async def _watch_start(self, instance_id: str) -> None:
        item = self._managed.get(instance_id)
        if item is None:
            return
        await asyncio.sleep(0.05)
        if item.process.returncode is not None:
            self._process_done(instance_id)
            return
        models = await query_models(item.instance.port, client=self.http_client)
        if models:
            item.instance.state = "ready"
        else:
            item.instance.state = "running"
        item.instance.observed_at = datetime.now(UTC)
        self.registry.register_instance(
            item.instance, upstream_models=models or [item.instance.model_name or ""]
        )

    async def refresh(self, *, probe_models: bool = True) -> InstanceList:
        """Reconcile managed and externally discovered processes.

        This is an active observation operation: it may finalize child
        processes, update the model registry, and run external discovery. It
        must only be called by explicit process/system refresh paths, never by
        a liveness or health probe.
        """
        self.discovery_error = None
        for item in list(self._managed.values()):
            if item.process.returncode is not None:
                self._process_done(item.instance.id)
            elif item.instance.state == "starting":
                item.instance.state = "running"
            item.instance.observed_at = datetime.now(UTC)
        excluded = {
            (item.instance.pid, item.instance.process_start_time_ns)
            for item in self._managed.values()
            if item.process.returncode is None
        }
        try:
            found = await self.external_discovery(
                excluded_keys=excluded,
                username=self.username,
                http_client=self.http_client,
                probe_models=probe_models,
            )
        except Exception as exc:  # noqa: BLE001 - external process boundary
            self.discovery_error = str(exc)
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
        self._check_identity(instance_value)
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

    def _check_identity(self, expected: Instance) -> int:
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
        try:
            pgid = os.getpgid(expected.pid)
            group_leader = psutil.Process(pgid)
            group_user = group_leader.username()
            studio_pgid = os.getpgrp()
        except (OSError, psutil.Error) as exc:
            raise AppError(
                "instance_identity_changed",
                "Не удалось подтвердить группу процесса",
                status_code=409,
                details={"pid": expected.pid},
            ) from exc
        if pgid <= 0 or pgid == studio_pgid or group_user != self.username:
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


def _extract_port(command: str) -> int | None:
    import re

    match = re.search(r"(?:--port(?:=|\s+)|(?:^|\s)-p\s+)(\d{1,5})(?:\s|$)", command)
    if not match:
        return None
    value = int(match.group(1))
    return value if 1 <= value <= 65535 else None


__all__ = ["ProcessManager"]
