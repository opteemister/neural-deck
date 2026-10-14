"""Independent model units; ordinary children remain the Docker launch mechanism."""

from __future__ import annotations

import asyncio
import json
import os
import re
from pathlib import Path
from typing import Any

import psutil

from ..errors import AppError


def process_start_ns(pid: int) -> int | None:
    try:
        process = psutil.Process(pid)
        if process.status() == psutil.STATUS_ZOMBIE:
            return None
        return int(process.create_time() * 1_000_000_000)
    except psutil.NoSuchProcess:
        return None


class ObservedProcess:
    """A process owned by an earlier backend or by systemd, not an asyncio child."""

    def __init__(self, pid: int, start_ns: int) -> None:
        self.pid = pid
        self.start_ns = start_ns

    @property
    def returncode(self) -> int | None:
        return None if process_start_ns(self.pid) == self.start_ns else 0

    async def wait(self) -> int:
        while self.returncode is None:  # noqa: ASYNC110 - external process observation
            await asyncio.sleep(0.25)
        return 0


class SystemdLauncher:
    def __init__(self, runtime_dir: Path) -> None:
        self.pending_dir = runtime_dir / "model-launches"
        self.pending_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.environment = os.environ.copy()
        self.environment["XDG_RUNTIME_DIR"] = f"/run/user/{os.getuid()}"
        self.environment["DBUS_SESSION_BUS_ADDRESS"] = f"unix:path=/run/user/{os.getuid()}/bus"

    async def _call(self, *args: str, allow_missing: bool = False) -> str:
        process = await asyncio.create_subprocess_exec(
            *args,
            env=self.environment,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=40)
        except (TimeoutError, asyncio.CancelledError):
            process.kill()
            await process.wait()
            raise
        if process.returncode and not (allow_missing and b"LoadState=not-found" in stdout):
            raise AppError("model_unit_failed", stderr.decode(errors="replace"), status_code=503)
        return stdout.decode().strip()

    def pending(self, unit: str, snapshot: dict[str, Any]) -> Path:
        path = self.pending_dir / f"{unit}.json"
        temporary = path.with_suffix(".tmp")
        with temporary.open("w", encoding="utf-8") as handle:
            os.chmod(temporary, 0o600)
            json.dump(snapshot, handle)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(path)
        return path

    async def start(
        self,
        unit: str,
        command: str,
        env: dict[str, str],
        log: Path,
        working_directory: Path | None = None,
    ) -> int:
        env_path = self.pending_dir / f"{unit}.env"
        with env_path.open("w", encoding="utf-8") as handle:
            os.chmod(env_path, 0o600)
            for key, value in env.items():
                # These descriptors belong to the backend unit; systemd creates
                # fresh values for the model rather than inheriting its parent.
                if key in {
                    "INVOCATION_ID",
                    "SYSTEMD_EXEC_PID",
                    "NOTIFY_SOCKET",
                    "JOURNAL_STREAM",
                    "LISTEN_PID",
                    "LISTEN_FDS",
                    "LISTEN_FDNAMES",
                    "WATCHDOG_PID",
                    "WATCHDOG_USEC",
                }:
                    continue
                if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
                    raise AppError("invalid_environment", "Недопустимое имя переменной окружения")
                escaped = (
                    value.replace("\\", "\\\\")
                    .replace('"', '\\"')
                    .replace("`", "\\`")
                    .replace("$", "\\$")
                )
                handle.write(f'{key}="{escaped}"\n')
        try:
            await self._call(
                "systemd-run",
                "--user",
                f"--unit={unit}",
                "--collect",
                "--service-type=exec",
                "--property=Restart=no",
                "--property=KillMode=control-group",
                "--property=TimeoutStopSec=10",
                f"--property=WorkingDirectory={str((working_directory or Path.cwd()).resolve())}",
                f"--property=EnvironmentFile={str(env_path)}",
                f"--property=StandardOutput=append:{str(log)}",
                "--property=StandardError=inherit",
                "/bin/bash",
                "-c",
                command.replace("$", "$$"),
            )
            return await self.pid(unit)
        finally:
            env_path.unlink(missing_ok=True)

    async def pid(self, unit: str) -> int:
        value = await self._call(
            "systemctl",
            "--user",
            "show",
            unit,
            "--property=MainPID",
            "--property=LoadState",
            allow_missing=True,
        )
        properties = dict(line.split("=", 1) for line in value.splitlines() if "=" in line)
        if properties.get("LoadState") == "not-found":
            return 0
        return int(properties["MainPID"])

    async def stop(self, unit: str) -> None:
        await self._call("systemctl", "--user", "stop", unit)
