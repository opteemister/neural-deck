"""OpenAI-compatible forwarding service.

The proxy deliberately knows only the process manager and its model registry.
It does not duplicate engine lifecycle logic or profile parsing.
"""

from __future__ import annotations

import ast
import asyncio
import json
import logging
import re
import time
from collections.abc import AsyncIterator, Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, cast

import httpx

from ..errors import AppError
from ..process.manager import ProcessManager
from ..schemas.instances import Instance
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


@dataclass(slots=True)
class _ModelsCache:
    identity: tuple[int, int | None, int | None, str | None, str]
    refreshed_at: float
    metadata: dict[str, dict[str, Any]]


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
        self._models_lock = asyncio.Lock()
        self._models_cache: dict[str, _ModelsCache] = {}

    async def close(self) -> None:
        """Release proxy-owned resources.

        Version Zero creates one short-lived HTTP client per request, closing
        it on every response path.  The hook keeps application shutdown
        composable for a future pooled client without owning injected clients.
        """

        return None

    async def models(self) -> dict[str, Any]:
        async with self._models_lock:
            # Observe process identities on every request without probing ready engines.
            observed = await self.process_manager.refresh(probe_models=False)
            instances = {
                item.id: item for item in observed.items
                if item.state not in {"exited", "error", "stopping"}
                and (item.source == "managed" or item.identity_verified)
            }
            logger.info("Обновление списка моделей: активных запусков=%s", len(instances))
            for instance_id in list(self._models_cache):
                if instance_id not in instances:
                    del self._models_cache[instance_id]
            metadata = dict(zip(instances, await asyncio.gather(*(
                self._cached_model_metadata(instance) for instance in instances.values()
            )), strict=True))
            registered_models = sorted(
                self.process_manager.model_registry().list(), key=lambda item: item.id
            )
            result = []
            for registered in registered_models:
                engine_models = metadata.get(registered.instance_id, {})
                if (
                    registered.upstream_model is None
                    or registered.upstream_model not in engine_models
                ):
                    continue
                result.append({
                    "id": registered.id,
                    "object": "model",
                    "created": int(registered.observed_at.timestamp()),
                    "owned_by": "llm-studio",
                    "engine_id": registered.engine_id,
                    **engine_models[registered.upstream_model],
                })
            return {"object": "list", "data": result}

    async def _cached_model_metadata(self, instance: Instance) -> dict[str, dict[str, Any]]:
        identity = (
            instance.pid, instance.process_start_time_ns, instance.port,
            instance.model_name if instance.source == "managed" else None, instance.engine_id,
        )
        cached = self._models_cache.get(instance.id)
        if cached is not None and cached.identity == identity:
            if time.monotonic() - cached.refreshed_at < 600:
                logger.info("Модели из кеша: instance_id=%s", instance.id)
                self.process_manager.model_registry().register_instance(
                    instance, upstream_models=list(cached.metadata)
                )
                return cached.metadata
        self._models_cache.pop(instance.id, None)
        metadata = await self._model_metadata(instance)
        # Only a nonempty, confirmed engine response starts the ten-minute TTL.
        if metadata:
            self._models_cache[instance.id] = _ModelsCache(identity, time.monotonic(), metadata)
        return metadata

    async def _model_metadata(self, instance: Instance) -> dict[str, dict[str, Any]]:
        """Refresh engine model names and their metadata in one HTTP probe."""
        details: dict[str, dict[str, Any]] = {}
        try:
            if instance.port is None:
                logger.warning("Опрос моделей пропущен: нет порта instance_id=%s", instance.id)
                return {}
            client = (
                self.client_factory() if self.client_factory else httpx.AsyncClient(timeout=1.0)
            )
            async with client:
                base = f"http://127.0.0.1:{instance.port}"
                logger.info("Опрос моделей: instance_id=%s url=%s/v1/models", instance.id, base)
                response = await client.get(f"{base}/v1/models", timeout=1.0)
                response.raise_for_status()
                items = response.json().get("data")
                if not isinstance(items, list):
                    raise ValueError("Engine /v1/models data is not a list")
                for model in items:
                    if not isinstance(model, dict):
                        continue
                    name = model.get("id")
                    if not isinstance(name, str) or not name:
                        continue
                    item_details = _reasoning_capabilities(model)
                    for key in ("max_model_len", "context_length", "max_context_length"):
                        limit = model.get(key)
                        if isinstance(limit, int) and not isinstance(limit, bool) and limit > 0:
                            item_details["context_length"] = limit
                            break
                    details[name] = item_details
                logger.info(
                    "Ответ движка: instance_id=%s status=%s models=%s",
                    instance.id, response.status_code, list(details),
                )
                if details:
                    self.process_manager.model_registry().register_instance(
                        instance, upstream_models=list(details)
                    )
                    instance.state = "ready"
                else:
                    self.process_manager.model_registry().clear_instance(instance.id)
                if instance.engine_id == "llama.cpp":
                    response = await client.get(f"{base}/props", timeout=1.0)
                    if response.is_success:
                        props = response.json()
                        shared = _reasoning_capabilities(props)
                        limit = props.get("default_generation_settings", {}).get("n_ctx")
                        if isinstance(limit, int) and not isinstance(limit, bool) and limit > 0:
                            shared["context_length"] = limit
                        for item_details in details.values():
                            item_details.update(shared)
        except (httpx.HTTPError, AppError, ValueError, TypeError, AttributeError) as exc:
            logger.warning(
                "Не удалось обновить модели движка: instance_id=%s error=%s", instance.id, exc
            )
        return details

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


def _reasoning_capabilities(metadata: Mapping[str, Any]) -> dict[str, Any]:
    """Expose only modes explicitly reported or constrained by the served template."""
    efforts = metadata.get("supported_reasoning_efforts")
    parameter = "reasoning_effort"
    template = metadata.get("chat_template")
    can_disable = metadata.get("supports_enable_thinking") is True
    if isinstance(template, str):
        # Inspect Jinja expressions only, not prose or comments in the template.
        expressions = " ".join(re.findall(r"{%.*?%}|{{.*?}}", template, re.DOTALL))
        can_disable = can_disable or bool(re.search(r"\benable_thinking\b", expressions))
        constraint = re.search(r"\breasoning_effort\s+(?:not\s+)?in\s*(\[[^\]]+\])", expressions)
        if constraint:
            try:
                efforts = ast.literal_eval(constraint.group(1))
                parameter = "chat_template_kwargs"
            except (ValueError, SyntaxError):
                pass
    allowed = {"none", "minimal", "low", "medium", "high", "xhigh"}
    efforts = (
        [item for item in efforts if isinstance(item, str) and item in allowed]
        if isinstance(efforts, list) else []
    )
    if not efforts and not can_disable:
        return {}
    return {"reasoning_capabilities": {
        "efforts": efforts,
        "effort_parameter": parameter,
        "can_disable_thinking": can_disable,
    }}


def _nonnegative_int(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return cast(int, value)


__all__ = ["ProxyService"]
