"""Persistent route configuration and runtime route resolution."""

from __future__ import annotations

import os
import re
import tempfile
from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped]

from ..errors import AppError
from ..profiles.repository import ProfileRepository, StrictYamlLoader
from ..schemas.instances import Instance
from ..schemas.profiles import ProfileData

ALIAS_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
ROUTE_CONFIG_FILENAME = "proxy-routes.yaml"


@dataclass(frozen=True, slots=True)
class RuntimeModel:
    """One managed runtime and the model metadata observed from its engine."""

    instance: Instance
    metadata: Mapping[str, dict[str, Any]]


@dataclass(frozen=True, slots=True)
class RouteCandidate:
    profile_id: str
    profile_name: str | None
    instance_id: str | None
    upstream_model: str | None
    engine_id: str | None
    port: int | None
    state: str
    available: bool
    reason: str | None
    metadata: Mapping[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            "profile_id": self.profile_id,
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
        return any(candidate.available for candidate in self.candidates)

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "kind": self.kind,
            "candidate_profile_ids": list(self.candidate_profile_ids),
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
        return {"schema_version": 1, "auto_routes": {}, "aliases": {}}

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
        return result

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
        return self.profile_repository.list().items

    def snapshot(
        self,
        profiles: list[ProfileData],
        runtime: Mapping[str, RuntimeModel],
    ) -> list[RouteInfo]:
        config = self.config.load()
        by_name = {profile.name: profile for profile in profiles}
        grouped: dict[str, list[str]] = defaultdict(list)
        for profile in profiles:
            grouped[profile.model_name].append(profile.name)
        for names in grouped.values():
            names.sort()

        route_names = set(grouped) | set(config["auto_routes"])
        routes: list[RouteInfo] = []
        for name in sorted(route_names):
            refs = self._ordered(
                config["auto_routes"].get(name, []), grouped.get(name, [])
            )
            refs = [profile_id for profile_id in refs if profile_id in runtime]
            routes.append(self._build_route(name, "auto", refs, by_name, runtime))

        auto_names = set(route_names)
        for name, refs in config["aliases"].items():
            diagnostics: list[str] = []
            if name in auto_names or name in grouped:
                diagnostics.append("route_name_collision")
            routes.append(
                self._build_route(name, "alias", refs, by_name, runtime, diagnostics)
            )
        return sorted(routes, key=lambda route: (route.name, route.kind))

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

    def create_alias(self, name: str, profile_ids: list[str]) -> None:
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
        self.config.save(config)

    def delete_alias(self, name: str) -> None:
        config = self.config.load()
        if name not in config["aliases"]:
            raise AppError("route_not_found", "Алиас не найден", status_code=404)
        del config["aliases"][name]
        self.config.save(config)

    def update_order(self, name: str, profile_ids: list[str]) -> None:
        profiles = self.profiles()
        config = self.config.load()
        by_name = {profile.name: profile for profile in profiles}
        if name in config["aliases"]:
            self._validate_alias_members(profile_ids)
            self._validate_profile_ids(profile_ids, by_name)
            config["aliases"][name] = list(profile_ids)
        else:
            matching = [profile.name for profile in profiles if profile.model_name == name]
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

    def _validate_alias(
        self, name: str, profile_ids: list[str], profiles: list[ProfileData]
    ) -> None:
        if not ALIAS_NAME_RE.fullmatch(name):
            raise AppError(
                "invalid_alias_name",
                "Имя алиаса должно содержать 1–128 ASCII-символов: "
                "буквы, цифры, точку, дефис или подчёркивание",
                status_code=422,
            )
        by_name = {profile.name: profile for profile in profiles}
        if any(profile.model_name == name for profile in profiles):
            raise AppError(
                "route_name_collision",
                "Имя алиаса совпадает с публичным именем профиля",
                status_code=409,
                details={"name": name},
            )
        config = self.config.load()
        if name in config["auto_routes"]:
            raise AppError(
                "route_name_collision",
                "Имя алиаса совпадает с auto-route",
                status_code=409,
                details={"name": name},
            )
        self._validate_alias_members(profile_ids)
        self._validate_profile_ids(profile_ids, by_name)

    @staticmethod
    def _validate_alias_members(profile_ids: list[str]) -> None:
        if not profile_ids:
            raise AppError(
                "route_membership_invalid",
                "Алиас должен содержать хотя бы один профиль",
                status_code=422,
            )

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
