"""OpenAI-compatible forwarding service.

The proxy deliberately knows only the process manager and its model registry.
It does not duplicate engine lifecycle logic or profile parsing.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import AsyncIterator, Callable, Mapping
from datetime import UTC, datetime
from typing import Any, cast

import httpx

from ..errors import AppError
from ..process.manager import ProcessManager
from ..token_stats import RequestJournalService

_HOP_BY_HOP = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
    "content-length",
    "host",
}
_SSE_USAGE_BUFFER_LIMIT = 2 * 1024 * 1024
logger = logging.getLogger(__name__)


class ProxyService:
    def __init__(
        self,
        process_manager: ProcessManager,
        *,
        journal: RequestJournalService | None = None,
        client_factory: Callable[[], httpx.AsyncClient] | None = None,
        timeout: float = 120.0,
    ) -> None:
        self.process_manager = process_manager
        self.journal = journal
        self.client_factory = client_factory
        self.timeout = timeout

    async def close(self) -> None:
        """Release proxy-owned resources.

        Version Zero creates one short-lived HTTP client per request, closing
        it on every response path.  The hook keeps application shutdown
        composable for a future pooled client without owning injected clients.
        """

        return None

    async def models(self) -> dict[str, Any]:
        await self.process_manager.refresh()
        result = []
        for registered in sorted(
            self.process_manager.model_registry().list(), key=lambda item: item.id
        ):
            result.append(
                {
                    "id": registered.id,
                    "object": "model",
                    "created": int(registered.observed_at.timestamp()),
                    "owned_by": "llm-studio",
                }
            )
        return {"object": "list", "data": result}

    async def forward(
        self,
        *,
        route: str,
        payload: Mapping[str, Any],
        headers: Mapping[str, str],
        request_id: str,
    ) -> httpx.Response | Any:
        started = time.perf_counter()
        model_name = payload.get("model")
        if not isinstance(model_name, str) or not model_name:
            await self._journal_without_target(
                request_id=request_id,
                route=route,
                model_name="<missing>",
                status_code=400,
                started=started,
                error_code="invalid_model",
            )
            raise AppError("invalid_model", "Поле model обязательно", status_code=400)
        await self.process_manager.refresh()
        registered = self.process_manager.model_registry().get(model_name)
        if registered is None:
            await self._journal_without_target(
                request_id=request_id,
                route=route,
                model_name=model_name,
                status_code=404,
                started=started,
                error_code="model_not_found",
            )
            raise AppError(
                "model_not_found",
                "Модель не зарегистрирована активным экземпляром",
                status_code=404,
                details={"model": model_name},
            )
        try:
            instance = await self.process_manager.get(registered.instance_id)
        except AppError as exc:
            await self._journal(
                request_id=request_id,
                route=route,
                model_name=model_name,
                registered=registered,
                status_code=exc.status_code,
                started=started,
                input_tokens=None,
                output_tokens=None,
                stream=bool(payload.get("stream")) and route == "/v1/chat/completions",
                error_code=exc.code,
            )
            raise
        if instance.port is None:
            await self._journal(
                request_id=request_id,
                route=route,
                model_name=model_name,
                registered=registered,
                status_code=503,
                started=started,
                input_tokens=None,
                output_tokens=None,
                stream=False,
                error_code="upstream_unavailable",
            )
            raise AppError(
                "upstream_unavailable",
                "У экземпляра отсутствует подтверждённый API-порт",
                status_code=503,
                retryable=True,
            )
        upstream_model = registered.upstream_model or model_name
        upstream_payload = dict(payload)
        upstream_payload["model"] = upstream_model
        target = f"http://127.0.0.1:{instance.port}{route}"
        outgoing_headers = {
            key: value for key, value in headers.items() if key.lower() not in _HOP_BY_HOP
        }
        stream = bool(upstream_payload.get("stream")) and route == "/v1/chat/completions"
        client = (
            self.client_factory()
            if self.client_factory is not None
            else httpx.AsyncClient(timeout=self.timeout)
        )
        if stream:
            return await self._forward_stream(
                client,
                target,
                upstream_payload,
                outgoing_headers,
                route,
                model_name,
                registered,
                request_id,
                started,
            )
        try:
            response = await client.request(
                "POST",
                target,
                json=upstream_payload,
                headers=outgoing_headers,
            )
        except (httpx.TimeoutException, httpx.NetworkError, httpx.RemoteProtocolError) as exc:
            await client.aclose()
            await self._journal(
                request_id=request_id,
                route=route,
                model_name=model_name,
                registered=registered,
                status_code=502,
                started=started,
                input_tokens=None,
                output_tokens=None,
                stream=False,
                error_code="upstream_unavailable",
            )
            raise AppError(
                "upstream_unavailable",
                "Экземпляр инференса недоступен",
                status_code=502,
                details={"reason": str(exc)[:200]},
                retryable=True,
            ) from exc
        try:
            body = await response.aread()
            input_tokens, output_tokens = _extract_usage(body, response.headers.get("content-type"))
            await self._journal(
                request_id=request_id,
                route=route,
                model_name=model_name,
                registered=registered,
                status_code=response.status_code,
                started=started,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                stream=False,
                error_code=None if response.status_code < 400 else "upstream_error",
            )
            return _passthrough_response(response.status_code, response.headers, body)
        finally:
            await response.aclose()
            await client.aclose()

    async def _forward_stream(
        self,
        client: httpx.AsyncClient,
        target: str,
        payload: Mapping[str, Any],
        headers: Mapping[str, str],
        route: str,
        model_name: str,
        registered: Any,
        request_id: str,
        started: float,
    ) -> Any:
        context = client.stream("POST", target, json=payload, headers=headers)
        try:
            response = await context.__aenter__()
        except (httpx.TimeoutException, httpx.NetworkError, httpx.RemoteProtocolError) as exc:
            await client.aclose()
            await self._journal(
                request_id=request_id,
                route=route,
                model_name=model_name,
                registered=registered,
                status_code=502,
                started=started,
                input_tokens=None,
                output_tokens=None,
                stream=True,
                error_code="upstream_unavailable",
            )
            raise AppError(
                "upstream_unavailable",
                "Экземпляр инференса недоступен",
                status_code=502,
                details={"reason": str(exc)[:200]},
                retryable=True,
            ) from exc
        if response.status_code >= 400:
            try:
                error_body = await response.aread()
                return_response = _passthrough_response(
                    response.status_code, response.headers, error_body
                )
                await self._journal(
                    request_id=request_id,
                    route=route,
                    model_name=model_name,
                    registered=registered,
                    status_code=response.status_code,
                    started=started,
                    input_tokens=None,
                    output_tokens=None,
                    stream=True,
                    error_code="upstream_error",
                )
                return return_response
            finally:
                await context.__aexit__(None, None, None)
                await client.aclose()

        async def stream_body() -> AsyncIterator[bytes]:
            usage_buffer = bytearray()
            error_code: str | None = None
            try:
                # MockTransport and a few compatible clients return an
                # already-closed in-memory response even when ``stream=True``.
                # Preserve that body while retaining true chunked iteration for
                # live upstream responses.
                if response.is_closed:
                    chunk = response.content
                    if chunk:
                        usage_buffer.extend(chunk)
                        yield chunk
                else:
                    async for chunk in response.aiter_raw():
                        usage_buffer.extend(chunk)
                        if len(usage_buffer) > _SSE_USAGE_BUFFER_LIMIT:
                            del usage_buffer[: len(usage_buffer) - _SSE_USAGE_BUFFER_LIMIT]
                        yield chunk
            except (httpx.TimeoutException, httpx.NetworkError, httpx.RemoteProtocolError):
                error_code = "upstream_unavailable"
                raise
            except Exception:
                error_code = "upstream_error"
                raise
            finally:
                input_tokens, output_tokens = _extract_sse_usage(bytes(usage_buffer))
                await self._journal(
                    request_id=request_id,
                    route=route,
                    model_name=model_name,
                    registered=registered,
                    status_code=response.status_code,
                    started=started,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    stream=True,
                    error_code=error_code,
                )
                await context.__aexit__(None, None, None)
                await client.aclose()

        return _streaming_response(response.status_code, response.headers, stream_body())

    async def _journal(
        self,
        *,
        request_id: str,
        route: str,
        model_name: str,
        registered: Any,
        status_code: int,
        started: float,
        input_tokens: int | None,
        output_tokens: int | None,
        stream: bool,
        error_code: str | None,
    ) -> None:
        if self.journal is None:
            return
        try:
            await self.journal.record_safely(
                request_id=request_id,
                route=route,
                model_name=model_name,
                profile_name=registered.profile_name,
                instance_id=registered.instance_id,
                engine_id=registered.engine_id,
                status_code=status_code,
                latency_ms=(time.perf_counter() - started) * 1000,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                streaming=stream,
                error_code=error_code,
                created_at=datetime.now(UTC),
            )
        except Exception:  # noqa: BLE001 - journal is non-critical
            logger.exception("Request journal write failed on proxy path")

    async def _journal_without_target(
        self,
        *,
        request_id: str,
        route: str,
        model_name: str,
        status_code: int,
        started: float,
        error_code: str,
    ) -> None:
        if self.journal is None:
            return
        try:
            await self.journal.record_safely(
                request_id=request_id,
                route=route,
                model_name=model_name,
                profile_name=None,
                instance_id=None,
                engine_id=None,
                status_code=status_code,
                latency_ms=(time.perf_counter() - started) * 1000,
                input_tokens=None,
                output_tokens=None,
                streaming=False,
                error_code=error_code,
                created_at=datetime.now(UTC),
            )
        except Exception:  # noqa: BLE001 - journal is non-critical
            logger.exception("Request journal write failed on proxy path")


def _forward_headers(headers: Mapping[str, str]) -> dict[str, str]:
    return {key: value for key, value in headers.items() if key.lower() not in _HOP_BY_HOP}


def _passthrough_response(
    status_code: int, headers: Mapping[str, str], body: bytes
) -> httpx.Response:
    return httpx.Response(status_code, headers=_forward_headers(headers), content=body)


def _streaming_response(
    status_code: int, headers: Mapping[str, str], body: AsyncIterator[bytes]
) -> Any:
    from starlette.responses import StreamingResponse

    return StreamingResponse(body, status_code=status_code, headers=_forward_headers(headers))


def _extract_usage(body: bytes, content_type: str | None) -> tuple[int | None, int | None]:
    if content_type and "json" not in content_type.lower():
        return None, None
    try:
        payload = json.loads(body)
    except (TypeError, ValueError):
        return None, None
    return _usage_values(payload.get("usage") if isinstance(payload, dict) else None)


def _extract_sse_usage(body: bytes) -> tuple[int | None, int | None]:
    input_tokens: int | None = None
    output_tokens: int | None = None
    for line in body.splitlines():
        if not line.startswith(b"data:"):
            continue
        raw = line[5:].strip()
        if not raw or raw == b"[DONE]":
            continue
        try:
            payload = json.loads(raw)
        except (TypeError, ValueError):
            continue
        values = _usage_values(payload.get("usage") if isinstance(payload, dict) else None)
        if values != (None, None):
            input_tokens, output_tokens = values
    return input_tokens, output_tokens


def _usage_values(usage: Any) -> tuple[int | None, int | None]:
    if not isinstance(usage, dict):
        return None, None
    prompt = _nonnegative_int(usage.get("prompt_tokens", usage.get("input_tokens")))
    completion = _nonnegative_int(usage.get("completion_tokens", usage.get("output_tokens")))
    if completion is None and prompt is not None:
        total = _nonnegative_int(usage.get("total_tokens"))
        if total is not None and total >= prompt:
            completion = total - prompt
    return prompt, completion


def _nonnegative_int(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return cast(int, value)


__all__ = ["ProxyService"]
