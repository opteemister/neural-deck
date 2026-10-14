from __future__ import annotations

from pathlib import Path

import pytest
from app.config import Settings
from app.engines.discovery import DiscoveryCoordinator, installation_id
from app.models.catalogue import ModelCatalogue
from app.paths import build_paths, prepare_data_dirs


def test_image_component_delete_preserves_other_quants_and_sidecars(tmp_path: Path) -> None:
    settings = Settings(
        data_root=tmp_path / "data", models_root=tmp_path / "models",
        host="127.0.0.1", port=8000, _env_file=None,
    )
    paths = build_paths(settings)
    prepare_data_dirs(paths)
    root = paths.models_root / "vendor" / "image"
    files = [
        "diffusion_models/example_bf16.safetensors",
        "diffusion_models/example_int8_convrot.safetensors",
        "text_encoders/encoder_bf16.safetensors",
        "vae/decoder_fp16.safetensors",
        "diffusion_models/config.json",
    ]
    for name in files:
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"test")
    catalogue = ModelCatalogue(paths)
    model = catalogue._describe(root)
    catalogue._items = [model]
    group = next(item for item in model.groups if item.label.endswith("example_int8_convrot"))
    assert group.path is None
    assert group.deletable
    assert [file.path for file in group.files] == [files[1]]
    catalogue.delete_group(model.id, group.id)
    assert not (root / files[1]).exists()
    assert all((root / name).exists() for name in files if name != files[1])


def test_text_model_directory_grouping_is_unchanged(tmp_path: Path) -> None:
    from app.models.catalogue import _model_groups

    for filename in ["model-00001-of-00002.safetensors", "model-00002-of-00002.safetensors"]:
        target = tmp_path / "text-variant" / filename
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"test")
    groups, _, _, _, _ = _model_groups(tmp_path, None)
    assert len(groups) == 1
    assert groups[0].path == "text-variant"
    assert groups[0].file_count == 2


@pytest.mark.version_zero
def test_discovery_verifies_each_python_engine_inside_a_venv(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # spec: version-zero
    settings = Settings(data_root=tmp_path / "data", host="127.0.0.1", port=8000)
    paths = build_paths(settings)
    prepare_data_dirs(paths)
    venv = paths.venvs_dir / "worker"
    (venv / "bin").mkdir(parents=True)
    (venv / "bin" / "vllm").write_text("#!/bin/sh\n", encoding="utf-8")
    (venv / "bin" / "vllm").chmod(0o755)

    monkeypatch.setattr(
        "app.engines.adapters._run_version",
        lambda command: (True, "0.9.0", None),
    )
    result = DiscoveryCoordinator(settings, paths).scan(explicit=[])
    vllm = next(engine for engine in result.engines if engine.id == "vllm")
    assert len(vllm.installations) == 1
    assert vllm.installations[0].status == "ready"
    assert not next(engine for engine in result.engines if engine.id == "sglang").installations


@pytest.mark.version_zero
def test_installation_id_is_stable_for_a_canonical_path(tmp_path: Path) -> None:
    # spec: version-zero
    path = tmp_path / "venv"
    assert installation_id("vllm", "python_venv", path) == installation_id(
        "vllm", "python_venv", path / ".." / "venv"
    )


@pytest.mark.version_zero
def test_catalogue_groups_model_directories_and_evaluates_hf_manifest(tmp_path: Path) -> None:
    # spec: version-zero
    root = tmp_path / "models"
    model = root / "Qwen"
    model.mkdir(parents=True)
    weights = model / "model.safetensors"
    weights.write_bytes(b"model")
    (model / "config.json").write_text("{}", encoding="utf-8")
    (model / ".neural-deck-hf.yaml").write_text(
        "schema_version: 1\n"
        "repo_id: Qwen/Qwen\n"
        "revision: 0123456789abcdef0123456789abcdef01234567\n"
        "scope: snapshot\n"
        "files:\n"
        "  - path: model.safetensors\n"
        "    size: 5\n",
        encoding="utf-8",
    )
    (model / "README.md").write_text("model", encoding="utf-8")
    settings = Settings(data_root=tmp_path / "data", host="127.0.0.1", port=8000, models_root=root)
    paths = build_paths(settings)
    prepare_data_dirs(paths)
    items = ModelCatalogue(paths).scan()
    qwen = next(item for item in items if item.path == model)
    assert qwen.hf_match.status == "downloaded"
    assert [item.path for item in items] == [model]
    assert [item.path for item in qwen.variants] == ["model.safetensors"]
    assert qwen.supporting_files == 2
