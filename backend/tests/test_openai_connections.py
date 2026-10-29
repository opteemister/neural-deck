import json
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI

from app.api.dependencies import get_proxy_service
from app.api.openai_connections import router as connections_router
from app.api.proxy_routes import router as routes_router
from app.errors import AppError, app_error_handler
from app.proxy.openai_connections import OpenAIConnections
from app.proxy.routes import RouteManager
from app.proxy.service import ProxyService
from tests.test_proxy_routes import ProcessManagerStub, ProfileRepositoryStub, profile, instance, JournalStub


def service(tmp_path, handler):
    return ProxyService(
        ProcessManagerStub([]),
        route_manager=RouteManager(tmp_path, ProfileRepositoryStub([])),
        client_factory=lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )


@pytest.mark.asyncio
async def test_remote_alias_catalogue_json_and_key_isolation(tmp_path):
    requests = []
    def upstream(request):
        requests.append(request)
        assert request.url.host == "remote.test"
        assert request.headers["authorization"] == "Bearer upstream-secret"
        assert "cookie" not in request.headers
        if request.method == "GET":
            assert request.url.path == "/api/v1/models"
            return httpx.Response(200, json={"data": [{"id": "actual-model", "context_length": 8192}]})
        assert request.url.path == "/api/v1/chat/completions"
        assert json.loads(request.content)["model"] == "actual-model"
        return httpx.Response(200, json={"choices": [{"message": {"content": "remote answer"}}], "usage": {"prompt_tokens": 3, "completion_tokens": 2}})

    proxy = service(tmp_path, upstream)
    connection = proxy.connections().save(None, "Remote", "https://remote.test/api/v1/", "upstream-secret")
    alias = await proxy.create_alias("public-alias", [], {"connection_id": connection["id"], "model": "actual-model"})
    assert alias["candidates"][0]["upstream_model"] == "actual-model"
    assert alias["candidates"][0]["profile_id"] is None
    assert "upstream-secret" not in json.dumps(proxy.connections().list())
    assert proxy.connections().path.stat().st_mode & 0o777 == 0o600
    models = await proxy.models()
    public = next(item for item in models["data"] if item["id"] == "public-alias")
    assert public["context_length"] == 8192
    response = await proxy.forward(route="/v1/chat/completions", payload={"model": "public-alias", "messages": []}, headers={"Authorization": "Bearer caller-secret", "Cookie": "session=private"}, request_id="test")
    assert response.json()["choices"][0]["message"]["content"] == "remote answer"
    assert len(requests) == 2
    await proxy.update_alias("public-alias", "renamed", [], {"connection_id": connection["id"], "model": "actual-model"})
    assert proxy.route_manager.config.load()["remote_aliases"].keys() == {"renamed"}
    await proxy.update_alias("renamed", "local-empty", [])
    assert proxy.route_manager.config.load()["remote_aliases"] == {}


class Stream(httpx.AsyncByteStream):
    async def __aiter__(self):
        yield b'data: {"choices":[{"delta":{"content":"hello"}}]}\n\n'
        yield b'data: {"choices":[],"usage":{"prompt_tokens":2,"completion_tokens":1}}\n\n'
        yield b'data: [DONE]\n\n'


@pytest.mark.asyncio
async def test_remote_sse_uses_shared_stream_handler(tmp_path):
    def upstream(request):
        assert request.headers["authorization"] == "Bearer upstream-secret"
        body = json.loads(request.content)
        assert body["model"] == "actual-model"
        assert body["stream_options"]["include_usage"] is True
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=Stream())
    proxy = service(tmp_path, upstream)
    connection = proxy.connections().save(None, "Remote", "http://remote.test:9000", "upstream-secret")
    await proxy.create_alias("stream-alias", [], {"connection_id": connection["id"], "model": "actual-model"})
    response = await proxy.forward(route="/v1/chat/completions", payload={"model": "stream-alias", "messages": [], "stream": True}, headers={"authorization": "Bearer caller-secret"}, request_id="test", force_usage=True)
    body = b"".join([chunk async for chunk in response.body_iterator])
    assert b"hello" in body and b"[DONE]" in body


@pytest.mark.asyncio
async def test_connection_api_masks_keeps_and_clears_key_and_blocks_referenced_delete(tmp_path):
    proxy = service(tmp_path, lambda request: httpx.Response(200, json={"data": []}))
    app = FastAPI()
    app.include_router(connections_router)
    app.include_router(routes_router)
    app.dependency_overrides[get_proxy_service] = lambda: proxy
    app.add_exception_handler(AppError, app_error_handler)
    @app.middleware("http")
    async def request_id(request, call_next):
        request.state.request_id = "test"
        return await call_next(request)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://deck.test") as client:
        root = "/api/v1/proxy/openai/connections"
        response = await client.post(root, json={"name": "Remote", "base_url": "https://remote.test/v1", "api_key": "stored-secret"})
        assert response.status_code == 201
        item = response.json()["data"]
        assert item["has_api_key"] and "stored-secret" not in response.text
        assert "stored-secret" not in (await client.get(root)).text
        url = root + "/" + item["id"]
        body = {"name": "Renamed", "base_url": "https://remote.test/v1"}
        assert (await client.patch(url, json=body)).status_code == 200
        assert proxy.connections().get(item["id"])["api_key"] == "stored-secret"
        assert (await client.patch(url, json={**body, "api_key": ""})).status_code == 200
        assert not proxy.connections().get(item["id"])["api_key"]
        alias_root = "/api/v1/proxy/routes/aliases"
        response = await client.post(alias_root, json={"name": "remote", "candidates": [{"connection_id": item["id"], "model": "actual-model"}]})
        assert response.status_code == 201
        assert (await client.delete(url)).status_code == 409
        await client.delete(alias_root + "/remote")
        assert (await client.delete(url)).status_code == 200


@pytest.mark.parametrize("url", ["file:///etc/passwd", "http://user:password@remote.test/v1", "http://remote.test/v1?key=secret", "http://[broken"])
def test_invalid_connection_urls_are_rejected(tmp_path: Path, url):
    with pytest.raises(AppError) as error:
        OpenAIConnections(tmp_path).save(None, "Remote", url, "secret")
    assert error.value.status_code == 422


@pytest.mark.asyncio
async def test_remote_upstream_errors_are_preserved(tmp_path):
    proxy = service(tmp_path, lambda request: httpx.Response(401, json={"error": {"message": "Invalid API key"}}))
    connection = proxy.connections().save(None, "Remote", "https://remote.test/v1", "bad-key")
    await proxy.create_alias("remote", [], {"connection_id": connection["id"], "model": "model"})
    response = await proxy.forward(route="/v1/chat/completions", payload={"model": "remote", "messages": []}, headers={}, request_id="test")
    assert response.status_code == 401
    assert response.json()["error"]["message"] == "Invalid API key"


@pytest.mark.asyncio
@pytest.mark.parametrize("remote_first", [True, False])
async def test_mixed_candidates_keep_order_and_fail_over_without_crossing_keys(tmp_path, remote_first):
    calls = []
    def upstream(request):
        remote = request.url.host == "remote.test"
        if request.method == "GET":
            return httpx.Response(200, json={"data": [{"id": "actual-remote" if remote else "actual-local"}]})
        calls.append("remote" if remote else "local")
        assert request.headers.get("authorization") == ("Bearer connection-key" if remote else None)
        body = json.loads(request.content)
        assert body["model"] == ("actual-remote" if remote else "actual-local")
        if len(calls) == 1:
            raise httpx.ConnectError("target down", request=request)
        return httpx.Response(200, json={"model": body["model"]})
    journal = JournalStub()
    proxy = ProxyService(ProcessManagerStub([instance("first", 8000)]), journal=journal,
        route_manager=RouteManager(tmp_path, ProfileRepositoryStub([profile("first")])),
        client_factory=lambda: httpx.AsyncClient(transport=httpx.MockTransport(upstream)))
    connection = proxy.connections().save(None, "Remote", "http://remote.test:9000/v1", "connection-key")
    remote = {"connection_id": connection["id"], "model": "actual-remote"}
    local = {"profile_id": "first"}
    targets = [remote, local] if remote_first else [local, remote]
    created = await proxy.create_alias("mixed", [], candidates=targets)
    assert len(created["candidates"]) == 2
    assert created["candidate_ids"] == [proxy.route_manager.candidate_id(target) for target in targets]
    response = await proxy.forward(route="/v1/chat/completions", payload={"model": "mixed", "messages": []}, headers={"authorization": "Bearer caller-key"}, request_id="mixed-test")
    assert response.status_code == 200
    assert calls == (["remote", "local"] if remote_first else ["local", "remote"])
    if not remote_first:
        assert journal.records[-1]["pid"] is None
        assert journal.records[-1]["profile_name"] is None
    reordered = await proxy.update_route_order("mixed", [], list(reversed(created["candidate_ids"])))
    assert reordered["candidate_ids"] == list(reversed(created["candidate_ids"]))
    reloaded = RouteManager(tmp_path, ProfileRepositoryStub([profile("first")]))
    assert reloaded.config.load()["alias_candidates"]["mixed"] == list(reversed(targets))
    cleared = await proxy.update_route_order("mixed", [], [])
    assert cleared["candidates"] == []


@pytest.mark.asyncio
async def test_mixed_candidates_do_not_fail_over_after_an_upstream_response(tmp_path):
    calls = []
    def upstream(request):
        calls.append(request.url.host)
        return httpx.Response(401, json={"error": {"message": "Bad provider key"}})
    proxy = service(tmp_path, upstream)
    first = proxy.connections().save(None, "First", "http://first.test/v1", "key-1")
    second = proxy.connections().save(None, "Second", "http://second.test/v1", "key-2")
    await proxy.create_alias("mixed", [], candidates=[{"connection_id": item["id"], "model": "model"} for item in (first, second)])
    response = await proxy.forward(route="/v1/chat/completions", payload={"model": "mixed", "messages": []}, headers={}, request_id="no-replay")
    assert response.status_code == 401
    assert calls == ["first.test"]
