"""Persistent route configuration and runtime route resolution."""

from __future__ import annotations

import os
import math
import re
import tempfile
from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import yaml  # type: ignore[import-untyped]

from ..errors import AppError
from ..profiles.repository import ProfileRepository, StrictYamlLoader
from ..schemas.instances import Instance
from ..schemas.profiles import ProfileData

ALIAS_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
ROUTE_CONFIG_FILENAME = "proxy-routes.yaml"
COMFYUI_DEFAULT_SOURCE = "local"
COMFYUI_DEFAULT_BASE_URL = "http://127.0.0.1:8188"


@dataclass(frozen=True, slots=True)
class RuntimeModel:
    """One managed runtime and the model metadata observed from its engine."""

    instance: Instance
    metadata: Mapping[str, dict[str, Any]]


@dataclass(frozen=True, slots=True)
class RouteCandidate:
    profile_id: str | None
    profile_name: str | None
    instance_id: str | None
    upstream_model: str | None
    engine_id: str | None
    port: int | None
    state: str
    available: bool
    reason: str | None
    metadata: Mapping[str, Any]
    id: str | None = None
    connection_id: str | None = None
    connection_name: str | None = None

    @property
    def candidate_id(self) -> str:
        if self.id is not None:
            return self.id
        assert self.profile_id is not None
        return self.profile_id

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.candidate_id,
            "profile_id": self.profile_id,
            "connection_id": self.connection_id,
            "connection_name": self.connection_name,
            "profile_name": self.profile_name,
            "instance_id": self.instance_id,
            "upstream_model": self.upstream_model,
            "engine_id": self.engine_id,
            "port": self.port,
            "state": self.state,
            "available": self.available,
            "reason": self.reason,
        }


@dataclass(frozen=True, slots=True)
class RouteInfo:
    name: str
    kind: str
    candidate_profile_ids: tuple[str, ...]
    candidates: tuple[RouteCandidate, ...]
    diagnostics: tuple[str, ...]

    @property
    def available(self) -> bool:
        return not self.diagnostics and any(candidate.available for candidate in self.candidates)

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "kind": self.kind,
            "candidate_profile_ids": list(self.candidate_profile_ids),
            "candidate_ids": [candidate.candidate_id for candidate in self.candidates],
            "candidates": [candidate.as_dict() for candidate in self.candidates],
            "available": self.available,
            "diagnostics": list(self.diagnostics),
        }

    def available_candidates(self) -> tuple[RouteCandidate, ...]:
        return tuple(candidate for candidate in self.candidates if candidate.available)


class RouteConfigRepository:
    """Read and atomically write the small human-editable route YAML."""

    def __init__(self, config_dir: Path) -> None:
        self.config_dir = config_dir
        self.path = config_dir / ROUTE_CONFIG_FILENAME
        self.config_dir.mkdir(parents=True, exist_ok=True)

    def load(self) -> dict[str, Any]:
        if not self.path.exists():
            return self._empty()
        try:
            raw = yaml.load(self.path.read_text(encoding="utf-8"), Loader=StrictYamlLoader)
        except (OSError, UnicodeError, yaml.YAMLError, ValueError) as exc:
            raise AppError(
                "route_config_invalid",
                f"Не удалось прочитать конфигурацию маршрутов: {exc}",
                status_code=422,
                details={"file": self.path.name},
            ) from exc
        if raw is None:
            return self._empty()
        if not isinstance(raw, Mapping):
            raise AppError(
                "route_config_invalid",
                "Корень конфигурации маршрутов должен быть YAML-объектом",
                status_code=422,
                details={"file": self.path.name},
            )
        return self._normalise(raw)

    def save(self, config: Mapping[str, Any]) -> None:
        payload = yaml.safe_dump(
            dict(config),
            allow_unicode=True,
            default_flow_style=False,
            sort_keys=False,
            width=120,
        )
        fd, temporary = tempfile.mkstemp(
            prefix=f".{self.path.name}.", suffix=".tmp", dir=self.config_dir
        )
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
            try:
                directory_fd = os.open(self.config_dir, os.O_DIRECTORY)
            except OSError:
                directory_fd = -1
            if directory_fd >= 0:
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
        except BaseException:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass
            raise

    @staticmethod
    def _empty() -> dict[str, Any]:
        return {
            "schema_version": 1,
            "auto_routes": {},
            "aliases": {},
            "remote_aliases": {},
            "alias_candidates": {},
            "comfyui": {"sources": {}, "models": {}},
        }

    @classmethod
    def _normalise(cls, raw: Mapping[str, Any]) -> dict[str, Any]:
        schema_version = raw.get("schema_version", 1)
        if schema_version != 1:
            raise AppError(
                "route_config_unsupported",
                f"Неподдерживаемая версия конфигурации маршрутов: {schema_version}",
                status_code=422,
            )
        result = cls._empty()
        result["schema_version"] = schema_version
        for key in ("auto_routes", "aliases"):
            value = raw.get(key, {})
            if not isinstance(value, Mapping):
                raise AppError(
                    "route_config_invalid",
                    f"Поле {key} конфигурации маршрутов должно быть объектом",
                    status_code=422,
                )
            result[key] = {
                str(name): cls._profile_list(value_item, key, str(name))
                for name, value_item in value.items()
            }
        remote_aliases = raw.get("remote_aliases", {})
        if not isinstance(remote_aliases, Mapping):
            raise AppError("route_config_invalid", "remote_aliases должен быть объектом", status_code=422)
        for name, target in remote_aliases.items():
            if name not in result["aliases"] or not isinstance(target, Mapping) or any(
                not isinstance(target.get(field), str) or not target[field].strip()
                for field in ("connection_id", "model")
            ) or result["aliases"][name]:
                raise AppError("route_config_invalid", "Неверный удалённый маршрут алиаса", status_code=422)
            result["remote_aliases"][name] = {field: target[field] for field in ("connection_id", "model")}
        candidate_sets = raw.get("alias_candidates", {})
        if not isinstance(candidate_sets, Mapping):
            raise AppError("route_config_invalid", "alias_candidates должен быть объектом", status_code=422)
        for name, targets in candidate_sets.items():
            if name not in result["aliases"] or name in result["remote_aliases"]:
                raise AppError("route_config_invalid", "Неоднозначные кандидаты алиаса", status_code=422)
            result["alias_candidates"][name] = cls.candidate_targets(targets)
            result["aliases"][name] = [item["profile_id"] for item in result["alias_candidates"][name] if "profile_id" in item]
        result["comfyui"] = cls._comfyui(raw.get("comfyui", {}))
        return result

    @classmethod
    def candidate_targets(cls, values: Any) -> list[dict[str, str]]:
        if not isinstance(values, list):
            raise AppError("route_membership_invalid", "Кандидаты должны быть списком", status_code=422)
        result = []
        for value in values:
            if not isinstance(value, Mapping) or set(value) not in ({"profile_id"}, {"connection_id", "model"}) or any(not isinstance(item, str) or not item.strip() for item in value.values()):
                raise AppError("route_membership_invalid", "Неверный кандидат алиаса", status_code=422)
            result.append({key: item.strip() for key, item in value.items()})
        identities = [RouteManager.candidate_id(value) for value in result]
        if len(identities) != len(set(identities)):
            raise AppError("duplicate_route_member", "Кандидат не должен повторяться", status_code=422)
        return result

    def load_comfyui(self) -> dict[str, Any]:
        """Return the validated ComfyUI section of the route configuration."""

        value = self.load().get("comfyui", {})
        return dict(value) if isinstance(value, Mapping) else {"sources": {}, "models": {}}

    @classmethod
    def validate_comfyui(cls, value: Mapping[str, Any]) -> dict[str, Any]:
        """Validate a ComfyUI section without reading or writing a file."""

        return cls._comfyui(value)

    def save_comfyui(self, value: Mapping[str, Any]) -> None:
        """Validate and persist only the ComfyUI section.

        The rest of ``proxy-routes.yaml`` is loaded and written unchanged so
        callers editing the provider settings cannot accidentally replace text
        aliases or auto-route order.
        """

        config = self.load()
        config["comfyui"] = self._comfyui(value)
        self.save(config)

    @classmethod
    def _comfyui(cls, value: Any) -> dict[str, Any]:
        if value is None:
            value = {}
        if not isinstance(value, Mapping):
            raise AppError(
                "route_config_invalid",
                "comfyui конфигурации маршрутов должен быть объектом",
                status_code=422,
            )

        sources_value = value.get("sources", {})
        models_value = value.get("models", {})
        if not isinstance(sources_value, Mapping):
            raise AppError(
                "route_config_invalid",
                "comfyui.sources должен быть объектом",
                status_code=422,
            )
        if not isinstance(models_value, Mapping):
            raise AppError(
                "route_config_invalid",
                "comfyui.models должен быть объектом",
                status_code=422,
            )

        sources: dict[str, dict[str, Any]] = {}
        for raw_name, raw_source in sources_value.items():
            name = str(raw_name)
            if not ALIAS_NAME_RE.fullmatch(name):
                raise AppError(
                    "route_config_invalid",
                    f"Недопустимое имя ComfyUI source: {name}",
                    status_code=422,
                    details={"field": f"comfyui.sources.{name}"},
                )
            if not isinstance(raw_source, Mapping):
                raise AppError(
                    "route_config_invalid",
                    f"comfyui.sources.{name} должен быть объектом",
                    status_code=422,
                )
            base_url = raw_source.get("base_url")
            if not isinstance(base_url, str) or not base_url.strip():
                raise AppError(
                    "route_config_invalid",
                    f"comfyui.sources.{name}.base_url должен быть непустым URL",
                    status_code=422,
                )
            parsed = urlparse(base_url.strip())
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                raise AppError(
                    "route_config_invalid",
                    f"comfyui.sources.{name}.base_url должен начинаться с http:// или https://",
                    status_code=422,
                )
            source: dict[str, Any] = {"base_url": base_url.strip().rstrip("/")}
            # A caller may supply a stable user/context key for installations
            # where the same ComfyUI URL serves more than one user namespace.
            if "user_context" in raw_source:
                context = raw_source["user_context"]
                if not isinstance(context, (str, int, float, bool)):
                    raise AppError(
                        "route_config_invalid",
                        f"comfyui.sources.{name}.user_context должен быть скаляром",
                        status_code=422,
                    )
                source["user_context"] = str(context)
            sources[name] = source

        models: dict[str, dict[str, Any]] = {}
        for raw_alias, raw_model in models_value.items():
            alias = str(raw_alias)
            if not ALIAS_NAME_RE.fullmatch(alias):
                raise AppError(
                    "route_config_invalid",
                    f"Недопустимое имя ComfyUI model alias: {alias}",
                    status_code=422,
                )
            if not isinstance(raw_model, Mapping):
                raise AppError(
                    "route_config_invalid",
                    f"comfyui.models.{alias} должен быть объектом",
                    status_code=422,
                )
            source_name = raw_model.get("source")
            if not isinstance(source_name, str) or not source_name:
                raise AppError(
                    "route_config_invalid",
                    f"comfyui.models.{alias}.source должен быть именем source",
                    status_code=422,
                )
            if source_name not in sources and source_name != COMFYUI_DEFAULT_SOURCE:
                raise AppError(
                    "route_config_invalid",
                    f"comfyui.models.{alias}.source не найден: {source_name}",
                    status_code=422,
                )
            model_type = raw_model.get("type")
            if model_type not in {"image", "video"}:
                raise AppError(
                    "route_config_invalid",
                    f"comfyui.models.{alias}.type должен быть image или video",
                    status_code=422,
                )
            workflows_value = raw_model.get("workflows", {})
            if not isinstance(workflows_value, Mapping):
                raise AppError(
                    "route_config_invalid",
                    f"comfyui.models.{alias}.workflows должен быть объектом",
                    status_code=422,
                )
            workflows: dict[str, str] = {}
            for operation, raw_path in workflows_value.items():
                operation_name = str(operation)
                if operation_name not in {"generate", "edit", "edit_masked", "variations"}:
                    raise AppError(
                        "route_config_invalid",
                        f"comfyui.models.{alias}.workflows: "
                        f"неизвестная операция {operation_name}",
                        status_code=422,
                    )
                if not isinstance(raw_path, str) or not raw_path.strip():
                    raise AppError(
                        "route_config_invalid",
                        f"comfyui.models.{alias}.workflows.{operation_name} должен быть путём",
                        status_code=422,
                    )
                workflow_path = raw_path.strip().replace("\\", "/")
                if workflow_path.startswith("/") or ".." in workflow_path.split("/"):
                    raise AppError(
                        "route_config_invalid",
                        f"comfyui.models.{alias}.workflows.{operation_name}: "
                        "требуется относительный путь",
                        status_code=422,
                    )
                workflows[operation_name] = workflow_path

            model: dict[str, Any] = {
                "source": source_name,
                "type": model_type,
                "workflows": workflows,
            }
            if model_type == "image" and "max_megapixels" in raw_model:
                megapixels = raw_model["max_megapixels"]
                if (
                    isinstance(megapixels, bool)
                    or not isinstance(megapixels, (int, float))
                    or not math.isfinite(megapixels)
                    or not 0.1 <= megapixels <= 128
                ):
                    raise AppError(
                        "route_config_invalid",
                        "Максимальное разрешение должно быть от 0.1 до 128 МП",
                        status_code=422,
                        details={"field": f"comfyui.models.{alias}.max_megapixels"},
                    )
                model["max_megapixels"] = megapixels
            if model_type == "video":
                for field, minimum, maximum, label in (
                    ("max_resolution", 32, 16384, "Максимальное разрешение"),
                    ("max_seconds", 1, 600, "Максимальная длительность"),
                ):
                    if field not in raw_model:
                        continue
                    limit = raw_model[field]
                    if isinstance(limit, bool) or not isinstance(limit, int) or not minimum <= limit <= maximum:
                        raise AppError(
                            "route_config_invalid",
                            f"{label}: требуется целое число от {minimum} до {maximum}",
                            status_code=422,
                            details={"field": f"comfyui.models.{alias}.{field}"},
                        )
                    model[field] = limit
            if "bindings" in raw_model:
                model["bindings"] = cls._comfy_bindings(raw_model["bindings"], alias)
            models[alias] = model

        return {"sources": sources, "models": models}

    @staticmethod
    def _comfy_bindings(value: Any, alias: str) -> dict[str, Any]:
        """Validate the deliberately small ``{node, input}`` binding shape.

        Bindings may be supplied per operation (``bindings.generate.prompt``)
        or as a mapping shared by all operations.  The provider resolves the
        operation-specific map first.  Lists are accepted as a compact
        ``[node, input]`` spelling but no expressions or node discovery are
        performed.
        """

        if not isinstance(value, Mapping):
            raise AppError(
                "route_config_invalid",
                f"comfyui.models.{alias}.bindings должен быть объектом",
                status_code=422,
            )
        result: dict[str, Any] = {}
        operations = {"generate", "edit", "edit_masked", "variations"}
        for key, item in value.items():
            key = str(key)
            # Operation maps are one level deeper; field maps are validated by
            # the same helper below.
            if key in operations:
                result[key] = RouteConfigRepository._comfy_binding_map(item, alias, key)
            else:
                result[key] = RouteConfigRepository._comfy_binding(item, alias, key)
        return result

    @staticmethod
    def _comfy_binding_map(value: Any, alias: str, operation: str) -> dict[str, Any]:
        if not isinstance(value, Mapping):
            raise AppError(
                "route_config_invalid",
                f"comfyui.models.{alias}.bindings.{operation} должен быть объектом",
                status_code=422,
            )
        return {
            str(field): RouteConfigRepository._comfy_binding(item, alias, str(field))
            for field, item in value.items()
        }

    @staticmethod
    def _comfy_binding(value: Any, alias: str, field: str) -> dict[str, Any]:
        if isinstance(value, (list, tuple)) and len(value) == 2:
            value = {"node": value[0], "input": value[1]}
        if not isinstance(value, Mapping):
            raise AppError(
                "route_config_invalid",
                f"comfyui.models.{alias}.bindings.{field} должен иметь node и input",
                status_code=422,
            )
        node = value.get("node")
        input_name = value.get("input")
        if node is None and input_name is None:
            if not value:
                raise AppError(
                    "route_config_invalid",
                    f"comfyui.models.{alias}.bindings.{field} не может быть пустым",
                    status_code=422,
                )
            return {
                str(key): RouteConfigRepository._comfy_binding(item, alias, f"{field}.{key}")
                for key, item in value.items()
            }
        if not isinstance(node, (str, int)) or not str(node):
            raise AppError(
                "route_config_invalid",
                f"comfyui.models.{alias}.bindings.{field}.node должен быть строкой/числом",
                status_code=422,
            )
        if not isinstance(input_name, str) or not input_name.strip():
            raise AppError(
                "route_config_invalid",
                f"comfyui.models.{alias}.bindings.{field}.input должен быть непустой строкой",
                status_code=422,
            )
        return {"node": str(node), "input": input_name.strip()}

    @staticmethod
    def _profile_list(value: Any, section: str, name: str) -> list[str]:
        if isinstance(value, Mapping):
            value = value.get("profiles", [])
        if not isinstance(value, list) or any(
            not isinstance(item, str) or not item for item in value
        ):
            raise AppError(
                "route_config_invalid",
                f"{section}.{name} должен содержать список имён профилей",
                status_code=422,
            )
        if len(value) != len(set(value)):
            raise AppError(
                "route_config_invalid",
                f"{section}.{name} содержит повторяющиеся профили",
                status_code=422,
            )
        return list(value)


class RouteManager:
    """Build public routes from profiles, config order and live instances."""

    def __init__(self, config_dir: Path, profile_repository: ProfileRepository) -> None:
        self.config = RouteConfigRepository(config_dir)
        self.profile_repository = profile_repository

    def profiles(self) -> list[ProfileData]:
        ensure_ids = getattr(self.profile_repository, "ensure_ids", None)
        if ensure_ids is not None:
            ensure_ids()
        profiles = self.profile_repository.list().items
        self._migrate_saved_refs(profiles)
        return profiles

    def _migrate_saved_refs(self, profiles: list[ProfileData]) -> None:
        """Canonicalise legacy name references in route YAML to profile IDs.

        A route file may predate stable profile IDs and therefore contain
        display names.  Names that no longer resolve are deliberately kept as
        dangling references so the management response can show
        ``profile_missing`` instead of silently dropping history.
        """
        config = self.config.load()
        by_name = {profile.name: profile.profile_id for profile in profiles if profile.profile_id}
        changed = False
        for section in ("auto_routes", "aliases"):
            for name, refs in config[section].items():
                migrated: list[str] = []
                for ref in refs:
                    canonical = by_name.get(ref, ref)
                    if canonical != ref:
                        changed = True
                    if canonical not in migrated:
                        migrated.append(canonical)
                    elif canonical != ref:
                        changed = True
                if migrated != refs:
                    config[section][name] = migrated
                    changed = True
        for targets in config["alias_candidates"].values():
            for target in targets:
                if "profile_id" in target:
                    canonical = by_name.get(target["profile_id"], target["profile_id"])
                    if canonical != target["profile_id"]:
                        target["profile_id"] = canonical
                        changed = True
        if changed:
            self.config.save(config)

    def snapshot(
        self,
        profiles: list[ProfileData],
        runtime: Mapping[str, RuntimeModel],
    ) -> list[RouteInfo]:
        config = self.config.load()
        by_ref = {
            reference: profile
            for profile in profiles
            for reference in (profile.profile_id, profile.name)
            if reference
        }
        grouped: dict[str, list[str]] = defaultdict(list)
        for profile in profiles:
            grouped[profile.model_name].append(profile.profile_id or profile.name)
        for refs in grouped.values():
            refs.sort(key=lambda reference: self._runtime_order_key(reference, runtime))

        route_names = set(grouped) | set(config["auto_routes"]) | set(config["aliases"])
        routes: list[RouteInfo] = []
        for name in sorted(route_names):
            explicit = config["aliases"].get(name)
            if explicit is None:
                live_refs = [reference for reference in grouped.get(name, []) if reference in runtime]
                refs = self._ordered(config["auto_routes"].get(name, []), live_refs)
            elif name in config["remote_aliases"]:
                refs = []
            elif name in config["alias_candidates"]:
                refs = list(explicit)
            else:
                refs = list(dict.fromkeys(explicit))
                live_auto = [
                    reference
                    for reference, item in runtime.items()
                    if reference not in refs
                    and (profile := by_ref.get(reference)) is not None
                    and profile.model_name == name
                    and item.instance.state not in {"exited", "error", "stopping"}
                ]
                live_auto.sort(key=lambda reference: self._runtime_order_key(reference, runtime))
                refs.extend(live_auto)
            routes.append(
                self._build_route(
                    name,
                    "alias" if explicit is not None else "auto",
                    refs,
                    by_ref,
                    runtime,
                )
            )
        return routes

    @staticmethod
    def _runtime_order_key(
        reference: str, runtime: Mapping[str, RuntimeModel]
    ) -> tuple[int, int, str]:
        item = runtime.get(reference)
        if item is None:
            return (1, 0, reference)
        started = item.instance.process_start_time_ns or 0
        return (0, started, reference)

    def resolve(
        self,
        name: str,
        profiles: list[ProfileData],
        runtime: Mapping[str, RuntimeModel],
    ) -> RouteInfo | None:
        return next(
            (route for route in self.snapshot(profiles, runtime) if route.name == name),
            None,
        )

    def create_alias(self, name: str, profile_ids: list[str], remote: Mapping[str, str] | None = None, candidates: list[dict[str, str]] | None = None) -> None:
        profiles = self.profiles()
        self._validate_alias(name, profile_ids, profiles)
        config = self.config.load()
        if name in config["aliases"]:
            raise AppError(
                "route_conflict",
                "Алиас с таким именем уже существует",
                status_code=409,
                details={"name": name},
            )
        config["aliases"][name] = list(profile_ids)
        if candidates is not None:
            self._set_candidates(config, name, candidates)
        if remote is not None:
            config["remote_aliases"][name] = dict(remote)
        self.config.save(config)

    def update_alias(self, current_name: str, name: str, profile_ids: list[str], remote: Mapping[str, str] | None = None, candidates: list[dict[str, str]] | None = None) -> None:
        config = self.config.load()
        if current_name not in config["aliases"]:
            raise AppError("route_not_found", "Алиас не найден", status_code=404)
        if name != current_name and name in config["aliases"]:
            raise AppError("route_conflict", "Алиас с таким именем уже существует", status_code=409)
        self._validate_alias(name, profile_ids, self.profiles(), allow_existing_name=current_name)
        del config["aliases"][current_name]
        config["remote_aliases"].pop(current_name, None)
        config["alias_candidates"].pop(current_name, None)
        if remote is not None:
            config["remote_aliases"][name] = dict(remote)
        config["aliases"][name] = list(profile_ids)
        if candidates is not None:
            self._set_candidates(config, name, candidates)
        self.config.save(config)

    def delete_alias(self, name: str) -> None:
        config = self.config.load()
        if name not in config["aliases"]:
            raise AppError("route_not_found", "Алиас не найден", status_code=404)
        del config["aliases"][name]
        config["remote_aliases"].pop(name, None)
        config["alias_candidates"].pop(name, None)
        self.config.save(config)

    def update_order(self, name: str, profile_ids: list[str]) -> None:
        profiles = self.profiles()
        config = self.config.load()
        by_name = {
            reference: profile
            for profile in profiles
            for reference in (profile.name, profile.profile_id)
            if reference
        }
        if name in config["aliases"]:
            if name in config["alias_candidates"]:
                if any("connection_id" in item for item in config["alias_candidates"][name]):
                    raise AppError("route_membership_invalid", "Используйте общий список candidate_ids", status_code=422)
                self._set_candidates(config, name, [{"profile_id": value} for value in profile_ids])
                self.config.save(config)
                return
            if name in config["remote_aliases"]:
                raise AppError("route_membership_invalid", "Удалённый маршрут изменяется в настройках алиаса", status_code=422)
            self._validate_profile_ids(profile_ids, by_name)
            config["aliases"][name] = list(profile_ids)
        else:
            matching = [profile.profile_id or profile.name for profile in profiles if profile.model_name == name]
            if not matching and name not in config["auto_routes"]:
                raise AppError("route_not_found", "Маршрут не найден", status_code=404)
            if set(profile_ids) != set(matching):
                raise AppError(
                    "route_membership_invalid",
                    "Auto-route должен содержать все профили своей модели ровно по одному разу",
                    status_code=422,
                )
            config["auto_routes"][name] = list(profile_ids)
        self.config.save(config)

    @staticmethod
    def candidate_id(target: Mapping[str, str]) -> str:
        return target["profile_id"] if "profile_id" in target else f"openai:{target['connection_id']}/{target['model']}"

    def _set_candidates(self, config: dict[str, Any], name: str, candidates: list[dict[str, str]]) -> None:
        targets = self.config.candidate_targets(candidates)
        profiles = {ref: profile for profile in self.profiles() for ref in (profile.name, profile.profile_id) if ref}
        local = [target["profile_id"] for target in targets if "profile_id" in target]
        self._validate_profile_ids(local, profiles)
        config["aliases"][name] = local
        config["alias_candidates"][name] = targets
        config["remote_aliases"].pop(name, None)

    def update_candidates(self, name: str, candidates: list[dict[str, str]]) -> None:
        config = self.config.load()
        if name not in config["aliases"]:
            raise AppError("route_not_found", "Алиас не найден", status_code=404)
        self._set_candidates(config, name, candidates)
        self.config.save(config)

    def _validate_alias(
        self, name: str, profile_ids: list[str], profiles: list[ProfileData], *, allow_existing_name: str | None = None
    ) -> None:
        if not ALIAS_NAME_RE.fullmatch(name):
            raise AppError(
                "invalid_alias_name",
                "Имя алиаса должно содержать 1–128 ASCII-символов: "
                "буквы, цифры, точку, дефис или подчёркивание",
                status_code=422,
            )
        config = self.config.load()
        if name in config["aliases"] and name != allow_existing_name:
            raise AppError(
                "route_conflict",
                "Ручной алиас с таким именем уже существует",
                status_code=409,
                details={"name": name},
            )
        if name in config.get("comfyui", {}).get("models", {}):
            raise AppError(
                "route_conflict",
                "Имя уже занято ComfyUI-моделью",
                status_code=409,
                details={"name": name},
            )
        self._validate_profile_ids(profile_ids, {ref: profile for profile in profiles for ref in (profile.name, profile.profile_id) if ref})

    @staticmethod
    def _validate_profile_ids(profile_ids: list[str], profiles: Mapping[str, ProfileData]) -> None:
        if len(profile_ids) != len(set(profile_ids)):
            raise AppError(
                "duplicate_route_member",
                "Профиль нельзя добавить в один маршрут повторно",
                status_code=422,
            )
        missing = [profile_id for profile_id in profile_ids if profile_id not in profiles]
        if missing:
            raise AppError(
                "profile_not_found",
                "В маршруте указан неизвестный профиль",
                status_code=422,
                details={"profiles": missing},
            )

    @staticmethod
    def _ordered(saved: list[str], defaults: list[str]) -> list[str]:
        current = set(defaults)
        result = [item for item in saved if item in current]
        result.extend(item for item in defaults if item not in result)
        return result

    def _build_route(
        self,
        name: str,
        kind: str,
        profile_ids: list[str],
        profiles: Mapping[str, ProfileData],
        runtime: Mapping[str, RuntimeModel],
        diagnostics: list[str] | None = None,
    ) -> RouteInfo:
        candidates = tuple(
            self._candidate(profile_id, profiles.get(profile_id), runtime.get(profile_id))
            for profile_id in profile_ids
        )
        return RouteInfo(
            name=name,
            kind=kind,
            candidate_profile_ids=tuple(profile_ids),
            candidates=candidates,
            diagnostics=tuple(diagnostics or []),
        )

    @staticmethod
    def _candidate(
        profile_id: str, profile: ProfileData | None, runtime: RuntimeModel | None
    ) -> RouteCandidate:
        if profile is None:
            return RouteCandidate(
                profile_id=profile_id,
                profile_name=None,
                instance_id=None,
                upstream_model=None,
                engine_id=None,
                port=None,
                state="missing",
                available=False,
                reason="profile_missing",
                metadata={},
            )
        if runtime is None:
            return RouteCandidate(
                profile_id=profile_id,
                profile_name=profile.name,
                instance_id=None,
                upstream_model=None,
                engine_id=profile.engine.id,
                port=None,
                state="stopped",
                available=False,
                reason="profile_not_running",
                metadata={},
            )
        instance = runtime.instance
        base = {
            "profile_id": profile_id,
            "profile_name": profile.name,
            "instance_id": instance.id,
            "engine_id": instance.engine_id,
            "port": instance.port,
            "state": instance.state,
        }
        if not instance.identity_verified:
            return RouteCandidate(
                **base,
                upstream_model=None,
                available=False,
                reason="identity_unverified",
                metadata={},
            )
        if instance.state != "ready":
            return RouteCandidate(
                **base,
                upstream_model=None,
                available=False,
                reason="engine_not_ready",
                metadata={},
            )
        if instance.api_protocol == "none":
            return RouteCandidate(
                **base,
                upstream_model=None,
                available=False,
                reason="api_disabled",
                metadata={},
            )
        if profile.model_name in runtime.metadata:
            upstream = profile.model_name
        elif len(runtime.metadata) == 1:
            upstream = next(iter(runtime.metadata))
        elif instance.api_protocol == "jev":
            upstream = None
        elif not runtime.metadata and "text" not in instance.types and instance.model_name:
            # Decision and media-serving APIs need not expose OpenAI's
            # /models catalogue. The process identity/profile supplies the
            # public-to-upstream selection in that case.
            upstream = instance.model_name
        else:
            return RouteCandidate(
                **base,
                upstream_model=None,
                available=False,
                reason="multiple_upstream_models",
                metadata={},
            )
        return RouteCandidate(
            **base,
            upstream_model=upstream,
            available=True,
            reason=None,
            metadata=runtime.metadata.get(upstream, {}) if upstream is not None else {},
        )


__all__ = [
    "ALIAS_NAME_RE",
    "ROUTE_CONFIG_FILENAME",
    "RouteCandidate",
    "RouteConfigRepository",
    "RouteInfo",
    "RouteManager",
    "RuntimeModel",
]
