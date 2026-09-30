from __future__ import annotations

from pathlib import Path

import pytest
from app.paths import build_paths, prepare_data_dirs, validate_extra_venv_roots


@pytest.mark.version_zero
def test_prepare_data_dirs_creates_persistent_layout(settings) -> None:
    # spec: version-zero
    paths = build_paths(settings)

    prepare_data_dirs(paths)

    for directory in (
        paths.config_dir,
        paths.data_dir,
        paths.models_root,
        paths.profiles_dir,
        paths.venvs_dir,
        paths.studio_log_dir,
        paths.profiles_log_dir,
        paths.api_log_dir,
        paths.audit_dir,
    ):
        assert directory.is_dir()
    assert paths.sqlite_path.parent == paths.data_dir


@pytest.mark.version_zero
def test_missing_extra_venv_root_is_diagnostic_only(settings, tmp_path: Path) -> None:
    # spec: version-zero
    missing = tmp_path / "missing-venvs"
    settings.extra_venv_roots.append(missing)

    diagnostics = validate_extra_venv_roots(settings)

    assert [(item.code, item.path) for item in diagnostics] == [("venv_root_missing", missing)]
