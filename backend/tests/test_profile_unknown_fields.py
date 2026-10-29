from uuid import uuid4

import yaml
from app.profiles.repository import ProfileRepository


def test_unknown_fields_do_not_block_load_and_disappear_on_normal_save(tmp_path):
    document = {
        "version": 1, "name": "test model", "model_name": "test-model",
        "profile_id": str(uuid4()),
        "engine": {"id": "llama.cpp", "installation_id": "inst-1"},
        "model": {"path": "/models/main.gguf", "draft_path": None},
        "types": ["text"], "api": {"protocol": "none"}, "ui_url": None,
        "some_other_field": "ignored",
        "command": "{{engine}} --model {{model}} --port 8013",
        "enable_metrics": False,
    }
    path = tmp_path / "test model.yaml"
    original = yaml.safe_dump(document)
    path.write_text(original)
    repository = ProfileRepository(tmp_path)

    result = repository.list()
    assert [item.name for item in result.items] == ["test model"]
    assert result.issues == []
    assert path.read_text() == original
    loaded = repository.get(document["profile_id"])
    assert loaded.command == document["command"]
    repository.put(document["profile_id"], loaded)
    written = yaml.safe_load(path.read_text())
    assert not {"types", "api", "ui_url", "some_other_field"} & written.keys()
    assert written["model"] == {"path": "/models/main.gguf"}
    assert written["command"] == document["command"]
    assert repository.list().issues == []
