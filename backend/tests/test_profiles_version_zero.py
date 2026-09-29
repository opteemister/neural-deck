# spec: version-zero
from __future__ import annotations

from pathlib import Path

import pytest
from app.errors import AppError
from app.profiles.formatting import PlaceholderError, format_script, substitute_placeholders
from app.profiles.repository import ProfileRepository
from app.schemas.profiles import ProfileData


def profile(name: str = "demo") -> ProfileData:
    return ProfileData.model_validate(
        {
            "version": 1,
            "name": name,
            "model_name": f"model-{name}",
            "engine": {"id": "vllm", "installation_id": "inst-1"},
            "model": {"path": "/models/demo"},
            "command": "{{engine}} serve --model {{model}} --port 1901",
            "environment": {"CUDA_VISIBLE_DEVICES": "0"},
            "autostart": False,
        }
    )


def test_profile_repository_is_atomic_and_round_trips(tmp_path: Path) -> None:
    repository = ProfileRepository(tmp_path / "profiles")
    saved = repository.create(profile())

    assert saved == profile()
    assert repository.get("demo") == profile()
    assert (tmp_path / "profiles" / "demo.yaml").stat().st_mode & 0o777 == 0o600

    with pytest.raises(AppError) as error:
        repository.create(profile())
    assert error.value.code == "profile_conflict"


def test_profile_list_keeps_valid_files_when_another_yaml_is_broken(tmp_path: Path) -> None:
    repository = ProfileRepository(tmp_path / "profiles")
    repository.create(profile())
    (tmp_path / "profiles" / "broken.yaml").write_text(
        "version: 1\nname: broken\nmodel_name: broken\nunknown: true\n",
        encoding="utf-8",
    )

    listed = repository.list()
    assert [item.name for item in listed.items] == ["demo"]
    assert listed.issues and listed.issues[0].file == "broken.yaml"


def test_profile_placeholder_rules_and_safe_quoting() -> None:
    with pytest.raises(PlaceholderError):
        substitute_placeholders('echo "{{engine}}"', engine="/bin/bash", model="/tmp/model")
    command = substitute_placeholders(
        "{{engine}} --model {{model}}", engine="/tmp/bin with spaces", model="/tmp/model x"
    )
    assert command == "'/tmp/bin with spaces' --model '/tmp/model x'"


def test_format_script_is_conservative_for_shell_syntax() -> None:
    formatted, changed, reason = format_script("vllm serve --model foo --port 1901")
    assert changed is True
    assert "\\\n" in formatted
    unchanged, changed, reason = format_script("echo '$HOME'")
    assert (unchanged, changed, reason) == (
        "echo '$HOME'",
        False,
        "shell_syntax_requires_manual_formatting",
    )
