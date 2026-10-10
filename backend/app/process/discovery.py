"""Discovery of supported inference processes outside Neural Deck."""

from __future__ import annotations

import getpass
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import httpx
import psutil

from ..schemas.instances import Instance

SUPPORTED_MARKERS: dict[str, tuple[str, ...]] = {
    "vllm": ("vllm", "vllm.entrypoints.openai.api_server"),
    "llama.cpp": ("llama-server", "llama.cpp"),
    "sglang": ("sglang", "sglang.launch_server"),
    "ninfer": ("ninfer", "ninfer-serve"),
    "freetoken": ("freetoken", "free_token", "ft"),
}
_PORT_RE = re.compile(r"(?:--port(?:=|\s+)|(?:^|\s)-p\s+)(\d{1,5})(?:\s|$)")


@dataclass(slots=True)
class ExternalObservation:
    instance: Instance
    upstream_models: list[str]


def _start_time_ns(process: Any) -> int | None:
    try:
        value = int(float(process.create_time()) * 1_000_000_000)
        return value if value > 0 else None
    except (AttributeError, psutil.Error, OSError, TypeError, ValueError):
        return None


def _command_line(process: Any) -> list[str]:
    try:
        result = process.cmdline()
    except (AttributeError, psutil.Error, OSError):
        return []
    return [str(item) for item in result]


def identify_engine(command: list[str], process_name: str = "") -> str | None:
    del process_name  # command-line evidence is required; name alone is ambiguous
    if not command:
        return None
    executable = Path(command[0]).name.lower()
    arguments = command[1:]
    module = False
    if re.fullmatch(r"python(?:\d+(?:\.\d+)*)?", executable):
        # A Python console entry point or explicit -m server module. Do not
        # inspect arbitrary -c code or package names passed to pip/build tools.
        while arguments and arguments[0] in {"-u", "-B", "-E", "-s", "-S", "-I"}:
            arguments = arguments[1:]
        if not arguments:
            return None
        if arguments[0] == "-m" and len(arguments) >= 2:
            executable, arguments = arguments[1], arguments[2:]
            module = True
        elif not arguments[0].startswith("-"):
            executable, arguments = Path(arguments[0]).name.lower(), arguments[1:]
        else:
            return None
    if any(
        argument.split("=", 1)[0] in {"--help", "-h", "--version", "-V"}
        for argument in arguments
    ):
        return None
    if module and executable == "vllm.entrypoints.openai.api_server":
        return "vllm"
    if module and executable == "sglang.launch_server":
        return "sglang"
    if executable in {"vllm", "sglang", "freetoken", "free_token", "ft"}:
        if arguments and arguments[0] == "serve":
            return {"free_token": "freetoken", "ft": "freetoken"}.get(executable, executable)
        return None
    if not module and executable == "llama-server":
        return "llama.cpp"
    if executable in {"ninfer", "ninfer-serve"} and arguments:
        if (not arguments[0].startswith("-") and "/" in arguments[0]) or any(
            arg.split("=", 1)[0] in {"--model", "--model-path"} for arg in arguments
        ):
            return "ninfer"
    return None


def process_port(process: Any, command: list[str]) -> int | None:
    text = " ".join(command)
    match = _PORT_RE.search(text)
    if match:
        port = int(match.group(1))
        return port if 1 <= port <= 65535 else None
    try:
        for connection in process.net_connections(kind="inet"):
            address = getattr(connection, "laddr", None)
            candidate_port = getattr(address, "port", None)
            if candidate_port and getattr(connection, "status", "") == psutil.CONN_LISTEN:
                return int(candidate_port)
    except (AttributeError, psutil.Error, OSError, TypeError, ValueError):
        return None
    return None


async def query_models(
    port: int | None, *, request_timeout: float = 0.35, client: Any | None = None
) -> list[str]:
    if port is None:
        return []
    own_client = client is None
    active_client = client or httpx.AsyncClient(timeout=request_timeout)
    try:
        response = await active_client.get(f"http://127.0.0.1:{port}/v1/models")
        if response.status_code != 200:
            return []
        payload = response.json()
    except (httpx.HTTPError, ValueError, TypeError, AttributeError):
        return []
    finally:
        if own_client:
            await active_client.aclose()
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, list):
        return []
    result: list[str] = []
    for item in data:
        if isinstance(item, dict) and isinstance(item.get("id"), str) and item["id"]:
            result.append(item["id"])
    return result


async def discover_external(
    *,
    excluded_keys: set[tuple[int, int | None]] | None = None,
    username: str | None = None,
    http_client: Any | None = None,
    probe_models: bool = True,
) -> list[ExternalObservation]:
    """Return only processes whose command confirms one supported engine.

    Missing command-line fields, ports or API models remain ``None``/empty;
    no values are inferred from a process name alone.
    """

    excluded = excluded_keys or set()
    current_user = username or getpass.getuser()
    processes: list[Any] = []
    try:
        processes = list(psutil.process_iter(["pid", "name", "username", "cmdline", "create_time"]))
    except (psutil.Error, OSError):
        return []
    observations: list[ExternalObservation] = []
    for process in processes:
        try:
            pid = int(process.pid)
        except (AttributeError, TypeError, ValueError):
            continue
        command = _command_line(process)
        try:
            process_name = str(process.name())
        except (AttributeError, psutil.Error, OSError):
            process_name = ""
        engine_id = identify_engine(command, process_name)
        if engine_id is None:
            continue
        start_ns = _start_time_ns(process)
        if (pid, start_ns) in excluded:
            continue
        try:
            process_user = process.username()
        except (AttributeError, psutil.Error, OSError):
            process_user = None
        port = process_port(process, command)
        model_names = await query_models(port, client=http_client) if probe_models else []
        model_name = model_names[0] if len(model_names) == 1 else None
        identity_verified = start_ns is not None
        can_stop = identity_verified and process_user == current_user
        state: Literal["ready", "running"] = "ready" if model_name else "running"
        # ``unknown`` is part of the public observation id when psutil cannot
        # provide create_time.  The process remains visible, but the explicit
        # capability flags prevent stop/routing code from treating it as a
        # verified target.
        stable_start = str(start_ns) if start_ns is not None else "unknown"
        instance = Instance(
            id=f"external/{engine_id}/{pid}-{stable_start}",
            source="external",
            profile_name=None,
            engine_id=engine_id,
            pid=pid,
            process_start_time_ns=start_ns,
            identity_verified=identity_verified,
            can_stop=can_stop,
            port=port,
            model_name=model_name,
            state=state,
            observed_at=datetime.now(UTC),
        )
        observations.append(ExternalObservation(instance=instance, upstream_models=model_names))
    return observations


__all__ = [
    "ExternalObservation",
    "SUPPORTED_MARKERS",
    "discover_external",
    "identify_engine",
    "process_port",
    "query_models",
]
