from types import SimpleNamespace

import httpx
from app.api.dependencies import get_proxy_service
from app.config import Settings
from app.main import create_app, create_proxy_app
from fastapi.testclient import TestClient
from starlette.responses import StreamingResponse


class Proxy:
    def __init__(self):
        self.calls = []

    async def models(self):
        self.calls.append("models")
        return {"object": "list", "data": [{
            "id": "public-model", "object": "model", "created": 0,
            "owned_by": "llm-studio", "engine_id": "vllm",
        }]}

    async def forward(self, **kwargs):
        self.calls.append(kwargs)
        if kwargs["payload"].get("stream"):
            async def chunks():
                yield b'data: {"choices":[{"delta":{"content":"hello"}}]}\n\n'
                yield b'data: [DONE]\n\n'
            return StreamingResponse(chunks(), media_type="text/event-stream")
        return httpx.Response(200, json={"choices": [{"message": {"content": "hello"}}]})


def test_internal_and_external_routes_share_proxy_and_preserve_wire_responses(tmp_path):
    settings = Settings(data_root=tmp_path, host="127.0.0.1", port=3000, openai_port=8080)
    proxy = Proxy()
    internal = create_app(settings)
    internal.dependency_overrides[get_proxy_service] = lambda: proxy
    external = create_proxy_app(SimpleNamespace(settings=settings, proxy_service=proxy))
    external.dependency_overrides[get_proxy_service] = lambda: proxy
    internal_client = TestClient(internal, base_url="https://ai.laguta.org")
    external_client = TestClient(external, base_url="http://127.0.0.1:8080")
    inside = internal_client.get("/api/proxy/models")
    outside = external_client.get("/v1/models")
    assert inside.status_code == outside.status_code == 200
    assert inside.json() == outside.json()
    assert "request_id" not in inside.json()
    assert proxy.calls == ["models", "models"]
    for client, path in [
        (internal_client, "/api/proxy/chat/completions"),
        (external_client, "/v1/chat/completions"),
    ]:
        body = {"model": "public-model", "messages": [{"role": "user", "content": "hi"}]}
        response = client.post(path, json=body, headers={"Authorization": "Bearer P-Work"})
        assert response.status_code == 200
        assert response.json()["choices"][0]["message"]["content"] == "hello"
        assert proxy.calls[-1]["route"] == "/v1/chat/completions"
        assert proxy.calls[-1]["payload"] == body
        assert proxy.calls[-1]["headers"]["authorization"] == (
            "Bearer l-chat" if path.startswith("/api/proxy/") else "Bearer P-Work"
        )
        assert proxy.calls[-1]["force_usage"] is path.startswith("/api/proxy/")
        response = client.post(path, json={**body, "stream": True})
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        assert '"content":"hello"' in response.text
        assert response.text.endswith("data: [DONE]\n\n")
        if path.startswith("/api/proxy/"):
            assert proxy.calls[-1]["headers"]["authorization"] == "Bearer l-chat"
        assert proxy.calls[-1]["force_usage"] is path.startswith("/api/proxy/")
    assert internal_client.get("/v1/models").status_code == 404
    assert external_client.get("/api/proxy/models").status_code == 404
