# spec: version-zero
from __future__ import annotations

import shlex
from pathlib import Path

import pytest
from app.errors import AppError
from app.profiles.formatting import (
    PlaceholderError,
    enable_command_metrics,
    format_script,
    substitute_placeholders,
)
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

    assert repository.delete("demo") == "demo"
    with pytest.raises(AppError) as error:
        repository.delete("demo")
    assert error.value.code == "profile_not_found"


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


def test_public_model_name_is_one_literal_argument() -> None:
    alias = "Qwen's alias; $(touch /tmp/no) {{model}}"
    command = substitute_placeholders(
        "{{engine}} --model {{model}} --alias {{model_name}}",
        engine="/bin/engine",
        model="/models/{{model_name}}",
        model_name=alias,
    )
    assert shlex.split(command) == [
        "/bin/engine",
        "--model",
        "/models/{{model_name}}",
        "--alias",
        alias,
    ]


@pytest.mark.parametrize(
    "command",
    [
        'echo "{{model_name}}"',
        "NAME={{model_name}}",
        "echo {{model_name}}x",
        "echo $({{model_name}})",
    ],
)
def test_public_model_name_rejects_unsafe_placeholder_placement(command: str) -> None:
    with pytest.raises(PlaceholderError):
        substitute_placeholders(command, engine="/bin/engine", model="/models/x", model_name="qwen")


def test_public_model_name_must_be_explicit_when_used() -> None:
    with pytest.raises(AppError) as error:
        substitute_placeholders("echo {{model_name}}", engine="/bin/engine", model="/models/x")
    assert error.value.code == "model_name_missing"


@pytest.mark.parametrize(
    "engine, flag",
    [
        ("llama.cpp", "--alias"),
        ("vllm", "--served-model-name"),
        ("sglang", "--served-model-name"),
        ("ninfer", "--model-id"),
        ("freetoken", "--served-model-name"),
    ],
)
def test_launch_templates_pass_public_model_name(engine: str, flag: str) -> None:
    from app.engines.templates import LAUNCH_TEMPLATES

    command = substitute_placeholders(
        LAUNCH_TEMPLATES[engine], engine="/bin/engine", model="/models/x", model_name="public alias"
    )
    words = shlex.split(command)
    assert words[words.index(flag) + 1] == "public alias"


def test_format_script_is_conservative_for_shell_syntax() -> None:
    formatted, changed, reason = format_script("vllm serve --model foo --port 1901")
    assert changed is True
    assert formatted == "vllm serve \\\n    --model foo \\\n    --port 1901"
    unchanged, changed, reason = format_script("echo '$HOME'")
    assert (unchanged, changed, reason) == (
        "echo '$HOME'",
        False,
        "shell_syntax_requires_manual_formatting",
    )


@pytest.mark.parametrize(
    "engine, flag", [("llama.cpp", "--metrics"), ("sglang", "--enable-metrics")]
)
def test_metrics_preference_is_saved_with_final_command(
    tmp_path: Path, engine: str, flag: str
) -> None:
    repository = ProfileRepository(tmp_path / "profiles")
    value = profile().model_copy(
        update={"engine": profile().engine.model_copy(update={"id": engine})}
    )
    saved = repository.create(value)
    assert saved.enable_metrics is True
    assert saved.command == value.command + " " + flag
    assert repository.get(value.name).command == saved.command
    assert repository.put(value.name, saved).command == saved.command
    unchecked = saved.model_copy(update={"enable_metrics": False, "command": value.command})
    repository.put(value.name, unchecked)
    assert repository.get(value.name) == unchecked
    # Turning off automatic modification must not remove an existing flag either.
    unchecked_with_flag = saved.model_copy(update={"enable_metrics": False})
    assert repository.put(value.name, unchecked_with_flag).command == saved.command


def test_metrics_flags_ignore_comments_and_preserve_multiline_commands() -> None:
    assert (
        enable_command_metrics("{{engine}} --model {{model}} # --metrics", "llama.cpp", True)
        == "{{engine}} --model {{model}} --metrics # --metrics"
    )
    command = "{{engine}} \\\n  --model {{model}}\n"
    assert enable_command_metrics(command, "llama.cpp", True) == command.rstrip() + " --metrics\n"
    existing = "{{engine}} --metrics; echo done"
    assert enable_command_metrics(existing, "llama.cpp", True) == existing
    for engine in ["vllm", "freetoken", "ninfer"]:
        assert enable_command_metrics(command, engine, True) == command


@pytest.mark.parametrize(
    "command",
    ["{{engine}} && echo done", "{{engine}}\necho done", "{{engine}} > /tmp/log", "# comment only"],
)
def test_metrics_never_append_to_an_ambiguous_script(command: str) -> None:
    with pytest.raises(AppError) as error:
        enable_command_metrics(command, "llama.cpp", True)
    assert error.value.code == "metrics_command_requires_manual_flag"
    assert enable_command_metrics(command, "llama.cpp", False) == command
