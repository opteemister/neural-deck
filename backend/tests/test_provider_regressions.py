from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import httpx
import pytest
from app.process.registry import ModelRegistry
from app.proxy.media_router import router as media_router
from app.proxy.operations import upstream_url
from app.proxy.service import ProxyService
from app.schemas.instances import Instance, InstanceList
from app.schemas.provider import (
    ChatCompletionsRequest,
    OpenAIEmbeddingsRequest,
    ResponsesRequest,
    SpeechRequest,
)
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel


def _instance(*, model_name: str, capability: str, base_url: str) -> Instance:
    return Instance(
        id=f"managed/{model_name}",
        source="managed",
        profile_name=model_name,
        engine_id="provider-regression-test",
        pid=100,
        process_start_time_ns=1,
        identity_verified=True,
        can_stop=True,
        port=8000,
        model_name=model_name,
        types=[capability],
        api_protocol="openai",
        api_base_url=base_url,
        state="ready",
        observed_at=datetime.now(UTC),
    )


class _ProcessManager:
    def __init__(self, instance: Instance) -> None:
        self.instance = instance
        self.registry = ModelRegistry()
        self.registry.register_instance(instance)

    async def refresh(self, **_: object) -> InstanceList:
        return InstanceList(items=[self.instance])

    async def get(self, instance_id: str) -> Instance:
        assert instance_id == self.instance.id
        return self.instance

    def model_registry(self) -> ModelRegistry:
        return self.registry


class _ResponseStream(httpx.AsyncByteStream):
    def __init__(self, body: bytes) -> None:
        self.body = body

    async def __aiter__(self):
        yield self.body


@pytest.mark.parametrize(
    ("base_url", "expected"),
    [
        ("http://cohere", "http://cohere/v2/embed"),
        ("http://cohere/v2/embed", "http://cohere/v2/embed"),
        ("http://cohere/nested/v2/embed", "http://cohere/nested/v2/embed"),
    ],
)
def test_cohere_endpoint_base_is_not_suffixed_twice(base_url: str, expected: str) -> None:
    instance = _instance(model_name="embed", capability="embeddings", base_url=base_url)
    assert upstream_url(instance, "/v2/embed") == expected


@pytest.mark.parametrize(
    "schema",
    [ChatCompletionsRequest, ResponsesRequest, OpenAIEmbeddingsRequest, SpeechRequest],
)
def test_native_provider_schemas_require_model(schema: type[BaseModel]) -> None:
    assert "model" in schema.model_json_schema().get("required", [])


@pytest.mark.parametrize(
    ("route", "multipart_data", "files", "expected_fields"),
    [
        (
            "/v1/images/edits",
            {"prompt": "remove the label", "size": "1024x1024", "n": "2"},
            {
                "image": ("input.png", b"image-bytes", "image/png"),
                "mask": ("mask.png", b"mask-bytes", "image/png"),
            },
            (b"remove the label", b"1024x1024", b"2", b"image-bytes", b"mask-bytes"),
        ),
        (
            "/v1/images/variations",
            {"n": "2", "output_format": "webp"},
            {"image": ("input.png", b"image-bytes", "image/png")},
            (b"2", b"webp", b"image-bytes"),
        ),
    ],
)
@pytest.mark.asyncio
async def test_image_multipart_forward_preserves_fields_and_files(
    route: str,
    multipart_data: dict[str, str],
    files: dict[str, tuple[str, bytes, str]],
    expected_fields: tuple[bytes, ...],
) -> None:
    instance = _instance(model_name="image-model", capability="image", base_url="http://image")
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            headers={"content-type": "application/json"},
            stream=_ResponseStream(b'{"created":1,"data":[]}'),
        )

    service = ProxyService(
        _ProcessManager(instance),
        client_factory=lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    response = await service.forward(
        route=route,
        payload={"model": "image-model"},
        headers={"content-type": "multipart/form-data; boundary=caller-boundary"},
        request_id="image-multipart-regression",
        multipart_data=multipart_data,
        multipart_files=files,
    )

    body = b"".join([chunk async for chunk in response.body_iterator])
    assert response.status_code == 200
    assert body == b'{"created":1,"data":[]}'
    assert len(requests) == 1
    request_body = requests[0].content
    assert requests[0].headers["content-type"].startswith("multipart/form-data;")
    assert b' name="model"\r\n\r\nimage-model' in request_body
    for expected in expected_fields:
        assert expected in request_body


@pytest.mark.parametrize("field, count", [("image", 1), ("image[]", 1), ("image[]", 3)])
def test_edit_http_route_forwards_all_sources_in_order_and_separate_mask(
    field: str, count: int
) -> None:
    instance = _instance(model_name="image-model", capability="image", base_url="http://image")
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200, headers={"content-type": "application/json"},
            stream=_ResponseStream(b'{"created":1,"data":[]}'),
        )

    service = ProxyService(
        _ProcessManager(instance),
        client_factory=lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    app = FastAPI()
    app.state.runtime = SimpleNamespace(
        proxy_service=service,
        comfy_service=SimpleNamespace(get_settings=lambda: {"models": {}}),
    )

    @app.middleware("http")
    async def request_id(request, call_next):
        request.state.request_id = "edit-array-regression"
        return await call_next(request)

    app.include_router(media_router)
    response = TestClient(app).post(
        "/v1/images/edits",
        data={"model": "image-model", "prompt": "combine", "n": "1"},
        files=[
            (field, (f"source-{i}.png", f"source-{i}".encode(), "image/png"))
            for i in range(count)
        ] + [("mask", ("mask.png", b"mask-bytes", "image/png"))],
    )
    assert response.status_code == 200
    assert len(requests) == 1
    assert str(requests[0].url) == "http://image/v1/images/edits"
    body = requests[0].content
    assert body.count(f'name="{field}";'.encode()) == count
    assert body.count(b'name="mask";') == 1
    positions = [body.index(f"source-{i}".encode()) for i in range(count)]
    assert positions == sorted(positions)
    assert b"mask-bytes" in body
    assert b'name="prompt"\r\n\r\ncombine' in body
    assert b'name="n"\r\n\r\n1' in body
