from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
from app.db import create_engine, create_session_factory
from app.mcp.server import _call_result
from app.metrics.providers import FreeTokenMetricsProvider, VllmMetricsProvider
from app.models import entities
from app.process.registry import ModelRegistry
from app.proxy.service import ProxyService
from app.schemas.instances import Instance
from app.token_stats import RequestJournalService
from sqlalchemy.ext.asyncio import async_sessionmaker


class _Manager:
    def __init__(self, *, public: str = "public", upstream: str = "actual") -> None:
        self.registry = ModelRegistry()
        self.instance = Instance(
            id="managed/profile/10-20",
            source="managed",
            profile_name="profile",
            engine_id="vllm",
            pid=10,
            process_start_time_ns=20,
            port=1919,
            model_name=public,
            state="ready",
            observed_at=datetime.now(UTC),
        )
        self.registry.register_instance(self.instance, upstream_models=[upstream])

    async def refresh(self) -> None:
        return None

    def model_registry(self) -> ModelRegistry:
        return self.registry

    async def get(self, instance_id: str) -> Instance:
        assert instance_id == self.instance.id
        return self.instance


def _client(handler: Any) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


@pytest.mark.version_zero
def test_proxy_substitutes_upstream_model_and_passthroughs_json() -> None:
    # spec: version-zero
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["body"] = request.content
        return httpx.Response(
            201,
            headers={"content-type": "application/json", "x-upstream": "yes"},
            content=b'{"answer":true}',
        )

    async def run() -> None:
        service = ProxyService(_Manager(), client_factory=lambda: _client(handler))
        response = await service.forward(
            route="/v1/chat/completions",
            payload={"model": "public", "messages": [{"role": "user", "content": "x"}]},
            headers={"authorization": "Bearer secret"},
            request_id="req_test",
        )
        assert response.status_code == 201
        assert response.content == b'{"answer":true}'
        assert seen["url"].endswith("/v1/chat/completions")
        assert b'"model":"actual"' in seen["body"]

    asyncio.run(run())


@pytest.mark.version_zero
def test_proxy_forwards_sse_without_buffering() -> None:
    # spec: version-zero
    payload = b'data: {"usage":{"prompt_tokens":2,"completion_tokens":3}}\n\ndata: [DONE]\n\n'

    def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=payload)

    async def run() -> None:
        service = ProxyService(_Manager(), client_factory=lambda: _client(handler))
        response = await service.forward(
            route="/v1/chat/completions",
            payload={"model": "public", "stream": True},
            headers={},
            request_id="req_stream",
        )
        chunks = [chunk async for chunk in response.body_iterator]
        assert b"".join(chunks) == payload

    asyncio.run(run())


@pytest.mark.version_zero
def test_proxy_enables_stream_usage_for_supported_engines() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["payload"] = json.loads(request.content)
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=(
                b'data: {"usage":{"prompt_tokens":2,"completion_tokens":3}}\n\n'
                b"data: [DONE]\n\n"
            ),
        )

    async def run() -> None:
        service = ProxyService(_Manager(), client_factory=lambda: _client(handler))
        response = await service.forward(
            route="/v1/chat/completions",
            payload={
                "model": "public",
                "stream": True,
                "stream_options": {"include_usage": False, "continuous_usage_stats": True},
            },
            headers={},
            request_id="req_stream_usage",
            force_usage=True,
        )
        [chunk async for chunk in response.body_iterator]

    asyncio.run(run())
    assert seen["payload"]["stream_options"] == {
        "include_usage": True,
        "continuous_usage_stats": True,
    }


@pytest.mark.version_zero
def test_token_stats_preserves_unknown_values(tmp_path: Path) -> None:
    # spec: version-zero
    async def run() -> None:
        engine = create_engine(tmp_path / "stats.db")
        factory: async_sessionmaker[Any] = create_session_factory(engine)
        async with engine.begin() as connection:
            await connection.run_sync(entities.Base.metadata.create_all)
        journal = RequestJournalService(session_factory=factory)
        now = datetime.now(UTC)
        await journal.record(
            request_id="known",
            route="/v1/chat/completions",
            model_name="m",
            profile_name="profile",
            instance_id="i",
            engine_id="vllm",
            status_code=200,
            latency_ms=1,
            input_tokens=4,
            output_tokens=None,
            created_at=now,
        )
        await journal.record(
            request_id="unknown",
            route="/v1/chat/completions",
            model_name="m",
            profile_name="profile",
            instance_id="i",
            engine_id="vllm",
            status_code=200,
            latency_ms=1,
            input_tokens=None,
            output_tokens=None,
            created_at=now,
        )
        stats = await journal.stats(profile_name="profile")
        item = stats.items[0]
        assert item.input_tokens.known_total == 4
        assert item.input_tokens.unknown_requests == 1
        assert item.output_tokens.known_total is None
        assert item.output_tokens.unknown_requests == 2
        await engine.dispose()

    asyncio.run(run())


@pytest.mark.version_zero
def test_metrics_provider_reports_unavailable_without_fake_values() -> None:
    # spec: version-zero
    async def run() -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            del request
            return httpx.Response(404, content=b"missing")

        provider = VllmMetricsProvider(client=_client(handler))
        instance = _Manager().instance
        metrics = await provider.collect(instance)
        assert metrics[0].value is None
        assert metrics[0].state == "unavailable"

    asyncio.run(run())


@pytest.mark.version_zero
def test_freetoken_stats_parser_accepts_known_json_and_rejects_unknown_values() -> None:
    async def run() -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.path == "/v1/stats"
            return httpx.Response(
                200,
                json={
                    "throughput": {"decode_tps": 12.5, "prefill_tps": 40},
                    "requests": {"active": 2, "p95_ms": "n/a"},
                    "vram_bytes": 123,
                    "unknown_counter": 999,
                },
            )

        provider = FreeTokenMetricsProvider(client=_client(handler))
        metrics = await provider.collect(
            _Manager().instance.model_copy(update={"engine_id": "freetoken"})
        )
        values = {item.name: item for item in metrics}
        assert values["decode_tps"].value == 12.5
        assert values["prefill_tps"].value == 40
        assert values["vram_bytes"].value == 123
        assert values["p95_ms"].state == "unavailable"
        assert "unknown_counter" not in values
        await provider.client.aclose()  # type: ignore[union-attr]

    asyncio.run(run())


@pytest.mark.version_zero
def test_mcp_envelope_has_matching_structured_and_text_payloads() -> None:
    # spec: version-zero
    envelope = {"data": {"state": "ok"}, "request_id": "req_test"}
    result = _call_result(envelope, is_error=False)
    if isinstance(result, dict):
        structured = result["structuredContent"]
        content = result["content"][0]
        text = content["text"]
    else:
        structured = result.structuredContent
        content = result.content[0]
        text = content.text
    assert structured == envelope
    content_type = content["type"] if isinstance(content, dict) else content.type
    assert content_type == "text"
    assert text == '{"data": {"state": "ok"}, "request_id": "req_test"}'
