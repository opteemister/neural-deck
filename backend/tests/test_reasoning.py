import json
from pathlib import Path

import httpx
import pytest

from app.errors import AppError
from app.reasoning import adapt_reasoning_request, reasoning_capabilities, runtime_reasoning_metadata
from app.proxy.routes import RouteManager
from app.proxy.service import ProxyService
from tests.test_proxy_routes import ProcessManagerStub, ProfileRepositoryStub, instance, profile

TEMPLATE = """{# A prose comment mentioning high does not make it supported. #}
{%- if enable_thinking is undefined or enable_thinking is true %}
{%- set resolved_reasoning_effort = reasoning_effort|default('xhigh') %}
{%- if resolved_reasoning_effort not in ('xhigh', 'medium', 'low') %}
{{- raise_exception('Unexpected reasoning effort') }}
{%- endif %}{%- endif %}
"""
CAPS = {"efforts": ["low", "medium", "xhigh"], "effort_parameter": "chat_template_kwargs",
        "can_disable_thinking": True, "default_effort": "xhigh"}


def checkpoint(tmp_path: Path):
    model = tmp_path / "model"
    model.mkdir()
    (model / "tokenizer_config.json").write_text(json.dumps({"chat_template": TEMPLATE}))
    return model


def test_running_template_reports_actual_levels_default_and_disable(tmp_path):
    model = checkpoint(tmp_path)
    result = runtime_reasoning_metadata(f"vllm serve {model}", None)
    assert result == {"reasoning_capabilities": CAPS}
    assert reasoning_capabilities(result) == result
    assert runtime_reasoning_metadata(f"python -m sglang.launch_server --model-path={model}", None) == result


def test_template_override_is_authoritative_and_cache_tracks_file_changes(tmp_path):
    model = checkpoint(tmp_path)
    override = tmp_path / "custom.jinja"
    override.write_text("{% if reasoning_effort not in ['low', 'high'] %}{{ raise_exception('bad') }}{% endif %}")
    command = f"vllm serve {model} --chat-template custom.jinja"
    assert runtime_reasoning_metadata(command, tmp_path)["reasoning_capabilities"]["efforts"] == ["low", "high"]
    override.write_text("{{ messages }}")
    assert runtime_reasoning_metadata(command, tmp_path) == {}
    assert runtime_reasoning_metadata(f"vllm serve {model} --chat-template missing.jinja", tmp_path) == {}


def test_prose_model_names_and_positive_branches_do_not_invent_levels(tmp_path):
    assert reasoning_capabilities({"id": "qwen-reasoning", "chat_template": "low medium high"}) == {}
    assert reasoning_capabilities({"chat_template": "{% if reasoning_effort in ['low'] %}a{% endif %}"}) == {}
    assert reasoning_capabilities({"reasoning_capabilities": {"effort_parameter": [], "efforts": ["high"]}}) == {}
    assert runtime_reasoning_metadata("vllm serve $UNKNOWN", tmp_path) == {}


@pytest.mark.parametrize("flag", ["--default-chat-template-kwargs", "--chat-template-kwargs"])
def test_launch_template_defaults_override_checkpoint_default(tmp_path, flag):
    model = checkpoint(tmp_path)
    result = runtime_reasoning_metadata(f"vllm serve {model} {flag} '{{\"enable_thinking\":false}}'", None)
    assert result["reasoning_capabilities"]["default_effort"] == "none"


@pytest.mark.parametrize("route,body", [
    ("/v1/chat/completions", {"reasoning_effort": "low"}),
    ("/v1/responses", {"reasoning": {"effort": "low", "summary": "auto"}}),
    ("/v1/chat/completions", {"chat_template_kwargs": {"reasoning_effort": "low"}}),
])
def test_standard_and_native_parameters_map_to_the_same_template_arguments(route, body):
    original = json.loads(json.dumps(body))
    result = adapt_reasoning_request(body, route, {"reasoning_capabilities": CAPS})
    assert result["chat_template_kwargs"] == {"reasoning_effort": "low", "enable_thinking": True}
    assert "reasoning_effort" not in result
    if route == "/v1/responses":
        assert result["reasoning"] == {"summary": "auto"}
    assert body == original


def test_disable_auto_unknown_and_native_standard_provider():
    assert adapt_reasoning_request({}, "/v1/chat/completions", {"reasoning_capabilities": CAPS}) == {}
    disabled = adapt_reasoning_request({"reasoning_effort": "none"}, "/v1/chat/completions", {"reasoning_capabilities": CAPS})
    assert disabled == {"chat_template_kwargs": {"enable_thinking": False}}
    body = {"reasoning_effort": "high"}
    assert adapt_reasoning_request(body, "/v1/chat/completions", {}) == body
    standard = {"supported_reasoning_efforts": ["low", "high", "max"]}
    assert adapt_reasoning_request({"reasoning": {"effort": "max", "summary": "auto"}}, "/v1/responses", standard) == {"reasoning": {"effort": "max", "summary": "auto"}}


@pytest.mark.parametrize("route,body", [
    ("/v1/chat/completions", {"reasoning_effort": "low"}),
    ("/v1/chat/completions", {"reasoning_effort": "none"}),
    ("/v1/responses", {"reasoning": {"effort": "xhigh", "summary": "auto"}}),
])
def test_neural_deck_to_neural_deck_does_not_change_the_selected_effort(route, body):
    metadata = {"reasoning_capabilities": CAPS}
    first_hop = adapt_reasoning_request(body, route, metadata)
    second_hop = adapt_reasoning_request(first_hop, route, metadata)
    assert second_hop == first_hop


@pytest.mark.parametrize("body,code", [
    ({"reasoning_effort": "high"}, "unsupported_reasoning_effort"),
    ({"reasoning_effort": "low", "chat_template_kwargs": {"reasoning_effort": "medium"}}, "conflicting_reasoning"),
    ({"reasoning_effort": "low", "chat_template_kwargs": {"enable_thinking": False}}, "conflicting_reasoning"),
    ({"reasoning_effort": "none", "chat_template_kwargs": {"enable_thinking": True}}, "conflicting_reasoning"),
])
def test_invalid_levels_and_conflicting_controls_fail_explicitly(body, code):
    with pytest.raises(AppError) as error:
        adapt_reasoning_request(body, "/v1/chat/completions", {"reasoning_capabilities": CAPS})
    assert error.value.code == code


@pytest.mark.asyncio
async def test_local_catalogue_and_calls_share_runtime_capabilities(tmp_path):
    model = checkpoint(tmp_path)
    p = profile("local", "public-model")
    manager = ProcessManagerStub([instance("local", 8000)])
    posted = []
    def upstream(request):
        if request.method == "GET":
            return httpx.Response(200, json={"data": [{"id": "actual-model"}]})
        body = json.loads(request.content)
        posted.append(body)
        assert body["model"] == "actual-model"
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})
    proxy = ProxyService(manager, route_manager=RouteManager(tmp_path, ProfileRepositoryStub([p])),
                         reasoning_metadata=lambda _: runtime_reasoning_metadata(f"vllm serve {model}", None),
                         client_factory=lambda: httpx.AsyncClient(transport=httpx.MockTransport(upstream)))
    try:
        advertised = next(item for item in (await proxy.models())["data"] if item["id"] == "public-model")
        assert advertised["reasoning_capabilities"] == CAPS
        await proxy.forward(route="/v1/chat/completions", payload={"model": "public-model", "messages": [], "reasoning_effort": "low"}, headers={}, request_id="r")
        assert posted[-1]["chat_template_kwargs"] == {"reasoning_effort": "low", "enable_thinking": True}
        with pytest.raises(AppError) as error:
            await proxy.forward(route="/v1/chat/completions", payload={"model": "public-model", "messages": [], "reasoning_effort": "high"}, headers={}, request_id="r")
        assert error.value.code == "unsupported_reasoning_effort"
        assert len(posted) == 1
    finally:
        await proxy.close()


@pytest.mark.asyncio
async def test_remote_catalogue_and_responses_preserve_capabilities_without_exposing_credentials(tmp_path):
    posted = []
    def upstream(request):
        assert request.headers["authorization"] == "Bearer upstream-key"
        if request.method == "GET":
            return httpx.Response(200, json={"data": [{"id": "upstream", "reasoning_capabilities": CAPS, "api_key": "never-publish"}]})
        posted.append(json.loads(request.content))
        return httpx.Response(200, json={"ok": True})
    proxy = ProxyService(ProcessManagerStub([]), route_manager=RouteManager(tmp_path, ProfileRepositoryStub([])),
                         client_factory=lambda: httpx.AsyncClient(transport=httpx.MockTransport(upstream)))
    connection = proxy.connections().save(None, "remote", "http://remote.test/v1", "upstream-key")
    await proxy.create_alias("alias", [], {"connection_id": connection["id"], "model": "upstream"})
    try:
        catalogue = await proxy.models()
        advertised = next(item for item in catalogue["data"] if item["id"] == "alias")
        assert advertised["reasoning_capabilities"] == CAPS
        assert "never-publish" not in json.dumps(catalogue)
        await proxy.forward(route="/v1/responses", payload={"model": "alias", "input": "Hello", "reasoning": {"effort": "medium", "summary": "auto"}}, headers={}, request_id="r")
        assert posted[-1]["model"] == "upstream"
        assert posted[-1]["reasoning"] == {"summary": "auto"}
        assert posted[-1]["chat_template_kwargs"] == {"reasoning_effort": "medium", "enable_thinking": True}
    finally:
        await proxy.close()


@pytest.mark.asyncio
async def test_public_models_and_litellm_shaped_requests_use_one_contract(tmp_path):
    from fastapi import FastAPI
    from app.api.dependencies import get_proxy_service
    from app.proxy.router import router

    posted = []
    def upstream(request):
        if request.method == "GET":
            return httpx.Response(200, json={"data": [{"id": "actual", "reasoning_capabilities": CAPS}]})
        posted.append(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    proxy = ProxyService(ProcessManagerStub([instance("local", 8000)]),
                         route_manager=RouteManager(tmp_path, ProfileRepositoryStub([profile("local")])),
                         client_factory=lambda: httpx.AsyncClient(transport=httpx.MockTransport(upstream)))
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_proxy_service] = lambda: proxy
    @app.middleware("http")
    async def request_id(request, call_next):
        request.state.request_id = "reasoning-api-test"
        return await call_next(request)
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
            models = (await client.get("/v1/models")).json()
            assert models["data"][0]["reasoning_capabilities"] == CAPS
            detail = await client.get("/v1/models/public-model")
            assert detail.json()["reasoning_capabilities"] == CAPS
            response = await client.post("/v1/chat/completions", json={"model": "public-model", "messages": [{"role": "user", "content": "Hi"}], "reasoning_effort": "low"})
            assert response.status_code == 200
            assert posted[-1]["chat_template_kwargs"]["reasoning_effort"] == "low"
            response = await client.post("/v1/responses", json={"model": "public-model", "input": "Hi", "reasoning": {"effort": "medium", "summary": "auto"}})
            assert response.status_code == 200
            assert posted[-1]["reasoning"] == {"summary": "auto"}
            response = await client.post("/v1/chat/completions", json={"model": "public-model", "messages": [], "reasoning_effort": "high"})
            assert response.status_code == 400
            assert response.json()["error"]["param"] == "reasoning_effort"
            assert len(posted) == 2
    finally:
        await proxy.close()
