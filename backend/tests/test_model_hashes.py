from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
import yaml
from app.config import Settings
from app.models.catalogue import (
    ModelCatalogue,
    _apply_remote_snapshot,
    _refresh_local_hash_cache,
    _tree_file_metadata,
)
from app.paths import build_paths


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _write_manifest(path: Path, content: bytes, *, revision: str = "a" * 40) -> None:
    path.mkdir(parents=True)
    (path / "model.gguf").write_bytes(content)
    (path / ".llm-studio-hf.yaml").write_text(
        "schema_version: 2\n"
        "repo_id: org/model\n"
        "tracking_ref: main\n"
        f"revision: {revision}\n"
        "downloaded_revision_date: 2026-10-01T12:00:00Z\n"
        "scope: snapshot\n"
        "files:\n"
        "  - path: model.gguf\n"
        f"    size: {len(content)}\n"
        f"    downloaded_remote_hash: {_sha256(content)}\n"
        "    downloaded_remote_hash_kind: lfs_sha256\n"
        "    downloaded_file_date: 2026-10-01T11:00:00Z\n",
        encoding="utf-8",
    )


@pytest.mark.version_zero
def test_local_hash_cache_reuses_mtime_and_size_without_reading_again(
    tmp_path: Path, monkeypatch
) -> None:
    model = tmp_path / "model"
    content = b"weights"
    _write_manifest(model, content)

    _refresh_local_hash_cache(model)
    first_manifest = (model / ".llm-studio-hf.yaml").read_text(encoding="utf-8")

    def fail_if_rehashed(_path: Path) -> dict[str, str]:
        raise AssertionError("stable files must reuse the cached digest")

    monkeypatch.setattr("app.models.catalogue._compute_local_digests", fail_if_rehashed)
    _refresh_local_hash_cache(model)

    assert (model / ".llm-studio-hf.yaml").read_text(encoding="utf-8") == first_manifest


@pytest.mark.version_zero
def test_regular_scan_uses_cached_digest_for_catalogue_status(
    tmp_path: Path, monkeypatch
) -> None:
    model = tmp_path / "models" / "org" / "model"
    _write_manifest(model, b"weights")
    settings = Settings(
        data_root=tmp_path / "data",
        host="127.0.0.1",
        port=8000,
        openai_port=8001,
        models_root=tmp_path / "models",
    )
    catalogue = ModelCatalogue(build_paths(settings))

    assert catalogue.scan()[0].hf_match.status == "downloaded"

    def fail_if_rehashed(_path: Path) -> dict[str, str]:
        raise AssertionError("regular scan must reuse the stable digest")

    monkeypatch.setattr("app.models.catalogue._compute_local_digests", fail_if_rehashed)
    assert catalogue.scan()[0].hf_match.status == "downloaded"


@pytest.mark.version_zero
def test_remote_file_date_is_read_from_expanded_last_commit() -> None:
    metadata = _tree_file_metadata(
        {
            "path": "model.gguf",
            "size": 7,
            "lfs": {"sha256": "a" * 64, "size": 7},
            "lastCommit": {"date": "2026-10-02T14:30:00Z"},
        }
    )

    assert metadata == {
        "size": 7,
        "sha256": "a" * 64,
        "file_date": "2026-10-02T14:30:00Z",
    }


@pytest.mark.version_zero
def test_remote_difference_is_update_when_local_still_matches_downloaded_baseline(
    tmp_path: Path,
) -> None:
    model = tmp_path / "model"
    baseline = b"weights"
    _write_manifest(model, baseline)
    _refresh_local_hash_cache(model)

    _apply_remote_snapshot(
        model,
        {},
        "main",
        "b" * 40,
        "2026-10-03T10:00:00Z",
        {
            "model.gguf": {
                "size": len(baseline),
                "sha256": _sha256(b"new-weights"),
                "file_date": "2026-10-03T09:00:00Z",
            }
        },
        "2026-10-04T10:00:00Z",
    )

    manifest = yaml.safe_load((model / ".llm-studio-hf.yaml").read_text(encoding="utf-8"))
    assert manifest["remote_update"]["status"] == "update_available"
    assert manifest["changed_paths"][0]["status"] == "changed"
    assert manifest["remote_check"]["repository_date"] == "2026-10-03T10:00:00Z"


@pytest.mark.version_zero
def test_remote_difference_is_not_update_when_local_already_matches_remote(tmp_path: Path) -> None:
    model = tmp_path / "model"
    baseline = b"weights"
    _write_manifest(model, baseline)
    (model / "model.gguf").write_bytes(b"new-weights")
    _refresh_local_hash_cache(model)

    _apply_remote_snapshot(
        model,
        {},
        "main",
        "b" * 40,
        "2026-10-03T10:00:00Z",
        {
            "model.gguf": {
                "size": len(b"new-weights"),
                "sha256": _sha256(b"new-weights"),
                "file_date": "2026-10-03T09:00:00Z",
            }
        },
        "2026-10-04T10:00:00Z",
    )

    manifest = yaml.safe_load((model / ".llm-studio-hf.yaml").read_text(encoding="utf-8"))
    assert manifest["remote_update"]["status"] == "up_to_date"
    assert all(item["status"] != "local_divergence" for item in manifest["changed_paths"])
