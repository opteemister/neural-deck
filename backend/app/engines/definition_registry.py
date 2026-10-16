"""YAML-backed builtin and user-defined engine definitions."""

from __future__ import annotations

import os
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped]
from pydantic import TypeAdapter, ValidationError

from ..schemas.engines import EngineDefinition, EngineDefinitionIssue, EngineId


class DuplicateKeyError(ValueError):
    pass


class _StrictLoader(yaml.SafeLoader):  # type: ignore[misc]
    pass


def _construct_mapping(
    loader: _StrictLoader, node: yaml.MappingNode, deep: bool = False
) -> dict[Any, Any]:
    result: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in result:
            raise DuplicateKeyError(f"дублирующийся YAML-ключ: {key}")
        result[key] = loader.construct_object(value_node, deep=deep)
    return result


_StrictLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _construct_mapping)


def _safe_load(text: str) -> Any:
    return yaml.load(text, Loader=_StrictLoader)


class EngineDefinitionRegistry:
    """Read app-owned builtin definitions and atomically manage custom YAML."""

    def __init__(self, custom_dir: Path, *, builtin_dir: Path | None = None) -> None:
        self.builtin_dir = builtin_dir or Path(__file__).with_name("definitions")
        self.custom_dir = custom_dir
        self.custom_dir.mkdir(parents=True, exist_ok=True)
        self._builtins: dict[str, EngineDefinition] = {}
        self.issues: list[EngineDefinitionIssue] = []
        for path in sorted(self.builtin_dir.glob("*.yaml")):
            definition = self._load(path, builtin=True)
            if definition.id in self._builtins:
                raise ValueError(f"повторное встроенное определение: {definition.id}")
            self._builtins[definition.id] = definition

    def list(self) -> list[EngineDefinition]:
        values = dict(self._builtins)
        self.issues = []
        for path in sorted(self.custom_dir.glob("*.yaml")):
            if path.is_symlink() or not path.is_file():
                self.issues.append(
                    EngineDefinitionIssue(
                        file=path.name,
                        code="definition_unsafe_file",
                        message="ожидается обычный YAML-файл без символической ссылки",
                    )
                )
                continue
            try:
                definition = self._load(path, builtin=False)
                # A same-ID custom YAML is an explicit user override. The
                # builtin source remains app-owned and is never rewritten.
                if definition.id == "image":
                    raise ValueError("legacy ID image зарезервирован для comfyui")
                values[definition.id] = definition
            except (OSError, UnicodeError, yaml.YAMLError, ValidationError, ValueError) as exc:
                self.issues.append(
                    EngineDefinitionIssue(
                        file=path.name,
                        code="definition_invalid",
                        message=str(exc)[:500],
                    )
                )
        return sorted(values.values(), key=lambda item: item.id)

    def list_defaults(self) -> list[EngineDefinition]:
        return sorted(self._builtins.values(), key=lambda item: item.id)

    def get(self, engine_id: str) -> EngineDefinition:
        if engine_id == "image":
            engine_id = "comfyui"
        path = self._custom_path(engine_id)
        if path.exists() or path.is_symlink():
            definition = self._load(path, builtin=False)
            if definition.id != engine_id:
                raise ValueError(f"ID определения не совпадает с именем файла: {path.name}")
            return definition
        try:
            return self._builtins[engine_id]
        except KeyError as exc:
            raise KeyError(engine_id) from exc

    def create(self, definition: EngineDefinition) -> EngineDefinition:
        self._validate_new_id(definition.id)
        path = self._custom_path(definition.id)
        if path.exists():
            raise FileExistsError(definition.id)
        self._atomic_write(path, definition)
        return definition

    def put(self, engine_id: str, definition: EngineDefinition) -> EngineDefinition:
        if engine_id != definition.id:
            raise ValueError("ID в пути и определении различается")
        path = self._custom_path(engine_id)
        if engine_id not in self._builtins and not path.is_file():
            raise KeyError(engine_id)
        if path.is_symlink():
            raise PermissionError("символическую ссылку на пользовательское определение менять нельзя")
        self._atomic_write(path, definition)
        return definition

    def delete(self, engine_id: str) -> None:
        if engine_id == "image":
            engine_id = "comfyui"
        path = self._custom_path(engine_id)
        if not path.is_file() or path.is_symlink():
            raise KeyError(engine_id)
        path.unlink()
        self._fsync_directory(self.custom_dir)

    def reset(self, engine_id: str) -> EngineDefinition:
        if engine_id == "image":
            engine_id = "comfyui"
        try:
            definition = self._builtins[engine_id]
        except KeyError as exc:
            raise KeyError(engine_id) from exc

        path = self._custom_path(engine_id)
        if path.is_symlink():
            raise PermissionError("символическую ссылку на пользовательское определение сбросить нельзя")
        if path.exists():
            if not path.is_file():
                raise PermissionError("пользовательское определение должно быть обычным файлом")
            path.unlink()
            self._fsync_directory(self.custom_dir)
        return definition

    def custom_path(self, engine_id: str) -> Path:
        return self._custom_path(engine_id)

    def _validate_new_id(self, engine_id: str) -> None:
        # Pydantic applies the same identifier validation at the API boundary.
        if engine_id in self._builtins or engine_id == "image":
            raise ValueError("этот ID зарезервирован встроенным определением")
        if self._custom_path(engine_id).exists():
            raise FileExistsError(engine_id)

    def _custom_path(self, engine_id: str) -> Path:
        # Validate through the shared schema before using the value in a path.
        validated = TypeAdapter(EngineId).validate_python(engine_id)
        path = self.custom_dir / f"{validated}.yaml"
        if path.parent.resolve() != self.custom_dir.resolve():
            raise ValueError("ID выходит за каталог определений")
        return path

    def _load(self, path: Path, *, builtin: bool) -> EngineDefinition:
        if path.is_symlink():
            raise ValueError(f"символическая ссылка на определение запрещена: {path.name}")
        raw = _safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(raw, Mapping):
            raise ValueError(f"корень YAML-определения должен быть объектом: {path.name}")
        payload = dict(raw)
        if builtin:
            for field, script_key in (
                ("install_script", "install_script_file"),
                ("update_script", "update_script_file"),
            ):
                script_file = payload.pop(script_key, None)
                if script_file is not None:
                    if not isinstance(script_file, str) or Path(script_file).name != script_file:
                        raise ValueError(f"недопустимый путь встроенного скрипта: {path.name}")
                    script_path = Path(__file__).with_name("install_templates") / script_file
                    payload[field] = script_path.read_text(encoding="utf-8")
        try:
            definition = EngineDefinition.model_validate(payload)
        except ValidationError as exc:
            raise ValueError(f"ошибка схемы определения {path.name}: {exc}") from exc
        if not builtin and definition.id != path.stem:
            raise ValueError(f"ID определения не совпадает с именем файла: {path.name}")
        return definition

    def _atomic_write(self, path: Path, definition: EngineDefinition) -> None:
        payload = definition.model_dump(mode="json", exclude_none=True)
        text = yaml.safe_dump(
            payload,
            allow_unicode=True,
            default_flow_style=False,
            sort_keys=False,
            width=120,
        )
        fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=self.custom_dir)
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(text)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
            self._fsync_directory(self.custom_dir)
        except BaseException:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass
            raise

    @staticmethod
    def _fsync_directory(path: Path) -> None:
        try:
            fd = os.open(path, os.O_DIRECTORY)
        except OSError:
            return
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


__all__ = ["EngineDefinitionRegistry", "DuplicateKeyError"]
