"""Small asynchronous operation runner used by management features.

Operations deliberately live in the service process in Version Zero.  The
runner keeps the management API responsive while installation and model
downloads perform blocking external work.  The public representation is kept
independent from the storage implementation so it can be persisted later.
"""

from __future__ import annotations

import asyncio
import contextvars
import os
import re
import signal
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from functools import partial
from pathlib import Path
from typing import Any
from uuid import uuid4

from .errors import AppError

MAX_OUTPUT = 64 * 1024

# Commands started from an operation inherit this task-local registry.  It
# lets cancellation clean up the actual subprocess even when the worker is
# waiting in ``communicate``.
_CURRENT_PROCESSES: contextvars.ContextVar[set[asyncio.subprocess.Process] | None] = (
    contextvars.ContextVar("llm_studio_operation_processes", default=None)
)


def utc_now() -> datetime:
    return datetime.now(UTC)


def operation_id() -> str:
    return f"op_{uuid4().hex}"


class OperationManager:
    """Track background operations and serialise operations per target."""

    def __init__(self, log_dir: Path | None = None) -> None:
        self._operations: dict[str, dict[str, Any]] = {}
        self._target_locks: dict[str, asyncio.Lock] = {}
        self._active_targets: set[str] = set()
        self._tasks: set[asyncio.Task[None]] = set()
        self._operation_tasks: dict[str, asyncio.Task[None]] = {}
        self._guard = asyncio.Lock()
        self._log_dir = log_dir

    async def create(
        self,
        kind: str,
        target_id: str,
        worker: Callable[[dict[str, Any]], Awaitable[None]],
    ) -> dict[str, Any]:
        async with self._guard:
            lock = self._target_locks.setdefault(target_id, asyncio.Lock())
            if target_id in self._active_targets:
                raise AppError(
                    "operation_conflict",
                    "Для выбранной цели уже выполняется операция",
                    status_code=409,
                    details={"target_id": target_id},
                    retryable=True,
                )
            value: dict[str, Any] = {
                "id": operation_id(),
                "kind": kind,
                "state": "queued",
                "target_id": target_id,
                "created_at": utc_now(),
                "started_at": None,
                "finished_at": None,
                "stdout": "",
                "stderr": "",
                "exit_code": None,
                "error": None,
                "resolved_packages": None,
                "checked_at": None,
            }
            self._operations[value["id"]] = value
            self._active_targets.add(target_id)
            task = asyncio.create_task(self._run(value, lock, worker))
            self._tasks.add(task)
            self._operation_tasks[value["id"]] = task
            task.add_done_callback(self._tasks.discard)
            task.add_done_callback(partial(self._forget_operation_task, operation_id=value["id"]))
            return value

    def _forget_operation_task(self, _task: asyncio.Task[None], *, operation_id: str) -> None:
        self._operation_tasks.pop(operation_id, None)

    async def close(self) -> None:
        """Cancel pending operation workers during application shutdown."""

        tasks = list(self._tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._tasks.clear()

    async def cancel(self, value: str) -> None:
        """Cancel one operation and wait until its process cleanup finishes."""

        operation = self.get(value)
        task = self._operation_tasks.get(value)
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        elif operation["state"] in {"queued", "running"}:
            operation["state"] = "failed"
            operation["error"] = "Операция отменена"
            operation["exit_code"] = -1

    async def _run(
        self,
        operation: dict[str, Any],
        lock: asyncio.Lock,
        worker: Callable[[dict[str, Any]], Awaitable[None]],
    ) -> None:
        async with lock:
            operation["state"] = "running"
            operation["started_at"] = utc_now()
            process_token = _CURRENT_PROCESSES.set(set())
            try:
                await worker(operation)
                if operation["state"] == "running":
                    operation["state"] = "succeeded"
                    operation["exit_code"] = 0
            except asyncio.CancelledError:
                await _terminate_processes(_CURRENT_PROCESSES.get() or set())
                operation["state"] = "failed"
                operation["error"] = "Операция отменена"
                operation["exit_code"] = -1
            except Exception as exc:  # noqa: BLE001 - operation boundary
                operation["state"] = "failed"
                operation["error"] = str(exc)[:2000]
                operation["exit_code"] = operation.get("exit_code") or 1
            finally:
                operation["finished_at"] = utc_now()
                operation["stdout"] = _limit_output(operation.get("stdout", ""))
                operation["stderr"] = _limit_output(operation.get("stderr", ""))
                self._active_targets.discard(operation["target_id"])
                if self._log_dir is not None:
                    try:
                        self._log_dir.mkdir(parents=True, exist_ok=True)
                        log_path = self._log_dir / f"{operation['id']}.log"
                        log_path.write_text(
                            f"stdout:\n{operation['stdout']}\nstderr:\n{operation['stderr']}\n",
                            encoding="utf-8",
                        )
                    except OSError as exc:
                        diagnostic = f"Не удалось сохранить лог операции: {exc}"
                        operation["state"] = "failed"
                        operation["error"] = (
                            f"{operation['error']}; {diagnostic}"
                            if operation.get("error")
                            else diagnostic
                        )[:2000]
                        operation["exit_code"] = operation.get("exit_code") or 1
                _CURRENT_PROCESSES.reset(process_token)

    def get(self, value: str) -> dict[str, Any]:
        operation = self._operations.get(value)
        if operation is None:
            raise AppError("operation_not_found", "Операция не найдена", status_code=404)
        return operation

    def all(self) -> list[dict[str, Any]]:
        return list(self._operations.values())


def _limit_output(value: str | None) -> str:
    text = value or ""
    if len(text) <= MAX_OUTPUT:
        return text
    return text[-MAX_OUTPUT:]


async def run_command(
    command: list[str],
    *,
    cwd: str | None = None,
    env: dict[str, str] | None = None,
    timeout_seconds: float = 3600,
) -> tuple[int, str, str]:
    """Execute a controlled argv command without involving a shell."""

    if not command or any("\x00" in part for part in command):
        raise ValueError("invalid command")
    process_kwargs: dict[str, Any] = {
        "cwd": cwd,
        "env": env,
        "stdout": asyncio.subprocess.PIPE,
        "stderr": asyncio.subprocess.PIPE,
    }
    if os.name == "nt":
        process_kwargs["creationflags"] = getattr(asyncio.subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    else:
        process_kwargs["start_new_session"] = True
    process = await asyncio.create_subprocess_exec(*command, **process_kwargs)
    processes = _CURRENT_PROCESSES.get()
    if processes is not None:
        processes.add(process)
    completed = False
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout_seconds)
        completed = True
    except TimeoutError:
        await _terminate_process(process)
        completed = True
        raise RuntimeError("Команда превысила допустимое время выполнения") from None
    finally:
        # During task cancellation the operation manager still needs this
        # process in its registry to terminate its process group.
        if processes is not None and completed:
            processes.discard(process)
    return (
        process.returncode or 0,
        _limit_output(stdout.decode(errors="replace")),
        _limit_output(stderr.decode(errors="replace")),
    )


async def _terminate_processes(processes: set[asyncio.subprocess.Process]) -> None:
    """Terminate all operation children and wait for their reaping."""

    if not processes:
        return
    await asyncio.gather(*(_terminate_process(process) for process in tuple(processes)))
    processes.clear()


async def _terminate_process(process: asyncio.subprocess.Process) -> None:
    if process.returncode is not None:
        return
    try:
        if os.name == "nt":
            process.terminate()
        else:
            os.killpg(process.pid, signal.SIGTERM)
    except (ProcessLookupError, OSError):
        return
    try:
        await asyncio.wait_for(process.wait(), timeout=2)
    except TimeoutError:
        try:
            if os.name == "nt":
                process.kill()
            else:
                os.killpg(process.pid, signal.SIGKILL)
        except (ProcessLookupError, OSError):
            pass
        await process.wait()


def safe_target_name(name: str) -> str:
    """Validate a user-facing venv name before joining it to the runtime root."""

    if (
        not name
        or name in {".", ".."}
        or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", name)
    ):
        raise AppError(
            "invalid_venv_name",
            "Имя окружения содержит недопустимые символы",
            status_code=422,
        )
    return name


def ensure_safe_relative_path(value: str) -> str:
    """Return a normalised relative POSIX path for HF file selections."""

    from pathlib import PurePosixPath

    if not value or "\\" in value or value.startswith("/"):
        raise ValueError("path must be relative")
    path = PurePosixPath(value)
    if any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError("path escapes destination")
    return path.as_posix()


__all__ = [
    "OperationManager",
    "ensure_safe_relative_path",
    "run_command",
    "safe_target_name",
    "utc_now",
]
