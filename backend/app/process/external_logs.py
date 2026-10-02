"""Read observable external output without consuming pipes or terminals."""

from __future__ import annotations

import asyncio
import os
import stat
from datetime import UTC, datetime

import psutil

from ..schemas.instances import Instance, LogsData

LIMIT = 256_000


def _same_process(instance: Instance) -> bool:
    try:
        return instance.process_start_time_ns is not None and int(
            psutil.Process(instance.pid).create_time() * 1_000_000_000
        ) == instance.process_start_time_ns
    except (psutil.Error, OSError):
        return False


def _output_files(instance: Instance) -> tuple[str, bool, bool]:
    chunks: list[str] = []
    seen: set[tuple[int, int]] = set()
    truncated = False
    for number in (2, 1):
        try:
            path = f"/proc/{instance.pid}/fd/{number}"
            if not stat.S_ISREG(os.stat(path).st_mode):
                continue
            # Nonblocking open and fstat avoid draining another reader's pipe.
            fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
            with os.fdopen(fd, "rb") as stream:
                info = os.fstat(stream.fileno())
                identity = (info.st_dev, info.st_ino)
                if not stat.S_ISREG(info.st_mode) or identity in seen:
                    continue
                seen.add(identity)
                offset = max(0, info.st_size - LIMIT)
                stream.seek(offset)
                data = stream.read(LIMIT)
                if offset:
                    data = data.partition(b"\n")[2]
                    truncated = True
                chunks.append(data.decode("utf-8", errors="replace"))
        except OSError:
            continue
    return "\n".join(chunks), bool(seen), truncated


async def external_logs(instance: Instance, tail: int) -> LogsData:
    unavailable = LogsData(instance_id=instance.id, lines=[], truncated=False, available=False)
    if not _same_process(instance):
        return unavailable
    text, available, truncated = await asyncio.to_thread(_output_files, instance)
    if not available:
        since = datetime.fromtimestamp(instance.process_start_time_ns / 1e9, UTC).isoformat()
        try:
            process = await asyncio.create_subprocess_exec(
                "journalctl", "--quiet", "--no-pager", "--output=cat",
                f"_PID={instance.pid}", "--since", since, "-n", str(tail),
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
            )
        except OSError:
            return unavailable
        try:
            output, _ = await asyncio.wait_for(process.communicate(), timeout=2)
        except (TimeoutError, asyncio.CancelledError) as exc:
            if process.returncode is None:
                process.kill()
            await process.wait()
            if isinstance(exc, asyncio.CancelledError):
                raise
            return unavailable
        if process.returncode != 0 or not output.strip():
            return unavailable
        truncated = len(output) > LIMIT
        text = output[-LIMIT:].decode("utf-8", errors="replace")
    if not _same_process(instance):
        return unavailable
    lines = text.splitlines()
    return LogsData(instance_id=instance.id, lines=lines[-tail:],
                    truncated=truncated or len(lines) > tail, available=True)
