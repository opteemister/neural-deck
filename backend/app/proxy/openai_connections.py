"""Saved OpenAI servers. Credentials never leave the backend configuration."""

from __future__ import annotations

import asyncio
import os
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from threading import RLock
from typing import Any
from urllib.parse import urlsplit
from uuid import uuid4

import httpx
import yaml

from ..documents import document_capabilities
from ..errors import AppError
from ..profiles.repository import StrictYamlLoader


def endpoint(base_url: str, route: str) -> str:
    base = base_url.rstrip("/")
    return base + (route[3:] if base.endswith("/v1") and route.startswith("/v1/") else route)


@dataclass(frozen=True)
class RemoteTarget:
    connection_id: str
    upstream_model: str
    base_url: str
    api_key: str = field(repr=False)
    # Remote requests have no local process/profile to attribute in the journal.
    engine_id: str = "openai"
    profile_name: None = None
    profile_id: None = None
    instance_id: None = None


class OpenAIConnections:
    def __init__(self, config_dir: Path, client_factory: Any = None) -> None:
        self.path = config_dir / "openai-connections.yaml"
        self.client_factory = client_factory
        self.lock = RLock()
        self.cache: dict[str, tuple[tuple[str, str], float, list[dict[str, Any]]]] = {}

    def _load(self) -> dict[str, dict[str, Any]]:
        if not self.path.exists():
            return {}
        try:
            value = yaml.load(self.path.read_text(encoding="utf-8"), Loader=StrictYamlLoader)
            if not isinstance(value, dict) or any(
                not isinstance(key, str) or not isinstance(item, dict)
                or not isinstance(item.get("name"), str)
                or not isinstance(item.get("base_url"), str)
                or not isinstance(item.get("api_key", ""), str)
                for key, item in value.items()
            ):
                raise ValueError
            return value
        except (OSError, UnicodeError, yaml.YAMLError, ValueError):
            # YAML diagnostics can contain a credential-bearing source line.
            raise AppError("connection_config_invalid", "Не удалось прочитать OpenAI-подключения", status_code=422) from None

    def _write(self, values: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(dir=self.path.parent, prefix=".openai-connections-")
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as file:
                yaml.safe_dump(values, file, allow_unicode=True, sort_keys=False)
                file.flush()
                os.fsync(file.fileno())
            os.replace(temporary, self.path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    @staticmethod
    def public(connection_id: str, value: dict[str, Any]) -> dict[str, Any]:
        return {"id": connection_id, "name": value["name"], "base_url": value["base_url"], "has_api_key": bool(value.get("api_key"))}

    def list(self) -> list[dict[str, Any]]:
        return [self.public(key, value) for key, value in self._load().items()]

    def get(self, connection_id: str) -> dict[str, Any]:
        value = self._load().get(connection_id)
        if value is None:
            raise AppError("connection_not_found", "OpenAI-подключение не найдено", status_code=404)
        return value

    def save(self, connection_id: str | None, name: str, base_url: str, api_key: str | None) -> dict[str, Any]:
        name, base_url = name.strip(), base_url.strip().rstrip("/")
        try:
            parsed = urlsplit(base_url)
            parsed.port
            valid = parsed.scheme in {"http", "https"} and bool(parsed.hostname)
        except ValueError:
            valid = False
        if not valid or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise AppError("connection_url_invalid", "Укажите полный HTTP(S) URL без ключа, логина и query-параметров", status_code=422)
        if not name or len(name) > 128:
            raise AppError("connection_name_invalid", "Укажите имя подключения до 128 символов", status_code=422)
        if api_key is not None and (not api_key.isascii() or any(ord(char) < 32 or ord(char) == 127 for char in api_key)):
            raise AppError("connection_key_invalid", "API-ключ содержит недопустимые символы", status_code=422)
        with self.lock:
            values = self._load()
            if connection_id is not None and connection_id not in values:
                raise AppError("connection_not_found", "OpenAI-подключение не найдено", status_code=404)
            connection_id = connection_id or uuid4().hex
            if any(key != connection_id and item["name"] == name for key, item in values.items()):
                raise AppError("connection_conflict", "Подключение с таким именем уже существует", status_code=409)
            previous = values.get(connection_id, {})
            value = {"name": name, "base_url": base_url, "api_key": previous.get("api_key", "") if api_key is None else api_key.strip()}
            values[connection_id] = value
            self._write(values)
            self.cache.pop(connection_id, None)
            return self.public(connection_id, value)

    def delete(self, connection_id: str) -> None:
        with self.lock:
            values = self._load()
            if connection_id not in values:
                raise AppError("connection_not_found", "OpenAI-подключение не найдено", status_code=404)
            del values[connection_id]
            self._write(values)
            self.cache.pop(connection_id, None)

    async def models(self, connection_id: str, *, refresh: bool = False) -> list[dict[str, Any]]:
        value = await asyncio.to_thread(self.get, connection_id)
        identity = (value["base_url"], value.get("api_key", ""))
        cached = self.cache.get(connection_id)
        if not refresh and cached and cached[0] == identity and time.monotonic() - cached[1] < 30:
            return cached[2]
        headers = {"Authorization": f"Bearer {identity[1]}"} if identity[1] else {}
        client = self.client_factory() if self.client_factory else httpx.AsyncClient(timeout=3, follow_redirects=False)
        try:
            async with client:
                response = await client.get(endpoint(identity[0], "/v1/models"), headers=headers, timeout=3, follow_redirects=False)
                if response.status_code != 200:
                    raise AppError("connection_upstream_error", f"Сервер моделей вернул HTTP {response.status_code}", status_code=502)
                items = response.json().get("data")
                if not isinstance(items, list) or any(not isinstance(item, dict) or not isinstance(item.get("id"), str) or not item["id"] for item in items):
                    raise ValueError
                keys = ("id", "context_length", "reasoning_capabilities", "types", "max_model_len", "max_context_length",
                        "supported_reasoning_efforts", "default_reasoning_effort", "supports_enable_thinking")
                models = [{**{key: item[key] for key in keys if key in item}, **document_capabilities(item)} for item in items]
                self.cache[connection_id] = (identity, time.monotonic(), models)
                return models
        except httpx.HTTPError:
            raise AppError("connection_unavailable", "Не удалось подключиться к OpenAI-серверу", status_code=502) from None
        except (ValueError, TypeError, AttributeError):
            raise AppError("connection_models_invalid", "Сервер вернул неверный список моделей", status_code=502) from None
