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
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, cast
from uuid import uuid4

import httpx

from ..errors import AppError
from ..process.manager import ProcessManager
from ..process.registry import RegisteredModel
from ..schemas.instances import Instance
from ..token_stats import RequestJournalService
from .routes import RouteInfo, RouteManager, RuntimeModel

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
_USAGE_STREAM_OPTION_ENGINES = frozenset({"vllm", "llama.cpp", "sglang", "ninfer", "freetoken"})
_SSE_USAGE_BUFFER_LIMIT = 2 * 1024 * 1024
logger = logging.getLogger(__name__)
_request_tracking: ContextVar[dict[str, Any] | None] = ContextVar("request_tracking", default=None)
_route_override: ContextVar[RegisteredModel | None] = ContextVar("route_override", default=None)


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
        route_manager: RouteManager | None = None,
        client_factory: Callable[[], httpx.AsyncClient] | None = None,
        timeout: float = 120.0,
    ) -> None:
        self.process_manager = process_manager
        self.journal = journal
        self.route_manager = route_manager
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
        if self.route_manager is not None:
            _, instances, metadata, routes = await self._refresh_route_catalog()
            route_names = {route.name for route in routes}
            result: list[dict[str, Any]] = []
            for route_info in routes:
                if route_info.diagnostics:
                    continue
                candidate = next(iter(route_info.available_candidates()), None)
                if candidate is None:
                    continue
                instance = instances.get(candidate.instance_id or "")
                if instance is None:
                    continue
                result.append(
                    {
                        "id": route_info.name,
                        "object": "model",
                        "created": int(instance.observed_at.timestamp()),
                        "owned_by": "llm-studio",
                        "engine_id": candidate.engine_id,
                        **candidate.metadata,
                    }
                )
            for registered in sorted(
                self.process_manager.model_registry().list(), key=lambda item: item.id
            ):
                if registered.profile_name is not None or registered.id in route_names:
                    continue
                engine_models = metadata.get(registered.instance_id, {})
                if (
                    registered.upstream_model is None
                    or registered.upstream_model not in engine_models
                ):
                    continue
                result.append(
                    {
                        "id": registered.id,
                        "object": "model",
                        "created": int(registered.observed_at.timestamp()),
                        "owned_by": "llm-studio",
                        "engine_id": registered.engine_id,
                        **engine_models[registered.upstream_model],
                    }
                )
            return {"object": "list", "data": result}
        async with self._models_lock:
            # Observe process identities on every request without probing ready engines.
            observed = await self.process_manager.refresh(probe_models=False)
            instances = {
                item.id: item
                for item in observed.items
                if item.state not in {"exited", "error", "stopping"}
                and (item.source == "managed" or item.identity_verified)
            }
            logger.info("Обновление списка моделей: активных запусков=%s", len(instances))
            for instance_id in list(self._models_cache):
                if instance_id not in instances:
                    del self._models_cache[instance_id]
            metadata = dict(
                zip(
                    instances,
                    await asyncio.gather(
                        *(self._cached_model_metadata(instance) for instance in instances.values())
                    ),
                    strict=True,
                )
            )
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
                result.append(
                    {
                        "id": registered.id,
                        "object": "model",
                        "created": int(registered.observed_at.timestamp()),
                        "owned_by": "llm-studio",
                        "engine_id": registered.engine_id,
                        **engine_models[registered.upstream_model],
                    }
                )
            return {"object": "list", "data": result}

    async def route_data(self) -> dict[str, Any]:
        if self.route_manager is None:
            return {"items": []}
        _, _, _, routes = await self._refresh_route_catalog()
        return {"items": [route.as_dict() for route in routes]}

    async def create_alias(self, name: str, profile_ids: list[str]) -> dict[str, Any]:
        if self.route_manager is None:
            raise AppError("route_unavailable", "Управление маршрутами недоступно", status_code=503)
        await asyncio.to_thread(self.route_manager.create_alias, name, profile_ids)
        return await self._route_by_name(name)

    async def delete_alias(self, name: str) -> None:
        if self.route_manager is None:
            raise AppError("route_unavailable", "Управление маршрутами недоступно", status_code=503)
        await asyncio.to_thread(self.route_manager.delete_alias, name)

    async def update_route_order(self, name: str, profile_ids: list[str]) -> dict[str, Any]:
        if self.route_manager is None:
            raise AppError("route_unavailable", "Управление маршрутами недоступно", status_code=503)
        await asyncio.to_thread(self.route_manager.update_order, name, profile_ids)
        return await self._route_by_name(name)

    async def _route_by_name(self, name: str) -> dict[str, Any]:
        _, _, _, routes = await self._refresh_route_catalog()
        for route in routes:
            if route.name == name:
                return route.as_dict()
        raise AppError("route_not_found", "Маршрут не найден", status_code=404)

    async def _refresh_route_catalog(
        self,
    ) -> tuple[
        Any,
        dict[str, Instance],
        dict[str, dict[str, dict[str, Any]]],
        list[RouteInfo],
    ]:
        if self.route_manager is None:
            raise RuntimeError("route manager is not configured")
        async with self._models_lock:
            observed = await self.process_manager.refresh(probe_models=False)
            instances = {
                item.id: item
                for item in observed.items
                if item.state not in {"exited", "error", "stopping"}
                and (item.source == "managed" or item.identity_verified)
            }
            for instance_id in list(self._models_cache):
                if instance_id not in instances:
                    del self._models_cache[instance_id]
            metadata = dict(
                zip(
                    instances,
                    await asyncio.gather(
                        *(self._cached_model_metadata(instance) for instance in instances.values())
                    ),
                    strict=True,
                )
            )
            profiles = await asyncio.to_thread(self.route_manager.profiles)
            runtime = {
                instance.profile_name: RuntimeModel(instance, metadata.get(instance.id, {}))
                for instance in instances.values()
                if instance.source == "managed" and instance.profile_name
            }
            routes = await asyncio.to_thread(self.route_manager.snapshot, profiles, runtime)
            return observed, instances, metadata, routes

    async def _cached_model_metadata(self, instance: Instance) -> dict[str, dict[str, Any]]:
        identity = (
            instance.pid,
            instance.process_start_time_ns,
            instance.port,
            instance.model_name if instance.source == "managed" else None,
            instance.engine_id,
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
                    instance.id,
                    response.status_code,
                    list(details),
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

    async def _write_journal(self, **values: Any) -> None:
        if self.journal is None:
            return
        try:
            await self.journal.record_safely(**values)
        except Exception as exc:
            logger.error("Request journal write failed: %s", type(exc).__name__)

    async def forward(
        self,
        *,
        route: str,
        payload: Mapping[str, Any],
        headers: Mapping[str, str],
        request_id: str,
        client_ip: str | None = None,
        force_usage: bool = False,
    ) -> httpx.Response | Any:
        tracking: dict[str, Any] = {
            "journal_id": f"journal_{uuid4().hex}",
            "started_at": datetime.now(UTC),
            "client_ip": client_ip,
            "pid": None,
            "client_id": None,
        }
        if self.journal is not None:
            try:
                tracking["client_id"] = await self.journal.identify_client(
                    next(
                        (value for key, value in headers.items() if key.lower() == "authorization"),
                        None,
                    ),
                    client_ip,
                )
            except Exception as exc:
                logger.error("Client identification failed: %s", type(exc).__name__)
            await self._write_journal(
                request_id=request_id,
                route=route,
                model_name=str(payload.get("model", "<missing>")),
                profile_name=None,
                instance_id=None,
                engine_id=None,
                status_code=None,
                latency_ms=0,
                input_tokens=None,
                output_tokens=None,
                created_at=tracking["started_at"],
                **tracking,
            )
        token = _request_tracking.set(tracking)
        try:
            return await self._forward(
                route=route,
                payload=payload,
                headers=headers,
                request_id=request_id,
                force_usage=force_usage,
            )
        finally:
            _request_tracking.reset(token)

    async def _forward(
        self,
        *,
        route: str,
        payload: Mapping[str, Any],
        headers: Mapping[str, str],
        request_id: str,
        force_usage: bool,
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
        if self.route_manager is not None and _route_override.get() is None:
            _, _, _, routes = await self._refresh_route_catalog()
            route_info = next((item for item in routes if item.name == model_name), None)
            if route_info is not None:
                if route_info.diagnostics or not route_info.available:
                    await self._journal_without_target(
                        request_id=request_id,
                        route=route,
                        model_name=model_name,
                        status_code=503,
                        started=started,
                        error_code="route_unavailable",
                    )
                    raise AppError(
                        "route_unavailable",
                        "Маршрут модели временно недоступен",
                        status_code=503,
                        retryable=True,
                        details={"model": model_name},
                    )
                last_error: AppError | None = None
                for candidate in route_info.available_candidates():
                    registered = RegisteredModel(
                        id=route_info.name,
                        upstream_model=candidate.upstream_model,
                        instance_id=candidate.instance_id or "",
                        engine_id=candidate.engine_id or "",
                        profile_name=candidate.profile_name,
                        observed_at=datetime.now(UTC),
                    )
                    token = _route_override.set(registered)
                    try:
                        return await self._forward(
                            route=route,
                            payload=payload,
                            headers=headers,
                            request_id=request_id,
                            force_usage=force_usage,
                        )
                    except AppError as exc:
                        if exc.code != "upstream_unavailable" or not exc.retryable:
                            raise
                        last_error = exc
                    finally:
                        _route_override.reset(token)
                if last_error is not None:
                    raise last_error
                raise AppError(
                    "route_unavailable",
                    "Маршрут модели временно недоступен",
                    status_code=503,
                    retryable=True,
                )
        await self.process_manager.refresh()
        registered = _route_override.get() or self.process_manager.model_registry().get(model_name)
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
        tracking = _request_tracking.get()
        if tracking is not None:
            tracking["pid"] = instance.pid
            tracking["profile_name"] = registered.profile_name
            tracking["instance_id"] = registered.instance_id
            tracking["engine_id"] = registered.engine_id
            if self.journal is not None:
                # Persist the selected upstream before waiting for inference.
                await self._write_journal(
                    request_id=request_id,
                    route=route,
                    model_name=model_name,
                    status_code=None,
                    latency_ms=0,
                    input_tokens=None,
                    output_tokens=None,
                    created_at=tracking["started_at"],
                    **tracking,
                )
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
            key: value
            for key, value in headers.items()
            if key.lower() not in _HOP_BY_HOP and key.lower() != "authorization"
        }
        stream = bool(upstream_payload.get("stream")) and route == "/v1/chat/completions"
        if force_usage and stream and registered.engine_id in _USAGE_STREAM_OPTION_ENGINES:
            # All supported OpenAI-compatible engines use this standard option
            # for the terminal usage chunk. Preserve other engine-specific
            # stream options while making usage available to the journal.
            options = upstream_payload.get("stream_options")
            if not isinstance(options, Mapping):
                options = {}
            else:
                options = dict(options)
            options["include_usage"] = True
            upstream_payload["stream_options"] = options
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
            if tracking is not None:
                tracking.update(
                    _response_metrics(body, streaming=False, engine_id=registered.engine_id)
                )
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
        tracking = _request_tracking.get()
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
            pending = bytearray()
            first_token_ms: float | None = None

            def observe(chunk: bytes) -> None:
                nonlocal first_token_ms
                pending.extend(chunk)
                while b"\n" in pending:
                    line, _, tail = pending.partition(b"\n")
                    pending[:] = tail
                    if first_token_ms is not None or not line.startswith(b"data:"):
                        continue
                    try:
                        event = json.loads(line[5:].strip())
                        if any(
                            any(
                                choice.get("delta", {}).get(key)
                                for key in ("content", "reasoning_content", "reasoning")
                            )
                            for choice in event.get("choices", [])
                        ):
                            first_token_ms = (time.perf_counter() - started) * 1000
                    except (ValueError, TypeError, AttributeError):
                        continue
                if len(pending) > _SSE_USAGE_BUFFER_LIMIT:
                    pending.clear()

            try:
                # MockTransport and a few compatible clients return an
                # already-closed in-memory response even when ``stream=True``.
                # Preserve that body while retaining true chunked iteration for
                # live upstream responses.
                if response.is_closed:
                    chunk = response.content
                    if chunk:
                        usage_buffer.extend(chunk)
                        observe(chunk)
                        yield chunk
                else:
                    async for chunk in response.aiter_raw():
                        observe(chunk)
                        usage_buffer.extend(chunk)
                        if len(usage_buffer) > _SSE_USAGE_BUFFER_LIMIT:
                            del usage_buffer[: len(usage_buffer) - _SSE_USAGE_BUFFER_LIMIT]
                        yield chunk
            except (httpx.TimeoutException, httpx.NetworkError, httpx.RemoteProtocolError):
                error_code = "upstream_unavailable"
                raise
            except (asyncio.CancelledError, GeneratorExit):
                error_code = "client_disconnected"
                raise
            except Exception:
                error_code = "upstream_error"
                raise
            finally:
                input_tokens, output_tokens = _extract_sse_usage(bytes(usage_buffer))
                if tracking is not None:
                    tracking.update(
                        _response_metrics(
                            bytes(usage_buffer), streaming=True, engine_id=registered.engine_id
                        )
                    )
                    if first_token_ms is not None:
                        tracking["ttft_ms"] = first_token_ms
                        tracking.setdefault("metric_meta", {})["ttft_ms"] = {
                            "source": "proxy:first_token"
                        }
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
                    tracking=tracking,
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
        tracking: dict[str, Any] | None = None,
    ) -> None:
        if self.journal is None:
            return
        try:
            extra = dict(tracking if tracking is not None else _request_tracking.get() or {})
            for key in ("profile_name", "instance_id", "engine_id"):
                extra.pop(key, None)
            extra["finished_at"] = datetime.now(UTC)
            await self._write_journal(
                **extra,
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
            extra = dict(_request_tracking.get() or {})
            for key in ("profile_name", "instance_id", "engine_id"):
                extra.pop(key, None)
            extra["finished_at"] = datetime.now(UTC)
            await self._write_journal(
                **extra,
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
        if isinstance(efforts, list)
        else []
    )
    if not efforts and not can_disable:
        return {}
    return {
        "reasoning_capabilities": {
            "efforts": efforts,
            "effort_parameter": parameter,
            "can_disable_thinking": can_disable,
        }
    }


def _nonnegative_int(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return cast(int, value)


__all__ = ["ProxyService"]


def _response_metrics(body: bytes, *, streaming: bool, engine_id: str) -> dict[str, Any]:
    """Read request-scoped usage and documented llama.cpp/NInfer timings."""
    events: list[Any] = []
    if streaming:
        for line in body.splitlines():
            if line.startswith(b"data:"):
                try:
                    events.append(json.loads(line[5:].strip()))
                except ValueError:
                    pass
    else:
        try:
            events.append(json.loads(body))
        except ValueError:
            return {}
    result: dict[str, Any] = {}
    meta: dict[str, Any] = {}

    def number(value: Any) -> float | None:
        import math

        return (
            float(value)
            if isinstance(value, (int, float))
            and not isinstance(value, bool)
            and math.isfinite(value)
            and value >= 0
            else None
        )

    for event in events:
        if not isinstance(event, dict):
            continue
        usage = event.get("usage")
        if isinstance(usage, dict):
            prompt, _ = _usage_values(usage)
            details = usage.get("prompt_tokens_details", usage.get("input_tokens_details"))
            cached = (
                _nonnegative_int(details.get("cached_tokens"))
                if isinstance(details, dict)
                else None
            )
            result["usage_received_at"] = datetime.now(UTC)
            result["usage_source"] = "upstream:usage"
            if cached is not None and (prompt is None or cached <= prompt):
                result["cached_input_tokens"] = cached
                if prompt is not None and prompt > 0:
                    result["prefix_cache_hit_percent"] = cached / prompt * 100
        timings = event.get("timings")
        if engine_id not in {"llama.cpp", "ninfer"} or not isinstance(timings, dict):
            continue
        cached = _nonnegative_int(timings.get("cache_n"))
        prompt, _ = _usage_values(event.get("usage"))
        if cached is not None and (prompt is None or cached <= prompt):
            result.setdefault("cached_input_tokens", cached)
            meta["cached_input_tokens"] = {"source": f"{engine_id}:timings.cache_n"}
            if prompt is not None and prompt > 0:
                result.setdefault("prefix_cache_hit_percent", cached / prompt * 100)
        for field, source in (
            ("prefill_time_ms", "prompt_ms"),
            ("generation_time_ms", "predicted_ms"),
            ("prefill_tps", "prompt_per_second"),
            ("decode_tps", "predicted_per_second"),
        ):
            value = number(timings.get(source))
            if value is not None:
                result[field] = value
                meta[field] = {"source": f"{engine_id}:timings", "field": source}
        for field, tokens, duration in (
            ("prefill_tps", "prompt_n", "prompt_ms"),
            ("decode_tps", "predicted_n", "predicted_ms"),
        ):
            count, elapsed = number(timings.get(tokens)), number(timings.get(duration))
            if count is not None and elapsed is not None and elapsed > 0:
                meta[field] = {
                    "source": f"{engine_id}:timings",
                    "tokens": count,
                    "duration_ms": elapsed,
                }
                result.setdefault(field, count / elapsed * 1000)
        accepted, drafted = number(timings.get("draft_n_accepted")), number(timings.get("draft_n"))
        if accepted is not None and drafted is not None and drafted > 0 and accepted <= drafted:
            result["spec_acceptance_percent"] = accepted / drafted * 100
            meta["spec_acceptance_percent"] = {
                "source": f"{engine_id}:timings",
                "accepted": accepted,
                "drafted": drafted,
            }
    if meta:
        result["metric_meta"] = meta
    return result
