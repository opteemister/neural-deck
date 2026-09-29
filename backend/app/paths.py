"""Persistent directory layout and startup validation."""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

import yaml  # type: ignore[import-untyped]

from .config import Settings


@dataclass(frozen=True, slots=True)
class DataPaths:
    """All service-visible persistent paths derived from ``data_root``."""

    data_root: Path
    config_dir: Path
    data_dir: Path
    models_root: Path
    profiles_dir: Path
    runtime_dir: Path
    venvs_dir: Path
    log_root: Path
    studio_log_dir: Path
    engines_log_dir: Path
    api_log_dir: Path
    audit_dir: Path
    sqlite_path: Path
    settings_path: Path


@dataclass(frozen=True, slots=True)
class PathDiagnostic:
    """Non-fatal diagnostic for an optional path, such as a venv root."""

    path: Path
    code: str
    message: str


def build_paths(settings: Settings, *, include_saved_settings: bool = True) -> DataPaths:
    root = settings.data_root
    log_root = settings.log_root or root / "logs"
    models_root = settings.models_root or root / "models"
    saved_models_root = root / "config" / "settings.yaml"
    try:
        has_saved_settings = saved_models_root.exists()
    except (OSError, RuntimeError) as exc:
        raise ValueError(f"Не удалось проверить settings.yaml: {exc}") from exc
    if include_saved_settings and has_saved_settings:
        try:
            saved = yaml.safe_load(saved_models_root.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, yaml.YAMLError) as exc:
            raise ValueError(f"Не удалось прочитать settings.yaml: {exc}") from exc
        if not isinstance(saved, dict):
            raise ValueError("settings.yaml должен содержать YAML-объект")
        if "models_root" in saved:
            value = saved["models_root"]
            if not isinstance(value, str) or not value.strip():
                raise ValueError("settings.yaml: models_root должен быть непустой строкой")
            candidate = Path(value).expanduser()
            if not candidate.is_absolute():
                raise ValueError("settings.yaml: models_root должен быть абсолютным путём")
            models_root = candidate
    return DataPaths(
        data_root=root,
        config_dir=root / "config",
        data_dir=root / "data",
        models_root=models_root,
        profiles_dir=root / "profiles",
        runtime_dir=root / "runtime",
        venvs_dir=root / "runtime" / "venvs",
        log_root=log_root,
        studio_log_dir=log_root / "studio",
        engines_log_dir=log_root / "engines",
        api_log_dir=log_root / "api",
        audit_dir=root / "audit",
        sqlite_path=root / "data" / "llm-studio.sqlite3",
        settings_path=root / "config" / "settings.yaml",
    )


def prepare_data_dirs(paths: DataPaths) -> None:
    """Create required directories and fail loudly when they are unusable."""

    directories = (
        paths.data_root,
        paths.config_dir,
        paths.data_dir,
        paths.models_root,
        paths.profiles_dir,
        paths.runtime_dir,
        paths.venvs_dir,
        paths.log_root,
        paths.studio_log_dir,
        paths.engines_log_dir,
        paths.api_log_dir,
        paths.audit_dir,
    )
    for directory in directories:
        directory.mkdir(parents=True, exist_ok=True)
        if not directory.is_dir():
            raise NotADirectoryError(f"required path is not a directory: {directory}")
        if not _is_writable(directory):
            raise PermissionError(f"required path is not writable: {directory}")


def validate_extra_venv_roots(settings: Settings) -> list[PathDiagnostic]:
    """Return diagnostics for optional roots without failing application startup."""

    diagnostics: list[PathDiagnostic] = []
    for root in settings.extra_venv_roots:
        try:
            if root.is_symlink():
                try:
                    root.resolve(strict=True)
                except FileNotFoundError:
                    diagnostics.append(
                        PathDiagnostic(root, "venv_root_missing", "каталог не существует")
                    )
                    continue
            root_exists = root.exists()
            root_is_dir = root.is_dir() if root_exists else False
            readable = _is_readable(root) if root_is_dir else False
        except (OSError, RuntimeError) as exc:
            diagnostics.append(PathDiagnostic(root, "venv_root_error", str(exc)[:200]))
            continue
        if not root_exists:
            diagnostics.append(PathDiagnostic(root, "venv_root_missing", "каталог не существует"))
        elif not root_is_dir:
            diagnostics.append(
                PathDiagnostic(root, "venv_root_not_directory", "путь не является каталогом")
            )
        elif not readable:
            diagnostics.append(
                PathDiagnostic(root, "venv_root_unreadable", "каталог недоступен для чтения")
            )
    return diagnostics


def effective_settings(settings: Settings, paths: DataPaths) -> dict[str, object]:
    """Build the non-secret settings representation used by API schemas."""

    return {
        "data_root": paths.data_root,
        "models_root": paths.models_root,
        "log_root": paths.log_root,
        "extra_venv_roots": list(settings.extra_venv_roots),
        "host": settings.host,
        "port": settings.port,
    }


def _is_writable(path: Path) -> bool:
    """Check write permission without relying on the effective uid alone."""

    try:
        return path.stat().st_mode & 0o222 != 0 and os.access(path, os.W_OK)
    except OSError:
        return False


def _is_readable(path: Path) -> bool:
    try:
        return path.stat().st_mode & 0o444 != 0 and os.access(path, os.R_OK | os.X_OK)
    except OSError:
        return False


def validate_models_root(value: Path) -> Path:
    """Create and fully validate a models directory before changing settings."""

    path = value.expanduser()
    if not path.is_absolute():
        raise ValueError("models_root должен быть абсолютным путём")
    try:
        path.mkdir(parents=True, exist_ok=True)
        if not path.is_dir():
            raise NotADirectoryError(str(path))
        canonical = path.resolve(strict=True)
        if not _is_writable(canonical):
            raise PermissionError(str(canonical))
        # os.access can be affected by the effective uid.  A same-directory
        # probe also validates ACLs and mount options before settings mutate.
        with tempfile.NamedTemporaryFile(prefix=".llm-studio-write-test-", dir=canonical) as probe:
            probe.write(b"ok")
            probe.flush()
    except (OSError, RuntimeError) as exc:
        raise ValueError(f"models_root недоступен для записи: {exc}") from exc
    return canonical
