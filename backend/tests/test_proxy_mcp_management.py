"""Proxy configuration through MCP and HTTP uses the same domain services."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI
from pydantic import ValidationError

from app.api.openai_connections import router as connections_router
from app.api.proxy_routes import router as routes_router
from app.errors import AppError, app_error_handler
from app.mcp.operations import build_operations
from app.mcp.server import MCPDependencies, create_mcp_server
from app.proxy.comfy_provider import ComfyProviderService
from app.proxy.configuration import ProxyConfiguration
from app.proxy.media_router import management_router
from app.proxy.routes import RouteManager
from app.proxy.service import ProxyService
from app.schemas.proxy_management import MediaAliasRequest
from tests.test_proxy_routes import ProcessManagerStub, ProfileRepositoryStub, profile


@pytest.fixture
def setup(tmp_path):
    requests = []

    def upstream(request):
        requests.append(request)
        return httpx.Response(200, json={"data": [{"id": "remote-model", "context_length": 8192}]})

    local = profile("local-profile")
    routes = RouteManager(tmp_path, ProfileRepositoryStub([local]))
    proxy = ProxyService(ProcessManagerStub([]), route_manager=routes,
                         client_factory=lambda: httpx.AsyncClient(transport=httpx.MockTransport(upstream)))
    comfy = ComfyProviderService(routes.config)
    comfy.validate_workflow = AsyncMock(return_value={"usable": None, "path": "workflows/offline.json"})
    comfy.source_status = AsyncMock(return_value={"available": False})
    comfy.workflows = AsyncMock(return_value={"items": ["workflows/offline.json"], "available": False, "cached": True, "updated_at": 1})
    app = FastAPI()
    @app.middleware("http")
    async def request_id(request, call_next):
        request.state.request_id = "test-request"
        return await call_next(request)

    app.state.runtime = SimpleNamespace(proxy_service=proxy, comfy_service=comfy)
    app.state.proxy_service = proxy
    app.add_exception_handler(AppError, app_error_handler)
    for router in (connections_router, routes_router, management_router):
        app.include_router(router)
    operations = build_operations(app)

    async def unused(*args):
        raise AssertionError("Unexpected operation")

    server = create_mcp_server(MCPDependencies(unused, unused, unused, unused, unused, operations=operations))

    async def call(tool_name, **args):
        result = await server._tool_manager.call_tool(tool_name, args)
        return result.structuredContent

    return SimpleNamespace(app=app, proxy=proxy, comfy=comfy, server=server, call=call,
                           requests=requests, manager=ProxyConfiguration(proxy, comfy))


@pytest.mark.asyncio
async def test_openai_mixed_candidates_keys_refresh_and_delete_guard(setup):
    saved = await setup.call("nd_save_openai_connection", connection={"name": "Remote", "base_url": "https://remote.test/v1", "api_key": "test-secret"})
    connection = saved["data"]
    cid = connection["id"]
    assert connection["has_api_key"] is True
    assert "test-secret" not in json.dumps(saved)
    await setup.call("nd_save_openai_connection", connection_id=cid, connection={"name": "Remote renamed", "base_url": "https://remote.test/v1"})
    assert setup.proxy.connections().get(cid)["api_key"] == "test-secret"
    listed = await setup.call("nd_list_openai_connections")
    assert listed["data"]["items"][0]["name"] == "Remote renamed"
    assert "test-secret" not in json.dumps(listed)
    models = await setup.call("nd_list_openai_connection_models", connection_id=cid, refresh=True)
    assert models["data"]["items"][0]["id"] == "remote-model"
    assert setup.requests[-1].headers["authorization"] == "Bearer test-secret"
    count = len(setup.requests)
    choices = (await setup.call("nd_list_proxy_model_choices"))["data"]
    assert len(setup.requests) == count
    assert len(choices["items"]) == 2
    targets = [item["target"] for item in choices["items"]]
    alias = await setup.call("nd_save_proxy_alias", mode="create", name="mixed", candidates=targets)
    assert len(alias["data"]["candidate_ids"]) == 2
    ids = list(reversed(alias["data"]["candidate_ids"]))
    reordered = await setup.call("nd_set_proxy_route_order", route_name="mixed", candidate_ids=ids)
    assert reordered["data"]["candidate_ids"] == ids
    detail = await setup.call("nd_get_proxy_route", route_name="mixed")
    assert detail["data"]["candidate_ids"] == ids
    blocked = await setup.call("nd_delete_openai_connection", connection_id=cid)
    assert blocked["error"]["code"] == "connection_in_use"
    await setup.call("nd_save_proxy_alias", mode="update", name="renamed", previous_name="mixed", candidates=[])
    await setup.call("nd_delete_proxy_alias", name="renamed")
    await setup.call("nd_save_openai_connection", connection_id=cid, connection={"name": "Remote", "base_url": "https://remote.test/v1", "api_key": ""})
    assert not setup.proxy.connections().list()[0]["has_api_key"]
    await setup.call("nd_delete_openai_connection", connection_id=cid)
    assert setup.proxy.connections().list() == []


@pytest.mark.asyncio
async def test_comfy_sources_media_limits_workflows_and_http_parity(setup):
    await setup.call("nd_save_comfy_source", source={"name": "remote", "base_url": "http://remote.test:8188"})
    image = {"name": "image-alias", "type": "image", "source": "remote", "workflows": {"generate": "workflows/offline.json", "edit_masked": "workflows/mask.json"}, "max_megapixels": 8}
    assert "data" in await setup.call("nd_save_media_alias", alias=image)
    video = {"name": "video-alias", "type": "video", "source": "remote", "workflows": {"generate": "workflows/video.json", "edit": "workflows/edit.json"}, "max_resolution": 1080, "max_seconds": 30}
    assert "data" in await setup.call("nd_save_media_alias", alias=video)
    blocked = await setup.call("nd_delete_comfy_source", name="remote")
    assert blocked["error"]["code"] == "connection_in_use"
    renamed = await setup.call("nd_save_comfy_source", previous_name="remote", source={"name": "renamed", "base_url": "http://remote.test:8188"})
    assert {model["source"] for model in renamed["data"]["models"].values()} == {"renamed"}
    assert (await setup.call("nd_list_comfy_workflows", source="renamed"))["data"]["cached"] is True
    assert (await setup.call("nd_validate_comfy_workflow", source="renamed", path="workflows/offline.json"))["data"]["usable"] is None
    assert (await setup.call("nd_get_comfy_source_status", source="renamed"))["data"]["available"] is False
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=setup.app), base_url="http://test") as client:
        response = await client.get("/api/v1/proxy/comfyui")
        assert response.json()["data"] == (await setup.call("nd_get_proxy_media_config"))["data"]
        # HTTP edits the same entity and preserves unspecified limits.
        response = await client.patch("/api/v1/proxy/comfyui/aliases/video-alias", json={**video, "source": "renamed", "name": "renamed-video", "max_seconds": 45})
        assert response.status_code == 200
        assert (await setup.call("nd_get_proxy_media_config"))["data"]["models"]["renamed-video"]["max_seconds"] == 45
    for name in ("image-alias", "renamed-video"):
        await setup.call("nd_delete_media_alias", name=name)
    assert "data" in await setup.call("nd_delete_comfy_source", name="renamed")
    assert setup.manager.get()["models"] == {}
    for tool, args in (("nd_delete_comfy_source", {"name": "local"}), ("nd_save_comfy_source", {"previous_name": "local", "source": {"name": "local2", "base_url": "http://test:8188"}})):
        assert (await setup.call(tool, **args))["error"]["code"] == "default_source_readonly"


@pytest.mark.asyncio
async def test_preserves_unrelated_config_bindings_and_unspecified_limits(setup):
    settings = setup.manager.get()
    settings["models"]["image"] = {"type": "image", "source": "local", "max_megapixels": 12, "workflows": {"generate": "workflows/original.json", "edit": "workflows/edit.json"}, "bindings": {"generate": {"prompt": {"node": "6", "input": "text"}}, "edit": {"image": {"node": "1", "input": "image"}}}}
    setup.comfy.save_settings(settings)
    await setup.proxy.create_alias("text", [])
    payload = {"name": "image-new", "type": "image", "source": "local", "workflows": {"generate": "workflows/original.json", "edit": "workflows/new-edit.json"}}
    updated = (await setup.call("nd_save_media_alias", previous_name="image", alias=payload))["data"]
    assert updated["models"]["image-new"]["max_megapixels"] == 12
    assert set(updated["models"]["image-new"]["bindings"]) == {"generate"}
    assert "text" in setup.proxy.route_manager.config.load()["aliases"]


@pytest.mark.asyncio
async def test_invalid_workflow_and_cross_type_collisions_do_not_write(setup):
    before = setup.manager.get()
    payload = {"name": "bad", "type": "image", "source": "local", "workflows": {"generate": "workflows/bad.json"}}
    setup.comfy.validate_workflow.return_value = {"usable": False, "message": "Требуется API graph"}
    assert (await setup.call("nd_save_media_alias", alias=payload))["error"]["code"] == "workflow_invalid"
    assert setup.manager.get() == before
    setup.comfy.validate_workflow.return_value = {"usable": True}
    await setup.proxy.create_alias("text", [])
    assert (await setup.call("nd_save_media_alias", alias={**payload, "name": "text"}))["error"]["code"] == "route_conflict"
    await setup.call("nd_save_media_alias", alias=payload)
    assert (await setup.call("nd_save_proxy_alias", mode="create", name="bad", candidates=[]))["error"]["code"] == "route_conflict"


@pytest.mark.parametrize("extra", [{"max_megapixels": 0}, {"max_megapixels": 129}, {"max_seconds": 30}, {"workflows": {"unknown": "file.json"}}])
def test_image_fields_reject_invalid_settings(extra):
    with pytest.raises(ValidationError):
        MediaAliasRequest.model_validate({"name": "image", "type": "image", "source": "local", "workflows": {}, **extra})


def test_new_tool_and_field_descriptions_are_present(setup):
    tools = {tool.name: tool for tool in setup.server._tool_manager.list_tools()}
    for name in ("nd_get_proxy_route", "nd_list_proxy_model_choices", "nd_list_openai_connections", "nd_save_openai_connection", "nd_delete_openai_connection", "nd_list_openai_connection_models", "nd_get_proxy_media_config", "nd_save_comfy_source", "nd_delete_comfy_source", "nd_get_comfy_source_status", "nd_list_comfy_workflows", "nd_validate_comfy_workflow", "nd_save_media_alias", "nd_delete_media_alias", "nd_save_proxy_alias", "nd_set_proxy_route_order", "nd_list_proxy_routes", "nd_list_proxy_candidates", "nd_read_proxy_diagnostics", "nd_delete_proxy_alias"):
        tool = tools[name]
        assert tool.description
        schema = tool.parameters
        for definition in (schema, *schema.get("$defs", {}).values()):
            for field in definition.get("properties", {}).values():
                assert field.get("description"), (name, field)
