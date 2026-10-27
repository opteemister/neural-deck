"""Small API-format ComfyUI provider used by image and video adapters.

The provider intentionally has no knowledge of the local ComfyUI filesystem.
Workflow names and graph contents are read through ComfyUI's ``userdata`` API;
only the successful list of names is cached.  A graph is copied and patched in
memory for one request and is then submitted to ``/prompt``.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import contextlib
import copy
import json
import logging
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, cast
from urllib.parse import quote
from uuid import uuid4

import httpx
from sqlalchemy import text
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ..errors import AppError
from .routes import (
    COMFYUI_DEFAULT_BASE_URL,
    COMFYUI_DEFAULT_SOURCE,
    RouteConfigRepository,
)

logger = logging.getLogger(__name__)
COMFYUI_WORKFLOW_EXPORT_MESSAGE = (
    "Этот workflow сохранён для редактора ComfyUI. Выберите его API-экспорт."
)


def _with_default_source(section: Mapping[str, Any]) -> dict[str, Any]:
    """Expose the backend-local source without persisting or overwriting config."""

    result = dict(section)
    configured = section.get("sources", {})
    sources = dict(configured) if isinstance(configured, Mapping) else {}
    sources.setdefault(COMFYUI_DEFAULT_SOURCE, {"base_url": COMFYUI_DEFAULT_BASE_URL})
    result["sources"] = sources
    if not isinstance(result.get("models"), Mapping):
        result["models"] = {}
    return result


class ComfyUIUnavailable(AppError):
    """An upstream request failed before ComfyUI accepted a prompt."""

    def __init__(self, message: str = "ComfyUI недоступен") -> None:
        super().__init__("comfyui_unavailable", message, status_code=502, retryable=True)


@dataclass(frozen=True, slots=True)
class ComfyOutput:
    filename: str
    subfolder: str
    output_type: str
    content: bytes
    media_type: str
    source_url: str | None = None
    user_context: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "filename": self.filename,
            "subfolder": self.subfolder,
            "type": self.output_type,
            "content": self.content,
            "media_type": self.media_type,
        }


@dataclass(frozen=True, slots=True)
class ComfyVideoJob:
    id: str
    source: str
    prompt_id: str
    status: str
    model: str = ""
    seconds: str | None = None
    size: str | None = None
    prompt: str | None = None
    result: tuple[dict[str, str], ...] = ()
    error: dict[str, str] | None = None
    created_at: float = 0.0
    source_url: str | None = None
    user_context: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "object": "video",
            "model": self.model or self.source,
            "status": self.status,
            "seconds": self.seconds,
            "size": self.size,
            "error": self.error,
            "created_at": int(self.created_at),
        }


class ComfyUIClient:
    """HTTP-only client for the small set of ComfyUI APIs we need."""

    def __init__(
        self,
        base_url: str,
        *,
        user_context: str | None = None,
        client_factory: Any | None = None,
        timeout: float = 1800.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.user_context = user_context
        self.client_factory = client_factory
        self.timeout = timeout

    async def _client(self) -> httpx.AsyncClient:
        if self.client_factory is not None:
            return cast(httpx.AsyncClient, self.client_factory())
        return httpx.AsyncClient(timeout=self.timeout)

    async def request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        client = await self._client()
        try:
            try:
                if self.user_context is not None:
                    headers = dict(kwargs.pop("headers", {}) or {})
                    headers.setdefault("Comfy-User", self.user_context)
                    kwargs["headers"] = headers
                response = await client.request(
                    method, f"{self.base_url}/{path.lstrip('/')}", **kwargs
                )
                await response.aread()
                return response
            except (httpx.TimeoutException, httpx.NetworkError, httpx.RemoteProtocolError) as exc:
                raise ComfyUIUnavailable() from exc
        finally:
            with contextlib.suppress(Exception):
                await client.aclose()

    async def json_request(self, method: str, path: str, **kwargs: Any) -> Any:
        response = await self.request(method, path, **kwargs)
        if response.status_code >= 500:
            raise ComfyUIUnavailable(f"ComfyUI вернул HTTP {response.status_code}")
        try:
            payload = response.json()
        except (ValueError, TypeError) as exc:
            raise AppError(
                "comfyui_invalid_response",
                "ComfyUI вернул неверный JSON",
                status_code=502,
            ) from exc
        if response.status_code >= 400:
            raise AppError(
                "comfyui_request_failed",
                "ComfyUI отклонил запрос",
                status_code=502,
                details={"status_code": response.status_code, "response": payload},
            )
        return payload

    async def userdata_list(self) -> Any:
        return await self.json_request(
            "GET", "/userdata", params={"dir": "workflows", "recurse": "true"}
        )

    @staticmethod
    def normalise_workflow_path(path: str) -> str:
        """Return the only userdata path shape accepted by this provider."""

        normalized = path.strip("/").replace("\\", "/")
        if not normalized.startswith("workflows/") or ".." in normalized.split("/"):
            raise AppError(
                "comfyui_workflow_path_invalid",
                "Разрешено читать только файлы из каталога workflows",
                status_code=400,
            )
        return normalized

    async def userdata_file(self, path: str) -> Any:
        # ComfyUI's userdata endpoint accepts nested relative names.  quote()
        # protects a filename while retaining its directory separators.
        normalized = self.normalise_workflow_path(path)
        encoded = quote(normalized, safe="")
        return await self.json_request("GET", f"/userdata/{encoded}")

    async def upload_image(
        self, content: bytes, *, filename: str, content_type: str = "image/png"
    ) -> str:
        response = await self.request(
            "POST",
            "/upload/image",
            files={"image": (filename, content, content_type)},
            data={"type": "input"},
        )
        if response.status_code >= 400:
            raise AppError(
                "comfyui_upload_failed", "ComfyUI не принял входное изображение", status_code=502
            )
        try:
            payload = response.json()
        except (ValueError, TypeError) as exc:
            raise AppError(
                "comfyui_upload_failed", "ComfyUI вернул неверный ответ загрузки", status_code=502
            ) from exc
        if not isinstance(payload, Mapping) or not isinstance(payload.get("name"), str):
            raise AppError(
                "comfyui_upload_failed",
                "ComfyUI не вернул имя входного изображения",
                status_code=502,
            )
        subfolder = payload.get("subfolder", "")
        if not isinstance(subfolder, str):
            raise AppError(
                "comfyui_upload_failed",
                "ComfyUI вернул неверный каталог изображения",
                status_code=502,
            )
        return f"{subfolder}/{payload['name']}" if subfolder else payload["name"]

    async def prompt(self, graph: Mapping[str, Any], *, client_id: str | None = None) -> str:
        body: dict[str, Any] = {"prompt": graph}
        if client_id:
            body["client_id"] = client_id
        try:
            payload = await self.json_request("POST", "/prompt", json=body)
        except AppError as exc:
            if exc.details.get("status_code") == 400:
                raise AppError(
                    "comfyui_invalid_parameters",
                    "ComfyUI отклонил параметры workflow",
                    status_code=400,
                    details=exc.details,
                ) from exc
            raise
        prompt_id = payload.get("prompt_id") if isinstance(payload, Mapping) else None
        if not isinstance(prompt_id, str) or not prompt_id:
            raise AppError(
                "comfyui_prompt_failed",
                "ComfyUI не вернул prompt_id",
                status_code=502,
                details={"response": payload},
            )
        return prompt_id

    async def history(self, prompt_id: str) -> Any:
        return await self.json_request("GET", f"/history/{quote(prompt_id, safe='')}")

    async def queue(self) -> Any:
        return await self.json_request("GET", "/queue")

    async def system_stats(self) -> Any:
        return await self.json_request("GET", "/system_stats")

    async def view(self, output: Mapping[str, str]) -> tuple[bytes, str]:
        response = await self.request(
            "GET",
            "/view",
            params={
                "filename": output["filename"],
                "subfolder": output.get("subfolder", ""),
                "type": output.get("type", "output"),
            },
        )
        if response.status_code >= 400:
            raise AppError(
                "comfyui_output_unavailable", "ComfyUI не отдал результат", status_code=502
            )
        media_type = response.headers.get("content-type", "application/octet-stream").split(";", 1)[
            0
        ]
        return response.content, media_type


class ComfyWorkflowCatalog:
    """Live userdata listing with a per-source persistent name-only cache."""

    def __init__(self, cache_path: Path | None = None) -> None:
        self.cache_path = cache_path
        self._cache: dict[str, dict[str, Any]] = {}
        self._load_cache()

    @staticmethod
    def _cache_key(base_url: str, user_context: str | None) -> str:
        return json.dumps([base_url.rstrip("/"), user_context or ""], ensure_ascii=False)

    def _load_cache(self) -> None:
        if self.cache_path is None:
            return
        try:
            raw = json.loads(self.cache_path.read_text(encoding="utf-8"))
            if isinstance(raw, Mapping) and isinstance(raw.get("entries"), Mapping):
                self._cache = {
                    str(key): dict(value)
                    for key, value in raw["entries"].items()
                    if isinstance(value, Mapping)
                }
        except (OSError, UnicodeError, ValueError, TypeError) as exc:
            logger.warning("Не удалось прочитать кеш списка ComfyUI: %s", exc)
            self._cache = {}

    def _save_cache(self) -> None:
        if self.cache_path is None:
            return
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"version": 1, "entries": self._cache}
        temporary = self.cache_path.with_name(f".{self.cache_path.name}.{uuid4().hex}.tmp")
        try:
            temporary.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            temporary.replace(self.cache_path)
        finally:
            with contextlib.suppress(FileNotFoundError):
                temporary.unlink()

    @staticmethod
    def _normalise_listing(source: str, payload: Any) -> list[dict[str, str]]:
        if not isinstance(payload, list):
            raise AppError(
                "comfyui_invalid_response",
                "ComfyUI вернул неверный список userdata",
                status_code=502,
            )
        result: list[dict[str, str]] = []
        seen: set[str] = set()
        for value in payload:
            path: str | None = None
            name: str | None = None
            if isinstance(value, str):
                path = value
            elif isinstance(value, Mapping):
                raw_path = value.get("path")
                if isinstance(raw_path, str):
                    path = raw_path
                raw_name = value.get("name")
                if isinstance(raw_name, str):
                    name = raw_name
            if path is None:
                raise AppError(
                    "comfyui_invalid_response",
                    "ComfyUI вернул неверный путь userdata",
                    status_code=502,
                )
            path = path.replace("\\", "/")
            if not path or path.startswith("/") or ".." in path.split("/"):
                continue
            if not path.startswith("workflows/"):
                path = f"workflows/{path}"
            if not path or path in seen:
                continue
            seen.add(path)
            result.append({"source": source, "path": path, "name": name or Path(path).name})
        return result

    async def list_workflows(
        self,
        source: str,
        client: ComfyUIClient,
        *,
        user_context: str | None = None,
    ) -> list[dict[str, str]]:
        items, _available = await self.list_workflows_with_status(
            source, client, user_context=user_context
        )
        return items

    async def list_workflows_with_status(
        self,
        source: str,
        client: ComfyUIClient,
        *,
        user_context: str | None = None,
    ) -> tuple[list[dict[str, str]], bool]:
        key = self._cache_key(client.base_url, user_context)
        try:
            current = self._normalise_listing(source, await client.userdata_list())
        except ComfyUIUnavailable:
            cached = self._cache.get(key)
            if cached is None:
                return [], False
            return [
                {**dict(item), "source": source}
                for item in cached.get("items", [])
                if isinstance(item, Mapping)
            ], False
        self._cache[key] = {"source": source, "read_at": time.time(), "items": current}
        self._save_cache()
        return current, True

    def cached(self, base_url: str, *, user_context: str | None = None) -> list[dict[str, str]]:
        value = self._cache.get(self._cache_key(base_url, user_context), {})
        return [dict(item) for item in value.get("items", []) if isinstance(item, Mapping)]

    def cache_metadata(
        self, base_url: str, *, user_context: str | None = None
    ) -> dict[str, Any] | None:
        value = self._cache.get(self._cache_key(base_url, user_context))
        return dict(value) if value is not None else None


class ComfyVideoStore:
    """Small SQLite mapping for video external IDs and ComfyUI results."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession] | None = None) -> None:
        self.session_factory = session_factory
        self._memory: dict[str, ComfyVideoJob] = {}
        self._ready = False
        self._lock = asyncio.Lock()

    async def _ensure_table(self) -> None:
        if self.session_factory is None or self._ready:
            return
        async with self._lock:
            if self._ready:
                return
            async with self.session_factory() as session:
                await session.execute(
                    text(
                        """CREATE TABLE IF NOT EXISTS comfy_video_jobs (
                            id VARCHAR(120) PRIMARY KEY,
                            source VARCHAR(255) NOT NULL,
                            model VARCHAR(256),
                            prompt_id VARCHAR(255) NOT NULL,
                            status VARCHAR(32) NOT NULL,
                            prompt TEXT,
                            result_json TEXT NOT NULL,
                            error TEXT,
                            created_at FLOAT NOT NULL,
                            seconds VARCHAR(32),
                            size VARCHAR(64),
                            source_url VARCHAR(2048),
                            user_context VARCHAR(255)
                        )"""
                    )
                )
                # Databases created by an earlier development snapshot may
                # already have the table without the immutable source fields.
                columns = {
                    str(row[1])
                    for row in (
                        await session.execute(text("PRAGMA table_info(comfy_video_jobs)"))
                    ).all()
                }
                for column, definition in (
                    ("model", "VARCHAR(256)"),
                    ("seconds", "VARCHAR(32)"),
                    ("size", "VARCHAR(64)"),
                    ("source_url", "VARCHAR(2048)"),
                    ("user_context", "VARCHAR(255)"),
                ):
                    if column in columns:
                        continue
                    try:
                        await session.execute(
                            text(f"ALTER TABLE comfy_video_jobs ADD COLUMN {column} {definition}")
                        )
                    except OperationalError as exc:
                        # Another provider instance may have added the same
                        # compatibility column between PRAGMA and ALTER.
                        if "duplicate column" not in str(exc).lower():
                            raise
                await session.commit()
            self._ready = True

    @staticmethod
    def _from_row(row: Mapping[str, Any]) -> ComfyVideoJob:
        raw_result = row.get("result_json", "[]")
        try:
            parsed = json.loads(raw_result) if isinstance(raw_result, str) else raw_result
        except ValueError:
            parsed = []
        result = (
            tuple(dict(item) for item in parsed if isinstance(item, Mapping))
            if isinstance(parsed, list)
            else ()
        )
        raw_error = row.get("error")
        error: dict[str, str] | None = None
        if isinstance(raw_error, Mapping):
            code = raw_error.get("code")
            message = raw_error.get("message")
            if isinstance(code, str) and isinstance(message, str):
                error = {"code": code, "message": message}
        elif isinstance(raw_error, str) and raw_error:
            try:
                parsed_error = json.loads(raw_error)
            except (TypeError, ValueError):
                parsed_error = None
            if isinstance(parsed_error, Mapping):
                code = parsed_error.get("code")
                message = parsed_error.get("message")
                if isinstance(code, str) and isinstance(message, str):
                    error = {"code": code, "message": message}
            if error is None:
                error = {"code": "comfyui_execution_failed", "message": raw_error}
        model = row.get("model")
        return ComfyVideoJob(
            id=str(row["id"]),
            source=str(row["source"]),
            prompt_id=str(row["prompt_id"]),
            status=str(row["status"]),
            model=str(model) if model else str(row["source"]),
            seconds=str(row["seconds"]) if row.get("seconds") is not None else None,
            size=str(row["size"]) if row.get("size") is not None else None,
            prompt=row.get("prompt"),
            result=result,
            error=error,
            created_at=float(row.get("created_at", 0.0)),
            source_url=row.get("source_url"),
            user_context=row.get("user_context"),
        )

    async def put(self, job: ComfyVideoJob) -> None:
        await self._ensure_table()
        if self.session_factory is None:
            self._memory[job.id] = job
            return
        async with self.session_factory() as session:
            await session.execute(
                text(
                    """INSERT INTO comfy_video_jobs
                    (id, source, model, prompt_id, status, prompt, result_json, error, created_at,
                     seconds, size, source_url, user_context)
                    VALUES (:id, :source, :model, :prompt_id, :status, :prompt, :result_json,
                            :error, :created_at, :seconds, :size, :source_url, :user_context)
                    ON CONFLICT(id) DO UPDATE SET source=excluded.source,
                    model=excluded.model, prompt_id=excluded.prompt_id, status=excluded.status,
                    prompt=excluded.prompt, result_json=excluded.result_json,
                    error=excluded.error, created_at=excluded.created_at,
                    seconds=excluded.seconds, size=excluded.size,
                    source_url=excluded.source_url, user_context=excluded.user_context"""
                ),
                {
                    "id": job.id,
                    "source": job.source,
                    "model": job.model,
                    "prompt_id": job.prompt_id,
                    "status": job.status,
                    "prompt": job.prompt,
                    "result_json": json.dumps(list(job.result), ensure_ascii=False),
                    "error": json.dumps(job.error, ensure_ascii=False) if job.error else None,
                    "created_at": job.created_at,
                    "seconds": job.seconds,
                    "size": job.size,
                    "source_url": job.source_url,
                    "user_context": job.user_context,
                },
            )
            await session.commit()

    async def get(self, job_id: str) -> ComfyVideoJob | None:
        await self._ensure_table()
        if self.session_factory is None:
            return self._memory.get(job_id)
        async with self.session_factory() as session:
            row = (
                (
                    await session.execute(
                        text("SELECT * FROM comfy_video_jobs WHERE id=:id"), {"id": job_id}
                    )
                )
                .mappings()
                .first()
            )
            return self._from_row(cast(Mapping[str, Any], row)) if row is not None else None

    async def list(self) -> list[ComfyVideoJob]:
        await self._ensure_table()
        if self.session_factory is None:
            return list(self._memory.values())
        async with self.session_factory() as session:
            rows = (
                (
                    await session.execute(
                        text("SELECT * FROM comfy_video_jobs ORDER BY created_at DESC")
                    )
                )
                .mappings()
                .all()
            )
            return [self._from_row(cast(Mapping[str, Any], row)) for row in rows]

    async def delete(self, job_id: str) -> bool:
        await self._ensure_table()
        if self.session_factory is None:
            return self._memory.pop(job_id, None) is not None
        async with self.session_factory() as session:
            row = (
                await session.execute(
                    text("SELECT id FROM comfy_video_jobs WHERE id=:id"), {"id": job_id}
                )
            ).first()
            if row is None:
                return False
            await session.execute(text("DELETE FROM comfy_video_jobs WHERE id=:id"), {"id": job_id})
            await session.commit()
            return True


@dataclass(frozen=True, slots=True)
class ComfyMediaAsset:
    id: str
    source_url: str
    filename: str
    subfolder: str
    output_type: str
    media_type: str
    user_context: str | None = None


class ComfyMediaStore:
    """Persist descriptors only; media bytes remain in ComfyUI."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession] | None = None) -> None:
        self.session_factory = session_factory
        self._memory: dict[str, ComfyMediaAsset] = {}
        self._ready = False
        self._lock = asyncio.Lock()

    async def _ensure_table(self) -> None:
        if self.session_factory is None or self._ready:
            return
        async with self._lock:
            if self._ready:
                return
            async with self.session_factory() as session:
                await session.execute(
                    text(
                        """CREATE TABLE IF NOT EXISTS comfy_media_assets (
                            id VARCHAR(120) PRIMARY KEY,
                            source_url VARCHAR(2048) NOT NULL,
                            filename VARCHAR(2048) NOT NULL,
                            subfolder VARCHAR(2048) NOT NULL,
                            output_type VARCHAR(64) NOT NULL,
                            media_type VARCHAR(255) NOT NULL,
                            user_context VARCHAR(255)
                        )"""
                    )
                )
                await session.commit()
            self._ready = True

    @staticmethod
    def _row(row: Mapping[str, Any]) -> ComfyMediaAsset:
        return ComfyMediaAsset(
            id=str(row["id"]),
            source_url=str(row["source_url"]),
            filename=str(row["filename"]),
            subfolder=str(row["subfolder"]),
            output_type=str(row["output_type"]),
            media_type=str(row["media_type"]),
            user_context=row.get("user_context"),
        )

    async def put(self, asset: ComfyMediaAsset) -> None:
        await self._ensure_table()
        if self.session_factory is None:
            self._memory[asset.id] = asset
            return
        async with self.session_factory() as session:
            await session.execute(
                text(
                    """INSERT INTO comfy_media_assets
                    (id, source_url, filename, subfolder, output_type, media_type, user_context)
                    VALUES (:id, :source_url, :filename, :subfolder, :output_type, :media_type,
                            :user_context)
                    ON CONFLICT(id) DO UPDATE SET source_url=excluded.source_url,
                    filename=excluded.filename, subfolder=excluded.subfolder,
                    output_type=excluded.output_type, media_type=excluded.media_type,
                    user_context=excluded.user_context"""
                ),
                {
                    "id": asset.id,
                    "source_url": asset.source_url,
                    "filename": asset.filename,
                    "subfolder": asset.subfolder,
                    "output_type": asset.output_type,
                    "media_type": asset.media_type,
                    "user_context": asset.user_context,
                },
            )
            await session.commit()

    async def get(self, asset_id: str) -> ComfyMediaAsset | None:
        await self._ensure_table()
        if self.session_factory is None:
            return self._memory.get(asset_id)
        async with self.session_factory() as session:
            row = (
                (
                    await session.execute(
                        text("SELECT * FROM comfy_media_assets WHERE id=:id"), {"id": asset_id}
                    )
                )
                .mappings()
                .first()
            )
            return self._row(cast(Mapping[str, Any], row)) if row is not None else None


class ComfyUIProvider:
    """Alias/operation adapter for API-format ComfyUI workflows."""

    _PARAM_FIELDS = {
        "prompt",
        "image",
        "mask",
        "size",
        "n",
        "quality",
        "background",
        "output_format",
        "output_compression",
        "seconds",
        "reference",
        "input_reference",
    }

    def __init__(
        self,
        config: RouteConfigRepository | Mapping[str, Any],
        *,
        client_factory: Any | None = None,
        cache_path: Path | None = None,
        session_factory: async_sessionmaker[AsyncSession] | None = None,
        timeout: float = 1800.0,
        catalog_timeout: float = 5.0,
        poll_interval: float = 0.25,
    ) -> None:
        self.config = config
        self.client_factory = client_factory
        self.catalog = ComfyWorkflowCatalog(cache_path)
        self.video_store = ComfyVideoStore(session_factory)
        self.media_store = ComfyMediaStore(session_factory)
        self.timeout = timeout
        self.catalog_timeout = catalog_timeout
        self.poll_interval = poll_interval

    def _section(self) -> Mapping[str, Any]:
        raw: Any
        if isinstance(self.config, RouteConfigRepository):
            raw = self.config.load_comfyui()
        elif hasattr(self.config, "load_comfyui"):
            raw = self.config.load_comfyui()
        elif hasattr(self.config, "load"):
            raw = self.config.load()
        else:
            raw = self.config
        if isinstance(raw, Mapping) and "comfyui" in raw:
            raw = raw["comfyui"]
        return _with_default_source(raw) if isinstance(raw, Mapping) else _with_default_source({})

    def _model(self, alias: str) -> Mapping[str, Any]:
        models = self._section().get("models", {})
        model = models.get(alias) if isinstance(models, Mapping) else None
        if not isinstance(model, Mapping):
            raise AppError(
                "comfyui_model_not_found", f"ComfyUI alias не найден: {alias}", status_code=404
            )
        return model

    def _source(self, name: str) -> tuple[str, str | None]:
        sources = self._section().get("sources", {})
        source = sources.get(name) if isinstance(sources, Mapping) else None
        if not isinstance(source, Mapping) or not isinstance(source.get("base_url"), str):
            raise AppError(
                "comfyui_source_not_found", f"ComfyUI source не найден: {name}", status_code=404
            )
        return source["base_url"], str(source["user_context"]) if source.get(
            "user_context"
        ) is not None else None

    def _client_for_model(self, alias: str) -> tuple[ComfyUIClient, str, Mapping[str, Any]]:
        model = self._model(alias)
        source_name = model.get("source")
        if not isinstance(source_name, str):
            raise AppError(
                "comfyui_config_invalid", f"У alias {alias} не указан source", status_code=422
            )
        base_url, context = self._source(source_name)
        return (
            ComfyUIClient(
                base_url,
                user_context=context,
                client_factory=self.client_factory,
                timeout=self.timeout,
            ),
            source_name,
            model,
        )

    async def list_workflows(self, source: str) -> list[dict[str, str]]:
        base_url, context = self._source(source)
        client = ComfyUIClient(
            base_url,
            user_context=context,
            client_factory=self.client_factory,
            timeout=self.catalog_timeout,
        )
        return await self.catalog.list_workflows(source, client, user_context=context)

    async def list_workflows_with_status(self, source: str) -> tuple[list[dict[str, str]], bool]:
        base_url, context = self._source(source)
        client = ComfyUIClient(
            base_url,
            user_context=context,
            client_factory=self.client_factory,
            timeout=self.catalog_timeout,
        )
        return await self.catalog.list_workflows_with_status(source, client, user_context=context)

    async def source_status(self, source: str) -> dict[str, Any]:
        base_url, context = self._source(source)
        client = ComfyUIClient(
            base_url,
            user_context=context,
            client_factory=self.client_factory,
            timeout=self.catalog_timeout,
        )
        checked_at = time.time()
        try:
            await client.system_stats()
        except ComfyUIUnavailable:
            return {
                "source": source,
                "base_url": base_url,
                "available": False,
                "status": "unavailable",
                "message": "ComfyUI недоступен",
                "checked_at": checked_at,
            }
        except AppError as exc:
            return {
                "source": source,
                "base_url": base_url,
                "available": False,
                "status": "error",
                "message": exc.message,
                "checked_at": checked_at,
            }
        return {
            "source": source,
            "base_url": base_url,
            "available": True,
            "status": "ready",
            "message": "ComfyUI доступен",
            "checked_at": checked_at,
        }

    @staticmethod
    def _is_api_graph(payload: Any) -> bool:
        return (
            isinstance(payload, Mapping)
            and bool(payload)
            and all(
                isinstance(node, Mapping)
                and isinstance(node.get("class_type"), str)
                and isinstance(node.get("inputs"), Mapping)
                for node in payload.values()
            )
        )

    @classmethod
    def _api_graph_payload(cls, payload: Any) -> Mapping[str, Any] | None:
        if not isinstance(payload, Mapping):
            return None
        if cls._is_api_graph(payload):
            return payload
        # A few integrations wrap an API export in an explicit ``prompt``
        # object.  Accept that shape only when the wrapped object itself is a
        # valid API graph; never convert UI ``nodes``/``links`` data.
        wrapped = payload.get("prompt")
        return wrapped if cls._is_api_graph(wrapped) else None

    async def validate_workflow(self, source: str, path: str) -> dict[str, Any]:
        base_url, context = self._source(source)
        client = ComfyUIClient(
            base_url,
            user_context=context,
            client_factory=self.client_factory,
            timeout=self.catalog_timeout,
        )
        normalized = client.normalise_workflow_path(path)
        try:
            payload = await client.userdata_file(normalized)
        except ComfyUIUnavailable:
            return {
                "path": normalized,
                "usable": None,
                "message": "ComfyUI недоступен; workflow не проверен",
            }
        except AppError as exc:
            return {
                "path": normalized,
                "usable": False,
                "message": f"Не удалось прочитать workflow: {exc.message}",
            }
        if self._api_graph_payload(payload) is None:
            return {
                "path": normalized,
                "usable": False,
                "message": COMFYUI_WORKFLOW_EXPORT_MESSAGE,
            }
        return {"path": normalized, "usable": True}

    async def load_graph(self, alias: str, operation: str) -> dict[str, Any]:
        client, _source, model = self._client_for_model(alias)
        workflows = model.get("workflows")
        path = workflows.get(operation) if isinstance(workflows, Mapping) else None
        if not isinstance(path, str) or not path:
            raise AppError(
                "comfyui_operation_unsupported",
                f"Операция {operation} не настроена для {alias}",
                status_code=400,
                details={"operation": operation},
            )
        if not path.startswith("workflows/"):
            path = f"workflows/{path}"
        payload = await client.userdata_file(path)
        graph_payload = self._api_graph_payload(payload)
        if graph_payload is None:
            raise AppError(
                "comfyui_workflow_invalid", COMFYUI_WORKFLOW_EXPORT_MESSAGE, status_code=502
            )
        return copy.deepcopy(dict(graph_payload))

    @staticmethod
    def _decode_input(value: Any, field: str) -> bytes:
        if isinstance(value, bytes):
            content = value
        elif isinstance(value, str):
            raw = value.split(",", 1)[1] if value.startswith("data:") and "," in value else value
            try:
                content = base64.b64decode(raw, validate=True)
            except (ValueError, binascii.Error) as exc:
                raise AppError(
                    "invalid_input_image", f"Поле {field} содержит неверный base64", status_code=400
                ) from exc
        else:
            raise AppError(
                "invalid_input_image", f"Поле {field} должно быть изображением", status_code=400
            )
        if not content or len(content) > 25 * 1024 * 1024:
            raise AppError(
                "invalid_input_image",
                f"Размер {field} должен быть от 1 байта до 25 МБ",
                status_code=400,
            )
        return content

    @staticmethod
    def _bindings(model: Mapping[str, Any], operation: str) -> Mapping[str, Any]:
        raw = model.get("bindings", {})
        if not isinstance(raw, Mapping):
            return {}
        shared = {
            key: value
            for key, value in raw.items()
            if key not in {"generate", "edit", "edit_masked", "variations"}
        }
        operation_map = raw.get(operation)
        if isinstance(operation_map, Mapping):
            shared.update(operation_map)
        return shared

    @staticmethod
    def _binding_for(
        bindings: Mapping[str, Any], field: str, suffix: str | None = None
    ) -> Mapping[str, str] | None:
        value = bindings.get(f"{field}.{suffix}" if suffix else field)
        if value is None and suffix is not None and isinstance(bindings.get(field), Mapping):
            candidate = bindings[field]
            value = candidate.get(suffix) if isinstance(candidate, Mapping) else None
        if isinstance(value, (list, tuple)) and len(value) == 2:
            value = {"node": value[0], "input": value[1]}
        if (
            isinstance(value, Mapping)
            and isinstance(value.get("node"), (str, int))
            and isinstance(value.get("input"), str)
        ):
            return {"node": str(value["node"]), "input": value["input"]}
        return None

    @staticmethod
    def _unique_inferred_binding(
        graph: Mapping[str, Any],
        field: str,
        predicate: Any,
    ) -> dict[str, str] | None:
        candidates: list[dict[str, str]] = []
        for raw_node_id, raw_node in graph.items():
            if not isinstance(raw_node, Mapping) or not isinstance(raw_node.get("inputs"), Mapping):
                continue
            node_id = str(raw_node_id)
            input_name = predicate(raw_node)
            if isinstance(input_name, str):
                candidates.append({"node": node_id, "input": input_name})
        if len(candidates) > 1:
            raise AppError(
                "comfyui_binding_ambiguous",
                f"Невозможно однозначно определить binding для {field}",
                status_code=400,
                details={"field": field, "candidates": [item["node"] for item in candidates]},
            )
        return candidates[0] if candidates else None

    @staticmethod
    def _qwen_prompt_binding(
        graph: Mapping[str, Any], node_id: str, encoder: Mapping[str, Any]
    ) -> dict[str, str] | None:
        """Resolve the known Qwen edit prompt route without generic traversal.

        The exported Qwen graph uses ``TextEncodeQwenImage21.prompt`` ->
        ``PreviewAny.source`` -> ``ComfySwitchNode``.  The false branch is a
        literal default prompt; the true branch is the known ``TextGenerate``
        node.  Only these exact node types and inputs are accepted.
        """

        encoder_inputs = encoder.get("inputs")
        if not isinstance(encoder_inputs, Mapping) or "prompt" not in encoder_inputs:
            return None

        def linked_node_id(value: Any) -> str | None:
            if isinstance(value, (list, tuple)) and value and isinstance(value[0], (str, int)):
                return str(value[0])
            return None

        def switch_binding(switch_id: str, switch: Mapping[str, Any]) -> dict[str, str] | None:
            switch_inputs = switch.get("inputs")
            if not isinstance(switch_inputs, Mapping):
                return None
            enabled = switch_inputs.get("switch")
            if not isinstance(enabled, bool):
                return None
            if not enabled:
                return (
                    {"node": switch_id, "input": "on_false"}
                    if isinstance(switch_inputs.get("on_false"), str)
                    else None
                )
            true_id = linked_node_id(switch_inputs.get("on_true"))
            true_node = graph.get(true_id) if true_id is not None else None
            true_inputs = true_node.get("inputs") if isinstance(true_node, Mapping) else None
            if (
                isinstance(true_node, Mapping)
                and true_node.get("class_type") == "TextGenerate"
                and isinstance(true_inputs, Mapping)
                and "prompt" in true_inputs
                and true_id is not None
            ):
                return {"node": true_id, "input": "prompt"}
            return None

        prompt = encoder_inputs["prompt"]
        if isinstance(prompt, str):
            return {"node": node_id, "input": "prompt"}
        switch_id = linked_node_id(prompt)
        switch = graph.get(switch_id) if switch_id is not None else None
        if (
            switch_id is not None
            and isinstance(switch, Mapping)
            and switch.get("class_type") == "ComfySwitchNode"
        ):
            return switch_binding(switch_id, switch)
        if isinstance(switch, Mapping) and switch.get("class_type") == "PreviewAny":
            preview_inputs = switch.get("inputs")
            upstream_id = (
                linked_node_id(preview_inputs.get("source"))
                if isinstance(preview_inputs, Mapping)
                else None
            )
            upstream = graph.get(upstream_id) if upstream_id is not None else None
            if (
                upstream_id is not None
                and isinstance(upstream, Mapping)
                and upstream.get("class_type") == "ComfySwitchNode"
            ):
                return switch_binding(upstream_id, upstream)
        return None

    @classmethod
    def _infer_bindings(
        cls,
        graph: Mapping[str, Any],
        explicit: Mapping[str, Any],
        fields: set[str],
    ) -> dict[str, Any]:
        inferred: dict[str, Any] = {}
        canonical_fields = {
            "reference" if field == "input_reference" else field for field in fields
        }

        def missing(field: str) -> bool:
            if field != "size":
                return cls._binding_for(explicit, field) is None
            return not any(
                cls._binding_for(explicit, "size", suffix) is not None
                for suffix in (None, "width", "height")
            )

        if "prompt" in canonical_fields and missing("prompt"):
            qwen_types = {"TextEncodeQwenImage21"}
            qwen_candidates = {
                str(node_id): node
                for node_id, node in graph.items()
                if isinstance(node, Mapping) and node.get("class_type") in qwen_types
            }
            if len(qwen_candidates) > 1:
                raise AppError(
                    "comfyui_binding_ambiguous",
                    "Невозможно однозначно определить binding для prompt",
                    status_code=400,
                    details={"field": "prompt", "candidates": sorted(qwen_candidates)},
                )
            if qwen_candidates:
                qwen_id, qwen_node = next(iter(qwen_candidates.items()))
                qwen_binding = cls._qwen_prompt_binding(graph, qwen_id, qwen_node)
                if qwen_binding is None:
                    raise cls._unsupported("prompt")
                inferred["prompt"] = qwen_binding
            else:
                prompt_types = {
                    "CLIPTextEncode",
                    "CLIPTextEncodeSDXL",
                    "CLIPTextEncodeSDXLRefiner",
                }
                prompt_candidates = {
                    str(node_id): node
                    for node_id, node in graph.items()
                    if isinstance(node, Mapping)
                    and isinstance(node.get("inputs"), Mapping)
                    and node.get("class_type") in prompt_types
                    and "text" in node["inputs"]
                }
                positive_ids: set[str] = set()
                negative_ids: set[str] = set()
                for node in graph.values():
                    if not isinstance(node, Mapping) or not isinstance(node.get("inputs"), Mapping):
                        continue
                    for role, ids in (("positive", positive_ids), ("negative", negative_ids)):
                        link = node["inputs"].get(role)
                        if (
                            isinstance(link, (list, tuple))
                            and link
                            and str(link[0]) in prompt_candidates
                        ):
                            ids.add(str(link[0]))
                prompt_binding: dict[str, str] | None
                if len(positive_ids) == 1:
                    prompt_binding = {"node": next(iter(positive_ids)), "input": "text"}
                elif len(positive_ids) > 1:
                    raise AppError(
                        "comfyui_binding_ambiguous",
                        "Невозможно однозначно определить binding для prompt",
                        status_code=400,
                        details={"field": "prompt", "candidates": sorted(positive_ids)},
                    )
                elif negative_ids:
                    # A negative encoder is never a safe prompt fallback.  The
                    # positive conditioner may be a custom node that we do not
                    # understand, so leave the parameter explicitly unsupported.
                    prompt_binding = None
                else:
                    prompt_binding = cls._unique_inferred_binding(
                        graph,
                        "prompt",
                        lambda node: (
                            "text"
                            if node.get("class_type") in prompt_types and "text" in node["inputs"]
                            else None
                        ),
                    )
                if prompt_binding is not None:
                    inferred["prompt"] = prompt_binding

        def load_image_input(node: Mapping[str, Any]) -> str | None:
            if node.get("class_type") == "LoadImage" and "image" in node["inputs"]:
                return "image"
            return None

        if "image" in canonical_fields and missing("image"):
            image_binding = cls._unique_inferred_binding(graph, "image", load_image_input)
            if image_binding is not None:
                inferred["image"] = image_binding
        if "reference" in canonical_fields and missing("reference"):
            reference_binding = cls._unique_inferred_binding(graph, "reference", load_image_input)
            if reference_binding is not None:
                inferred["reference"] = reference_binding

        if "mask" in canonical_fields and missing("mask"):
            mask_binding = cls._unique_inferred_binding(
                graph,
                "mask",
                lambda node: (
                    "image"
                    if node.get("class_type") == "LoadImageMask" and "image" in node["inputs"]
                    else None
                ),
            )
            if mask_binding is not None:
                inferred["mask"] = mask_binding

        latent_types = {
            "EmptyLatentImage",
            "EmptySD3LatentImage",
            "EmptyLatentVideo",
            "EmptyMochiLatentVideo",
        }

        def latent_input(name: str) -> Any:
            return lambda node: (
                name if node.get("class_type") in latent_types and name in node["inputs"] else None
            )

        if "size" in canonical_fields and missing("size"):
            width = cls._unique_inferred_binding(graph, "size.width", latent_input("width"))
            height = cls._unique_inferred_binding(graph, "size.height", latent_input("height"))
            if width is not None and height is not None and width["node"] == height["node"]:
                inferred["size"] = {"width": width, "height": height}
        if "n" in canonical_fields and missing("n"):
            n_binding = cls._unique_inferred_binding(graph, "n", latent_input("batch_size"))
            if n_binding is not None:
                inferred["n"] = n_binding
        return inferred

    @staticmethod
    def _set(graph: dict[str, Any], binding: Mapping[str, str], value: Any, field: str) -> None:
        node_id, input_name = binding["node"], binding["input"]
        node = graph.get(node_id)
        if not isinstance(node, Mapping) or not isinstance(node.get("inputs"), Mapping):
            raise AppError(
                "comfyui_binding_invalid",
                f"Binding {field} указывает на неизвестный node",
                status_code=400,
                details={"field": field},
            )
        # Graph nodes are copied from JSON and each input map is mutable.
        inputs = cast(dict[str, Any], node["inputs"])
        if input_name not in inputs:
            raise AppError(
                "comfyui_binding_invalid",
                f"Binding {field} указывает на отсутствующий вход workflow",
                status_code=400,
                details={"field": field},
            )
        inputs[input_name] = value

    @staticmethod
    def _unsupported(field: str) -> AppError:
        return AppError(
            "comfyui_parameter_unsupported",
            f"Workflow не поддерживает параметр {field}",
            status_code=400,
            details={"field": field},
        )

    @staticmethod
    def _validate_param(field: str, value: Any) -> Any:
        if field == "prompt" and (not isinstance(value, str) or not value.strip()):
            raise AppError(
                "comfyui_parameter_invalid",
                "prompt должен быть непустой строкой",
                status_code=400,
                details={"field": field},
            )
        if field == "n" and (not isinstance(value, int) or isinstance(value, bool) or value < 1):
            raise AppError(
                "comfyui_parameter_invalid",
                "n должен быть положительным целым числом",
                status_code=400,
                details={"field": field},
            )
        if field == "quality" and value not in {"auto", "low", "medium", "high", "standard", "hd"}:
            raise AppError(
                "comfyui_parameter_invalid",
                "quality имеет недопустимое значение",
                status_code=400,
                details={"field": field},
            )
        if field == "background" and value not in {"auto", "transparent", "opaque"}:
            raise AppError(
                "comfyui_parameter_invalid",
                "background имеет недопустимое значение",
                status_code=400,
                details={"field": field},
            )
        if field == "output_format" and value not in {"png", "jpeg", "webp"}:
            raise AppError(
                "comfyui_parameter_invalid",
                "output_format имеет недопустимое значение",
                status_code=400,
                details={"field": field},
            )
        if field == "output_compression" and (
            not isinstance(value, int) or isinstance(value, bool) or not 0 <= value <= 100
        ):
            raise AppError(
                "comfyui_parameter_invalid",
                "output_compression должен быть целым числом от 0 до 100",
                status_code=400,
                details={"field": field},
            )
        if field == "seconds" and (
            not isinstance(value, int) or isinstance(value, bool) or not 1 <= value <= 600
        ):
            if isinstance(value, str) and value.strip().isdigit():
                value = int(value.strip())
            else:
                raise AppError(
                    "comfyui_parameter_invalid",
                    "seconds должен быть целым числом от 1 до 600",
                    status_code=400,
                    details={"field": field},
                )
            if not 1 <= value <= 600:
                raise AppError(
                    "comfyui_parameter_invalid",
                    "seconds должен быть целым числом от 1 до 600",
                    status_code=400,
                    details={"field": field},
                )
        return value

    def patch_graph(
        self,
        graph: Mapping[str, Any],
        model: Mapping[str, Any],
        operation: str,
        params: Mapping[str, Any],
        *,
        uploaded_inputs: Mapping[str, str] | None = None,
        provided_fields: set[str] | None = None,
    ) -> dict[str, Any]:
        result = copy.deepcopy(dict(graph))
        bindings = dict(self._bindings(model, operation))
        uploaded = uploaded_inputs or {}
        fields = provided_fields if provided_fields is not None else set(params)
        for field, binding in self._infer_bindings(result, bindings, fields).items():
            bindings.setdefault(field, binding)
        for field in sorted(fields):
            if field in {"model", "response_format"}:
                continue
            elif field not in self._PARAM_FIELDS and field not in {"seed", "steps", "transparent"}:
                raise self._unsupported(field)
            value = uploaded.get(field, params.get(field))
            if field == "input_reference":
                field = "reference"
                value = uploaded.get("reference", params.get("input_reference"))
            if field != "size":
                value = self._validate_param(field, value)
            if field == "size":
                raw_size = value
                if raw_size == "auto":
                    automatic = self._binding_for(bindings, "size")
                    if automatic is None:
                        raise self._unsupported("size")
                    self._set(result, automatic, raw_size, "size")
                    continue
                if not isinstance(raw_size, str) or "x" not in raw_size.lower():
                    raise AppError(
                        "invalid_image_size",
                        "Размер должен иметь вид WIDTHxHEIGHT",
                        status_code=400,
                        details={"field": "size"},
                    )
                try:
                    width, height = (int(part) for part in raw_size.lower().split("x", 1))
                except ValueError as exc:
                    raise AppError(
                        "invalid_image_size",
                        "Размер должен иметь вид WIDTHxHEIGHT",
                        status_code=400,
                        details={"field": "size"},
                    ) from exc
                if width < 1 or height < 1:
                    raise AppError(
                        "invalid_image_size",
                        "Ширина и высота должны быть положительными числами",
                        status_code=400,
                        details={"field": "size"},
                    )
                width_binding = self._binding_for(bindings, "size", "width")
                height_binding = self._binding_for(bindings, "size", "height")
                direct_binding = self._binding_for(bindings, "size")
                if width_binding and height_binding:
                    self._set(result, width_binding, width, "size.width")
                    self._set(result, height_binding, height, "size.height")
                elif direct_binding:
                    self._set(result, direct_binding, raw_size, "size")
                else:
                    raise self._unsupported("size")
                continue
            binding = self._binding_for(bindings, field)
            if binding is None:
                raise self._unsupported(field)
            self._set(result, binding, value, field)
        return result

    async def _upload_inputs(
        self, client: ComfyUIClient, params: Mapping[str, Any], fields: set[str]
    ) -> dict[str, str]:
        uploaded: dict[str, str] = {}
        for field in ("image", "mask", "reference", "input_reference"):
            if field not in fields or params.get(field) is None:
                continue
            content = self._decode_input(params[field], field)
            canonical = "reference" if field == "input_reference" else field
            uploaded[canonical] = await client.upload_image(
                content,
                filename=f"neural-deck-{uuid4().hex}.png",
            )
        return uploaded

    async def submit(
        self,
        alias: str,
        operation: str,
        params: Mapping[str, Any],
        *,
        request_id: str | None = None,
        provided_fields: set[str] | None = None,
    ) -> tuple[ComfyUIClient, str, Mapping[str, Any]]:
        client, source, model = self._client_for_model(alias)
        graph = await self.load_graph(alias, operation)
        fields = set(provided_fields if provided_fields is not None else params)
        uploaded = await self._upload_inputs(client, params, fields)
        patched = self.patch_graph(
            graph, model, operation, params, uploaded_inputs=uploaded, provided_fields=fields
        )
        prompt_id = await client.prompt(patched, client_id=request_id)
        return client, source, {"model": model, "prompt_id": prompt_id, "graph": patched}

    @staticmethod
    def _history_entry(payload: Any, prompt_id: str) -> Mapping[str, Any] | None:
        if isinstance(payload, Mapping):
            entry = payload.get(prompt_id)
            return entry if isinstance(entry, Mapping) else None
        return None

    @staticmethod
    def _queue_has_prompt(payload: Any, queue_name: str, prompt_id: str) -> bool:
        if not isinstance(payload, Mapping):
            return False
        entries = payload.get(queue_name)
        if not isinstance(entries, list):
            return False
        for item in entries:
            if isinstance(item, Mapping) and str(item.get("prompt_id", "")) == prompt_id:
                return True
            if isinstance(item, (list, tuple)) and len(item) > 1 and str(item[1]) == prompt_id:
                return True
        return False

    @staticmethod
    def _outputs(entry: Mapping[str, Any]) -> list[dict[str, str]]:
        outputs = entry.get("outputs")
        if not isinstance(outputs, Mapping):
            return []
        result: list[dict[str, str]] = []
        seen: set[tuple[str, str, str]] = set()
        for node_output in outputs.values():
            if not isinstance(node_output, Mapping):
                continue
            for key in ("images", "gifs", "videos", "files"):
                values = node_output.get(key)
                if not isinstance(values, list):
                    continue
                for value in values:
                    if not isinstance(value, Mapping) or not isinstance(value.get("filename"), str):
                        continue
                    item = {
                        "filename": value["filename"],
                        "subfolder": str(value.get("subfolder", "")),
                        "type": str(value.get("type", "output")),
                    }
                    identity = (item["filename"], item["subfolder"], item["type"])
                    if identity not in seen:
                        seen.add(identity)
                        result.append(item)
        return result

    async def wait_outputs(self, client: ComfyUIClient, prompt_id: str) -> list[dict[str, str]]:
        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            payload = await client.history(prompt_id)
            entry = self._history_entry(payload, prompt_id)
            if entry is not None:
                status = entry.get("status")
                if isinstance(status, Mapping) and status.get("status_str") in {
                    "error",
                    "failed",
                    "failure",
                }:
                    raise AppError(
                        "comfyui_execution_failed",
                        "ComfyUI завершил workflow с ошибкой",
                        status_code=502,
                        details={"status": dict(status)},
                    )
                result = self._outputs(entry)
                if result:
                    return result
                if isinstance(status, Mapping) and status.get("status_str") == "success":
                    raise AppError(
                        "comfyui_output_missing",
                        "Workflow завершён без выходных файлов",
                        status_code=502,
                    )
            await asyncio.sleep(self.poll_interval)
        raise AppError(
            "comfyui_timeout",
            "ComfyUI не завершил workflow за отведённое время",
            status_code=504,
            retryable=True,
        )

    async def download_outputs(
        self, client: ComfyUIClient, outputs: Sequence[Mapping[str, str]]
    ) -> list[ComfyOutput]:
        result: list[ComfyOutput] = []
        for item in outputs:
            content, media_type = await client.view(item)
            result.append(
                ComfyOutput(
                    item["filename"],
                    item.get("subfolder", ""),
                    item.get("type", "output"),
                    content,
                    media_type,
                    source_url=client.base_url,
                    user_context=client.user_context,
                )
            )
        return result

    async def run_image(
        self,
        alias: str,
        operation: str,
        params: Mapping[str, Any],
        *,
        request_id: str | None = None,
        provided_fields: set[str] | None = None,
    ) -> list[ComfyOutput]:
        client, _source, submitted = await self.submit(
            alias, operation, params, request_id=request_id, provided_fields=provided_fields
        )
        outputs = await self.wait_outputs(client, str(submitted["prompt_id"]))
        return await self.download_outputs(client, outputs)

    async def create_video(
        self,
        alias: str,
        params: Mapping[str, Any],
        *,
        request_id: str | None = None,
        provided_fields: set[str] | None = None,
    ) -> ComfyVideoJob:
        model = self._model(alias)
        workflows = model.get("workflows")
        has_reference = (
            params.get("reference") is not None or params.get("input_reference") is not None
        )
        operation = (
            "edit"
            if has_reference and isinstance(workflows, Mapping) and workflows.get("edit")
            else "generate"
        )
        client, source, submitted = await self.submit(
            alias, operation, params, request_id=request_id, provided_fields=provided_fields
        )
        raw_seconds = params.get("seconds")
        seconds = str(raw_seconds) if raw_seconds is not None else None
        raw_size = params.get("size")
        size = raw_size if isinstance(raw_size, str) else None
        job = ComfyVideoJob(
            id=f"video_{uuid4().hex}",
            source=source,
            prompt_id=str(submitted["prompt_id"]),
            status="queued",
            model=alias,
            seconds=seconds,
            size=size,
            prompt=str(params.get("prompt")) if params.get("prompt") is not None else None,
            created_at=time.time(),
            source_url=client.base_url,
            user_context=client.user_context,
        )
        await self.video_store.put(job)
        return job

    async def video_status(self, video_id: str) -> ComfyVideoJob:
        job = await self.video_store.get(video_id)
        if job is None:
            raise AppError("video_not_found", "Видео не найдено", status_code=404)
        if job.status in {"completed", "failed"}:
            return job
        # The source is persisted, so status can be checked after process restart.
        if job.source_url:
            source_url, context = job.source_url, job.user_context
        else:
            source_url, context = self._source(job.source)
        client = ComfyUIClient(
            source_url,
            user_context=context,
            client_factory=self.client_factory,
            timeout=self.timeout,
        )
        payload = await client.history(job.prompt_id)
        entry = self._history_entry(payload, job.prompt_id)
        if not entry:
            queue = await client.queue()
            if self._queue_has_prompt(queue, "queue_running", job.prompt_id):
                updated = replace(job, status="in_progress")
            elif self._queue_has_prompt(queue, "queue_pending", job.prompt_id):
                updated = replace(job, status="queued")
            else:
                raise AppError(
                    "comfyui_job_unknown",
                    "ComfyUI не содержит это задание ни в истории, ни в очереди. "
                    "Проверку состояния можно повторить; генерация повторно не отправляется.",
                    status_code=502,
                )
            if updated != job:
                await self.video_store.put(updated)
            return updated
        status_value = entry.get("status")
        result = self._outputs(entry)
        if isinstance(status_value, Mapping) and status_value.get("status_str") in {
            "error",
            "failed",
            "failure",
        }:
            updated = replace(
                job,
                status="failed",
                error={
                    "code": "comfyui_execution_failed",
                    "message": "ComfyUI execution failed",
                },
            )
        elif result:
            updated = replace(job, status="completed", result=tuple(result))
        else:
            updated = job
            if isinstance(status_value, Mapping) and status_value.get("status_str") == "success":
                updated = replace(
                    job,
                    status="failed",
                    error={
                        "code": "comfyui_output_missing",
                        "message": "Workflow завершён без выходного видео",
                    },
                )
            elif isinstance(status_value, Mapping) and status_value.get("status_str") == "running":
                updated = replace(job, status="in_progress")
        if updated != job:
            await self.video_store.put(updated)
        return updated

    async def video_content(self, video_id: str) -> tuple[bytes, str]:
        job = await self.video_status(video_id)
        if not job.result:
            raise AppError("video_not_ready", "Видео ещё не готово", status_code=409)
        if job.source_url:
            source_url, context = job.source_url, job.user_context
        else:
            source_url, context = self._source(job.source)
        client = ComfyUIClient(
            source_url,
            user_context=context,
            client_factory=self.client_factory,
            timeout=self.timeout,
        )
        return await client.view(job.result[0])

    async def register_asset(self, output: ComfyOutput) -> str:
        if output.source_url is None:
            raise AppError(
                "comfyui_output_unavailable", "У результата отсутствует источник", status_code=502
            )
        asset_id = uuid4().hex
        await self.media_store.put(
            ComfyMediaAsset(
                id=asset_id,
                source_url=output.source_url,
                filename=output.filename,
                subfolder=output.subfolder,
                output_type=output.output_type,
                media_type=output.media_type,
                user_context=output.user_context,
            )
        )
        return asset_id

    async def asset(self, asset_id: str) -> tuple[bytes, str, str]:
        item = await self.media_store.get(asset_id)
        if item is None:
            raise AppError("image_not_found", "Изображение не найдено", status_code=404)
        client = ComfyUIClient(
            item.source_url,
            user_context=item.user_context,
            client_factory=self.client_factory,
            timeout=self.timeout,
        )
        content, media_type = await client.view(
            {"filename": item.filename, "subfolder": item.subfolder, "type": item.output_type}
        )
        return content, media_type, item.filename

    async def close(self) -> None:
        return None


class ComfyProviderService:
    """Stable integration facade for API routes and the frontend settings page."""

    def __init__(
        self,
        repository: RouteConfigRepository | Mapping[str, Any],
        sqlite_path: Path | None = None,
        *,
        session_factory: async_sessionmaker[AsyncSession] | None = None,
        client_factory: Any | None = None,
        cache_path: Path | None = None,
        timeout: float = 1800.0,
        catalog_timeout: float = 5.0,
        poll_interval: float = 0.25,
    ) -> None:
        self.repository = repository
        self._owned_engine: Any | None = None
        if session_factory is None and sqlite_path is not None:
            from ..db import create_engine, create_session_factory

            self._owned_engine = create_engine(sqlite_path)
            session_factory = create_session_factory(self._owned_engine)
        if cache_path is None and sqlite_path is not None:
            cache_path = sqlite_path.with_name("comfyui-workflow-cache.json")
        self.provider = ComfyUIProvider(
            repository,
            client_factory=client_factory,
            cache_path=cache_path,
            session_factory=session_factory,
            timeout=timeout,
            catalog_timeout=catalog_timeout,
            poll_interval=poll_interval,
        )
        # Import lazily to avoid image -> proxy import cycles at module load.
        from ..image.comfy import ComfyImageService

        self.images = ComfyImageService(self.provider)

    def get_settings(self) -> dict[str, Any]:
        repository = cast(Any, self.repository)
        if isinstance(self.repository, RouteConfigRepository) or hasattr(
            self.repository, "load_comfyui"
        ):
            return _with_default_source(repository.load_comfyui())
        if hasattr(self.repository, "load"):
            loaded = repository.load()
            if isinstance(loaded, Mapping) and isinstance(loaded.get("comfyui"), Mapping):
                return _with_default_source(loaded["comfyui"])
        if isinstance(self.repository, Mapping):
            section = self.repository.get("comfyui", self.repository)
            return (
                _with_default_source(section)
                if isinstance(section, Mapping)
                else _with_default_source({})
            )
        return _with_default_source({})

    def save_settings(self, value: Mapping[str, Any]) -> dict[str, Any]:
        repository = cast(Any, self.repository)
        if isinstance(self.repository, RouteConfigRepository) or hasattr(
            self.repository, "save_comfyui"
        ):
            repository.save_comfyui(value)
            return self.get_settings()
        if hasattr(self.repository, "save"):
            config = repository.load()
            if not isinstance(config, dict):
                raise AppError(
                    "comfyui_config_readonly",
                    "ComfyUI конфигурация доступна только для чтения",
                    status_code=500,
                )
            config["comfyui"] = dict(value)
            repository.save(config)
            return self.get_settings()
        if isinstance(self.repository, dict):
            # A plain mapping is useful in unit tests and embedded integrations;
            # mutate only its comfyui section and leave other providers untouched.
            self.repository["comfyui"] = dict(value)
            return _with_default_source(value)
        raise AppError(
            "comfyui_config_readonly",
            "ComfyUI конфигурация доступна только для чтения",
            status_code=500,
        )

    async def workflows(self, source: str) -> dict[str, Any]:
        base_url, context = self.provider._source(source)
        items, available = await self.provider.list_workflows_with_status(source)
        after = self.provider.catalog.cache_metadata(base_url, user_context=context)
        read_at = after.get("read_at") if after else None
        return {
            "items": [item["path"] for item in items],
            "available": available,
            "cached": bool(after) and not available,
            "updated_at": read_at,
        }

    async def source_status(self, source: str) -> dict[str, Any]:
        return await self.provider.source_status(source)

    async def validate_workflow(self, source: str, path: str) -> dict[str, Any]:
        return await self.provider.validate_workflow(source, path)

    async def generate_image(
        self, request: Mapping[str, Any], *, request_id: str | None = None, url_prefix: str = "/v1"
    ) -> dict[str, Any]:
        return await self.images.generate(request, request_id=request_id, url_prefix=url_prefix)

    async def asset(self, asset_id: str) -> tuple[bytes, str, str]:
        return await self.images.asset(asset_id)

    async def create_video(
        self, request: Mapping[str, Any], *, request_id: str | None = None
    ) -> dict[str, Any]:
        return await self.images.create_video(request, request_id=request_id)

    async def video_status(self, video_id: str) -> dict[str, Any]:
        return await self.images.video_status(video_id)

    async def video_content(self, video_id: str) -> tuple[bytes, str]:
        return await self.images.video_content(video_id)

    async def video_list(self) -> list[dict[str, Any]]:
        return await self.images.video_list()

    async def video_delete(self, video_id: str) -> bool:
        return await self.images.video_delete(video_id)

    async def close(self) -> None:
        await self.images.close()
        if self._owned_engine is not None:
            await self._owned_engine.dispose()


__all__ = [
    "ComfyOutput",
    "ComfyMediaAsset",
    "ComfyMediaStore",
    "ComfyProviderService",
    "ComfyUIClient",
    "ComfyUIProvider",
    "ComfyUIUnavailable",
    "ComfyVideoJob",
    "ComfyVideoStore",
    "ComfyWorkflowCatalog",
]
