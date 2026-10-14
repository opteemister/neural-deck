"""Filesystem-backed profile repository.

Profiles are deliberately not copied into SQLite.  Reads are fresh on every
operation and writes use a same-directory temporary file plus ``os.replace``
so a manual editor or a failed write cannot leave a half YAML document.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped]
from pydantic import ValidationError

from ..errors import AppError
from ..schemas.profiles import ProfileData, ProfileIssue, ProfileListData
from .formatting import enable_command_metrics, validate_command_or_error


class DuplicateKeyError(ValueError):
    pass


class StrictYamlLoader(yaml.SafeLoader):  # type: ignore[misc]
    """SafeLoader variant that does not silently accept duplicate keys."""


def _construct_mapping(
    loader: StrictYamlLoader, node: yaml.MappingNode, deep: bool = False
) -> dict[Any, Any]:
    mapping: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in mapping:
            raise DuplicateKeyError(f"дублирующийся YAML-ключ: {key}")
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


StrictYamlLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_mapping,
)


def _safe_load(text: str) -> Any:
    # SafeLoader rejects custom constructors and Python object tags.  The
    # explicit loader context is kept local to prevent global yaml mutations.
    return yaml.load(text, Loader=StrictYamlLoader)


def _safe_name(name: str) -> str:
    if not name or name in {".", ".."} or "/" in name or "\\" in name or ".." in name:
        raise AppError(
            "invalid_profile_name", "Имя профиля содержит недопустимый путь", status_code=422
        )
    if any(ord(char) < 32 or ord(char) == 127 for char in name):
        raise AppError(
            "invalid_profile_name", "Имя профиля содержит управляющий символ", status_code=422
        )
    return name


class ProfileRepository:
    def __init__(self, profiles_dir: Path) -> None:
        self.profiles_dir = profiles_dir
        self.profiles_dir.mkdir(parents=True, exist_ok=True)

    def profile_path(self, name: str) -> Path:
        safe = _safe_name(name)
        path = (self.profiles_dir / f"{safe}.yaml").resolve()
        root = self.profiles_dir.resolve()
        if path.parent != root:
            raise AppError(
                "invalid_profile_name", "Имя профиля выходит за каталог профилей", status_code=422
            )
        return path

    def read_file(self, path: Path) -> ProfileData:
        if path.is_symlink():
            raise ValueError("символическая ссылка на профиль запрещена")
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError("файл профиля не является UTF-8") from exc
        except OSError as exc:
            raise ValueError(f"не удалось прочитать файл: {exc}") from exc
        try:
            raw = _safe_load(text)
        except Exception as exc:  # noqa: BLE001 - YAML boundary
            raise ValueError(f"ошибка YAML: {exc}") from exc
        if not isinstance(raw, Mapping):
            raise ValueError("корень YAML-профиля должен быть объектом")
        try:
            profile = ProfileData.model_validate(raw)
        except ValidationError as exc:
            raise ValueError("ошибка схемы профиля: " + _validation_message(exc)) from exc
        if profile.name != path.stem:
            raise ValueError("поле name не совпадает с именем файла")
        validate_command_or_error(profile.command)
        return profile

    def get(self, name: str) -> ProfileData:
        path = self.profile_path(name)
        if not path.exists():
            raise AppError("profile_not_found", "Профиль не найден", status_code=404)
        try:
            return self.read_file(path)
        except ValueError as exc:
            raise AppError(
                "profile_invalid",
                str(exc),
                status_code=422,
                details={"file": path.name},
            ) from exc

    def list(self) -> ProfileListData:
        items: list[ProfileData] = []
        issues: list[ProfileIssue] = []
        try:
            files = sorted(self.profiles_dir.glob("*.yaml"))
        except OSError as exc:
            issues.append(
                ProfileIssue(file=str(self.profiles_dir), code="directory_error", message=str(exc))
            )
            return ProfileListData(items=items, issues=issues)
        for path in files:
            if not path.is_file():
                continue
            try:
                items.append(self.read_file(path))
            except (OSError, ValueError, TypeError) as exc:
                code = "profile_invalid"
                message = str(exc)
                if "не удалось прочитать" in message:
                    code = "profile_read_error"
                elif "UTF-8" in message:
                    code = "profile_encoding_error"
                elif "дублирующийся" in message:
                    code = "profile_duplicate_key"
                issues.append(ProfileIssue(file=path.name, code=code, message=message))
        return ProfileListData(items=items, issues=issues)

    def migrate_legacy_image_engine_id(self) -> Path | None:
        """Back up and rename the engine ID while preserving stable installation IDs."""
        candidates: list[tuple[Path, ProfileData]] = []
        for path in sorted(self.profiles_dir.glob("*.yaml")):
            if path.is_symlink() or not path.is_file():
                continue
            try:
                profile = self.read_file(path)
            except (OSError, ValueError, TypeError):
                continue
            if profile.engine.id == "image":
                candidates.append((path, profile))
        if not candidates:
            return None

        backup_dir = self.profiles_dir / ".backups" / (
            "image-to-comfyui-" + datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
        )
        backup_dir.mkdir(parents=True, exist_ok=False)
        for source, _profile in candidates:
            shutil.copy2(source, backup_dir / source.name)
        try:
            directory_fd = os.open(backup_dir, os.O_DIRECTORY)
        except OSError:
            directory_fd = -1
        if directory_fd >= 0:
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)

        for source, profile in candidates:
            # Installation IDs deliberately hash the legacy image alias for
            # both engine IDs, so retaining this field keeps references valid.
            migrated = profile.model_copy(
                update={
                    "engine": profile.engine.model_copy(update={"id": "comfyui"}),
                }
            )
            self._atomic_write(source, migrated)
        return backup_dir

    def create(self, profile: ProfileData) -> ProfileData:
        profile = profile.model_copy(
            update={
                "command": enable_command_metrics(
                    profile.command, profile.engine.id, profile.enable_metrics
                )
            }
        )
        validate_command_or_error(profile.command)
        path = self.profile_path(profile.name)
        if path.exists():
            raise AppError(
                "profile_conflict",
                "Профиль с таким именем уже существует",
                status_code=409,
                details={"name": profile.name},
            )
        self._atomic_write(path, profile)
        return self.get(profile.name)

    def put(self, name: str, profile: ProfileData) -> ProfileData:
        _safe_name(name)
        if profile.name != name:
            raise AppError(
                "profile_name_mismatch",
                "Имя профиля в пути и теле запроса различается",
                status_code=422,
            )
        path = self.profile_path(name)
        if not path.exists():
            raise AppError("profile_not_found", "Профиль не найден", status_code=404)
        validate_command_or_error(profile.command)
        profile = profile.model_copy(
            update={
                "command": enable_command_metrics(
                    profile.command, profile.engine.id, profile.enable_metrics
                )
            }
        )
        self._atomic_write(path, profile)
        return self.get(name)

    def delete(self, name: str) -> str:
        path = self.profile_path(name)
        if not path.exists():
            raise AppError("profile_not_found", "Профиль не найден", status_code=404)
        if path.is_symlink():
            raise AppError(
                "profile_invalid", "Символическая ссылка на профиль запрещена", status_code=422
            )
        try:
            path.unlink()
        except OSError as exc:
            raise AppError(
                "profile_delete_failed", f"Не удалось удалить профиль: {exc}", status_code=500
            ) from exc
        try:
            directory_fd = os.open(self.profiles_dir, os.O_DIRECTORY)
        except OSError:
            directory_fd = -1
        if directory_fd >= 0:
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        return name

    def _atomic_write(self, path: Path, profile: ProfileData) -> None:
        payload = profile.model_dump(mode="json")
        # model_dump serialises Path values as strings in JSON mode.  Keep the
        # YAML readable and retain insertion order from the contract.
        text = yaml.safe_dump(
            payload,
            allow_unicode=True,
            default_flow_style=False,
            sort_keys=False,
            width=120,
        )
        fd, temporary = tempfile.mkstemp(
            prefix=f".{path.name}.", suffix=".tmp", dir=self.profiles_dir
        )
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(text)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
            try:
                directory_fd = os.open(self.profiles_dir, os.O_DIRECTORY)
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


def _validation_message(error: ValidationError) -> str:
    return "; ".join(
        f"{'.'.join(str(part) for part in item['loc'])}: {item['msg']}" for item in error.errors()
    )


__all__ = ["ProfileRepository", "StrictYamlLoader"]
