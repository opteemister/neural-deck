"""Discovery of supported inference processes outside Neural Deck."""

from __future__ import annotations

import getpass
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit, urlunsplit

import httpx
import psutil

from ..schemas.instances import Instance

SUPPORTED_MARKERS: dict[str, tuple[str, ...]] = {
    "vllm": ("vllm", "vllm.entrypoints.openai.api_server"),
    "llama.cpp": ("llama-server", "llama.cpp"),
    "sglang": ("sglang", "sglang.launch_server"),
    "ninfer": ("ninfer", "ninfer-serve"),
    "freetoken": ("freetoken", "free_token", "ft"),
    "image": ("comfyui", "comfy", "comfyui/main.py"),
}
_PORT_RE = re.compile(r"(?:--port(?:=|\s+)|(?:^|\s)-p\s+)(\d{1,5})(?:\s|$)")
_TEXT_ENGINES = frozenset({"vllm", "llama.cpp", "sglang", "ninfer", "freetoken"})


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
    if executable in {"comfyui", "comfy"} or (
        executable == "main.py" and any("comfy" in part.lower() for part in command)
    ):
        return "image"
    if executable == "main.py" and any(
        arg.split("=", 1)[0] in {"--base-directory", "--models-directory", "--extra-model-paths-config"}
        for arg in arguments
    ):
        return "image"
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


def process_host(command: list[str]) -> str | None:
    """Return an explicitly configured listen host from a process command."""

    flags = {"--host", "--listen-host", "--listen-address", "--bind", "--address", "--listen"}
    for index, argument in enumerate(command):
        name, separator, value = argument.partition("=")
        if name not in flags:
            continue
        if separator:
            return value.strip("[]") or None
        if index + 1 < len(command) and not command[index + 1].startswith("-"):
            return command[index + 1].strip("[]") or None
        # ComfyUI's bare --listen flag means bind on all interfaces.
        if name == "--listen":
            return "0.0.0.0"
    return None


def _format_host(host: str) -> str:
    value = host.strip()
    if ":" in value and not value.startswith("["):
        return f"[{value}]"
    return value


def backend_probe_url(url: str) -> str:
    """Use a local destination for wildcard binds, without inventing UI URLs."""

    parsed = urlsplit(url)
    if parsed.hostname not in {"0.0.0.0", "::"}:
        return url
    host = "[::1]" if parsed.hostname == "::" else "127.0.0.1"
    netloc = f"{host}:{parsed.port}" if parsed.port is not None else host
    return urlunsplit((parsed.scheme, netloc, parsed.path, parsed.query, parsed.fragment))


def _legacy_base_url(protocol: str, command: list[str], port: int | None) -> str | None:
    if port is None:
        return None
    host = _format_host(process_host(command) or "127.0.0.1")
    suffix = "/v1" if protocol == "openai" else ""
    return f"http://{host}:{port}{suffix}"


def legacy_runtime_metadata(
    engine_id: str, command: list[str], port: int | None
) -> dict[str, Any]:
    """Map pre-definition engine IDs to explicit compatibility metadata."""

    if engine_id in _TEXT_ENGINES:
        return {
            "types": ["text"],
            "api_protocol": "openai",
            "api_base_url": _legacy_base_url("openai", command, port),
            "ui_url": None,
        }
    if engine_id in {"image", "comfyui"}:
        base_url = _legacy_base_url("comfyui", command, port)
        return {
            "types": ["image", "video"],
            "api_protocol": "comfyui",
            "api_base_url": base_url,
            "ui_url": None,
        }
    return {"types": [], "api_protocol": "none", "api_base_url": None, "ui_url": None}


async def query_models(
    port: int | None,
    *,
    api_base_url: str | None = None,
    request_timeout: float = 0.35,
    client: Any | None = None,
) -> list[str]:
    if port is None and not api_base_url:
        return []
    own_client = client is None
    active_client = client or httpx.AsyncClient(timeout=request_timeout)
    base_url = backend_probe_url(api_base_url).rstrip("/") if api_base_url else f"http://127.0.0.1:{port}/v1"
    try:
        response = await active_client.get(f"{base_url}/models")
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


async def query_image_ready(
    port: int | None,
    *,
    api_base_url: str | None = None,
    request_timeout: float = 0.5,
    client: Any | None = None,
) -> bool:
    if port is None and not api_base_url:
        return False
    own_client = client is None
    active_client = client or httpx.AsyncClient(timeout=request_timeout)
    if api_base_url:
        parsed = urlsplit(backend_probe_url(api_base_url))
        url = urlunsplit((parsed.scheme, parsed.netloc, "/system_stats", "", ""))
    else:
        url = f"http://127.0.0.1:{port}/system_stats"
    try:
        response = await active_client.get(url)
        return response.status_code == 200
    except (httpx.HTTPError, ValueError, TypeError, AttributeError):
        return False
    finally:
        if own_client:
            await active_client.aclose()


async def query_readiness(
    engine_id: str,
    port: int | None,
    *,
    client: Any | None = None,
    api_protocol: str | None = None,
    api_base_url: str | None = None,
    healthcheck: Any | None = None,
) -> tuple[bool, list[str]]:
    protocol = api_protocol
    if protocol is None:
        # Compatibility for callers using the pre-definition function shape.
        protocol = "comfyui" if engine_id == "image" else "openai"
    if protocol == "none" or (port is None and not api_base_url):
        return False, []
    if healthcheck is None:
        if protocol == "comfyui":
            return await query_image_ready(port, api_base_url=api_base_url, client=client), []
        if protocol == "openai":
            models = await query_models(port, api_base_url=api_base_url, client=client)
            return bool(models), models
        # A custom protocol has no implicit readiness URL. Profiles must
        # provide a healthcheck explicitly; otherwise startup records only
        # that the process is running, without advertising API readiness.
        return False, []

    path = getattr(healthcheck, "path", None)
    timeout = getattr(healthcheck, "timeout_seconds", None)
    if isinstance(healthcheck, dict):
        path = healthcheck.get("path")
        timeout = healthcheck.get("timeout_seconds", 3.0)
    if not isinstance(path, str) or not path.startswith("/") or not api_base_url:
        return False, []
    try:
        parsed = urlsplit(backend_probe_url(api_base_url))
        url = urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))
        if client is None:
            async with httpx.AsyncClient(timeout=float(timeout or 3.0)) as active_client:
                response = await active_client.get(url)
        else:
            response = await client.get(url, timeout=float(timeout or 3.0))
        ready = 200 <= response.status_code < 300
    except (httpx.HTTPError, TypeError, ValueError, AttributeError):
        return False, []
    if not ready or protocol != "openai":
        return ready, []
    models = await query_models(port, api_base_url=api_base_url, client=client)
    return True, models


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
        metadata = legacy_runtime_metadata(engine_id, command, port)
        image_ready = (
            await query_image_ready(port, api_base_url=metadata["api_base_url"], client=http_client)
            if engine_id in {"image", "comfyui"} and probe_models
            else False
        )
        model_names = (
            await query_models(port, api_base_url=metadata["api_base_url"], client=http_client)
            if probe_models and engine_id not in {"image", "comfyui"}
            else []
        )
        model_name = model_names[0] if len(model_names) == 1 else None
        identity_verified = start_ns is not None
        can_stop = identity_verified and process_user == current_user
        state: Literal["ready", "running"] = (
            "ready" if (image_ready or model_names) else "running"
        )
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
            **metadata,
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
    "legacy_runtime_metadata",
    "backend_probe_url",
    "process_host",
    "process_port",
    "query_image_ready",
    "query_models",
    "query_readiness",
]
