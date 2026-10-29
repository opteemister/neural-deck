"""Обновление выбранных файлов на прежнем месте без смены остальных baseline."""

import asyncio
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from app.api.dependencies import get_model_catalogue
from app.api.management import router
from app.config import Settings
from app.errors import AppError, app_error_handler
from app.main import RequestIdMiddleware
from app.mcp.operations import build_operations
from app.models.catalogue import (
    SIDECAR_DIR,
    ModelCatalogue,
    _apply_remote_snapshot,
    _read_manifest,
    _refresh_local_hash_cache,
)
from app.paths import build_paths
from app.schemas.models import HfFile, HfRepoData, ModelFileUpdateRequest, ModelFileUpdates
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError

OLD = "a" * 40
NEW = "b" * 40
LOCAL_DATE = "2026-10-01T00:00:00Z"
REMOTE_DATE = "2026-10-20T00:00:00Z"


def sha(content):
    return hashlib.sha256(content).hexdigest()


@pytest.fixture
async def model_update(tmp_path, monkeypatch):
    settings = Settings(data_root=tmp_path / "data", host="127.0.0.1", port=3000)
    paths = build_paths(settings)
    destination = paths.models_root / "custom-location"
    destination.mkdir(parents=True)
    old_files = {"weights.gguf": b"old weights", "config.json": b"old config"}
    contents = {
        "weights.gguf": b"new weights",
        "config.json": b"new config",
        "other-quant.gguf": b"unselected",
    }
    for name, content in old_files.items():
        (destination / name).write_bytes(content)
    manifest = {
        "schema_version": 2,
        "repo_id": "org/model",
        "revision": OLD,
        "tracking_ref": "main",
        "scope": "selection",
        "files": [
            {
                "path": name,
                "size": len(content),
                "sha256": sha(content),
                "downloaded_remote_hash": sha(content),
                "downloaded_remote_hash_kind": "lfs_sha256",
                "downloaded_file_date": LOCAL_DATE,
            }
            for name, content in old_files.items()
        ],
    }
    (destination / SIDECAR_DIR).write_text(yaml.safe_dump(manifest))
    _refresh_local_hash_cache(destination)
    remote_files = [
        HfFile(path=name, size=len(content), sha256=sha(content), file_date=REMOTE_DATE)
        for name, content in contents.items()
    ]
    tree = {item.path: item.model_dump(mode="json") for item in remote_files}
    _apply_remote_snapshot(destination, manifest, "main", NEW, REMOTE_DATE, tree, REMOTE_DATE)
    catalogue = ModelCatalogue(paths)
    model = catalogue._describe(destination)
    catalogue._items = [model]
    calls = []

    async def repo(repo_id, revision):
        assert (repo_id, revision) == ("org/model", NEW)
        return HfRepoData(
            repo_id=repo_id, revision=NEW, files=remote_files, revision_date=REMOTE_DATE
        )

    async def download(command, **kwargs):
        plan = json.loads(Path(command[-1]).read_text())
        calls.append(plan)
        assert plan["force"] is True
        assert plan["revision"] == NEW
        for item in plan["files"]:
            target = Path(plan["destination"]) / item["path"]
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(contents[item["path"]])
        kwargs["on_stdout_line"](
            json.dumps(
                {
                    "files_total": len(plan["files"]),
                    "files_completed": len(plan["files"]),
                    "bytes_total": 1,
                    "bytes_completed": 1,
                    "current_file": None,
                }
            )
        )
        return 0, "", ""

    monkeypatch.setattr(catalogue, "hf_repo", repo)
    monkeypatch.setattr("app.models.catalogue.run_command", download)
    yield SimpleNamespace(catalogue=catalogue, model=model, path=destination, calls=calls)
    await catalogue.close()


async def finish(catalogue, operation):
    await asyncio.gather(*list(catalogue.operations._tasks))
    return catalogue.operations.get(operation["id"])


async def test_one_file_update_preserves_other_files_and_baselines(model_update):
    f = model_update
    before = _read_manifest(f.path)
    details = ModelFileUpdates.model_validate(f.catalogue.file_updates(f.model.id))
    assert {item.path for item in details.items} == {"weights.gguf", "config.json"}
    weights = next(item for item in details.items if item.path == "weights.gguf")
    assert weights.local.hash == sha(b"old weights")
    assert weights.local.file_date == LOCAL_DATE
    assert weights.remote.hash == sha(b"new weights")
    assert weights.remote.file_date == REMOTE_DATE
    result = await finish(f.catalogue, await f.catalogue.update_files(f.model.id, ["weights.gguf"]))
    assert result["state"] == "succeeded", result["error"]
    assert (f.path / "weights.gguf").read_bytes() == b"new weights"
    assert (f.path / "config.json").read_bytes() == b"old config"
    assert not (f.path / "other-quant.gguf").exists()
    assert f.calls[0]["destination"] == str(f.path)
    after = _read_manifest(f.path)
    old_config = next(item for item in before["files"] if item["path"] == "config.json")
    new_config = next(item for item in after["files"] if item["path"] == "config.json")
    for key in ["downloaded_remote_hash", "downloaded_file_date", "size"]:
        assert new_config[key] == old_config[key]
    remaining = f.catalogue.file_updates(f.model.id)
    assert [item["path"] for item in remaining["items"]] == ["config.json"]
    assert f.catalogue.items()[0].hf_match.remote_update.changed_files == 1


async def test_update_all_uses_only_changed_files_in_this_installation(model_update):
    f = model_update
    result = await finish(f.catalogue, await f.catalogue.update_files(f.model.id, None))
    assert result["state"] == "succeeded", result["error"]
    assert {item["path"] for item in f.calls[0]["files"]} == {"weights.gguf", "config.json"}
    assert not (f.path / "other-quant.gguf").exists()
    assert f.catalogue.file_updates(f.model.id)["items"] == []
    assert f.catalogue.items()[0].hf_match.remote_update.status == "up_to_date"


async def test_failed_download_does_not_advance_baseline_and_can_be_retried(
    model_update, monkeypatch
):
    f = model_update
    baseline = (f.path / SIDECAR_DIR).read_bytes()
    original = f.catalogue._download_files

    async def fail(*args, **kwargs):
        raise OSError("network disconnected")

    monkeypatch.setattr(f.catalogue, "_download_files", fail)
    operation = await f.catalogue.update_files(f.model.id, ["weights.gguf"])
    result = await finish(f.catalogue, operation)
    assert result["state"] == "failed"
    assert "network disconnected" in result["error"]
    assert (f.path / SIDECAR_DIR).read_bytes() == baseline
    assert (f.path / "weights.gguf").read_bytes() == b"old weights"
    monkeypatch.setattr(f.catalogue, "_download_files", original)
    result = await finish(f.catalogue, await f.catalogue.update_files(f.model.id, ["weights.gguf"]))
    assert result["state"] == "succeeded", result["error"]


async def test_invalid_paths_unknown_files_and_untracked_models_are_rejected(model_update):
    f = model_update
    with pytest.raises(ValidationError):
        ModelFileUpdateRequest(files=["../other.gguf"])
    with pytest.raises(ValidationError):
        ModelFileUpdateRequest(files=[])
    for files in [[], ["other-quant.gguf"], ["../other.gguf"]]:
        with pytest.raises(AppError):
            await f.catalogue.update_files(f.model.id, files)
    with pytest.raises(AppError) as error:
        f.catalogue.file_updates("missing")
    assert error.value.status_code == 404
    assert f.calls == []


async def test_http_and_mcp_use_the_same_file_updater(model_update):
    f = model_update
    app = FastAPI()
    app.add_middleware(RequestIdMiddleware)
    app.include_router(router)
    app.dependency_overrides[get_model_catalogue] = lambda: f.catalogue
    app.add_exception_handler(AppError, app_error_handler)
    app.state.runtime = SimpleNamespace(model_catalogue=f.catalogue)
    async with AsyncClient(transport=ASGITransport(app), base_url="http://test") as client:
        response = await client.get(f"/api/v1/models/local/{f.model.id}/updates")
        assert response.status_code == 200
        assert len(response.json()["data"]["items"]) == 2
        response = await client.post(
            f"/api/v1/models/local/{f.model.id}/updates", json={"files": ["weights.gguf"]}
        )
        assert response.status_code == 202
        result = await finish(f.catalogue, response.json()["data"])
        assert result["state"] == "succeeded", result["error"]
        response = await client.post(
            f"/api/v1/models/local/{f.model.id}/updates", json={"files": ["../x"]}
        )
        assert response.status_code == 422
    operations = build_operations(app)
    assert len((await operations["get_model_file_updates"](f.model.id))["items"]) == 1
    result = await operations["update_model_files"](f.model.id, None)
    assert (await finish(f.catalogue, {"id": result["id"]}))["state"] == "succeeded"


async def test_local_divergence_is_replaced_even_when_hf_revision_is_unchanged(model_update):
    f = model_update
    await finish(f.catalogue, await f.catalogue.update_files(f.model.id, None))
    (f.path / "weights.gguf").write_bytes(b"local edit!")
    _refresh_local_hash_cache(f.path)
    current = _read_manifest(f.path)
    tree = {
        item["path"]: {"size": item["size"], "sha256": item["sha256"], "file_date": REMOTE_DATE}
        for item in current["files"]
    }
    _apply_remote_snapshot(f.path, current, "main", NEW, REMOTE_DATE, tree, REMOTE_DATE)
    f.catalogue._items = [f.catalogue._describe(f.path)]
    assert f.catalogue.file_updates(f.model.id)["items"][0]["status"] == "local_divergence"
    result = await finish(f.catalogue, await f.catalogue.update_files(f.model.id, ["weights.gguf"]))
    assert result["state"] == "succeeded", result["error"]
    assert f.calls[-1]["force"] is True
    assert (f.path / "weights.gguf").read_bytes() == b"new weights"
    assert f.catalogue.file_updates(f.model.id)["items"] == []


async def test_symlink_escape_fails_before_downloading(model_update, tmp_path):
    f = model_update
    outside = tmp_path / "outside.gguf"
    outside.write_bytes(b"untouched")
    (f.path / "weights.gguf").unlink()
    (f.path / "weights.gguf").symlink_to(outside)
    result = await finish(f.catalogue, await f.catalogue.update_files(f.model.id, ["weights.gguf"]))
    assert result["state"] == "failed"
    assert f.calls == []
    assert outside.read_bytes() == b"untouched"


async def test_standalone_model_keeps_filename_and_can_clear_update_badge(model_update):
    f = model_update
    standalone = f.path.parent / "renamed.gguf"
    standalone.write_bytes(b"old weights")
    current = _read_manifest(f.path)
    current["files"] = [item for item in current["files"] if item["path"] == "weights.gguf"]
    current["changed_paths"] = [
        item for item in current["changed_paths"] if item["path"] == "weights.gguf"
    ]
    Path(f"{standalone}{SIDECAR_DIR}").write_text(yaml.safe_dump(current))
    model = f.catalogue._describe(standalone)
    f.catalogue._items = [model]
    result = await finish(f.catalogue, await f.catalogue.update_files(model.id, None))
    assert result["state"] == "succeeded", result["error"]
    assert standalone.read_bytes() == b"new weights"
    assert f.catalogue.file_updates(model.id)["items"] == []
    assert not list(standalone.parent.glob(".nd-update-*"))
