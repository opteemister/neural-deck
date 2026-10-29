"""OpenAI-compatible forwarding service.

The proxy deliberately knows only the process manager and its model registry.
It does not duplicate engine lifecycle logic or profile parsing.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import AsyncIterator, Callable, Mapping
from contextvars import ContextVar
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any, cast
from uuid import uuid4

import httpx

from ..documents import document_capabilities
from ..errors import AppError
from ..reasoning import adapt_reasoning_request, reasoning_capabilities as _reasoning_capabilities
from ..process.manager import ProcessManager
from ..process.registry import RegisteredModel
from ..schemas.instances import Instance
from ..token_stats import RequestJournalService
from .decisions import adapt_jev_response, build_jev_request
from .operations import OPERATIONS, public_types, supports, upstream_url
from .openai_connections import OpenAIConnections, RemoteTarget, endpoint
from .routes import RouteCandidate, RouteInfo, RouteManager, RuntimeModel

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
_STREAMING_TEXT_ROUTES = frozenset({"/v1/chat/completions", "/v1/responses"})
_SSE_USAGE_BUFFER_LIMIT = 2 * 1024 * 1024
logger = logging.getLogger(__name__)
_request_tracking: ContextVar[dict[str, Any] | None] = ContextVar("request_tracking", default=None)
_route_override: ContextVar[RegisteredModel | None] = ContextVar("route_override", default=None)


@dataclass(slots=True)
class _ModelsCache:
    identity: tuple[Any, ...]
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
        image_service: Any | None = None,
        reasoning_metadata: Callable[[Instance], dict[str, Any]] | None = None,
    ) -> None:
        self.process_manager = process_manager
        self.journal = journal
        self.route_manager = route_manager
        self.client_factory = client_factory
        self.timeout = timeout
        self.image_service = image_service
        self.reasoning_metadata = reasoning_metadata
        self._connections = OpenAIConnections(route_manager.config.config_dir, client_factory) if route_manager else None
        self._models_lock = asyncio.Lock()
        self._models_cache: dict[str, _ModelsCache] = {}
        # Locks live for this ProxyService's application lifespan. Keeping them
        # stable avoids removing a lock while requests are queued on it.
        self._decision_locks: dict[tuple[str, str], asyncio.Lock] = {}

    async def close(self) -> None:
        """Release proxy-owned resources.

        Version Zero creates one short-lived HTTP client per request, closing
        it on every response path.  The hook keeps application shutdown
        composable for a future pooled client without owning injected clients.
        """

        return None

    def _decision_lock_for(self, instance: Instance, registered: RegisteredModel) -> asyncio.Lock:
        key = (
            registered.instance_id or instance.id,
            registered.profile_name or instance.profile_name or "",
        )
        lock = self._decision_locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self._decision_locks[key] = lock
        return lock

    async def generate_image(
        self, payload: Mapping[str, Any], *, request_id: str
    ) -> dict[str, Any]:
        """Run MCP image generation through the same operation/protocol router as /v1."""

        requested_model = payload.get("model")
        models = (await self.models()).get("data", [])
        image_models = sorted({
            item["id"]
            for item in models
            if isinstance(item, Mapping)
            and isinstance(item.get("id"), str)
            and isinstance(item.get("types"), list)
            and "image" in item["types"]
        })
        if self.route_manager is not None:
            _, instances, _, routes = await self._refresh_route_catalog()
            image_routes = {
                route.name
                for route in routes
                if not route.diagnostics
                and any(
                    candidate.available
                    and candidate.instance_id is not None
                    and candidate.instance_id in instances
                    and supports(instances[candidate.instance_id], "/v1/images/generations")
                    for candidate in route.candidates
                )
            }
            image_models = sorted(set(image_models) | image_routes)
        model_name: str | None = None
        if requested_model is not None:
            if not isinstance(requested_model, str) or not requested_model:
                raise AppError("invalid_model", "Поле model должно быть непустой строкой", status_code=400)
            if requested_model in image_models:
                model_name = requested_model
            else:
                registered = [
                    item for item in self.process_manager.model_registry().list()
                    if item.profile_name == requested_model and item.id in image_models
                ]
                if registered:
                    model_name = registered[0].id
                elif self.route_manager is not None:
                    profiles = await asyncio.to_thread(self.route_manager.profiles)
                    profile = next((item for item in profiles if item.name == requested_model), None)
                    if profile is not None and profile.model_name in image_models:
                        model_name = profile.model_name
            if model_name is None:
                raise AppError(
                    "model_not_found", "Image-профиль не найден или не поддерживает image API",
                    status_code=404, details={"model": requested_model},
                )
        elif len(image_models) == 1:
            model_name = image_models[0]
        elif not image_models:
            raise AppError("image_instance_not_found", "Нет готового image-профиля", status_code=404)
        else:
            raise AppError(
                "image_model_required", "Укажите модель для генерации изображения",
                status_code=409, details={"models": image_models},
            )

        request_payload = {**payload, "model": model_name}
        response = await self.forward(
            route="/v1/images/generations",
            payload=request_payload,
            headers={},
            request_id=request_id,
        )
        if isinstance(response, httpx.Response):
            if response.status_code >= 400:
                try:
                    upstream_error = response.json()
                except (ValueError, TypeError):
                    upstream_error = {"body": response.text[:500]}
                error = upstream_error.get("error") if isinstance(upstream_error, Mapping) else None
                message = error.get("message") if isinstance(error, Mapping) else None
                raise AppError(
                    "image_generation_failed",
                    message if isinstance(message, str) and message else "Image upstream завершил запрос с ошибкой",
                    status_code=response.status_code,
                    details={"upstream_error": upstream_error},
                )
            try:
                result = response.json()
            except (ValueError, TypeError) as exc:
                raise AppError(
                    "upstream_contract_unsupported", "Image upstream вернул не JSON",
                    status_code=502,
                ) from exc
            if not isinstance(result, Mapping):
                raise AppError(
                    "upstream_contract_unsupported", "Image upstream вернул неверный JSON",
                    status_code=502,
                )
            return dict(result)
        if isinstance(response, Mapping):
            return dict(response)
        raise AppError(
            "upstream_contract_unsupported", "Image router вернул неподдерживаемый ответ",
            status_code=502,
        )

    async def models(self) -> dict[str, Any]:
        if self.route_manager is not None:
            _, instances, metadata, routes = await self._refresh_route_catalog()
            route_names = {route.name for route in routes}
            result: list[dict[str, Any]] = []
            for route_info in routes:
                candidate = next(iter(route_info.available_candidates()), None)
                instance = instances.get(candidate.instance_id or "") if candidate else None
                if route_info.diagnostics or candidate is None or instance is None:
                    # Aliases are persistent public model names. Runtime
                    # availability controls routing, not catalogue membership.
                    if route_info.kind == "alias":
                        result.append({
                            "id": route_info.name,
                            "object": "model",
                            "created": 0,
                            "owned_by": "neural-deck",
                        })
                    continue
                result.append(
                    {
                        "id": route_info.name,
                        "object": "model",
                        "created": int(instance.observed_at.timestamp()),
                        "owned_by": "neural-deck",
                        "engine_id": candidate.engine_id,
                        **_public_metadata(candidate.metadata),
                        "types": public_types(instance),
                    }
                )
            for registered in sorted(
                self.process_manager.model_registry().list(), key=lambda item: item.id
            ):
                if registered.profile_name is not None or registered.id in route_names:
                    continue
                engine_models = metadata.get(registered.instance_id, {})
                instance = instances.get(registered.instance_id)
                if instance is None or instance.state != "ready" or not public_types(instance):
                    continue
                if (
                    "text" in instance.types
                    and registered.upstream_model not in engine_models
                ):
                    continue
                result.append(
                    {
                        "id": registered.id,
                        "object": "model",
                        "created": int(registered.observed_at.timestamp()),
                        "owned_by": "neural-deck",
                        "engine_id": registered.engine_id,
                        **_public_metadata(engine_models.get(registered.upstream_model or "", {})),
                        "types": public_types(instance),
                    }
                )
            self._append_image_models(result, instances)
            await self._append_remote_models(result, routes)
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
            llm_instances = {
                instance_id: instance
                for instance_id, instance in instances.items()
                if instance.api_protocol == "openai" and instance.state == "ready"
            }
            metadata = dict(
                zip(
                    llm_instances,
                    await asyncio.gather(
                        *(self._cached_model_metadata(instance) for instance in llm_instances.values())
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
                instance = instances.get(registered.instance_id)
                if instance is None or instance.state != "ready" or not public_types(instance):
                    continue
                if (
                    "text" in instance.types
                    and registered.upstream_model not in engine_models
                ):
                    continue
                result.append(
                    {
                        "id": registered.id,
                        "object": "model",
                        "created": int(registered.observed_at.timestamp()),
                        "owned_by": "neural-deck",
                        "engine_id": registered.engine_id,
                        **_public_metadata(engine_models.get(registered.upstream_model or "", {})),
                        "types": public_types(instance),
                    }
                )
            self._append_image_models(result, instances)
            return {"object": "list", "data": result}

    @staticmethod
    def _append_image_models(result: list[dict[str, Any]], instances: dict[str, Instance]) -> None:
        existing_ids = {item["id"] for item in result}
        for instance in instances.values():
            if not supports(instance, "/v1/images/generations") or instance.state != "ready":
                continue
            image_id = instance.model_name or instance.profile_name
            if not image_id or image_id in existing_ids:
                continue
            result.append({
                "id": image_id,
                "object": "model",
                "created": int(instance.observed_at.timestamp()),
                "owned_by": "neural-deck",
                "engine_id": instance.engine_id,
                "types": public_types(instance),
                "capabilities": {"image_generation": True},
            })
            existing_ids.add(image_id)
        result.sort(key=lambda item: item["id"])

    async def route_data(self) -> dict[str, Any]:
        if self.route_manager is None:
            return {"items": []}
        _, _, _, routes = await self._refresh_route_catalog()
        return {"items": [route.as_dict() for route in routes]}

    async def create_alias(self, name: str, profile_ids: list[str], remote: dict[str, str] | None = None, candidates: list[dict[str, str]] | None = None) -> dict[str, Any]:
        if self.route_manager is None:
            raise AppError("route_unavailable", "Управление маршрутами недоступно", status_code=503)
        self._validate_remote_alias(remote, profile_ids)
        self._validate_candidate_targets(candidates, profile_ids, remote)
        await asyncio.to_thread(self.route_manager.create_alias, name, profile_ids, remote, candidates)
        return await self.get_route(name)

    async def update_alias(
        self, current_name: str, name: str, profile_ids: list[str], remote: dict[str, str] | None = None, candidates: list[dict[str, str]] | None = None
    ) -> dict[str, Any]:
        if self.route_manager is None:
            raise AppError("route_unavailable", "Управление маршрутами недоступно", status_code=503)
        self._validate_remote_alias(remote, profile_ids)
        self._validate_candidate_targets(candidates, profile_ids, remote)
        await asyncio.to_thread(self.route_manager.update_alias, current_name, name, profile_ids, remote, candidates)
        return await self.get_route(name)

    async def delete_alias(self, name: str) -> None:
        if self.route_manager is None:
            raise AppError("route_unavailable", "Управление маршрутами недоступно", status_code=503)
        await asyncio.to_thread(self.route_manager.delete_alias, name)

    async def update_route_order(self, name: str, profile_ids: list[str], candidate_ids: list[str] | None = None) -> dict[str, Any]:
        if self.route_manager is None:
            raise AppError("route_unavailable", "Управление маршрутами недоступно", status_code=503)
        if candidate_ids is not None:
            if profile_ids or len(candidate_ids) != len(set(candidate_ids)):
                raise AppError("route_membership_invalid", "Неверный список кандидатов", status_code=422)
            route = await self.get_route(name)
            if route["kind"] == "auto":
                await asyncio.to_thread(self.route_manager.update_order, name, candidate_ids)
            else:
                by_id = {item["id"]: item for item in route["candidates"]}
                if any(key not in by_id for key in candidate_ids):
                    raise AppError("route_membership_invalid", "Неизвестный кандидат алиаса", status_code=422)
                targets = [{"connection_id": by_id[key]["connection_id"], "model": by_id[key]["upstream_model"]} if by_id[key]["connection_id"] else {"profile_id": by_id[key]["profile_id"]} for key in candidate_ids]
                self._validate_candidate_targets(targets, [], None)
                await asyncio.to_thread(self.route_manager.update_candidates, name, targets)
        else:
            await asyncio.to_thread(self.route_manager.update_order, name, profile_ids)
        return await self.get_route(name)

    async def get_route(self, name: str) -> dict[str, Any]:
        _, _, _, routes = await self._refresh_route_catalog()
        for route in routes:
            if route.name == name:
                return route.as_dict()
        raise AppError("route_not_found", "Маршрут не найден", status_code=404)

    def connections(self) -> OpenAIConnections:
        if self._connections is None:
            raise AppError("route_unavailable", "Управление подключениями недоступно", status_code=503)
        return self._connections

    def delete_connection(self, connection_id: str) -> None:
        config = self.route_manager.config.load() if self.route_manager else {}
        targets = list(config.get("remote_aliases", {}).values()) + [
            target for candidates in config.get("alias_candidates", {}).values() for target in candidates
        ]
        if any(target.get("connection_id") == connection_id for target in targets):
            raise AppError("connection_in_use", "Сначала уберите подключение из его алиасов", status_code=409)
        self.connections().delete(connection_id)

    async def model_choices(self, route_name: str | None = None, refresh: bool = False) -> dict[str, Any]:
        if self.route_manager is None:
            raise AppError("route_unavailable", "Управление маршрутами недоступно", status_code=503)
        profiles = await asyncio.to_thread(self.route_manager.profiles)
        items = [{"id": p.profile_id or p.name, "title": p.model_name, "detail": p.name,
                  "target": {"profile_id": p.profile_id or p.name}} for p in profiles]
        connections = self.connections().list()

        async def catalogue(connection):
            try:
                return connection, await self.connections().models(connection["id"], refresh=refresh), None
            except AppError as exc:
                return connection, [], {"connection_id": connection["id"], "code": exc.code, "message": exc.message}

        diagnostics = []
        for connection, models, error in await asyncio.gather(*(catalogue(c) for c in connections)):
            if error is not None:
                diagnostics.append(error)
            for model in models:
                target = {"connection_id": connection["id"], "model": model["id"]}
                items.append({"id": self.route_manager.candidate_id(target), "title": model["id"], "detail": connection["name"], "target": target})
        if route_name is not None:
            route = await self.get_route(route_name)
            known = {item["id"] for item in items}
            for candidate in route["candidates"]:
                if candidate["id"] in known:
                    continue
                target = ({"connection_id": candidate["connection_id"], "model": candidate["upstream_model"]}
                          if candidate["connection_id"] else {"profile_id": candidate["profile_id"]})
                items.append({"id": candidate["id"], "title": candidate["upstream_model"] or candidate["profile_name"] or candidate["id"],
                              "detail": candidate["connection_name"] or candidate["profile_name"] or "Недоступен", "target": target})
        items.sort(key=lambda item: (item["title"], item["detail"]))
        return {"items": items, "diagnostics": diagnostics}

    def _validate_remote_alias(self, remote: dict[str, str] | None, profile_ids: list[str]) -> None:
        if remote is None:
            return
        if profile_ids or not remote["model"].strip():
            raise AppError("route_membership_invalid", "Выберите локальные профили либо удалённую модель", status_code=422)
        self.connections().get(remote["connection_id"])
        remote["model"] = remote["model"].strip()

    def _validate_candidate_targets(self, candidates: list[dict[str, str]] | None, profiles: list[str], remote: dict[str, str] | None) -> None:
        if candidates is None:
            return
        if profiles or remote is not None:
            raise AppError("route_membership_invalid", "Используйте один список кандидатов", status_code=422)
        assert self.route_manager is not None
        for target in self.route_manager.config.candidate_targets(candidates):
            if "connection_id" in target:
                self.connections().get(target["connection_id"])

    def _remote_candidate_target(self, candidate: RouteCandidate) -> RemoteTarget:
        assert candidate.connection_id is not None and candidate.upstream_model is not None
        connection = self.connections().get(candidate.connection_id)
        return RemoteTarget(candidate.connection_id, candidate.upstream_model, connection["base_url"], connection.get("api_key", ""))

    def _merge_candidates(self, routes: list[RouteInfo]) -> list[RouteInfo]:
        assert self.route_manager is not None
        config = self.route_manager.config.load()
        merged = []
        for route in routes:
            targets = config["alias_candidates"].get(route.name)
            if targets is None and route.name in config["remote_aliases"]:
                targets = [config["remote_aliases"][route.name]]
            if targets is None:
                merged.append(route)
                continue
            local = {candidate.profile_id: candidate for candidate in route.candidates}
            candidates = []
            for target in targets:
                if "profile_id" in target:
                    candidates.append(local[target["profile_id"]])
                    continue
                try:
                    connection = self.connections().get(target["connection_id"])
                    name, available, reason = connection["name"], True, None
                except AppError as exc:
                    if exc.code != "connection_not_found":
                        raise
                    name, available, reason = None, False, "connection_not_found"
                candidates.append(RouteCandidate(
                    id=self.route_manager.candidate_id(target), profile_id=None,
                    profile_name=None, instance_id=None, upstream_model=target["model"],
                    engine_id="openai", port=None, state="configured" if available else "missing",
                    available=available, reason=reason, metadata={},
                    connection_id=target["connection_id"], connection_name=name,
                ))
            merged.append(replace(route, candidates=tuple(candidates)))
        return merged

    async def _append_remote_models(self, items: list[dict[str, Any]], routes: list[RouteInfo]) -> None:
        if self.route_manager is None:
            return
        aliases = {route.name: {"connection_id": candidate.connection_id, "model": candidate.upstream_model}
                   for route in routes if (candidate := next(iter(route.available_candidates()), None)) is not None and candidate.connection_id}
        async def catalogue(connection_id: str) -> tuple[str, list[dict[str, Any]]]:
            try:
                return connection_id, await self.connections().models(connection_id)
            except AppError as exc:
                logger.info("Каталог OpenAI-подключения недоступен: connection_id=%s code=%s", connection_id, exc.code)
                return connection_id, []
        catalogues = dict(await asyncio.gather(*(catalogue(key) for key in {value["connection_id"] for value in aliases.values()})))
        for name, target in aliases.items():
            source = next((item for item in catalogues[target["connection_id"]] if item["id"] == target["model"]), {})
            metadata = {**_reasoning_capabilities(source), **document_capabilities(source)}
            for field in ("context_length", "max_model_len", "max_context_length"):
                if isinstance(source.get(field), int) and not isinstance(source[field], bool) and source[field] > 0:
                    metadata["context_length"] = source[field]
                    break
            types = source.get("types")
            public = {"id": name, "object": "model", "created": 0, "owned_by": "neural-deck", "engine_id": "openai", **_public_metadata(metadata), "types": types if isinstance(types, list) and types and all(isinstance(value, str) for value in types) else ["text"]}
            items[:] = [item for item in items if item["id"] != name]
            items.append(public)

    async def _forward_remote(
        self, remote: RemoteTarget, route: str, model_name: str, payload: Mapping[str, Any],
        request_id: str, started: float, force_usage: bool,
        multipart_data: Mapping[str, Any] | None, multipart_files: Mapping[str, Any] | None,
    ) -> Any:
        operation = OPERATIONS.get(route)
        if operation is None or "openai" not in operation[1]:
            raise AppError("unsupported_operation", "Операция не относится к OpenAI API", status_code=400)
        target = endpoint(remote.base_url, route)
        outgoing = {"x-request-id": request_id}
        if remote.api_key:
            outgoing["authorization"] = f"Bearer {remote.api_key}"
        upstream = {**payload, "model": remote.upstream_model}
        if route in _STREAMING_TEXT_ROUTES and any(
            key in payload for key in ("reasoning_effort", "reasoning", "chat_template_kwargs")
        ):
            catalogue = await self.connections().models(remote.connection_id)
            source: dict[str, Any] = next(
                (item for item in catalogue if item["id"] == remote.upstream_model), {}
            )
            upstream = adapt_reasoning_request(upstream, route, source)
        streaming = bool(payload.get("stream")) and route in _STREAMING_TEXT_ROUTES
        if streaming and force_usage and route == "/v1/chat/completions" and (upstream.get("stream_options") is None or isinstance(upstream.get("stream_options"), Mapping)):
            upstream["stream_options"] = {**(upstream.get("stream_options") or {}), "include_usage": True}
        tracking = _request_tracking.get()
        if tracking is not None:
            tracking.update(pid=None, profile_name=None, profile_id=None, instance_id=None, engine_id="openai")
        client = self.client_factory() if self.client_factory else httpx.AsyncClient(timeout=self.timeout, follow_redirects=False)
        if multipart_files is not None or route == "/v1/audio/speech":
            return await self._forward_media(client, target, upstream, outgoing, route, model_name, remote, request_id, started, data={**(multipart_data or {}), "model": remote.upstream_model}, files=multipart_files)
        if streaming:
            return await self._forward_stream(client, target, upstream, outgoing, route, model_name, remote, request_id, started)
        try:
            response = await client.post(target, json=upstream, headers=outgoing, follow_redirects=False)
            body = await response.aread()
            input_tokens, output_tokens = _extract_usage(body, response.headers.get("content-type"))
            if tracking is not None:
                tracking.update(_response_metrics(body, streaming=False, engine_id="openai"))
            await self._journal(request_id=request_id, route=route, model_name=model_name, registered=remote, status_code=response.status_code, started=started, input_tokens=input_tokens, output_tokens=output_tokens, stream=False, error_code="upstream_error" if response.is_error else None)
            return _passthrough_response(response.status_code, response.headers, body)
        except httpx.HTTPError as exc:
            await self._journal(request_id=request_id, route=route, model_name=model_name, registered=remote, status_code=502, started=started, input_tokens=None, output_tokens=None, stream=False, error_code="upstream_unavailable")
            raise AppError("upstream_unavailable", "OpenAI-сервер недоступен", status_code=502, retryable=_safe_to_retry(exc)) from None
        finally:
            await client.aclose()

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
            llm_instances = {
                instance_id: instance
                for instance_id, instance in instances.items()
                if instance.api_protocol == "openai" and instance.state == "ready"
            }
            metadata = dict(
                zip(
                    llm_instances,
                    await asyncio.gather(
                        *(self._cached_model_metadata(instance) for instance in llm_instances.values())
                    ),
                    strict=True,
                )
            )
            profiles = await asyncio.to_thread(self.route_manager.profiles)
            profile_ids_by_name = {
                profile.name: profile.profile_id
                for profile in profiles
                if profile.profile_id is not None
            }
            runtime = {
                (
                    instance.profile_id
                    or profile_ids_by_name.get(instance.profile_name or "")
                    or instance.profile_name
                ): RuntimeModel(instance, metadata.get(instance.id, {}))
                for instance in instances.values()
                if instance.source == "managed" and (instance.profile_id or instance.profile_name)
            }
            routes = await asyncio.to_thread(self.route_manager.snapshot, profiles, runtime)
            routes = await asyncio.to_thread(self._merge_candidates, routes)
            return observed, instances, metadata, routes

    async def _cached_model_metadata(self, instance: Instance) -> dict[str, dict[str, Any]]:
        identity = (
            instance.pid,
            instance.process_start_time_ns,
            instance.port,
            instance.model_name if instance.source == "managed" else None,
            instance.engine_id,
            instance.api_protocol,
            instance.api_base_url,
            tuple(instance.types),
        )
        cached = self._models_cache.get(instance.id)
        if cached is not None and cached.identity == identity:
            if time.monotonic() - cached.refreshed_at < 600:
                logger.info("Модели из кеша: instance_id=%s", instance.id)
                self.process_manager.model_registry().register_instance(
                    instance, upstream_models=list(cached.metadata)
                )
                return await self._runtime_reasoning_metadata(instance, cached.metadata)
        self._models_cache.pop(instance.id, None)
        metadata = await self._model_metadata(instance)
        # Only a nonempty, confirmed engine response starts the ten-minute TTL.
        if metadata:
            self._models_cache[instance.id] = _ModelsCache(identity, time.monotonic(), metadata)
        return await self._runtime_reasoning_metadata(instance, metadata)

    async def _runtime_reasoning_metadata(
        self, instance: Instance, metadata: dict[str, dict[str, Any]],
    ) -> dict[str, dict[str, Any]]:
        if not metadata or self.reasoning_metadata is None:
            return metadata
        local = await asyncio.to_thread(self.reasoning_metadata, instance)
        return {name: {**local, **details} for name, details in metadata.items()}

    async def _model_metadata(self, instance: Instance) -> dict[str, dict[str, Any]]:
        """Refresh engine model names and their metadata in one HTTP probe."""
        details: dict[str, dict[str, Any]] = {}
        try:
            if instance.api_protocol != "openai" or instance.state != "ready":
                return {}
            client = (
                self.client_factory() if self.client_factory else httpx.AsyncClient(timeout=1.0)
            )
            async with client:
                target = upstream_url(instance, "/v1/models")
                response = await client.get(target, timeout=1.0)
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
                    item_details = {**_reasoning_capabilities(model), **document_capabilities(model)}
                    for key in ("dimensions", "embedding_dimensions", "embedding_space"):
                        if key in model:
                            item_details[key] = model[key]
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
                else:
                    self.process_manager.model_registry().clear_instance(instance.id)
                if instance.engine_id == "llama.cpp":
                    from urllib.parse import urlsplit, urlunsplit

                    base = urlsplit(target)
                    props_url = urlunsplit((base.scheme, base.netloc, "/props", "", ""))
                    response = await client.get(props_url, timeout=1.0)
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
        multipart_data: Mapping[str, Any] | None = None,
        multipart_files: Mapping[str, Any] | None = None,
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
                multipart_data=multipart_data,
                multipart_files=multipart_files,
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
        multipart_data: Mapping[str, Any] | None = None,
        multipart_files: Mapping[str, Any] | None = None,
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
            _, instances, _, routes = await self._refresh_route_catalog()
            route_info = next((item for item in routes if item.name == model_name), None)
            if route_info is not None:
                matching = [candidate for candidate in route_info.candidates
                            if (candidate.connection_id and route in OPERATIONS and "openai" in OPERATIONS[route][1])
                            or (candidate.instance_id in instances and supports(instances[candidate.instance_id], route))]
                if not matching:
                    raise AppError("unsupported_operation", "Модель не поддерживает эту операцию",
                                   status_code=400, details={"model": model_name, "route": route})
                available = [candidate for candidate in matching if candidate.available]
                if route in {"/v1/embeddings", "/v1/embeddings/media", "/v2/embed"}:
                    await self._validate_embedding_route(matching)
                if route_info.diagnostics or not available:
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
                for candidate in available:
                    if candidate.connection_id:
                        try:
                            remote = await asyncio.to_thread(self._remote_candidate_target, candidate)
                            return await self._forward_remote(remote, route, model_name, payload, request_id, started, force_usage, multipart_data, multipart_files)
                        except AppError as exc:
                            if exc.code != "upstream_unavailable" or not exc.retryable:
                                raise
                            last_error = exc
                            continue
                    registered = RegisteredModel(
                        id=route_info.name,
                        upstream_model=candidate.upstream_model,
                        instance_id=candidate.instance_id or "",
                        engine_id=candidate.engine_id or "",
                        profile_name=candidate.profile_name,
                        profile_id=candidate.profile_id if hasattr(candidate, "profile_id") else None,
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
                            multipart_data=multipart_data,
                            multipart_files=multipart_files,
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
                stream=bool(payload.get("stream")) and route in _STREAMING_TEXT_ROUTES,
                error_code=exc.code,
            )
            raise
        tracking = _request_tracking.get()
        if not supports(instance, route):
            raise AppError("unsupported_operation", "Модель не поддерживает эту операцию",
                           status_code=400)
        if instance.state != "ready" or not instance.identity_verified:
            raise AppError("upstream_unavailable", "API экземпляра не готов", status_code=503)
        if tracking is not None:
            tracking["pid"] = instance.pid
            tracking["profile_name"] = registered.profile_name
            tracking["profile_id"] = registered.profile_id
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
        if not instance.api_base_url:
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
                "У экземпляра отсутствует API URL",
                status_code=503,
                retryable=True,
            )
        upstream_model = registered.upstream_model or model_name
        upstream_payload = dict(payload)
        upstream_payload["model"] = upstream_model
        if route in _STREAMING_TEXT_ROUTES and any(
            key in payload for key in ("reasoning_effort", "reasoning", "chat_template_kwargs")
        ):
            model_metadata = (await self._cached_model_metadata(instance)).get(upstream_model, {})
            upstream_payload = adapt_reasoning_request(upstream_payload, route, model_metadata)
        if route == "/v1/images/generations" and instance.api_protocol == "openai":
            # The MCP schema retains ComfyUI controls. Drop their defaults for
            # OpenAI servers, but report non-default requests instead of
            # silently claiming to honor unsupported behavior.
            unsupported_controls = {}
            if payload.get("seed") is not None:
                unsupported_controls["seed"] = payload["seed"]
            if payload.get("steps", 25) not in (None, 25):
                unsupported_controls["steps"] = payload["steps"]
            if payload.get("transparent") is True:
                unsupported_controls["transparent"] = True
            if unsupported_controls:
                raise AppError(
                    "unsupported_operation",
                    "Выбранный OpenAI image profile не поддерживает параметры ComfyUI",
                    status_code=400,
                    details={"parameters": sorted(unsupported_controls)},
                )
            for key in ("seed", "steps", "transparent"):
                upstream_payload.pop(key, None)
        if route == "/v1/images/generations" and instance.api_protocol == "comfyui":
            if self.image_service is None:
                raise AppError("unsupported_operation", "Image adapter недоступен", status_code=503)
            upstream_payload["model"] = instance.profile_name or instance.model_name
            result = await self.image_service.generate(
                upstream_payload, request_id=request_id,
                url_prefix="/api/proxy" if force_usage else "/v1",
            )
            await self._journal(request_id=request_id, route=route, model_name=model_name,
                                registered=registered, status_code=200, started=started,
                                input_tokens=None, output_tokens=None, stream=False, error_code=None)
            return httpx.Response(200, json=result)
        if route == "/v1/decisions":
            _, upstream_payload, decision_mappings = build_jev_request(payload)
        elif route == "/v1/systemone":
            # ``model`` selects the Neural Deck route.  Native Jev/System One
            # receives its state and named questions, not the public model ID.
            upstream_payload.pop("model", None)
        target = upstream_url(instance, route)
        outgoing_headers = {
            key: value
            for key, value in headers.items()
            if key.lower() not in _HOP_BY_HOP and key.lower() != "authorization"
            and (multipart_files is None or key.lower() != "content-type")
        }
        stream = bool(upstream_payload.get("stream")) and route in _STREAMING_TEXT_ROUTES
        if (force_usage and stream and route == "/v1/chat/completions"
                and registered.engine_id in _USAGE_STREAM_OPTION_ENGINES):
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
        if route in {
            "/v1/audio/speech",
            "/v1/audio/transcriptions",
            "/v1/audio/translations",
            "/v1/images/edits",
            "/v1/images/variations",
        }:
            data = dict(multipart_data or {})
            if multipart_files is not None:
                data["model"] = upstream_model
            return await self._forward_media(
                client, target, upstream_payload, outgoing_headers, route, model_name,
                registered, request_id, started,
                data=data if multipart_files is not None else None, files=multipart_files,
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
        decision_lock = (
            self._decision_lock_for(instance, registered)
            if route in {"/v1/decisions", "/v1/systemone"}
            else None
        )
        if decision_lock is not None:
            await decision_lock.acquire()
        try:
            try:
                response = await client.request(
                    "POST",
                    target,
                    json=upstream_payload,
                    headers=outgoing_headers,
                )
            except (httpx.TimeoutException, httpx.NetworkError, httpx.RemoteProtocolError) as exc:
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
                    retryable=_safe_to_retry(exc),
                ) from exc
            try:
                body = await response.aread()
                if route == "/v1/decisions" and response.status_code < 400:
                    try:
                        raw_decision = json.loads(body)
                        if not isinstance(raw_decision, Mapping):
                            raise AppError("upstream_contract_unsupported", "Jev вернул неверный JSON",
                                           status_code=502)
                        body = json.dumps(adapt_jev_response(
                            raw_decision, model_name, decision_mappings
                        ), ensure_ascii=False).encode("utf-8")
                    except (ValueError, AppError) as exc:
                        await self._journal(request_id=request_id, route=route, model_name=model_name,
                                            registered=registered, status_code=502, started=started,
                                            input_tokens=None, output_tokens=None, stream=False,
                                            error_code="upstream_contract_unsupported")
                        if isinstance(exc, AppError):
                            raise
                        raise AppError("upstream_contract_unsupported", "Jev вернул неверный JSON",
                                       status_code=502) from exc
                input_tokens, output_tokens = _extract_usage(body, response.headers.get("content-type"))
                error_code = None if response.status_code < 400 else "upstream_error"
                if route == "/v1/responses" and response.status_code < 400:
                    try:
                        error_code = _responses_error_code(json.loads(body))
                    except ValueError:
                        error_code = "upstream_error"
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
                    error_code=error_code,
                )
                if route == "/v1/decisions" and response.status_code < 400:
                    return httpx.Response(response.status_code, content=body,
                                          headers={"content-type": "application/json"})
                return _passthrough_response(response.status_code, response.headers, body)
            finally:
                await response.aclose()
        finally:
            try:
                await client.aclose()
            finally:
                if decision_lock is not None:
                    decision_lock.release()

    async def _validate_embedding_route(
        self, candidates: list[RouteCandidate]
    ) -> None:
        if len(candidates) > 1 and any(candidate.connection_id for candidate in candidates):
            raise AppError("embedding_space_unverified", "Для embeddings выберите одного кандидата, если общее пространство моделей не подтверждено", status_code=409)
        profile_ids = tuple(dict.fromkeys(candidate.profile_id for candidate in candidates))
        if self.route_manager is None or len(profile_ids) < 2:
            return
        profiles = {profile.name: profile for profile in
                    await asyncio.to_thread(self.route_manager.profiles)}
        paths = []
        for profile_id in profile_ids:
            profile = profiles.get(profile_id)
            if profile is None or profile.model is None:
                raise AppError("embedding_space_unverified",
                               "Для embeddings alias нужно явно выбрать одну модель во всех профилях",
                               status_code=409)
            paths.append(str(profile.model.path.resolve(strict=False)))
        dimensions = [candidate.metadata.get(
            "dimensions", candidate.metadata.get("embedding_dimensions")
        ) for candidate in candidates]
        known_dimensions = [value for value in dimensions if value is not None]
        spaces = [candidate.metadata.get("embedding_space")
                  for candidate in candidates
                  if candidate.metadata.get("embedding_space") is not None]
        if (len(set(paths)) != 1
                or any(value != known_dimensions[0] for value in known_dimensions[1:])
                or any(value != spaces[0] for value in spaces[1:])):
            raise AppError("embedding_space_mismatch",
                           "Embeddings alias объединяет разные модели или размерности",
                           status_code=409)

    async def _forward_media(
        self, client: httpx.AsyncClient, target: str, payload: Mapping[str, Any],
        headers: Mapping[str, str], route: str, model_name: str, registered: RegisteredModel,
        request_id: str, started: float, *, data: Mapping[str, Any] | None,
        files: Mapping[str, Any] | None,
    ) -> Any:
        # httpx's multipart encoder reads the spooled upload in fixed-size
        # chunks. Neither uploads nor binary/SSE replies are materialised here.
        tracking = _request_tracking.get()
        kwargs = {"data": data, "files": files} if files is not None else {"json": payload}
        context = client.stream("POST", target, headers=headers, **kwargs)
        try:
            response = await context.__aenter__()
        except (httpx.TimeoutException, httpx.NetworkError, httpx.RemoteProtocolError) as exc:
            await client.aclose()
            await self._journal(request_id=request_id, route=route, model_name=model_name,
                                registered=registered, status_code=502, started=started,
                                input_tokens=None, output_tokens=None, stream=True,
                                error_code="upstream_unavailable")
            raise AppError("upstream_unavailable", "Media upstream недоступен", status_code=502,
                           retryable=_safe_to_retry(exc)) from exc

        async def body() -> AsyncIterator[bytes]:
            error_code = None
            try:
                async for chunk in response.aiter_raw(chunk_size=64 * 1024):
                    yield chunk
            except (asyncio.CancelledError, GeneratorExit):
                error_code = "client_disconnected"
                raise
            except httpx.HTTPError:
                error_code = "upstream_unavailable"
                raise
            finally:
                await self._journal(request_id=request_id, route=route, model_name=model_name,
                                    registered=registered, status_code=response.status_code,
                                    started=started, input_tokens=None, output_tokens=None,
                                    stream=True, error_code=error_code or (
                                        "upstream_error" if response.status_code >= 400 else None
                                    ), tracking=tracking)
                await context.__aexit__(None, None, None)
                await client.aclose()

        return _streaming_response(response.status_code, response.headers, body())

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
                retryable=_safe_to_retry(exc),
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
            responses_terminal_seen = False

            def observe(chunk: bytes) -> None:
                nonlocal first_token_ms, error_code, responses_terminal_seen
                pending.extend(chunk)
                while b"\n" in pending:
                    line, _, tail = pending.partition(b"\n")
                    pending[:] = tail
                    if not line.startswith(b"data:"):
                        continue
                    try:
                        event = json.loads(line[5:].strip())
                        if not isinstance(event, dict):
                            continue
                        event_type = event.get("type")
                        if route == "/v1/responses":
                            if event_type in {
                                "response.completed", "response.failed", "response.incomplete",
                            }:
                                responses_terminal_seen = True
                            event_error = _responses_error_code(event)
                            if event_error is not None:
                                error_code = event_error
                        text_delta = any(
                            any(
                                choice.get("delta", {}).get(key)
                                for key in ("content", "reasoning_content", "reasoning")
                            )
                            for choice in event.get("choices", [])
                        )
                        responses_delta = route == "/v1/responses" and event_type in {
                            "response.output_text.delta", "response.reasoning_text.delta",
                            "response.reasoning_summary_text.delta",
                            "response.function_call_arguments.delta",
                            "response.custom_tool_call_input.delta",
                        } and bool(event.get("delta"))
                        if first_token_ms is None and (text_delta or responses_delta):
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
                if (route == "/v1/responses" and not responses_terminal_seen
                        and error_code is None):
                    error_code = "upstream_incomplete"
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
            for key in ("profile_name", "profile_id", "instance_id", "engine_id"):
                extra.pop(key, None)
            extra["finished_at"] = datetime.now(UTC)
            await self._write_journal(
                **extra,
                request_id=request_id,
                route=route,
                model_name=model_name,
                profile_name=registered.profile_name,
                profile_id=registered.profile_id,
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
            for key in ("profile_name", "profile_id", "instance_id", "engine_id"):
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


def _safe_to_retry(exc: Exception) -> bool:
    # Read/write timeouts and connection loss may happen after generation was
    # accepted. Only connection acquisition failures permit alias failover.
    return isinstance(exc, (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout))


def _public_metadata(metadata: Mapping[str, Any]) -> dict[str, Any]:
    result = {key: metadata[key] for key in ("context_length", "reasoning_capabilities")
              if key in metadata}
    capabilities = {**document_capabilities(dict(metadata)).get("capabilities", {}), **{key: metadata[key] for key in
                    ("dimensions", "embedding_dimensions", "embedding_space") if key in metadata}}
    if capabilities:
        result["capabilities"] = capabilities
    return result


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
        values = _usage_values(
            _response_payload(payload).get("usage") if isinstance(payload, dict) else None
        )
        if values != (None, None):
            input_tokens, output_tokens = values
    return input_tokens, output_tokens


def _response_payload(event: dict[str, Any]) -> dict[str, Any]:
    """Responses SSE wraps response metadata and usage in a response object."""
    response = event.get("response")
    event_type = event.get("type")
    if isinstance(event_type, str) and event_type.startswith("response.") and isinstance(response, dict):
        return response
    return event


def _responses_error_code(payload: Any) -> str | None:
    if not isinstance(payload, dict):
        return None
    event_type = payload.get("type")
    status = _response_payload(payload).get("status")
    if event_type in {"error", "response.failed"} or status == "failed":
        return "upstream_error"
    if event_type == "response.incomplete" or status == "incomplete":
        return "upstream_incomplete"
    return None


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
        event = _response_payload(event)
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
