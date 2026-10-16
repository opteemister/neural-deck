"""Contract tests for the compact MCP management surface."""

from __future__ import annotations

import json

import pytest
from app.mcp.server import MCPDependencies, create_mcp_server


async def _ok(*_args: object, **_kwargs: object) -> dict[str, object]:
    return {"items": list(range(45))}


def _server() -> object:
    list_names = {
        "list_instances",
        "list_engines",
        "list_engine_definitions",
        "list_engine_definition_issues",
        "list_installations",
        "list_operations",
        "list_profiles",
        "list_profile_issues",
        "list_local_models",
        "list_local_model_contents",
        "search_hub_models",
        "list_hub_model_contents",
        "list_model_updates",
        "list_statistics_rows",
        "get_instance_metrics",
        "list_log_sources",
        "list_chat_models",
        "list_proxy_routes",
        "list_proxy_candidates",
        "read_proxy_diagnostics",
    }
    return create_mcp_server(
        MCPDependencies(
            _ok,
            _ok,
            _ok,
            _ok,
            _ok,
            operations={name: _ok for name in list_names},
        )
    )


@pytest.mark.asyncio
async def test_mcp_catalog_contains_management_tools() -> None:
    server = _server()
    names = {item.name for item in server._tool_manager.list_tools()}  # type: ignore[attr-defined]
    assert {
        "nd_get_status",
        "nd_get_hardware",
        "nd_list_engines",
        "nd_list_profiles",
        "nd_download_model",
        "nd_start_profile",
        "nd_get_statistics",
        "nd_read_log",
        "nd_list_chat_models",
        "nd_chat",
    } <= names


@pytest.mark.asyncio
async def test_mcp_page_size_is_configurable_and_cursor_reaches_end(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MCP_DEFAULT_PAGE_SIZE", "7")
    server = _server()
    first = await server._tool_manager.call_tool("nd_list_engines", {})  # type: ignore[attr-defined]
    first_data = first.structuredContent["data"]
    assert first_data["page_size"] == 7
    assert first_data["has_more"] is True

    seen = list(first_data["items"])
    cursor = first_data["next_cursor"]
    while cursor is not None:
        page = await server._tool_manager.call_tool(  # type: ignore[attr-defined]
            "nd_list_engines", {"page_size": 11, "cursor": cursor}
        )
        data = page.structuredContent["data"]
        seen.extend(data["items"])
        cursor = data["next_cursor"]
    assert seen == list(range(45))


@pytest.mark.asyncio
async def test_mcp_page_size_limit_is_explicit_and_text_matches_structured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MCP_MAX_PAGE_SIZE", "3")
    server = _server()
    result = await server._tool_manager.call_tool(  # type: ignore[attr-defined]
        "nd_list_engines", {"page_size": 4}
    )
    assert result.isError is True
    assert result.structuredContent["error"]["code"] == "page_size_exceeded"
    assert json.loads(result.content[0].text) == result.structuredContent
