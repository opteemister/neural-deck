from __future__ import annotations

from pathlib import Path

import pytest
from app.engines.adapters import read_engine_manifest
from app.models.catalogue import SIDECAR_DIR, _refresh_local_hash_cache


@pytest.mark.version_zero
def test_hugging_face_metadata_name() -> None:
    assert SIDECAR_DIR == ".neural-deck-hf.yaml"


@pytest.mark.version_zero
def test_engine_metadata_is_read_under_neural_deck_name(tmp_path: Path) -> None:
    (tmp_path / ".neural-deck-engine.yaml").write_text(
        "engine_id: vllm\nstatus: ready\nversion: '1.0'\n", encoding="utf-8"
    )

    manifest = read_engine_manifest(tmp_path)

    assert manifest is not None
    assert manifest.engine_id == "vllm"
    assert manifest.status == "ready"
    assert manifest.version == "1.0"


@pytest.mark.version_zero
@pytest.mark.parametrize("directory", [False, True])
def test_hash_metadata_uses_neural_deck_name_without_changing_weights(
    tmp_path: Path, directory: bool
) -> None:
    target = tmp_path / "model" if directory else tmp_path / "model.gguf"
    if directory:
        target.mkdir()
    weights = target / "model.gguf" if directory else target
    weights.write_bytes(b"weights")

    _refresh_local_hash_cache(target)

    sidecar = (
        target / ".neural-deck-hashes.yaml"
        if directory
        else Path(f"{target}.neural-deck-hashes.yaml")
    )
    assert sidecar.is_file()
    assert weights.read_bytes() == b"weights"
