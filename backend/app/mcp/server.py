"""MCP management surface for Neural Deck.

The adapter stays thin: domain validation and side effects are owned by the
existing application services, while this module adds a compact tool contract,
opaque cursors and a stable error envelope.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import os
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from ..errors import AppError
from ..schemas.engines import EngineDefinition, InstallationCreate
from ..schemas.profiles import ProfileData

AsyncOperation = Callable[..., Awaitable[Any]]


@dataclass(frozen=True, slots=True)
class MCPDependencies:
    """Operations supplied by the application composition root."""

    get_system_state: AsyncOperation
    list_models: AsyncOperation
    list_profiles: AsyncOperation
    start_profile: AsyncOperation
    stop_instance: AsyncOperation
    generate_image: AsyncOperation | None = None
    operations: Mapping[str, AsyncOperation] = field(default_factory=dict)


def _value(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json", by_alias=True)
    if isinstance(value, Mapping):
        return {str(key): _value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_value(item) for item in value]
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    return value


def _success(data: Any, request_id: str) -> dict[str, Any]:
    return {"data": _value(data), "request_id": request_id}


def _error(exc: Exception, request_id: str) -> dict[str, Any]:
    if isinstance(exc, AppError):
        return {
            "error": {
                "code": exc.code,
                "message": exc.message,
                "details": _value(exc.details),
                "request_id": request_id,
                "retryable": exc.retryable,
            }
        }
    return {
        "error": {
            "code": "internal_error",
            "message": "Внутренняя ошибка сервиса",
            "details": {},
            "request_id": request_id,
            "retryable": False,
        }
    }


def _call_result(envelope: dict[str, Any], *, is_error: bool) -> Any:
    """Create the SDK result while keeping text and structured output equal."""

    try:
        from mcp.types import CallToolResult, TextContent
    except ImportError:  # pragma: no cover - only minimal source checkouts
        return {
            "structuredContent": envelope,
            "content": [{"type": "text", "text": json.dumps(envelope, ensure_ascii=False)}],
            "isError": is_error,
        }
    return CallToolResult(
        content=[TextContent(type="text", text=json.dumps(envelope, ensure_ascii=False))],
        structuredContent=envelope,
        isError=is_error,
    )


def _default_page_size() -> int:
    raw = os.getenv("MCP_DEFAULT_PAGE_SIZE", "20")
    try:
        value = int(raw)
    except ValueError:
        raise AppError(
            "invalid_mcp_configuration", "MCP_DEFAULT_PAGE_SIZE должен быть числом"
        ) from None
    if value < 1:
        raise AppError("invalid_mcp_configuration", "MCP_DEFAULT_PAGE_SIZE должен быть больше нуля")
    return value


def _page_limit(page_size: int | None) -> int:
    requested = _default_page_size() if page_size is None else page_size
    if requested < 1:
        raise AppError("invalid_page_size", "page_size должен быть больше нуля", status_code=422)
    raw_max = os.getenv("MCP_MAX_PAGE_SIZE", "").strip()
    if raw_max:
        try:
            maximum = int(raw_max)
        except ValueError as exc:
            raise AppError(
                "invalid_mcp_configuration", "MCP_MAX_PAGE_SIZE должен быть числом"
            ) from exc
        if maximum < 1:
            raise AppError("invalid_mcp_configuration", "MCP_MAX_PAGE_SIZE должен быть больше нуля")
        if requested > maximum:
            raise AppError(
                "page_size_exceeded",
                "Запрошенный размер страницы превышает MCP_MAX_PAGE_SIZE",
                status_code=422,
                details={"requested_page_size": requested, "max_page_size": maximum},
            )
    return requested


def _encode_cursor(offset: int, *, context: str = "", snapshot: str | None = None) -> str:
    payload = {"offset": offset}
    if context:
        payload["context"] = context
    if snapshot:
        payload["snapshot"] = snapshot
    payload = json.dumps(payload, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(payload).decode().rstrip("=")


def _decode_cursor(cursor: str | None) -> tuple[int, str | None, str | None]:
    if not cursor:
        return 0, None, None
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded).decode())
        if not isinstance(payload, dict):
            raise ValueError
        offset = payload["offset"]
        if not isinstance(offset, int) or offset < 0:
            raise ValueError
        context = payload.get("context")
        snapshot = payload.get("snapshot")
        if context is not None and not isinstance(context, str):
            raise ValueError
        if snapshot is not None and not isinstance(snapshot, str):
            raise ValueError
        return offset, context, snapshot
    except (
        ValueError,
        KeyError,
        TypeError,
        json.JSONDecodeError,
        binascii.Error,
        UnicodeError,
    ) as exc:
        raise AppError(
            "invalid_cursor", "Курсор пагинации недействителен", status_code=422
        ) from exc


def _page(
    items: Any,
    *,
    page_size: int | None,
    cursor: str | None,
    context: str = "",
    snapshots: dict[str, tuple[float, list[Any], dict[str, Any]]] | None = None,
) -> dict[str, Any]:
    """Page an in-memory service result without a hidden total cap."""

    converted = _value(items)
    metadata: dict[str, Any] = {}
    if isinstance(converted, Mapping):
        raw_items = converted.get("items")
        if isinstance(raw_items, list):
            values = raw_items
            metadata = {
                str(key): value
                for key, value in converted.items()
                if key != "items" and key not in {"next_cursor", "has_more", "page_size"}
            }
        elif isinstance(converted.get("data"), list):
            values = list(converted["data"])
            metadata = {"object": converted.get("object", "list")}
        else:
            values = [converted]
    elif isinstance(converted, Sequence) and not isinstance(converted, (str, bytes, bytearray)):
        values = list(converted)
    else:
        values = [converted]
    limit = _page_limit(page_size)
    offset, cursor_context, snapshot_id = _decode_cursor(cursor)
    now = time.monotonic()
    ttl_raw = os.getenv("MCP_CURSOR_TTL_SECONDS", "900")
    try:
        ttl = max(float(ttl_raw), 1.0)
    except ValueError:
        raise AppError(
            "invalid_mcp_configuration", "MCP_CURSOR_TTL_SECONDS должен быть числом"
        ) from None
    if snapshots is not None:
        for key, (created, _, _) in list(snapshots.items()):
            if now - created >= ttl:
                snapshots.pop(key, None)
    if cursor:
        if cursor_context != context:
            raise AppError(
                "invalid_cursor", "Курсор не соответствует выбранным фильтрам", status_code=422
            )
        if snapshots is not None and snapshot_id:
            stored = snapshots.get(snapshot_id)
            if stored is None:
                raise AppError("cursor_expired", "Срок действия курсора истёк", status_code=422)
            _, values, metadata = stored
    if offset > len(values):
        raise AppError("invalid_cursor", "Курсор выходит за пределы выборки", status_code=422)
    selected = values[offset : offset + limit]
    next_offset = offset + len(selected)
    has_more = next_offset < len(values)
    next_cursor = None
    if has_more:
        if snapshots is not None:
            if snapshot_id is None:
                snapshot_id = hashlib.sha256(
                    f"{context}:{time.monotonic_ns()}".encode()
                ).hexdigest()[:24]
                snapshots[snapshot_id] = (now, values, metadata)
            next_cursor = _encode_cursor(next_offset, context=context, snapshot=snapshot_id)
        else:
            next_cursor = _encode_cursor(next_offset, context=context)
    return {
        **metadata,
        "items": selected,
        "page_size": limit,
        "has_more": has_more,
        "next_cursor": next_cursor,
    }


def _operation(dependencies: MCPDependencies, name: str) -> AsyncOperation:
    operation = dependencies.operations.get(name)
    if operation is not None:
        return operation

    async def missing(*_: Any, **__: Any) -> Any:
        raise AppError("unsupported_tool", f"Тула {name} недоступна в этой сборке", status_code=501)

    return missing


def _paged_operation(
    dependencies: MCPDependencies,
    name: str,
    page_size: int | None,
    cursor: str | None,
    snapshots: dict[str, tuple[float, list[Any], dict[str, Any]]] | None = None,
) -> AsyncOperation:
    operation = _operation(dependencies, name)

    async def run(*args: Any, **kwargs: Any) -> Any:
        result = await operation(*args, **kwargs)
        context_payload = {"name": name, "args": _value(args), "kwargs": _value(kwargs)}
        context = hashlib.sha256(
            json.dumps(context_payload, sort_keys=True, default=str, separators=(",", ":")).encode()
        ).hexdigest()
        return _page(
            result,
            page_size=page_size,
            cursor=cursor,
            context=context,
            snapshots=snapshots,
        )

    return run


def create_mcp_server(
    dependencies: MCPDependencies,
    *,
    streamable_http_path: str = "/mcp",
) -> Any:
    """Build a stateless ``FastMCP`` server with the management tool catalog."""

    try:
        from mcp.server.fastmcp import FastMCP
        from mcp.server.fastmcp import server as fastmcp_server_module
        from mcp.server.transport_security import TransportSecuritySettings
        from mcp.types import ToolAnnotations
    except ImportError as exc:  # pragma: no cover - dependency boundary
        raise RuntimeError("официальный пакет MCP SDK не установлен") from exc

    fastmcp_server_module.Settings.model_rebuild(
        force=True,
        _types_namespace=vars(fastmcp_server_module),
    )
    server = FastMCP(
        "Neural Deck",
        stateless_http=True,
        json_response=True,
        streamable_http_path=streamable_http_path,
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
    )
    cursor_snapshots: dict[str, tuple[float, list[Any], dict[str, Any]]] = {}

    async def invoke(operation: AsyncOperation, *args: Any, **kwargs: Any) -> Any:
        request_id = f"req_{uuid4().hex}"
        try:
            result = await operation(*args, **kwargs)
        except Exception as exc:  # tool boundary must use the common envelope
            return _call_result(_error(exc, request_id), is_error=True)
        return _call_result(_success(result, request_id), is_error=False)

    readonly = ToolAnnotations(readOnlyHint=True, idempotentHint=True)
    mutation = ToolAnnotations(readOnlyHint=False, destructiveHint=False)
    destructive = ToolAnnotations(readOnlyHint=False, destructiveHint=True)

    @server.tool(
        name="get_system_state",
        description="Состояние железа и запущенных экземпляров.",
        annotations=readonly,
    )
    async def get_system_state() -> Any:
        return await invoke(dependencies.get_system_state)

    @server.tool(
        name="list_models",
        description="Зарегистрированные модели активных экземпляров.",
        annotations=readonly,
    )
    async def list_models() -> Any:
        return await invoke(dependencies.list_models)

    @server.tool(name="list_profiles", description="Профили запуска.", annotations=readonly)
    async def list_profiles() -> Any:
        return await invoke(dependencies.list_profiles)

    @server.tool(
        name="start_profile", description="Запустить сохранённый профиль.", annotations=mutation
    )
    async def start_profile(profile_name: str) -> Any:
        return await invoke(dependencies.start_profile, profile_name)

    @server.tool(
        name="stop_instance", description="Остановить экземпляр модели.", annotations=destructive
    )
    async def stop_instance(instance_id: str) -> Any:
        return await invoke(dependencies.stop_instance, instance_id)

    if dependencies.generate_image is not None:

        @server.tool(
            name="generate_image",
            description="Создать изображение через доступный image-профиль.",
            annotations=mutation,
        )
        async def generate_image(
            prompt: str,
            model: str | None = None,
            size: str = "1024x1024",
            seed: int | None = None,
            steps: int = 25,
            transparent: bool = False,
        ) -> Any:
            return await invoke(
                dependencies.generate_image,
                prompt=prompt,
                model=model,
                size=size,
                seed=seed,
                steps=steps,
                transparent=transparent,
            )

    @server.tool(
        name="nd_get_status", description="Состояние компонентов Neural Deck.", annotations=readonly
    )
    async def nd_get_status() -> Any:
        return await invoke(_operation(dependencies, "get_status"))

    @server.tool(
        name="nd_get_hardware", description="Текущая загрузка CPU, RAM и GPU.", annotations=readonly
    )
    async def nd_get_hardware() -> Any:
        return await invoke(_operation(dependencies, "get_hardware"))

    @server.tool(
        name="nd_list_instances",
        description="Постраничный список запущенных экземпляров.",
        annotations=readonly,
    )
    async def nd_list_instances(
        profile_id: str | None = None,
        state: str | None = None,
        page_size: int | None = None,
        cursor: str | None = None,
    ) -> Any:
        return await invoke(
            _paged_operation(dependencies, "list_instances", page_size, cursor, cursor_snapshots),
            profile_id=profile_id,
            state=state,
        )

    @server.tool(
        name="nd_get_instance", description="Детали одного экземпляра.", annotations=readonly
    )
    async def nd_get_instance(instance_id: str) -> Any:
        return await invoke(_operation(dependencies, "get_instance"), instance_id)

    @server.tool(
        name="nd_stop_instance",
        description="Остановить экземпляр с проверкой его идентичности.",
        annotations=destructive,
    )
    async def nd_stop_instance(instance_id: str) -> Any:
        return await invoke(_operation(dependencies, "stop_instance"), instance_id)

    @server.tool(
        name="nd_list_engines",
        description="Постраничный список обнаруженных движков.",
        annotations=readonly,
    )
    async def nd_list_engines(page_size: int | None = None, cursor: str | None = None) -> Any:
        return await invoke(
            _paged_operation(dependencies, "list_engines", page_size, cursor, cursor_snapshots)
        )

    @server.tool(
        name="nd_list_engine_definitions",
        description="Постраничный список определений движков.",
        annotations=readonly,
    )
    async def nd_list_engine_definitions(
        page_size: int | None = None, cursor: str | None = None
    ) -> Any:
        return await invoke(
            _paged_operation(
                dependencies, "list_engine_definitions", page_size, cursor, cursor_snapshots
            )
        )

    @server.tool(
        name="nd_list_engine_definition_issues",
        description="Ошибки чтения определений движков.",
        annotations=readonly,
    )
    async def nd_list_engine_definition_issues(
        page_size: int | None = None, cursor: str | None = None
    ) -> Any:
        return await invoke(
            _paged_operation(
                dependencies, "list_engine_definition_issues", page_size, cursor, cursor_snapshots
            )
        )

    @server.tool(
        name="nd_get_engine_definition",
        description="Полное определение движка.",
        annotations=readonly,
    )
    async def nd_get_engine_definition(engine_id: str) -> Any:
        return await invoke(_operation(dependencies, "get_engine_definition"), engine_id)

    @server.tool(
        name="nd_save_engine_definition",
        description="Создать или обновить YAML-определение движка.",
        annotations=mutation,
    )
    async def nd_save_engine_definition(
        mode: Literal["create", "update"], definition: EngineDefinition
    ) -> Any:
        return await invoke(
            _operation(dependencies, "save_engine_definition"),
            mode,
            definition.model_dump(mode="json"),
        )

    @server.tool(
        name="nd_delete_engine_definition",
        description="Удалить пользовательское определение движка.",
        annotations=destructive,
    )
    async def nd_delete_engine_definition(engine_id: str) -> Any:
        return await invoke(_operation(dependencies, "delete_engine_definition"), engine_id)

    @server.tool(
        name="nd_list_installations",
        description="Постраничный список установок движков.",
        annotations=readonly,
    )
    async def nd_list_installations(
        engine_id: str | None = None, page_size: int | None = None, cursor: str | None = None
    ) -> Any:
        return await invoke(
            _paged_operation(
                dependencies, "list_installations", page_size, cursor, cursor_snapshots
            ),
            engine_id=engine_id,
        )

    @server.tool(
        name="nd_install_engine",
        description="Установка движка с operation_id.",
        annotations=mutation,
    )
    async def nd_install_engine(
        engine_id: str, installation: InstallationCreate, expected_version: str | None = None
    ) -> Any:
        return await invoke(
            _operation(dependencies, "install_engine"),
            engine_id,
            installation.model_dump(mode="json"),
            expected_version,
        )

    @server.tool(
        name="nd_update_installation",
        description="Обновить установку движка.",
        annotations=mutation,
    )
    async def nd_update_installation(
        installation_id: str, expected_version: str | None = None
    ) -> Any:
        return await invoke(
            _operation(dependencies, "update_installation"), installation_id, expected_version
        )

    @server.tool(
        name="nd_run_engine_maintenance",
        description="Выполнить maintenance-команду установки.",
        annotations=mutation,
    )
    async def nd_run_engine_maintenance(
        installation_id: str, command: str, expected_version: str | None = None
    ) -> Any:
        return await invoke(
            _operation(dependencies, "run_engine_maintenance"),
            installation_id,
            command,
            expected_version,
        )

    @server.tool(
        name="nd_delete_installation",
        description="Удалить регистрацию установки движка.",
        annotations=destructive,
    )
    async def nd_delete_installation(installation_id: str) -> Any:
        return await invoke(_operation(dependencies, "delete_installation"), installation_id)

    @server.tool(
        name="nd_scan_engines", description="Просканировать доступные движки.", annotations=mutation
    )
    async def nd_scan_engines() -> Any:
        return await invoke(_operation(dependencies, "scan_engines"))

    @server.tool(
        name="nd_list_operations",
        description="Постраничный список длительных операций.",
        annotations=readonly,
    )
    async def nd_list_operations(
        kind: str | None = None,
        state: str | None = None,
        page_size: int | None = None,
        cursor: str | None = None,
    ) -> Any:
        return await invoke(
            _paged_operation(dependencies, "list_operations", page_size, cursor, cursor_snapshots),
            kind=kind,
            state=state,
        )

    @server.tool(
        name="nd_get_operation",
        description="Состояние операции по operation_id.",
        annotations=readonly,
    )
    async def nd_get_operation(operation_id: str) -> Any:
        return await invoke(_operation(dependencies, "get_operation"), operation_id)

    @server.tool(
        name="nd_cancel_operation",
        description="Отменить выполняемую операцию.",
        annotations=destructive,
    )
    async def nd_cancel_operation(operation_id: str) -> Any:
        return await invoke(_operation(dependencies, "cancel_operation"), operation_id)

    @server.tool(
        name="nd_list_profiles",
        description="Постраничный список профилей запуска.",
        annotations=readonly,
    )
    async def nd_list_profiles(
        query: str | None = None, page_size: int | None = None, cursor: str | None = None
    ) -> Any:
        return await invoke(
            _paged_operation(dependencies, "list_profiles", page_size, cursor, cursor_snapshots),
            query=query,
        )

    @server.tool(
        name="nd_list_profile_issues",
        description="Постраничный список ошибок профилей.",
        annotations=readonly,
    )
    async def nd_list_profile_issues(
        page_size: int | None = None, cursor: str | None = None
    ) -> Any:
        return await invoke(
            _paged_operation(
                dependencies, "list_profile_issues", page_size, cursor, cursor_snapshots
            )
        )

    @server.tool(
        name="nd_get_profile", description="Полная конфигурация профиля.", annotations=readonly
    )
    async def nd_get_profile(profile_id: str) -> Any:
        return await invoke(_operation(dependencies, "get_profile"), profile_id)

    @server.tool(
        name="nd_save_profile",
        description="Создать или обновить профиль в YAML.",
        annotations=mutation,
    )
    async def nd_save_profile(
        mode: Literal["create", "update"], profile: ProfileData, profile_id: str | None = None
    ) -> Any:
        return await invoke(
            _operation(dependencies, "save_profile"),
            mode,
            profile.model_dump(mode="json"),
            profile_id,
        )

    @server.tool(name="nd_delete_profile", description="Удалить профиль.", annotations=destructive)
    async def nd_delete_profile(profile_id: str) -> Any:
        return await invoke(_operation(dependencies, "delete_profile"), profile_id)

    @server.tool(
        name="nd_validate_profile",
        description="Проверить профиль без запуска и сохранения.",
        annotations=readonly,
    )
    async def nd_validate_profile(profile: ProfileData) -> Any:
        return await invoke(
            _operation(dependencies, "validate_profile"), profile.model_dump(mode="json")
        )

    @server.tool(
        name="nd_preview_profile",
        description="Сформировать команду профиля без запуска.",
        annotations=readonly,
    )
    async def nd_preview_profile(profile: ProfileData) -> Any:
        return await invoke(
            _operation(dependencies, "preview_profile"), profile.model_dump(mode="json")
        )

    @server.tool(
        name="nd_format_profile_script",
        description="Отформатировать script профиля.",
        annotations=readonly,
    )
    async def nd_format_profile_script(script: str) -> Any:
        return await invoke(_operation(dependencies, "format_profile_script"), script)

    @server.tool(
        name="nd_start_profile", description="Запустить сохранённый профиль.", annotations=mutation
    )
    async def nd_start_profile(profile_id: str) -> Any:
        return await invoke(_operation(dependencies, "start_profile"), profile_id)

    @server.tool(
        name="nd_list_local_models",
        description="Постраничный список локальных моделей.",
        annotations=readonly,
    )
    async def nd_list_local_models(
        query: str | None = None,
        format: str | None = None,
        model_kind: str | None = None,
        page_size: int | None = None,
        cursor: str | None = None,
    ) -> Any:
        return await invoke(
            _paged_operation(
                dependencies, "list_local_models", page_size, cursor, cursor_snapshots
            ),
            query=query,
            format=format,
            model_kind=model_kind,
        )

    @server.tool(
        name="nd_get_local_model",
        description="Локальная модель без вложенного каталога.",
        annotations=readonly,
    )
    async def nd_get_local_model(model_id: str) -> Any:
        return await invoke(_operation(dependencies, "get_local_model"), model_id)

    @server.tool(
        name="nd_list_local_model_contents",
        description="Постраничный список файлов локальной модели.",
        annotations=readonly,
    )
    async def nd_list_local_model_contents(
        model_id: str,
        kind: Literal["groups", "variants", "auxiliary"] = "groups",
        group_id: str | None = None,
        page_size: int | None = None,
        cursor: str | None = None,
    ) -> Any:
        return await invoke(
            _paged_operation(
                dependencies, "list_local_model_contents", page_size, cursor, cursor_snapshots
            ),
            model_id,
            kind,
            group_id,
        )

    @server.tool(
        name="nd_search_hub_models",
        description="Постраничный поиск или trending-модели Hugging Face.",
        annotations=readonly,
    )
    async def nd_search_hub_models(
        mode: Literal["search", "trending"] = "search",
        query: str | None = None,
        group: str = "all",
        page_size: int | None = None,
        cursor: str | None = None,
    ) -> Any:
        return await invoke(
            _paged_operation(
                dependencies, "search_hub_models", page_size, cursor, cursor_snapshots
            ),
            mode,
            query,
            group,
        )

    @server.tool(
        name="nd_get_hub_model",
        description="Метаданные Hub-репозитория и revision.",
        annotations=readonly,
    )
    async def nd_get_hub_model(repo_id: str, revision: str | None = None) -> Any:
        return await invoke(_operation(dependencies, "get_hub_model"), repo_id, revision)

    @server.tool(
        name="nd_read_model_readme",
        description="Постранично прочитать README модели.",
        annotations=readonly,
    )
    async def nd_read_model_readme(
        repo_id: str, revision: str, max_chars: int | None = None, cursor: str | None = None
    ) -> Any:
        return await invoke(
            _operation(dependencies, "read_model_readme"), repo_id, revision, max_chars, cursor
        )

    @server.tool(
        name="nd_list_hub_model_contents",
        description="Постраничный список файлов или quantizations Hub-модели.",
        annotations=readonly,
    )
    async def nd_list_hub_model_contents(
        repo_id: str,
        revision: str | None = None,
        kind: Literal["files", "quantizations"] = "files",
        page_size: int | None = None,
        cursor: str | None = None,
    ) -> Any:
        return await invoke(
            _paged_operation(
                dependencies, "list_hub_model_contents", page_size, cursor, cursor_snapshots
            ),
            repo_id,
            revision,
            kind,
        )

    @server.tool(
        name="nd_download_model",
        description="Скачать модель и получить operation_id.",
        annotations=mutation,
    )
    async def nd_download_model(
        repo_id: str, revision: str, files: list[str] | None = None, label: str | None = None
    ) -> Any:
        return await invoke(
            _operation(dependencies, "download_model"), repo_id, revision, files, label
        )

    @server.tool(
        name="nd_scan_local_models",
        description="Сканировать локальный каталог моделей.",
        annotations=mutation,
    )
    async def nd_scan_local_models(verify_hashes: bool = False) -> Any:
        return await invoke(_operation(dependencies, "scan_local_models"), verify_hashes)

    @server.tool(
        name="nd_check_model_updates",
        description="Проверить обновления локальных моделей.",
        annotations=mutation,
    )
    async def nd_check_model_updates(model_id: str | None = None) -> Any:
        return await invoke(_operation(dependencies, "check_model_updates"), model_id)

    @server.tool(
        name="nd_list_model_updates",
        description="Постраничный список результатов проверки обновлений.",
        annotations=readonly,
    )
    async def nd_list_model_updates(
        model_id: str | None = None, page_size: int | None = None, cursor: str | None = None
    ) -> Any:
        return await invoke(
            _paged_operation(
                dependencies, "list_model_updates", page_size, cursor, cursor_snapshots
            ),
            model_id,
        )

    @server.tool(
        name="nd_delete_local_model",
        description="Удалить локальную модель или её группу.",
        annotations=destructive,
    )
    async def nd_delete_local_model(model_id: str, group_id: str | None = None) -> Any:
        return await invoke(_operation(dependencies, "delete_local_model"), model_id, group_id)

    @server.tool(
        name="nd_get_statistics",
        description="Статистика запросов, токенов и стоимости.",
        annotations=readonly,
    )
    async def nd_get_statistics(
        period: str = "month",
        from_: str | None = None,
        to: str | None = None,
        profile_id: str | None = None,
        model_name: str | None = None,
    ) -> Any:
        return await invoke(
            _operation(dependencies, "get_statistics"), period, from_, to, profile_id, model_name
        )

    @server.tool(
        name="nd_list_statistics_rows",
        description="Постраничные строки статистики по профилю, дню или часу.",
        annotations=readonly,
    )
    async def nd_list_statistics_rows(
        group_by: Literal["profile", "client", "day", "hour"] = "day",
        period: str = "month",
        from_: str | None = None,
        to: str | None = None,
        profile_id: str | None = None,
        model_name: str | None = None,
        page_size: int | None = None,
        cursor: str | None = None,
    ) -> Any:
        return await invoke(
            _paged_operation(
                dependencies, "list_statistics_rows", page_size, cursor, cursor_snapshots
            ),
            group_by,
            period,
            from_,
            to,
            profile_id,
            model_name,
        )

    @server.tool(
        name="nd_get_profile_summary",
        description="Показатели профиля и период измерения.",
        annotations=readonly,
    )
    async def nd_get_profile_summary(
        profile_id: str | None = None, profile_name: str | None = None
    ) -> Any:
        return await invoke(
            _operation(dependencies, "get_profile_summary"), profile_id, profile_name
        )

    @server.tool(
        name="nd_get_instance_metrics",
        description="Текущие или исторические метрики экземпляра.",
        annotations=readonly,
    )
    async def nd_get_instance_metrics(
        instance_id: str,
        mode: Literal["current", "history", "peaks"] = "current",
        from_: str | None = None,
        to: str | None = None,
        page_size: int | None = None,
        cursor: str | None = None,
    ) -> Any:
        return await invoke(
            _paged_operation(
                dependencies, "get_instance_metrics", page_size, cursor, cursor_snapshots
            ),
            instance_id,
            mode,
            from_,
            to,
        )

    @server.tool(
        name="nd_list_log_sources",
        description="Постраничный список источников логов.",
        annotations=readonly,
    )
    async def nd_list_log_sources(
        kind: str | None = None,
        profile_id: str | None = None,
        page_size: int | None = None,
        cursor: str | None = None,
    ) -> Any:
        return await invoke(
            _paged_operation(dependencies, "list_log_sources", page_size, cursor, cursor_snapshots),
            kind,
            profile_id,
        )

    @server.tool(
        name="nd_read_log",
        description="Прочитать страницу строк лога с продолжением cursor.",
        annotations=readonly,
    )
    async def nd_read_log(
        source_id: str,
        direction: Literal["tail", "forward"] = "tail",
        page_size: int | None = None,
        cursor: str | None = None,
    ) -> Any:
        return await invoke(
            _operation(dependencies, "read_log"), source_id, direction, page_size, cursor
        )

    @server.tool(
        name="nd_export_log", description="Подготовить выгрузку лога.", annotations=readonly
    )
    async def nd_export_log(source_id: str, latest_only: bool = False) -> Any:
        return await invoke(_operation(dependencies, "export_log"), source_id, latest_only)

    @server.tool(
        name="nd_delete_log_source", description="Удалить источник логов.", annotations=destructive
    )
    async def nd_delete_log_source(source_id: str) -> Any:
        return await invoke(_operation(dependencies, "delete_log_source"), source_id)

    @server.tool(
        name="nd_list_chat_models",
        description="Постраничный список текстовых моделей для чата.",
        annotations=readonly,
    )
    async def nd_list_chat_models(page_size: int | None = None, cursor: str | None = None) -> Any:
        return await invoke(
            _paged_operation(dependencies, "list_chat_models", page_size, cursor, cursor_snapshots)
        )

    @server.tool(
        name="nd_chat",
        description="Один полноценный запрос к выбранной модели.",
        annotations=mutation,
    )
    async def nd_chat(
        model: str,
        messages: list[dict[str, Any]],
        reasoning_effort: str = "auto",
        max_output_tokens: int | None = None,
        include_reasoning: bool = False,
    ) -> Any:
        return await invoke(
            _operation(dependencies, "chat"),
            model,
            messages,
            reasoning_effort,
            max_output_tokens,
            include_reasoning,
        )

    @server.tool(
        name="nd_list_proxy_routes",
        description="Постраничный список маршрутов и алиасов прокси.",
        annotations=readonly,
    )
    async def nd_list_proxy_routes(page_size: int | None = None, cursor: str | None = None) -> Any:
        return await invoke(
            _paged_operation(dependencies, "list_proxy_routes", page_size, cursor, cursor_snapshots)
        )

    @server.tool(
        name="nd_list_proxy_candidates",
        description="Постраничные кандидаты маршрута.",
        annotations=readonly,
    )
    async def nd_list_proxy_candidates(
        route_name: str, page_size: int | None = None, cursor: str | None = None
    ) -> Any:
        return await invoke(
            _paged_operation(
                dependencies, "list_proxy_candidates", page_size, cursor, cursor_snapshots
            ),
            route_name,
        )

    @server.tool(
        name="nd_read_proxy_diagnostics",
        description="Диагностика маршрута прокси.",
        annotations=readonly,
    )
    async def nd_read_proxy_diagnostics(
        route_name: str, page_size: int | None = None, cursor: str | None = None
    ) -> Any:
        return await invoke(
            _paged_operation(
                dependencies, "read_proxy_diagnostics", page_size, cursor, cursor_snapshots
            ),
            route_name,
        )

    @server.tool(
        name="nd_set_proxy_route_order",
        description="Изменить порядок профилей маршрута.",
        annotations=mutation,
    )
    async def nd_set_proxy_route_order(route_name: str, profile_ids: list[str]) -> Any:
        return await invoke(
            _operation(dependencies, "set_proxy_route_order"), route_name, profile_ids
        )

    @server.tool(
        name="nd_save_proxy_alias",
        description="Создать или изменить алиас прокси.",
        annotations=mutation,
    )
    async def nd_save_proxy_alias(
        mode: Literal["create", "update"],
        name: str,
        profile_ids: list[str],
        previous_name: str | None = None,
    ) -> Any:
        return await invoke(
            _operation(dependencies, "save_proxy_alias"), mode, name, profile_ids, previous_name
        )

    @server.tool(
        name="nd_delete_proxy_alias", description="Удалить алиас прокси.", annotations=destructive
    )
    async def nd_delete_proxy_alias(name: str) -> Any:
        return await invoke(_operation(dependencies, "delete_proxy_alias"), name)

    @server.tool(
        name="nd_get_settings",
        description="Эффективные пути и порты приложения.",
        annotations=readonly,
    )
    async def nd_get_settings() -> Any:
        return await invoke(_operation(dependencies, "get_settings"))

    @server.tool(
        name="nd_update_settings",
        description="Изменить models_root с проверкой существующего API.",
        annotations=mutation,
    )
    async def nd_update_settings(models_root: str) -> Any:
        return await invoke(_operation(dependencies, "update_settings"), models_root)

    return server


def build_mcp_app(dependencies: MCPDependencies) -> tuple[Any, Any]:
    """Return ``(FastMCP server, ASGI app)`` for mounting at ``/mcp``."""

    server = create_mcp_server(dependencies)
    return server, server.streamable_http_app()


def mount_mcp(app: Any, dependencies: MCPDependencies) -> Any:
    """Mount the SDK app at ``/mcp`` while retaining its own HTTP lifecycle."""

    server = create_mcp_server(dependencies, streamable_http_path="/")
    app.mount("/mcp", server.streamable_http_app())
    app.state.mcp_server = server
    return server


@asynccontextmanager
async def mcp_lifespan(server: Any) -> AsyncIterator[None]:
    """Context manager to add to the host FastAPI lifespan."""

    async with server.session_manager.run():
        yield


__all__ = ["MCPDependencies", "build_mcp_app", "create_mcp_server", "mcp_lifespan", "mount_mcp"]
