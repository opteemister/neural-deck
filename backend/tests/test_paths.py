from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from app.db import create_engine
from app.main import create_app
from app.paths import build_paths, prepare_data_dirs, validate_extra_venv_roots
from sqlalchemy import text


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
        paths.neural_deck_log_dir,
        paths.profiles_log_dir,
        paths.api_log_dir,
        paths.audit_dir,
    ):
        assert directory.is_dir()
    assert paths.sqlite_path.parent == paths.data_dir


@pytest.mark.version_zero
def test_neural_deck_persistent_paths(settings) -> None:
    paths = build_paths(settings)

    assert paths.sqlite_path == settings.data_root / "data" / "neural-deck.sqlite3"
    assert paths.neural_deck_log_dir == paths.log_root / "neural-deck"
    assert create_app(settings).title == "Neural Deck"


@pytest.mark.version_zero
async def test_existing_neural_deck_database_is_reused(settings) -> None:
    paths = build_paths(settings)
    paths.data_dir.mkdir(parents=True)
    with sqlite3.connect(paths.sqlite_path) as database:
        database.execute("CREATE TABLE rename_check (value TEXT NOT NULL)")
        database.execute("INSERT INTO rename_check VALUES ('preserved')")

    prepare_data_dirs(paths)
    engine = create_engine(paths.sqlite_path)
    try:
        async with engine.connect() as connection:
            result = await connection.execute(text("SELECT value FROM rename_check"))
            assert result.scalar_one() == "preserved"
    finally:
        await engine.dispose()

    assert list(paths.data_dir.glob("*.sqlite3")) == [paths.sqlite_path]


@pytest.mark.version_zero
def test_missing_extra_venv_root_is_diagnostic_only(settings, tmp_path: Path) -> None:
    # spec: version-zero
    missing = tmp_path / "missing-venvs"
    settings.extra_venv_roots.append(missing)

    diagnostics = validate_extra_venv_roots(settings)

    assert [(item.code, item.path) for item in diagnostics] == [("venv_root_missing", missing)]
