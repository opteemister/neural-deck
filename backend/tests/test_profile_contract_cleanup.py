import shlex
from unittest.mock import Mock

import pytest
import yaml
from app.engines.service import EngineService
from app.profiles.formatting import PlaceholderError, substitute_placeholders
from app.profiles.repository import ProfileRepository
from app.schemas.engines import EngineDefinition, Installation
from app.schemas.profiles import ProfileData
from pydantic import ValidationError

from tests.test_mcp_management import _server


def raw_profile():
    return {
        "version": 1, "name": "demo", "model_name": "demo",
        "engine": {"id": "llama.cpp", "installation_id": "inst-1"},
        "model": {"path": "/models/main.gguf"},
        "command": "{{engine}} --model {{model}} --port {{port}}", "port": 8013,
        "enable_metrics": False,
    }


@pytest.mark.parametrize(("field", "value"), [
    ("types", ["text"]),
    ("api", {"protocol": "openai", "base_url": "http://localhost:8013/v1"}),
    ("ui_url", "http://localhost:8013"),
])
def test_profile_ignores_unknown_engine_settings(field, value):
    profile = ProfileData.model_validate({**raw_profile(), field: value})
    assert field not in profile.model_dump()


def test_profile_ignores_unknown_model_fields_and_rejects_removed_placeholder():
    raw = raw_profile()
    raw["model"]["draft_path"] = "/models/draft.gguf"
    profile = ProfileData.model_validate(raw)
    assert "draft_path" not in profile.model.model_dump()
    raw = raw_profile()
    raw["command"] += " --model-draft {{model_draft}}"
    with pytest.raises(ValidationError, match="draft-модель"):
        ProfileData.model_validate(raw)
    with pytest.raises(PlaceholderError, match="draft-модель"):
        substitute_placeholders(
            raw["command"], engine="/bin/llama-server", model="/models/main.gguf", port=8013
        )


def test_explicit_draft_argument_survives_render_and_yaml_save(tmp_path):
    raw = raw_profile()
    raw["command"] += " --model-draft '/models/draft model.gguf' --gpu-layers-draft all"
    profile = ProfileData.model_validate(raw)
    repository = ProfileRepository(tmp_path)
    saved = repository.create(profile)
    rendered = substitute_placeholders(
        saved.command, engine="/bin/llama-server", model=str(saved.model.path), port=saved.port,
    )
    assert shlex.split(rendered)[-4:] == [
        "--model-draft", "/models/draft model.gguf", "--gpu-layers-draft", "all",
    ]
    document = yaml.safe_load((tmp_path / "demo.yaml").read_text())
    assert document["command"] == raw["command"]
    assert document["model"] == {"path": "/models/main.gguf"}
    assert not {"types", "api", "ui_url"} & document.keys()
    assert document["port"] == 8013


def test_profile_api_schema_does_not_advertise_removed_fields():
    schema = ProfileData.model_json_schema()
    assert not {"types", "api", "ui_url"} & schema["properties"].keys()
    assert schema["$defs"]["ProfileModel"]["properties"].keys() == {"path"}


def test_mcp_tools_use_clean_profile_contract():
    tools = _server()._tool_manager.list_tools()
    for name in ("nd_save_profile", "nd_validate_profile", "nd_preview_profile"):
        tool = next(item for item in tools if item.name == name)
        schema = tool.parameters
        assert not {"types", "api", "ui_url"} & schema["$defs"]["ProfileData"]["properties"].keys()
        assert schema["$defs"]["ProfileModel"]["properties"].keys() == {"path"}


def test_runtime_capabilities_and_endpoints_belong_to_engine(tmp_path):
    executable = tmp_path / "llama-server"
    executable.write_text("#!/bin/sh\n")
    executable.chmod(0o755)
    definition = EngineDefinition(
        version=1, id="llama.cpp", name="llama.cpp", types=["text", "embeddings"],
        executable="{{target}}/llama-server", command="{{engine}} --model {{model}}",
        api={"protocol": "openai", "base_url": "http://localhost:8000/v1"},
        ui_url="http://localhost:8000",
    )
    installation = Installation(
        id="inst-1", engine_id="llama.cpp", kind="binary", root_path=tmp_path,
        executable_path=executable, status="ready", verified=True,
    )
    service = object.__new__(EngineService)
    service.get_definition = Mock(return_value=definition)
    resolved = service.resolve_profile(ProfileData.model_validate(raw_profile()), installation)
    assert resolved.types == ["text", "embeddings"]
    assert resolved.api_protocol == "openai"
    assert resolved.api_base_url == definition.api.base_url
    assert resolved.ui_url == definition.ui_url
