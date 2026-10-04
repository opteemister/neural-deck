"""Resolve the effective model-server port without executing profile commands."""

from __future__ import annotations

import re
from dataclasses import dataclass

from ..schemas.profiles import EngineId

ENGINE_DEFAULT_PORTS: dict[EngineId, int] = {
    "llama.cpp": 8080,
    "vllm": 8000,
    "sglang": 30000,
    "ninfer": 8080,
    "freetoken": 1919,
}

_PORT_RE = re.compile(
    r"(?:^|\s)(?:--port(?:=(?P<equals>\d{1,5})|\s+(?P<value>\d{1,5}))|-p\s+(?P<short>\d{1,5}))(?:\s|$)"
)
_PORT_FLAG_RE = re.compile(r"(?:^|\s)(?:--port(?:=|\s+)|-p\s+)")


@dataclass(frozen=True, slots=True)
class PortResolution:
    port: int | None
    state: str
    source: str
    reason: str | None = None


def resolve_effective_port(engine_id: str, command: str) -> PortResolution:
    """Resolve a port from a literal command or the engine default.

    The profile environment is deliberately not inspected: the profile command
    is the source of an explicit override. Shell execution is never used.
    """

    match = _PORT_RE.search(command)
    if match:
        value = next(
            (match.group(name) for name in ("equals", "value", "short") if match.group(name)),
            None,
        )
        port = int(value) if value is not None else None
        if port is not None and 1 <= port <= 65535:
            return PortResolution(port, "ok", "command")
        return PortResolution(None, "unknown", "command", "port_out_of_range")
    if _PORT_FLAG_RE.search(command):
        return PortResolution(None, "unknown", "command", "dynamic_port")
    default = ENGINE_DEFAULT_PORTS.get(engine_id) if engine_id in ENGINE_DEFAULT_PORTS else None
    if default is None:
        return PortResolution(None, "unknown", "unavailable", "engine_default_unknown")
    return PortResolution(default, "ok", "engine_default")


__all__ = ["ENGINE_DEFAULT_PORTS", "PortResolution", "resolve_effective_port"]
