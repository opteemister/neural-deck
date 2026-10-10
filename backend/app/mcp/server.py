"""Official MCP SDK integration for the stateless management surface.

The MCP server is constructed from callables so it can be mounted by the
application lifespan without copying REST business logic into tools.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any
from uuid import uuid4

from ..errors import AppError

AsyncOperation = Callable[..., Awaitable[Any]]


@dataclass(frozen=True, slots=True)
class MCPDependencies:
    get_system_state: AsyncOperation
    list_models: AsyncOperation
    list_profiles: AsyncOperation
    start_profile: AsyncOperation
    stop_instance: AsyncOperation


def _value(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, dict):
        return {str(key): _value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_value(item) for item in value]
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return value


def _success(data: Any, request_id: str) -> dict[str, Any]:
    return {"data": _value(data), "request_id": request_id}


def _error(exc: Exception, request_id: str) -> dict[str, Any]:
    if isinstance(exc, AppError):
        return {
            "error": {
                "code": exc.code,
                "message": exc.message,
                "details": exc.details,
                "request_id": request_id,
                "retryable": exc.retryable,
            }
        }
    return {
        "error": {
            "code": "internal_error",
            "message": "Внутренняя ошибка сервиса",
            "details": {},
            "request_id": request_id,
            "retryable": False,
        }
    }


def _call_result(envelope: dict[str, Any], *, is_error: bool) -> Any:
    """Create the SDK result while keeping text and structured output equal."""

    try:
        from mcp.types import CallToolResult, TextContent
    except ImportError:
        # This fallback only keeps the module importable in minimal source
        # checkouts. Production installs include the locked MCP dependency.
        return {
            "structuredContent": envelope,
            "content": [{"type": "text", "text": json.dumps(envelope, ensure_ascii=False)}],
            "isError": is_error,
        }
    return CallToolResult(
        content=[TextContent(type="text", text=json.dumps(envelope, ensure_ascii=False))],
        structuredContent=envelope,
        isError=is_error,
    )


def create_mcp_server(
    dependencies: MCPDependencies,
    *,
    streamable_http_path: str = "/mcp",
) -> Any:
    """Build a stateless ``FastMCP`` server with exactly five tools."""

    try:
        from mcp.server.fastmcp import FastMCP
        from mcp.server.fastmcp import server as fastmcp_server_module
        from mcp.server.transport_security import TransportSecuritySettings
    except ImportError as exc:  # pragma: no cover - dependency boundary
        raise RuntimeError("официальный пакет MCP SDK не установлен") from exc

    # MCP SDK leaves the generic lifespan annotation unresolved with recent
    # pydantic-settings releases. Rebuild it with the SDK module namespace
    # before Settings is instantiated to keep application startup warning-free.
    fastmcp_server_module.Settings.model_rebuild(
        force=True,
        _types_namespace=vars(fastmcp_server_module),
    )

    server = FastMCP(
        "Neural Deck",
        stateless_http=True,
        json_response=True,
        streamable_http_path=streamable_http_path,
        # The application is intentionally reachable from the configured home
        # network.  FastMCP enables localhost-only DNS rebinding protection by
        # default, which would reject a LAN Host header before MCP sees it.
        # Authentication is deferred by the Version Zero contract.
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
    )

    async def invoke(operation: AsyncOperation, *args: Any, **kwargs: Any) -> Any:
        request_id = f"req_{uuid4().hex}"
        try:
            result = await operation(*args, **kwargs)
        except Exception as exc:  # tool boundary must use the common envelope
            return _call_result(_error(exc, request_id), is_error=True)
        return _call_result(_success(result, request_id), is_error=False)

    @server.tool()
    async def get_system_state() -> Any:
        return await invoke(dependencies.get_system_state)

    @server.tool()
    async def list_models() -> Any:
        return await invoke(dependencies.list_models)

    @server.tool()
    async def list_profiles() -> Any:
        return await invoke(dependencies.list_profiles)

    @server.tool()
    async def start_profile(profile_name: str) -> Any:
        return await invoke(dependencies.start_profile, profile_name)

    @server.tool()
    async def stop_instance(instance_id: str) -> Any:
        return await invoke(dependencies.stop_instance, instance_id)

    return server


def build_mcp_app(dependencies: MCPDependencies) -> tuple[Any, Any]:
    """Return ``(FastMCP server, ASGI app)`` for mounting at ``/mcp``."""

    server = create_mcp_server(dependencies)
    return server, server.streamable_http_app()


def mount_mcp(app: Any, dependencies: MCPDependencies) -> Any:
    """Mount the SDK app at ``/mcp`` while retaining its own HTTP lifecycle."""

    server = create_mcp_server(dependencies, streamable_http_path="/")
    app.mount("/mcp", server.streamable_http_app())
    app.state.mcp_server = server
    return server


@asynccontextmanager
async def mcp_lifespan(server: Any) -> AsyncIterator[None]:
    """Context manager to add to the host FastAPI lifespan."""

    async with server.session_manager.run():
        yield


__all__ = [
    "MCPDependencies",
    "build_mcp_app",
    "create_mcp_server",
    "mcp_lifespan",
    "mount_mcp",
]
