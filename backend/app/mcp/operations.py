"""Application service adapters used by the MCP tools."""

from __future__ import annotations

import asyncio
import hashlib
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import quote

import yaml  # type: ignore[import-untyped]
from pydantic import TypeAdapter, ValidationError

from ..chat.service import ChatService, ChatTurnRequest
from ..errors import AppError
from ..logs.service import LogsService
from ..models.catalogue import ModelCatalogue
from ..paths import build_paths, effective_settings, prepare_data_dirs, validate_models_root
from ..profiles.summary import build_profile_summary, utc_day_window
from ..schemas.engines import EngineDefinition, InstallationCreate, MaintenanceRequest
from ..schemas.models import DownloadRequest
from ..schemas.profiles import ProfileData
from ..system import collect_status, collect_system_data
from .server import _decode_cursor, _encode_cursor, _value
from .proxy_management import build_proxy_operations

CAPABILITY_TYPE_NOTE = (
    "types описывает основную задачу и результат движка, а не все модальности входа. "
    "Например, text может принимать изображения или видео у мультимодальной модели, "
    "но результатом остаётся текст; это не image/video generation."
)

CAPABILITY_TYPE_LEGEND: dict[str, str] = {
    "text": "Генерация текста (chat/completions); результат — текст.",
    "tts": "Синтез речи: текст -> аудио.",
    "asr": "Распознавание речи: аудио -> текст.",
    "embeddings": "Векторные представления: вход -> embedding-векторы.",
    "image": "Генерация или обработка изображений; результат — изображение.",
    "ocr": "Распознавание документов; результат — текст.",
    "video": "Генерация или обработка видео; результат — видео.",
    "decision": "Структурированные решения: вход и вопросы -> ответы решения.",
}


def _capability_metadata(types: Any = None) -> dict[str, Any]:
    selected = types if isinstance(types, list) else list(CAPABILITY_TYPE_LEGEND)
    return {
        "types_note": CAPABILITY_TYPE_NOTE,
        "type_legend": {
            capability: CAPABILITY_TYPE_LEGEND[capability]
            for capability in selected
            if capability in CAPABILITY_TYPE_LEGEND
        },
    }


def _dump(value: Any) -> Any:
    return _value(value)


def _instance_summary(value: Any, *, detailed: bool = False) -> dict[str, Any]:
    data = _dump(value)
    if not isinstance(data, dict):
        return {"id": str(data)}
    fields = (
        "id",
        "source",
        "profile_id",
        "profile_name",
        "engine_id",
        "model_name",
        "state",
        "pid",
        "port",
        "can_stop",
        "observed_at",
    )
    if detailed:
        fields += ("types", "api_protocol", "api_base_url", "ui_url", "identity_verified")
    result = {key: data.get(key) for key in fields if key in data}
    if detailed and "types" in result:
        result.update(_capability_metadata(result["types"]))
    return result


def _operation_summary(value: Any) -> dict[str, Any]:
    data = _dump(value)
    if not isinstance(data, dict):
        return {"id": str(data)}
    fields = (
        "id",
        "kind",
        "state",
        "target_id",
        "label",
        "created_at",
        "started_at",
        "finished_at",
        "progress",
        "error",
        "exit_code",
        "checked_at",
        "result",
        "log_source_id",
    )
    result = {key: data.get(key) for key in fields if key in data}
    if "id" in result:
        # ``id`` remains for compatibility with the existing service models;
        # MCP callers can use the explicit name from the public contract.
        result["operation_id"] = result["id"]
    return result


def _compact_model(value: Any) -> dict[str, Any]:
    data = _dump(value)
    if not isinstance(data, dict):
        return {"id": str(data)}
    fields = (
        "id",
        "model_name",
        "path",
        "kind",
        "model_kind",
        "format",
        "compatibility",
        "vendor",
        "size_bytes",
        "hf_match",
        "supporting_files",
    )
    return {key: data.get(key) for key in fields if key in data}


def _compact_hub_model(value: Any) -> dict[str, Any]:
    data = _dump(value)
    if not isinstance(data, dict):
        return {"repo_id": str(data)}
    fields = (
        "repo_id",
        "revision",
        "author",
        "description",
        "tags",
        "downloads",
        "likes",
        "trending_score",
        "used_storage",
        "pipeline_tag",
        "library_name",
        "created_at",
        "last_modified",
    )
    return {key: data.get(key) for key in fields if key in data}


def _compact_profile(value: Any, runtime_state: str | None = None) -> dict[str, Any]:
    data = _dump(value)
    if not isinstance(data, dict):
        return {"name": str(data)}
    engine = data.get("engine") if isinstance(data.get("engine"), dict) else {}
    model = data.get("model") if isinstance(data.get("model"), dict) else {}
    result = {
        "profile_id": data.get("profile_id"),
        "name": data.get("name"),
        "model_name": data.get("model_name"),
        "model_path": model.get("path"),
        "engine_id": engine.get("id"),
        "installation_id": engine.get("installation_id"),
        "port": data.get("port"),
        "autostart": data.get("autostart", False),
    }
    if runtime_state is not None:
        result["runtime_state"] = runtime_state
    return result


def _compact_statistics_row(value: Any) -> dict[str, Any]:
    data = _dump(value)
    if not isinstance(data, dict):
        return {"value": data}
    tokens = data.get("tokens") if isinstance(data.get("tokens"), dict) else {}
    cost = data.get("cost") if isinstance(data.get("cost"), dict) else {}
    result = {
        key: data[key]
        for key in (
            "date",
            "hour",
            "profile_id",
            "profile_name",
            "client_id",
            "label",
        )
        if key in data
    }
    result.update(
        {
            "requests": data.get("requests"),
            "input_tokens": tokens.get("input"),
            "cached_input_tokens": tokens.get("cached_input"),
            "output_tokens": tokens.get("output"),
            "cost": {
                "known_total_usd": cost.get("usd"),
                "unknown_requests": cost.get("unknown_requests"),
                "state": cost.get("state"),
            },
        }
    )
    return result


def _parse_datetime(value: str | None, field: str) -> datetime | None:
    if value is None:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise AppError(
            "validation_error", f"Поле {field} должно быть ISO datetime", status_code=422
        ) from exc
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _validate_model(model: dict[str, Any]) -> Any:
    try:
        return ProfileData.model_validate(model)
    except ValidationError as exc:
        raise AppError(
            "validation_error",
            "Профиль не прошёл проверку схемы",
            status_code=422,
            details={
                "errors": [{"loc": error["loc"], "message": error["msg"]} for error in exc.errors()]
            },
        ) from exc


def build_operations(app: Any) -> dict[str, Any]:
    """Build all MCP operations around the already composed application."""

    async def get_status() -> Any:
        runtime = app.state.runtime
        return await collect_status(
            runtime.db_engine,
            diagnostics=runtime.diagnostic_dicts(),
            openai_api_available=runtime.proxy_service is not None,
            openai_api_models=runtime.model_count_for_status(),
            journal_error=runtime.journal_service.last_error,
        )

    async def get_hardware() -> Any:
        data = await collect_system_data()
        return {
            "sampled_at": datetime.now(UTC),
            "cpu": data.cpu,
            "ram": data.ram,
            "gpus": data.gpus,
        }

    async def list_instances(profile_id: str | None = None, state: str | None = None) -> list[Any]:
        values = (await app.state.runtime.process_manager.refresh()).items
        return [
            _instance_summary(item)
            for item in values
            if (profile_id is None or item.profile_id == profile_id)
            and (state is None or item.state == state)
        ]

    async def get_instance(instance_id: str) -> Any:
        return _instance_summary(
            await app.state.runtime.process_manager.get(instance_id), detailed=True
        )

    async def stop_instance(instance_id: str) -> Any:
        return _operation_summary(await app.state.runtime.process_manager.stop(instance_id))

    async def list_engines() -> dict[str, Any]:
        values = await asyncio.to_thread(app.state.runtime.engine_service.engines)
        types = sorted({capability for item in values for capability in item.types})
        return {
            "items": [
                {
                    "id": item.id,
                    "display_name": item.display_name,
                    "types": item.types,
                    "installation_kinds": item.installation_kinds,
                    "installation_count": len(item.installations),
                }
                for item in values
            ],
            **_capability_metadata(types),
        }

    async def list_engine_definitions() -> dict[str, Any]:
        data = app.state.runtime.engine_service.list_definitions()
        types = sorted({capability for item in data.items for capability in item.types})
        # Diagnostics have a dedicated paginated tool.  Keeping them out of
        # every definition page avoids duplicating them in a large catalogue.
        return {
            "items": [
                {"id": item.id, "name": item.name, "types": item.types} for item in data.items
            ],
            "issue_count": len(data.issues),
            **_capability_metadata(types),
        }

    async def list_engine_definition_issues() -> list[Any]:
        return app.state.runtime.engine_service.list_definitions().issues

    async def get_engine_definition(engine_id: str) -> Any:
        data = _dump(app.state.runtime.engine_service.get_definition(engine_id))
        if isinstance(data, dict):
            data.update(_capability_metadata(data.get("types")))
        return data

    async def save_engine_definition(mode: str, definition: dict[str, Any]) -> Any:
        try:
            model = EngineDefinition.model_validate(definition)
        except ValidationError as exc:
            raise AppError(
                "validation_error",
                "Определение движка не прошло проверку",
                status_code=422,
                details={"errors": exc.errors()},
            ) from exc
        service = app.state.runtime.engine_service
        if mode == "create":
            saved = service.create_definition(model)
            return {"engine_id": saved.id, "created": True, "changed_fields": list(definition)}
        if mode == "update":
            saved = service.update_definition(model.id, model)
            return {"engine_id": saved.id, "created": False, "changed_fields": list(definition)}
        raise AppError("validation_error", "mode должен быть create или update", status_code=422)

    async def delete_engine_definition(engine_id: str) -> Any:
        app.state.runtime.engine_service.delete_definition(engine_id)
        return {"engine_id": engine_id, "deleted": True}

    async def list_installations(engine_id: str | None = None) -> list[Any]:
        values = await asyncio.to_thread(app.state.runtime.engine_service.installations)
        return [_dump(item) for item in values if engine_id is None or item.engine_id == engine_id]

    async def install_engine(
        engine_id: str, installation: dict[str, Any], expected_version: str | None = None
    ) -> Any:
        try:
            request = TypeAdapter(InstallationCreate).validate_python(installation)
        except ValidationError as exc:
            raise AppError(
                "validation_error",
                "Параметры установки не прошли проверку",
                status_code=422,
                details={"errors": exc.errors()},
            ) from exc
        result = await app.state.runtime.engine_service.create_installation(engine_id, request)
        return {**_operation_summary(result), "expected_version": expected_version}

    async def update_installation(installation_id: str, expected_version: str | None = None) -> Any:
        result = await app.state.runtime.engine_service.update_installation(installation_id)
        return {**_operation_summary(result), "expected_version": expected_version}

    async def run_engine_maintenance(
        installation_id: str, command: str, expected_version: str | None = None
    ) -> Any:
        request = MaintenanceRequest(command=command)
        result = await app.state.runtime.engine_service.maintenance(
            installation_id, request.command
        )
        return {**_operation_summary(result), "expected_version": expected_version}

    async def delete_installation(installation_id: str) -> Any:
        await asyncio.to_thread(
            app.state.runtime.engine_service.forget_installation, installation_id
        )
        return {"installation_id": installation_id, "deleted": True}

    async def scan_engines() -> Any:
        async def worker(operation: dict[str, Any]) -> None:
            result = await asyncio.to_thread(app.state.runtime.engine_service.scan)
            operation["result"] = {
                "engine_count": len(result.engines),
                "installation_count": len(result.installations),
            }

        operation = await app.state.runtime.operation_manager.create(
            "engine_scan", "engines", worker
        )
        return _operation_summary(operation)

    async def list_operations(kind: str | None = None, state: str | None = None) -> list[Any]:
        values = list(app.state.runtime.operation_manager.all())
        values.extend(
            _dump(item) for item in app.state.runtime.process_manager._operations.values()
        )
        return [
            _operation_summary(item)
            for item in values
            if (kind is None or item.get("kind") == kind)
            and (state is None or item.get("state") == state)
        ]

    async def get_operation(operation_id: str) -> Any:
        try:
            return _operation_summary(app.state.runtime.operation_manager.get(operation_id))
        except AppError:
            return _operation_summary(app.state.runtime.process_manager.get_operation(operation_id))

    async def cancel_operation(operation_id: str) -> Any:
        try:
            await app.state.runtime.operation_manager.cancel(operation_id)
            return _operation_summary(app.state.runtime.operation_manager.get(operation_id))
        except AppError as exc:
            if exc.code != "operation_not_found":
                raise
            try:
                operation = app.state.runtime.process_manager.get_operation(operation_id)
            except AppError:
                raise exc from None
            if operation.state in {"succeeded", "failed"}:
                return _operation_summary(operation)
            raise AppError(
                "operation_not_cancellable",
                "Операцию остановки экземпляра нельзя отменить после запуска",
                status_code=409,
                details={"operation_id": operation_id},
            ) from None

    async def list_profiles(query: str | None = None) -> list[Any]:
        profiles = (await asyncio.to_thread(app.state.runtime.profile_service.list)).items
        instances = {
            item.profile_id: item.state
            for item in (await app.state.runtime.process_manager.refresh()).items
        }
        values = [
            _compact_profile(profile, instances.get(profile.profile_id)) for profile in profiles
        ]
        if query:
            needle = query.casefold()
            values = [item for item in values if needle in str(item).casefold()]
        return values

    async def list_profile_issues() -> list[Any]:
        values = app.state.runtime.profile_service.list()
        issues = getattr(values, "issues", [])
        return _dump(issues)

    async def get_profile(profile_id: str) -> Any:
        profiles = (await asyncio.to_thread(app.state.runtime.profile_service.list)).items
        profile = next(
            (item for item in profiles if item.profile_id == profile_id or item.name == profile_id),
            None,
        )
        if profile is None:
            raise AppError("profile_not_found", "Профиль не найден", status_code=404)
        return profile

    async def save_profile(
        mode: str, profile: dict[str, Any], profile_id: str | None = None
    ) -> Any:
        value = _validate_model(profile)
        service = app.state.runtime.profile_service
        if mode == "create":
            saved = await asyncio.to_thread(service.create, value)
            return {
                "profile_id": saved.profile_id,
                "name": saved.name,
                "created": True,
                "changed_fields": list(profile),
                "restart_required": False,
            }
        if mode == "update":
            target = profile_id or value.profile_id or value.name
            current = await get_profile(target)
            saved = await asyncio.to_thread(service.put, current.name, value)
            return {
                "profile_id": saved.profile_id,
                "name": saved.name,
                "created": False,
                "changed_fields": list(profile),
                "restart_required": True,
            }
        raise AppError("validation_error", "mode должен быть create или update", status_code=422)

    async def delete_profile(profile_id: str) -> Any:
        profile = await get_profile(profile_id)
        deleted = await asyncio.to_thread(app.state.runtime.profile_service.delete, profile.name)
        return {"profile_id": profile.profile_id, "name": deleted, "deleted": True}

    async def validate_profile(profile: dict[str, Any]) -> Any:
        value = _validate_model(profile)
        issues: list[dict[str, Any]] = []
        try:
            await app.state.runtime.profile_service.preview_configuration(value)
        except AppError as exc:
            issues.append({"field": "profile", "code": exc.code, "message": exc.message})
        except Exception as exc:  # validation boundary
            issues.append(
                {"field": "profile", "code": "preflight_failed", "message": str(exc)[:300]}
            )
        return {"valid": not issues, "issues": issues}

    async def preview_profile(profile: dict[str, Any]) -> Any:
        return await app.state.runtime.profile_service.preview_configuration(
            _validate_model(profile)
        )

    async def format_profile_script(script: str) -> Any:
        return app.state.runtime.profile_service.format_script(script)

    async def start_profile(profile_id: str) -> Any:
        profile = await get_profile(profile_id)
        return _instance_summary(await app.state.runtime.profile_service.start(profile.name))

    async def list_local_models(
        query: str | None = None, format: str | None = None, model_kind: str | None = None
    ) -> dict[str, Any]:
        catalogue = app.state.runtime.model_catalogue
        values = catalogue.items()
        if query:
            needle = query.casefold()
            values = [
                item
                for item in values
                if needle in str(item.model_name).casefold() or needle in str(item.path).casefold()
            ]
        if format:
            values = [item for item in values if item.format == format]
        if model_kind:
            values = [item for item in values if item.model_kind == model_kind]
        return {
            "items": [_compact_model(item) for item in values],
            "scanning": catalogue.scanning,
            "scan_error": catalogue.scan_error,
        }

    async def get_local_model(model_id: str) -> Any:
        item = next(
            (item for item in app.state.runtime.model_catalogue.items() if item.id == model_id),
            None,
        )
        if item is None:
            raise AppError("model_not_found", "Локальная модель не найдена", status_code=404)
        data = _compact_model(item)
        data.update(
            {
                "group_count": len(item.groups),
                "variant_count": len(item.variants),
                "auxiliary_count": len(item.auxiliary_files),
            }
        )
        return data

    async def list_local_model_contents(
        model_id: str, kind: str, group_id: str | None
    ) -> list[Any]:
        item = next(
            (item for item in app.state.runtime.model_catalogue.items() if item.id == model_id),
            None,
        )
        if item is None:
            raise AppError("model_not_found", "Локальная модель не найдена", status_code=404)
        files = [*item.variants, *item.auxiliary_files]
        hashes = app.state.runtime.model_catalogue.cached_file_hashes(
            item.path, [file.path for file in files]
        )

        def with_hashes(file: Any) -> dict[str, Any]:
            data = _dump(file)
            if isinstance(data, dict):
                data["hashes"] = hashes.get(
                    str(data.get("path", "")),
                    {"local": None, "downloaded": None, "remote": None},
                )
            return data

        def with_group_hashes(group: Any) -> dict[str, Any]:
            data = _dump(group)
            if isinstance(data, dict):
                data["files"] = [with_hashes(file) for file in group.files]
            return data

        if kind == "groups":
            return [
                with_group_hashes(group)
                for group in item.groups
                if group_id is None or group.id == group_id
            ]
        if kind == "variants":
            return [with_hashes(file) for file in item.variants]
        if kind == "auxiliary":
            return [with_group_hashes(group) for group in item.auxiliary_blocks]
        raise AppError(
            "validation_error", "kind должен быть groups, variants или auxiliary", status_code=422
        )

    async def search_hub_models(mode: str, query: str | None, group: str) -> list[Any]:
        catalogue = app.state.runtime.model_catalogue
        if mode == "search":
            if not query:
                raise AppError("invalid_query", "query обязателен для search", status_code=422)
            return [_compact_hub_model(item) for item in (await catalogue.hf_search(query)).items]
        if mode == "trending":
            return [
                _compact_hub_model(item)
                for item in (await catalogue.hf_trending(group=group, limit=50)).items
            ]
        raise AppError("validation_error", "mode должен быть search или trending", status_code=422)

    async def get_hub_model(repo_id: str, revision: str | None) -> Any:
        data = await app.state.runtime.model_catalogue.hf_repo(repo_id, revision)
        return {
            "repo_id": data.repo_id,
            "revision": data.revision,
            "author": data.author,
            "description": data.description,
            "revision_date": data.revision_date,
            "file_count": len(data.files),
        }

    async def read_model_readme(
        repo_id: str, revision: str, max_chars: int | None, cursor: str | None
    ) -> Any:
        data = await app.state.runtime.model_catalogue.hf_repo(repo_id, revision)
        text = data.readme or ""
        default_raw = os.getenv("MCP_DEFAULT_TEXT_CHARS", "4000")
        try:
            default_chunk = int(default_raw)
        except ValueError:
            raise AppError(
                "invalid_mcp_configuration", "MCP_DEFAULT_TEXT_CHARS должен быть числом"
            ) from None
        chunk = max_chars or default_chunk
        if chunk < 1:
            raise AppError(
                "invalid_page_size", "max_chars должен быть больше нуля", status_code=422
            )
        raw_max = os.getenv("MCP_MAX_TEXT_CHARS", "").strip()
        if raw_max:
            try:
                maximum = int(raw_max)
            except ValueError:
                raise AppError(
                    "invalid_mcp_configuration", "MCP_MAX_TEXT_CHARS должен быть числом"
                ) from None
            if maximum < 1:
                raise AppError(
                    "invalid_mcp_configuration", "MCP_MAX_TEXT_CHARS должен быть больше нуля"
                )
            if chunk > maximum:
                raise AppError(
                    "text_size_exceeded",
                    "Запрошенный размер фрагмента README превышает MCP_MAX_TEXT_CHARS",
                    status_code=422,
                    details={"requested_chars": chunk, "max_chars": maximum},
                )
        offset = 0
        context = hashlib.sha256(f"readme:{repo_id}:{data.revision}".encode()).hexdigest()
        if cursor:
            offset, cursor_context, _ = _decode_cursor(cursor)
            if cursor_context != context:
                raise AppError(
                    "invalid_cursor", "Курсор README не соответствует revision", status_code=422
                )
        part = text[offset : offset + chunk]
        end = offset + len(part)
        return {
            "repo_id": repo_id,
            "revision": data.revision,
            "text": part,
            "has_more": end < len(text),
            "next_cursor": _encode_cursor(end, context=context) if end < len(text) else None,
        }

    async def list_hub_model_contents(repo_id: str, revision: str | None, kind: str) -> list[Any]:
        if kind == "files":
            return (await app.state.runtime.model_catalogue.hf_files(repo_id, revision)).files
        if kind == "quantizations":
            return [
                _compact_hub_model(item)
                for item in (
                    await app.state.runtime.model_catalogue.hf_quantizations(repo_id)
                ).items
            ]
        raise AppError(
            "validation_error", "kind должен быть files или quantizations", status_code=422
        )

    async def download_model(
        repo_id: str, revision: str, files: list[str] | None, label: str | None
    ) -> Any:
        if not revision.strip():
            raise AppError("validation_error", "revision не должен быть пустым", status_code=422)
        if files == []:
            raise AppError(
                "validation_error", "files должен быть непустым или не передан", status_code=422
            )
        request = DownloadRequest(repo_id=repo_id, revision=revision, files=files, label=label)
        return _operation_summary(await app.state.runtime.model_catalogue.download(request))

    async def get_model_file_updates(model_id: str) -> Any:
        return await asyncio.to_thread(app.state.runtime.model_catalogue.file_updates, model_id)

    async def update_model_files(model_id: str, files: list[str] | None) -> Any:
        from ..schemas.models import ModelFileUpdateRequest

        request = ModelFileUpdateRequest(files=files)
        return _operation_summary(await app.state.runtime.model_catalogue.update_files(model_id, request.files))

    async def scan_local_models(verify_hashes: bool) -> Any:
        async def worker(operation: dict[str, Any]) -> None:
            items = await asyncio.to_thread(
                app.state.runtime.model_catalogue.scan, verify_hashes=verify_hashes
            )
            operation["result"] = {"model_count": len(items)}

        operation = await app.state.runtime.operation_manager.create("model_scan", "models", worker)
        return _operation_summary(operation)

    async def check_model_updates(model_id: str | None) -> Any:
        operation = await app.state.runtime.model_catalogue.start_update_check(model_id)
        return _operation_summary(operation)

    async def list_model_updates(model_id: str | None) -> list[Any]:
        values = app.state.runtime.model_catalogue.items()
        if model_id:
            values = [item for item in values if item.id == model_id]
        return [
            {
                "model_id": item.id,
                "status": item.hf_match.remote_update.status
                if item.hf_match.remote_update
                else "unknown",
                "remote_revision": item.hf_match.remote_check.current_revision
                if item.hf_match.remote_check
                else None,
                "remote_check": _dump(item.hf_match.remote_check),
                "local_integrity": _dump(item.hf_match.local_integrity),
                "changed_paths": _dump(item.hf_match.changed_paths),
            }
            for item in values
        ]

    async def delete_local_model(model_id: str, group_id: str | None) -> Any:
        if group_id is None:
            values = await asyncio.to_thread(app.state.runtime.model_catalogue.delete, model_id)
        else:
            values = await asyncio.to_thread(
                app.state.runtime.model_catalogue.delete_group, model_id, group_id
            )
        return {
            "model_id": model_id,
            "group_id": group_id,
            "deleted": True,
            "remaining_count": len(values),
        }

    async def _read_statistics(
        period: str,
        from_: str | None,
        to: str | None,
        profile_id: str | None,
        model_name: str | None,
    ) -> Any:
        return await app.state.runtime.statistics_service.read(
            period=period,
            from_=_parse_datetime(from_, "from"),
            to=_parse_datetime(to, "to"),
            profile_id=profile_id,
            model_name=model_name,
        )

    async def get_statistics(
        period: str,
        from_: str | None,
        to: str | None,
        profile_id: str | None,
        model_name: str | None,
    ) -> Any:
        data = await _read_statistics(period, from_, to, profile_id, model_name)
        if not isinstance(data, dict):
            return data
        # Detailed rows are available through nd_list_statistics_rows.  The
        # summary tool stays small enough for an agent to inspect repeatedly.
        totals = data.get("totals") if isinstance(data.get("totals"), dict) else {}
        tokens = totals.get("tokens") if isinstance(totals.get("tokens"), dict) else {}
        cost = totals.get("cost") if isinstance(totals.get("cost"), dict) else {}
        return {
            "period": data.get("period"),
            "filters": data.get("filters"),
            "requests": totals.get("requests"),
            "input_tokens": tokens.get("input"),
            "cached_input_tokens": tokens.get("cached_input"),
            "output_tokens": tokens.get("output"),
            "cost": {
                "known_total_usd": cost.get("usd"),
                "unknown_requests": cost.get("unknown_requests"),
                "state": cost.get("state"),
            },
            "summary": data.get("summary"),
            "benchmarks": data.get("benchmarks"),
            "source": data.get("source"),
            "row_counts": {
                "activity": len(data.get("activity", [])),
                "profiles": len(data.get("profiles", [])),
                "clients": len(data.get("clients", [])),
            },
        }

    async def list_statistics_rows(
        group_by: str,
        period: str,
        from_: str | None,
        to: str | None,
        profile_id: str | None,
        model_name: str | None,
    ) -> list[Any]:
        data = await _read_statistics(period, from_, to, profile_id, model_name)
        key = {
            "day": "activity",
            "hour": "activity",
            "profile": "profiles",
            "client": "clients",
        }.get(group_by)
        if key is None:
            raise AppError(
                "validation_error",
                "group_by должен быть profile, client, day или hour",
                status_code=422,
            )
        values = data.get(key, []) if isinstance(data, dict) else []
        return (
            [_compact_statistics_row(item) for item in values] if isinstance(values, list) else []
        )

    async def get_profile_summary(profile_id: str | None, profile_name: str | None) -> Any:
        profiles = (await asyncio.to_thread(app.state.runtime.profile_service.list)).items
        selected = [
            profile
            for profile in profiles
            if (profile_id is None or profile.profile_id == profile_id)
            and (profile_name is None or profile.name == profile_name)
        ]
        if not selected:
            raise AppError("profile_not_found", "Профиль не найден", status_code=404)
        start, end = utc_day_window()
        return await build_profile_summary(
            selected,
            metrics=app.state.runtime.metrics_service,
            catalogue=app.state.runtime.model_catalogue,
            start=start,
            end=end,
        )

    async def get_instance_metrics(
        instance_id: str, mode: str, from_: str | None, to: str | None
    ) -> Any:
        instance = await app.state.runtime.process_manager.get(instance_id)
        if mode == "current":
            data = await app.state.runtime.metrics_service.collect(instance_id)
            return {
                "items": data.items,
                "source": data.source,
                "peaks": data.peaks,
                "instance_id": instance_id,
                "mode": mode,
            }
        profile_name = instance.profile_name
        profile_id = instance.profile_id
        pid = instance.pid
        if mode == "peaks":
            data = await app.state.runtime.metrics_service.read(
                mode="peaks",
                pid=pid,
                profile_name=profile_name,
                profile_id=profile_id,
                scope="profile",
                from_=_parse_datetime(from_, "from"),
                to=_parse_datetime(to, "to"),
                limit=1,
                offset=0,
                client_id=None,
            )
            return {
                "items": [
                    {"name": name, "value": value, "unit": "", "state": "ok"}
                    for name, value in (data.get("peaks") or {}).items()
                ],
                "instance_id": instance_id,
                "mode": mode,
            }
        if mode == "history":
            data = await app.state.runtime.metrics_service.read(
                mode="history",
                pid=pid,
                profile_name=profile_name,
                profile_id=profile_id,
                scope="profile",
                from_=_parse_datetime(from_, "from"),
                to=_parse_datetime(to, "to"),
                limit=1000,
                offset=0,
                client_id=None,
            )
            return {
                "items": data.get("history", []),
                "source": data.get("source"),
                "instance_id": instance_id,
                "mode": mode,
            }
        raise AppError(
            "validation_error", "mode должен быть current, history или peaks", status_code=422
        )

    async def list_log_sources(kind: str | None, profile_id: str | None) -> list[Any]:
        values = LogsService(app.state.runtime.paths).list_sources().items
        return [
            _dump(item)
            for item in values
            if (kind is None or item.kind == kind)
            and (profile_id is None or item.profile_name == profile_id)
        ]

    async def read_log(
        source_id: str, direction: str, page_size: int | None, cursor: str | None
    ) -> Any:
        service = LogsService(app.state.runtime.paths)
        default_raw = os.getenv("MCP_DEFAULT_LOG_LINES", "200")
        try:
            default_limit = int(default_raw)
        except ValueError:
            raise AppError(
                "invalid_mcp_configuration", "MCP_DEFAULT_LOG_LINES должен быть числом"
            ) from None
        limit = page_size or default_limit
        if limit < 1:
            raise AppError(
                "invalid_page_size", "page_size должен быть больше нуля", status_code=422
            )
        before = None
        context = hashlib.sha256(f"log:{source_id}:{direction}".encode()).hexdigest()
        if cursor:
            before, cursor_context, _ = _decode_cursor(cursor)
            if cursor_context != context:
                raise AppError(
                    "invalid_cursor", "Курсор лога не соответствует направлению", status_code=422
                )
        if direction == "forward":
            if before is None:
                before = 0
            group = service._find(source_id)
            lines = service._read_lines(group.paths)
            start = min(max(before, 0), len(lines))
            end = min(start + limit, len(lines))
            return {
                "source_id": source_id,
                "page_size": limit,
                "lines": [
                    {"number": index, "text": text}
                    for index, text in enumerate(lines[start:end], start)
                ],
                "has_more": end < len(lines),
                "next_cursor": _encode_cursor(end, context=context) if end < len(lines) else None,
                "snapshot_at": datetime.now(UTC),
            }
        if direction not in {"tail", "forward"}:
            raise AppError(
                "validation_error", "direction должен быть tail или forward", status_code=422
            )
        data = service.read(source_id, before=before, limit=limit)
        return {
            "source_id": source_id,
            "page_size": limit,
            "lines": data.lines,
            "has_more": data.has_older,
            "next_cursor": (
                _encode_cursor(data.start_line, context=context) if data.has_older else None
            ),
            "snapshot_at": datetime.now(UTC),
        }

    async def export_log(source_id: str, latest_only: bool) -> Any:
        source, body = LogsService(app.state.runtime.paths).download(source_id, latest=latest_only)
        base_url = os.getenv("MCP_PUBLIC_BASE_URL", "").rstrip("/")
        if not base_url:
            host = app.state.runtime.settings.host
            if host in {"0.0.0.0", "::"}:
                host = "127.0.0.1"
            display_host = f"[{host}]" if ":" in host and not host.startswith("[") else host
            base_url = f"http://{display_host}:{app.state.runtime.settings.port}"
        ttl_raw = os.getenv("MCP_EXPORT_TTL_SECONDS", "3600")
        try:
            ttl = int(ttl_raw)
        except ValueError:
            raise AppError(
                "invalid_mcp_configuration", "MCP_EXPORT_TTL_SECONDS должен быть числом"
            ) from None
        if ttl < 1:
            raise AppError(
                "invalid_mcp_configuration", "MCP_EXPORT_TTL_SECONDS должен быть больше нуля"
            )
        expires = datetime.now(UTC) + timedelta(seconds=ttl)
        path = f"/api/v1/logs/{quote(source_id, safe='')}/download"
        query = f"expires={int(expires.timestamp())}"
        if latest_only:
            query += "&latest=true"
        return {
            "source_id": source_id,
            "filename": f"{source.label}.log",
            "size_bytes": len(body.encode()),
            "download_url": f"{base_url}{path}?{query}",
            "expires_at": expires,
        }

    async def delete_log_source(source_id: str) -> Any:
        return LogsService(app.state.runtime.paths).delete(source_id)

    def chat_service() -> ChatService:
        service: ChatService | None = app.state.runtime.chat_service
        if service is None:
            raise AppError("chat_unavailable", "Сервис чата недоступен", status_code=503)
        return service

    async def list_chats() -> Any:
        return await chat_service().list_conversations()

    async def delete_chat(conversation_id: str) -> Any:
        return await chat_service().delete(conversation_id)

    async def list_chat_models() -> dict[str, Any]:
        data = await chat_service().models()
        return {
            "items": [
                item
                for item in data.get("data", [])
                if set(item.get("types", [])) & {"text", "image", "video", "ocr"}
            ],
            **_capability_metadata(["text", "image", "video", "ocr"]),
        }

    async def chat(
        action: str, conversation_id: str | None, request: ChatTurnRequest | None
    ) -> Any:
        service = chat_service()
        if action == "send":
            if request is None:
                raise AppError(
                    "validation_error",
                    "Для nd_chat_send требуется request по схеме API чата",
                    status_code=422,
                )
            return await service.start(conversation_id or request.id, request)
        if action not in {"get", "stop"} or not conversation_id or request is not None:
            raise AppError(
                "validation_error",
                "Для nd_chat_get/nd_chat_stop укажите conversation_id без request",
                status_code=422,
            )
        return await (
            service.get(conversation_id) if action == "get" else service.stop(conversation_id)
        )

    async def list_proxy_routes() -> list[Any]:
        data = await app.state.runtime.proxy_service.route_data()
        values = data.get("items", []) if isinstance(data, dict) else []
        return [
            {
                "name": item.get("name"),
                "kind": item.get("kind"),
                "available": item.get("available"),
                "candidate_profile_ids": item.get("candidate_profile_ids", []),
                "candidate_ids": item.get("candidate_ids", []),
                "candidate_count": len(item.get("candidates", [])),
                "diagnostic_count": len(item.get("diagnostics", [])),
            }
            for item in values
        ]

    async def list_proxy_candidates(route_name: str) -> list[Any]:
        data = await app.state.runtime.proxy_service.route_data()
        route = next(
            (item for item in data.get("items", []) if item.get("name") == route_name), None
        )
        if route is None:
            raise AppError("route_not_found", "Маршрут прокси не найден", status_code=404)
        return route.get("candidates", [])

    async def read_proxy_diagnostics(route_name: str) -> list[Any]:
        data = await app.state.runtime.proxy_service.route_data()
        route = next(
            (item for item in data.get("items", []) if item.get("name") == route_name), None
        )
        if route is None:
            raise AppError("route_not_found", "Маршрут прокси не найден", status_code=404)
        return [{"message": message} for message in route.get("diagnostics", [])]

    async def set_proxy_route_order(route_name: str, profile_ids: list[str], candidate_ids: list[str] | None = None) -> Any:
        return await app.state.runtime.proxy_service.update_route_order(route_name, profile_ids, candidate_ids)

    async def save_proxy_alias(
        mode: str, name: str, profile_ids: list[str], previous_name: str | None, candidates: list[dict[str, str]] | None = None
    ) -> Any:
        if mode == "create":
            return await app.state.runtime.proxy_service.create_alias(name, profile_ids, candidates=candidates)
        if mode == "update":
            if previous_name is None:
                previous_name = name
            return await app.state.runtime.proxy_service.update_alias(
                previous_name, name, profile_ids, candidates=candidates
            )
        raise AppError("validation_error", "mode должен быть create или update", status_code=422)

    async def delete_proxy_alias(name: str) -> Any:
        await app.state.runtime.proxy_service.delete_alias(name)
        return {"name": name, "deleted": True}

    async def get_settings() -> Any:
        return effective_settings(app.state.runtime.settings, app.state.runtime.paths)

    async def update_settings(models_root: str) -> Any:
        try:
            root = validate_models_root(Path(models_root))
        except ValueError as exc:
            raise AppError("invalid_models_root", str(exc), status_code=422) from exc
        candidate = app.state.runtime.settings.model_copy(update={"models_root": root})
        updated = build_paths(candidate, include_saved_settings=False)
        prepare_data_dirs(updated)
        catalogue = ModelCatalogue(updated, operations=app.state.runtime.operation_manager)
        await asyncio.to_thread(catalogue.scan)
        settings_path = app.state.runtime.paths.settings_path
        settings_path.parent.mkdir(parents=True, exist_ok=True)
        settings_path.write_text(
            yaml.safe_dump({"models_root": str(root)}, sort_keys=True), encoding="utf-8"
        )
        app.state.runtime.settings.models_root = root
        await app.state.runtime.model_catalogue.stop_update_scheduler()
        app.state.runtime.replace_paths(updated, catalogue)
        app.state.runtime.engine_service.paths = updated
        app.state.runtime.process_manager.models_root = updated.models_root
        app.state.runtime.process_manager.runtime_root = updated.runtime_dir
        await catalogue.start_update_scheduler()
        app.state.paths = updated
        app.state.model_catalogue = catalogue
        return {"models_root": str(root), "restart_required": False}

    return {
        "get_status": get_status,
        "get_hardware": get_hardware,
        "list_instances": list_instances,
        "get_instance": get_instance,
        "stop_instance": stop_instance,
        "list_engines": list_engines,
        "list_engine_definitions": list_engine_definitions,
        "list_engine_definition_issues": list_engine_definition_issues,
        "get_engine_definition": get_engine_definition,
        "save_engine_definition": save_engine_definition,
        "delete_engine_definition": delete_engine_definition,
        "list_installations": list_installations,
        "install_engine": install_engine,
        "update_installation": update_installation,
        "run_engine_maintenance": run_engine_maintenance,
        "delete_installation": delete_installation,
        "scan_engines": scan_engines,
        "list_operations": list_operations,
        "get_operation": get_operation,
        "cancel_operation": cancel_operation,
        "list_profiles": list_profiles,
        "list_profile_issues": list_profile_issues,
        "get_profile": get_profile,
        "save_profile": save_profile,
        "delete_profile": delete_profile,
        "validate_profile": validate_profile,
        "preview_profile": preview_profile,
        "format_profile_script": format_profile_script,
        "start_profile": start_profile,
        "list_local_models": list_local_models,
        "get_local_model": get_local_model,
        "list_local_model_contents": list_local_model_contents,
        "search_hub_models": search_hub_models,
        "get_hub_model": get_hub_model,
        "read_model_readme": read_model_readme,
        "list_hub_model_contents": list_hub_model_contents,
        "download_model": download_model,
        "get_model_file_updates": get_model_file_updates,
        "update_model_files": update_model_files,
        "scan_local_models": scan_local_models,
        "check_model_updates": check_model_updates,
        "list_model_updates": list_model_updates,
        "delete_local_model": delete_local_model,
        "get_statistics": get_statistics,
        "list_statistics_rows": list_statistics_rows,
        "get_profile_summary": get_profile_summary,
        "get_instance_metrics": get_instance_metrics,
        "list_log_sources": list_log_sources,
        "read_log": read_log,
        "export_log": export_log,
        "delete_log_source": delete_log_source,
        "list_chat_models": list_chat_models,
        "list_chats": list_chats,
        "delete_chat": delete_chat,
        "chat": chat,
        "list_proxy_routes": list_proxy_routes,
        "list_proxy_candidates": list_proxy_candidates,
        "read_proxy_diagnostics": read_proxy_diagnostics,
        "set_proxy_route_order": set_proxy_route_order,
        "save_proxy_alias": save_proxy_alias,
        "delete_proxy_alias": delete_proxy_alias,
        **build_proxy_operations(app),
        "get_settings": get_settings,
        "update_settings": update_settings,
    }


__all__ = ["build_operations"]
