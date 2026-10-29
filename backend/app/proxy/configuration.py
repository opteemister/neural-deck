"""Entity operations for the Proxy page, shared by HTTP and MCP adapters."""

from copy import deepcopy
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

from ..errors import AppError
from ..schemas.proxy_management import ComfySourceRequest, MediaAliasRequest
from .routes import RouteConfigRepository

if TYPE_CHECKING:
    from .comfy_provider import ComfyProviderService
    from .service import ProxyService


class ProxyConfiguration:
    def __init__(self, proxy: "ProxyService", comfy: "ComfyProviderService | None") -> None:
        self.proxy = proxy
        self.comfy = comfy

    def get(self) -> dict[str, Any]:
        if self.comfy is None:
            raise AppError("comfyui_unavailable", "ComfyUI adapter не запущен", status_code=503)
        return deepcopy(self.comfy.get_settings())

    async def save(self, payload: dict[str, Any]) -> dict[str, Any]:
        normalized = RouteConfigRepository._comfyui(payload)
        current = self.get()
        routes = await self.proxy.route_data()
        collisions = set(normalized["models"]) & {route["name"] for route in routes["items"]}
        if collisions:
            raise AppError("route_conflict", "Имя уже занято маршрутом модели", status_code=409, details={"models": sorted(collisions)})
        if self.get() != current:
            raise AppError("configuration_changed", "Конфигурация изменилась; повторите сохранение", status_code=409)
        return self.comfy.save_settings(normalized)

    def _persist(self, config: dict[str, Any]) -> dict[str, Any]:
        return self.comfy.save_settings(RouteConfigRepository._comfyui(config))

    @staticmethod
    def _existing(values: dict[str, Any], name: str | None) -> dict[str, Any]:
        if name is None:
            return {}
        if name not in values:
            raise AppError("route_not_found", "Подключение или алиас не найден", status_code=404)
        return values[name]

    @staticmethod
    def _local(name: str | None, source: dict[str, Any]) -> None:
        if name == "local" and source.get("base_url", "").rstrip("/") == "http://127.0.0.1:8188":
            raise AppError("default_source_readonly", "Стандартное подключение local нельзя изменить или удалить", status_code=409)

    def save_source(self, payload: ComfySourceRequest, previous_name: str | None = None) -> dict[str, Any]:
        config = self.get()
        previous = self._existing(config["sources"], previous_name)
        if previous_name == "local" and (payload.name != "local" or payload.base_url.rstrip("/") != previous.get("base_url")):
            self._local(previous_name, previous)
        if payload.name != previous_name and payload.name in config["sources"]:
            raise AppError("route_conflict", "Подключение уже существует", status_code=409)
        try:
            parsed = urlsplit(payload.base_url)
            port = parsed.port
        except ValueError:
            raise AppError("validation_error", "Некорректный порт ComfyUI", status_code=422) from None
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username is not None or parsed.password is not None or parsed.query or parsed.fragment or port == 0:
            raise AppError("validation_error", "Требуется HTTP(S) URL без ключей, query и fragment", status_code=422)
        if previous_name:
            del config["sources"][previous_name]
        config["sources"][payload.name] = {**previous, "base_url": payload.base_url.rstrip("/")}
        for model in config["models"].values():
            if previous_name is not None and model["source"] == previous_name:
                model["source"] = payload.name
        return self._persist(config)

    def delete_source(self, name: str) -> dict[str, Any]:
        config = self.get()
        source = self._existing(config["sources"], name)
        self._local(name, source)
        if any(model["source"] == name for model in config["models"].values()):
            raise AppError("connection_in_use", "Сначала уберите подключение из его алиасов", status_code=409)
        del config["sources"][name]
        return self._persist(config)

    async def save_alias(self, payload: MediaAliasRequest, previous_name: str | None = None) -> dict[str, Any]:
        config = self.get()
        source = self._existing(config["sources"], payload.source)
        workflows = {key: path for key, path in payload.workflows.model_dump().items() if path}
        # Validate paths before fetching any workflow; no filesystem access.
        normalized = RouteConfigRepository._comfyui({"sources": config["sources"], "models": {payload.name: {"source": payload.source, "type": payload.type, "workflows": workflows}}})
        workflows = normalized["models"][payload.name]["workflows"]
        routes = await self.proxy.route_data()
        if any(route["name"] == payload.name for route in routes["items"]):
            raise AppError("route_conflict", "Имя уже занято маршрутом модели", status_code=409)
        # Reload after awaits so a save never overwrites unrelated concurrent edits.
        config = self.get()
        if config["sources"].get(payload.source) != source:
            raise AppError("configuration_changed", "Подключение изменилось; повторите сохранение", status_code=409)
        previous = self._existing(config["models"], previous_name)
        if payload.name != previous_name and payload.name in config["models"]:
            raise AppError("route_conflict", "Алиас уже существует", status_code=409)
        if previous and previous["type"] != payload.type:
            raise AppError("validation_error", "Тип существующего алиаса нельзя изменить", status_code=422)
        model = {"type": payload.type, "source": payload.source, "workflows": workflows}
        fields = ("max_megapixels",) if payload.type == "image" else ("max_resolution", "max_seconds")
        model.update({field: getattr(payload, field) for field in fields})
        if previous_name:
            del config["models"][previous_name]
        config["models"][payload.name] = model
        return self._persist(config)

    def delete_alias(self, name: str) -> dict[str, Any]:
        config = self.get()
        self._existing(config["models"], name)
        del config["models"][name]
        return self._persist(config)
