"""Small asynchronous operation runner used by management features.

Operations deliberately live in the service process in Version Zero.  The
runner keeps the management API responsive while installation and model
downloads perform blocking external work.  The public representation is kept
independent from the storage implementation so it can be persisted later.
"""

from __future__ import annotations

import asyncio
import contextvars
import logging
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
logger = logging.getLogger(__name__)

# Commands started from an operation inherit this task-local registry.  It
# lets cancellation clean up the actual subprocess even when the worker is
# waiting in ``communicate``.
_CURRENT_PROCESSES: contextvars.ContextVar[set[asyncio.subprocess.Process] | None] = (
    contextvars.ContextVar("neural_deck_operation_processes", default=None)
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
        self._streamed: set[str] = set()

    def append_output(self, operation: dict[str, Any], channel: str, text: str) -> None:
        """Publish command output immediately to the shared Operations log."""
        operation[channel] = _limit_output(operation.get(channel, '') + text)
        if self._log_dir is not None:
            self._log_dir.mkdir(parents=True, exist_ok=True)
            with (self._log_dir / 'operations.log').open('a', encoding='utf-8') as output:
                for line in text.splitlines():
                    output.write(f"[{operation['id']}] {line}\n")
            self._streamed.add(operation['id'])

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

            # A retry of a finished operation represents the same task in the
            # UI. Reuse its row instead of accumulating another entry for the
            # same target, and discard older terminal duplicates left by prior
            # retries.
            reusable: dict[str, Any] | None = None
            for existing_id, existing in reversed(list(self._operations.items())):
                if existing["kind"] != kind or existing["target_id"] != target_id:
                    continue
                if existing["state"] in {"queued", "running"}:
                    continue
                if reusable is None:
                    reusable = existing
                else:
                    del self._operations[existing_id]

            value = reusable or {
                "id": operation_id(),
                "kind": kind,
                "target_id": target_id,
            }
            value.update({
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
                "progress": None,
            })
            value.pop("label", None)
            self._operations[value["id"]] = value
            self._active_targets.add(target_id)
            task = asyncio.create_task(self._run(value, lock, worker))
            self._tasks.add(task)
            self._operation_tasks[value["id"]] = task
            task.add_done_callback(self._tasks.discard)
            task.add_done_callback(partial(self._forget_operation_task, operation_id=value["id"]))
            logger.info(
                "Изменение запланировано: kind=%s target=%s operation_id=%s",
                kind,
                target_id,
                value["id"],
            )
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
            logger.info(
                "Операция начата: kind=%s target=%s operation_id=%s",
                operation["kind"],
                operation["target_id"],
                operation["id"],
            )
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
                        log_path = self._log_dir / "operations.log"
                        with log_path.open("a", encoding="utf-8") as log_file:
                            log_file.write(
                                f"\n--- operation_id={operation['id']} "
                                f"kind={operation['kind']} target={operation['target_id']} ---\n"
                                + ("" if operation['id'] in self._streamed else f"stdout:\n{operation['stdout']}\nstderr:\n{operation['stderr']}\n")
                                + f"state={operation['state']} exit_code={operation['exit_code']} error={operation.get('error') or '-'}\n"
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
                self._streamed.discard(operation['id'])
                _CURRENT_PROCESSES.reset(process_token)
                log_method = logger.info if operation["state"] == "succeeded" else logger.warning
                log_method(
                    "Операция завершена: kind=%s target=%s operation_id=%s state=%s error=%s",
                    operation["kind"],
                    operation["target_id"],
                    operation["id"],
                    operation["state"],
                    operation.get("error") or "-",
                )

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
    on_stdout_line: Callable[[str], None] | None = None,
    on_stderr_line: Callable[[str], None] | None = None,
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
    async def collect_output() -> tuple[bytes, bytes]:
        if on_stdout_line is None and on_stderr_line is None:
            return await process.communicate()
        async def read_stdout() -> bytes:
            assert process.stdout is not None
            output = b""
            async for line in process.stdout:
                if on_stdout_line is not None:
                    on_stdout_line(line.decode(errors="replace"))
                output = (output + line)[-MAX_OUTPUT:]
            return output

        async def read_stderr() -> bytes:
            assert process.stderr is not None
            output = b""
            if on_stderr_line is not None:
                async for line in process.stderr:
                    on_stderr_line(line.decode(errors="replace"))
                    output = (output + line)[-MAX_OUTPUT:]
            else:
                while chunk := await process.stderr.read(8192):
                    output = (output + chunk)[-MAX_OUTPUT:]
            return output

        stdout, stderr = await asyncio.gather(read_stdout(), read_stderr())
        await process.wait()
        return stdout, stderr

    try:
        stdout, stderr = await asyncio.wait_for(collect_output(), timeout=timeout_seconds)
        completed = True
    except TimeoutError:
        await _terminate_process(process)
        completed = True
        raise RuntimeError("Команда превысила допустимое время выполнения") from None
    except BaseException:
        await _terminate_process(process)
        completed = True
        raise
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
