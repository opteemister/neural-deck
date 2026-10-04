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
def test_local_hash_cache_reuses_saved_hash_without_reading_again(
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

    before = yaml.safe_load(first_manifest)
    after = yaml.safe_load((model / ".llm-studio-hf.yaml").read_text())
    assert before["files"] == after["files"]
    assert "last_hash_check_at" in after


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
    _refresh_local_hash_cache(model)
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


@pytest.mark.version_zero
def test_scan_publishes_models_before_hashing_and_coalesces_parallel_scan(tmp_path, monkeypatch):
    import threading
    from concurrent.futures import ThreadPoolExecutor

    paths = build_paths(Settings(
        data_root=tmp_path / "data", models_root=tmp_path / "models",
        host="127.0.0.1", port=3000,
    ))
    model = paths.models_root / "model"
    _write_manifest(model, b"weights")
    sidecar = model / ".llm-studio-hf.yaml"
    manifest = yaml.safe_load(sidecar.read_text())
    manifest["files"][0].pop("downloaded_remote_hash")
    sidecar.write_text(yaml.safe_dump(manifest))
    catalogue = ModelCatalogue(paths)
    catalogue.prepare_background_scan()
    entered = threading.Event()
    release = threading.Event()
    calls = []
    from app.models.catalogue import _compute_local_digests

    original = _compute_local_digests

    def slow_digest(path):
        calls.append(path)
        entered.set()
        assert release.wait(5)
        return original(path)

    monkeypatch.setattr("app.models.catalogue._compute_local_digests", slow_digest)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(catalogue.scan)
        try:
            assert entered.wait(5)
            assert catalogue.scanning
            assert len(catalogue.items()) == 1
            assert catalogue.scan(verify_hashes=True) == catalogue.items()
            assert len(calls) == 1
        finally:
            release.set()
        assert len(future.result(timeout=5)) == 1
    assert not catalogue.scanning
    catalogue.scan(verify_hashes=True)
    assert len(calls) == 1  # The second completed scan reuses the saved hash.


@pytest.mark.parametrize("directory", [False, True])
def test_untracked_file_hash_is_persisted_and_only_changed_file_is_read(tmp_path, monkeypatch, directory):
    import app.models.catalogue as module

    target = tmp_path / ("model" if directory else "model.gguf")
    if directory:
        target.mkdir()
        weights = target / "model.gguf"
        (target / "config.json").write_text('{}')
    else:
        weights = target
    weights.write_bytes(b"weights")
    module._refresh_local_hash_cache(target)
    calls = []
    original = module._compute_local_digests

    def tracked(path):
        calls.append(path)
        return original(path)

    monkeypatch.setattr(module, "_compute_local_digests", tracked)
    module._refresh_local_hash_cache(target)
    assert calls == []
    weights.write_bytes(b"new weights")
    module._refresh_local_hash_cache(target)
    assert calls == [weights]
    calls.clear()
    module._refresh_local_hash_cache(target)
    assert calls == []


async def test_remote_check_does_not_run_local_hash_scan(tmp_path, monkeypatch):
    catalogue = ModelCatalogue(build_paths(Settings(
        data_root=tmp_path / "data", host="127.0.0.1", port=3000,
    )))
    catalogue.prepare_background_scan()

    def forbidden(*args, **kwargs):
        raise AssertionError("remote checks must use saved local hashes")

    monkeypatch.setattr(catalogue, "scan", forbidden)
    assert await catalogue.check_updates() == []


async def test_default_scheduler_waits_for_midnight_utc(tmp_path, monkeypatch):
    import asyncio
    import app.models.catalogue as module
    from datetime import UTC, datetime

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 10, 4, 13, 0, tzinfo=UTC)

    async def sleep(delay):
        assert delay == 11 * 3600
        raise asyncio.CancelledError

    monkeypatch.setattr(module, "datetime", Clock)
    monkeypatch.setattr(module.asyncio, "sleep", sleep)
    catalogue = ModelCatalogue(build_paths(Settings(
        data_root=tmp_path / "data", host="127.0.0.1", port=3000,
    )))
    with pytest.raises(asyncio.CancelledError):
        await catalogue._update_loop(None)


@pytest.mark.parametrize("remote_date", ["2026-09-30T09:00:00Z", "2026-10-01T11:00:00Z", None])
def test_remote_hash_difference_without_newer_date_is_not_an_update(tmp_path, remote_date):
    model = tmp_path / "model"
    _write_manifest(model, b"weights")
    _refresh_local_hash_cache(model)
    _apply_remote_snapshot(model, {}, "main", "b" * 40, None, {
        "model.gguf": {"size": 7, "sha256": _sha256(b"new"), "file_date": remote_date},
    }, "2026-10-04T10:00:00Z")
    manifest = yaml.safe_load((model / ".llm-studio-hf.yaml").read_text())
    assert manifest["remote_update"]["status"] != "update_available"


def test_standalone_hf_manifest_hash_cache_is_reused(tmp_path, monkeypatch):
    source = tmp_path / "source"
    _write_manifest(source, b"weights")
    weights = tmp_path / "model.gguf"
    weights.write_bytes(b"weights")
    sidecar = Path(f"{weights}.llm-studio-hf.yaml")
    sidecar.write_text((source / ".llm-studio-hf.yaml").read_text())
    _refresh_local_hash_cache(weights)

    def forbidden(path):
        raise AssertionError("standalone hash must be saved and reused")

    monkeypatch.setattr("app.models.catalogue._compute_local_digests", forbidden)
    _refresh_local_hash_cache(weights)
    assert yaml.safe_load(sidecar.read_text())["files"][0]["downloaded_remote_hash"] == _sha256(b"weights")


def test_local_scan_never_reconciles_remote_manifest(tmp_path, monkeypatch):
    catalogue = ModelCatalogue(build_paths(Settings(
        data_root=tmp_path / "data", models_root=tmp_path / "models",
        host="127.0.0.1", port=3000,
    )))
    model = catalogue.paths.models_root / "org" / "model"
    model.mkdir(parents=True)
    (model / "model.gguf").write_bytes(b"weights")

    def forbidden(*args):
        raise AssertionError("local scan must never call Hugging Face")

    monkeypatch.setattr("app.models.catalogue._reconcile_local_manifest", forbidden)
    assert len(catalogue.scan()) == 1


@pytest.mark.parametrize("tracked", [True, False])
def test_each_hash_is_saved_before_next_file_and_survives_interruption(tmp_path, monkeypatch, tracked):
    import app.models.catalogue as module

    model = tmp_path / "model"
    model.mkdir()
    for name in ("a.gguf", "b.gguf"):
        (model / name).write_bytes(name.encode())
    sidecar = model / (".llm-studio-hf.yaml" if tracked else ".llm-studio-hashes.yaml")
    if tracked:
        sidecar.write_text(yaml.safe_dump({
            "schema_version": 1, "repo_id": "org/model", "revision": "a" * 40,
            "scope": "selection", "files": [
                {"path": name, "size": len(name)} for name in ("a.gguf", "b.gguf")
            ],
        }))
    original = module._compute_local_digests
    calls = []

    class Interrupted(BaseException):
        pass

    def interrupted(path):
        calls.append(path.name)
        if path.name == "b.gguf":
            saved = yaml.safe_load(sidecar.read_text())
            digest = saved["files"][0]["local"] if tracked else saved["files"]["a.gguf"]
            assert digest["sha256"] == _sha256(b"a.gguf")
            assert "mtime_ns" not in digest
            raise Interrupted
        return original(path)

    monkeypatch.setattr(module, "_compute_local_digests", interrupted)
    with pytest.raises(Interrupted):
        module._refresh_local_hash_cache(model)

    calls.clear()

    def resumed(path):
        calls.append(path.name)
        return original(path)

    monkeypatch.setattr(module, "_compute_local_digests", resumed)
    module._refresh_local_hash_cache(model)
    assert calls == ["b.gguf"]
    assert "last_hash_check_at" in yaml.safe_load(sidecar.read_text())


@pytest.mark.parametrize("offset,expected_reads", [(-10, []), (10, ["model.gguf"])])
def test_only_mtime_after_global_check_triggers_recalculation(tmp_path, monkeypatch, offset, expected_reads):
    import os
    from datetime import UTC, datetime
    import app.models.catalogue as module

    model = tmp_path / "model"
    _write_manifest(model, b"weights")
    sidecar = model / ".llm-studio-hf.yaml"
    manifest = yaml.safe_load(sidecar.read_text())
    checked = datetime.now(UTC).timestamp() - 30
    manifest["last_hash_check_at"] = datetime.fromtimestamp(checked, UTC).isoformat()
    sidecar.write_text(yaml.safe_dump(manifest))
    # A different size alone is not a reason to hash when the date is older.
    weights = model / "model.gguf"
    weights.write_bytes(b"different weights")
    os.utime(weights, (checked + offset, checked + offset))
    original = module._compute_local_digests
    calls = []

    def record(path):
        calls.append(path.name)
        return original(path)

    monkeypatch.setattr(module, "_compute_local_digests", record)
    module._refresh_local_hash_cache(model)
    assert calls == expected_reads
