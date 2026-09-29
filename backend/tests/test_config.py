from __future__ import annotations

from pathlib import Path

import pytest
from app.config import Settings
from pydantic import ValidationError


@pytest.mark.version_zero
def test_required_runtime_environment_contract(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # spec: version-zero
    monkeypatch.setenv("LLM_STUDIO_DATA_ROOT", str(tmp_path / "data"))
    monkeypatch.setenv("HOST", "0.0.0.0")
    monkeypatch.setenv("PORT", "18080")
    monkeypatch.setenv("EXTRA_VENV_ROOTS", "/opt/venvs, /srv/venvs")

    settings = Settings(_env_file=None)

    assert settings.data_root == tmp_path / "data"
    assert settings.host == "0.0.0.0"
    assert settings.port == 18080
    assert settings.extra_venv_roots == [Path("/opt/venvs"), Path("/srv/venvs")]


@pytest.mark.version_zero
def test_required_environment_has_no_silent_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    # spec: version-zero
    for name in ("LLM_STUDIO_DATA_ROOT", "HOST", "PORT"):
        monkeypatch.delenv(name, raising=False)

    with pytest.raises(ValidationError):
        Settings(_env_file=None)


@pytest.mark.version_zero
def test_empty_optional_paths_derive_from_data_root(tmp_path: Path) -> None:
    settings = Settings(
        _env_file=None,
        data_root=tmp_path / "data",
        host="127.0.0.1",
        port=8000,
        models_root="",
        log_root="",
    )

    assert settings.models_root is None
    assert settings.log_root is None
