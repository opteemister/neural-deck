from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest
from app.api.dependencies import get_proxy_service
from app.process.registry import ModelRegistry
from app.proxy.operations import public_types, supports, upstream_url
from app.proxy.router import internal_router, router, v2_router
from app.proxy.service import ProxyService
from app.schemas.instances import Instance, InstanceList
from fastapi import FastAPI
from fastapi.testclient import TestClient


def _instance(
    *,
    model_name: str,
    capability: str,
    protocol: str,
    base_url: str,
) -> Instance:
    return Instance(
        id=f"managed/{model_name}",
        source="managed",
        profile_name=model_name,
        engine_id="provider-test",
        pid=100,
        process_start_time_ns=1,
        identity_verified=True,
        can_stop=True,
        port=8000,
        model_name=model_name,
        types=[capability],
        api_protocol=protocol,
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


def _app(proxy: object) -> FastAPI:
    app = FastAPI()

    @app.middleware("http")
    async def request_id(request, call_next):
        request.state.request_id = "req_provider_test"
        return await call_next(request)

    app.include_router(router)
    app.include_router(v2_router)
    app.include_router(internal_router)
    app.dependency_overrides[get_proxy_service] = lambda: proxy
    return app


class _Proxy:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def forward(self, **kwargs: object) -> httpx.Response:
        self.calls.append(kwargs)
        return httpx.Response(200, json={"ok": True})

    async def models(self) -> dict[str, object]:
        return {
            "object": "list",
            "data": [
                {
                    "id": "text-model",
                    "object": "model",
                    "created": 1,
                    "owned_by": "neural-deck",
                    "types": ["text"],
                },
                {
                    "id": "ocr-model",
                    "object": "model",
                    "created": 1,
                    "owned_by": "neural-deck",
                    "types": ["ocr"],
                },
            ],
        }


def test_provider_operations_and_v2_upstream_path() -> None:
    ocr = _instance(
        model_name="ocr", capability="ocr", protocol="openai", base_url="http://ocr/v1"
    )
    embed = _instance(
        model_name="embed", capability="embeddings", protocol="openai", base_url="http://embed/v1"
    )
    systemone = _instance(
        model_name="decision", capability="decision", protocol="jev", base_url="http://jev"
    )
    assert supports(ocr, "/v1/ocr")
    assert supports(systemone, "/v1/systemone")
    assert supports(embed, "/v2/embed")
    assert "ocr" in public_types(ocr)
    assert upstream_url(embed, "/v2/embed") == "http://embed/v2/embed"
    assert upstream_url(systemone, "/v1/systemone") == "http://jev/v1/systemone"


@pytest.mark.asyncio
async def test_systemone_is_forwarded_natively_without_public_model() -> None:
    instance = _instance(
        model_name="decision", capability="decision", protocol="jev", base_url="http://jev"
    )
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"answers": {"q": {"type": "noul", "noul": 0.7}}})

    service = ProxyService(
        _ProcessManager(instance),
        client_factory=lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    result = await service.forward(
        route="/v1/systemone",
        payload={"model": "decision", "state": "hello", "questions": {"q": {"type": "noul"}}},
        headers={},
        request_id="systemone-test",
    )
    assert result.status_code == 200
    assert requests[0].url.path == "/v1/systemone"
    assert requests[0].content == b'{"state":"hello","questions":{"q":{"type":"noul"}}}'


def test_provider_routes_forward_json_and_multipart() -> None:
    proxy = _Proxy()
    with TestClient(_app(proxy)) as client:
        assert client.post(
            "/v1/systemone",
            json={"model": "decision", "state": "s", "questions": {"q": {}}},
        ).status_code == 200
        assert client.post(
            "/v1/ocr",
            json={
                "model": "ocr",
                "document": {
                    "type": "image_url",
                    "image_url": "data:image/png;base64,AA==",
                },
            },
        ).status_code == 200
        assert client.post(
            "/v2/embed",
            json={"model": "embed", "texts": ["hello"], "input_type": "search_query"},
        ).status_code == 200
        assert client.post(
            "/v1/audio/translations",
            files={"file": ("sample.wav", b"RIFF", "audio/wav")},
            data={"model": "asr", "response_format": "json"},
        ).status_code == 200

    assert [call["route"] for call in proxy.calls] == [
        "/v1/systemone",
        "/v1/ocr",
        "/v2/embed",
        "/v1/audio/translations",
    ]
    assert proxy.calls[-1]["multipart_data"] == {
        "model": "asr",
        "response_format": "json",
    }


def test_internal_models_default_to_text_and_can_include_all_types() -> None:
    proxy = _Proxy()
    with TestClient(_app(proxy)) as client:
        assert [item["id"] for item in client.get("/api/proxy/models").json()["data"]] == [
            "text-model"
        ]
        assert [
            item["id"]
            for item in client.get("/api/proxy/models?all_types=true").json()["data"]
        ] == ["text-model", "ocr-model"]
        assert client.get("/v1/models/ocr-model").json()["id"] == "ocr-model"


def test_provider_openapi_documents_native_responses_and_request_examples() -> None:
    app = FastAPI()
    app.include_router(router)
    app.include_router(v2_router)
    document = app.openapi()

    chat = document["paths"]["/v1/chat/completions"]["post"]
    assert chat["responses"]["200"]["content"]["application/json"]["schema"]["$ref"].endswith(
        "/ChatCompletionResponse"
    )
    assert "text/event-stream" in chat["responses"]["200"]["content"]
    assert document["components"]["schemas"]["ChatCompletionsRequest"]["examples"][0]["messages"]

    responses = document["paths"]["/v1/responses"]["post"]["responses"]
    assert "text/event-stream" in responses["200"]["content"]
    assert "400" in responses and "502" in responses

    embedding = document["paths"]["/v1/embeddings"]["post"]
    assert embedding["responses"]["200"]["content"]["application/json"]["schema"]["$ref"].endswith(
        "/EmbeddingsResponse"
    )
    assert document["components"]["schemas"]["OpenAIEmbeddingsRequest"]["examples"][0]["input"]
    media_embedding = document["paths"]["/v1/embeddings/media"]["post"]["responses"]
    assert "400" in media_embedding and "502" in media_embedding

    speech = document["paths"]["/v1/audio/speech"]["post"]["responses"]["200"]["content"]
    assert "application/json" not in speech
    assert speech["audio/mpeg"]["schema"] == {"type": "string", "format": "binary"}

    transcription = document["paths"]["/v1/audio/transcriptions"]["post"]
    assert "text/plain" in transcription["responses"]["200"]["content"]
    transcription_schema = transcription["requestBody"]["content"]["multipart/form-data"]["schema"]
    assert transcription_schema["properties"]["model"]["example"] == "whisper-model"

    for path, schema_name in (
        ("/v1/ocr", "OCRResponse"),
        ("/v1/systemone", "SystemOneResponse"),
        ("/v2/embed", "CohereEmbedResponse"),
    ):
        operation = document["paths"][path]["post"]
        response_schema = operation["responses"]["200"]["content"]["application/json"]["schema"]
        error_schema = operation["responses"]["400"]["content"]["application/json"]["schema"]
        assert response_schema["$ref"].endswith(f"/{schema_name}")
        assert error_schema["$ref"].endswith("/ProviderErrorResponse")
