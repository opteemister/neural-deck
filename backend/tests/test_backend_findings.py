from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from app.config import Settings
from app.engines.adapters import PythonEngineAdapter
from app.engines.discovery import DiscoveryCoordinator
from app.engines.service import EngineService
from app.models.catalogue import _safe_hf_path, _stream_response_to_file
from app.operations import OperationManager
from app.paths import build_paths, validate_extra_venv_roots
from app.schemas.engines import PythonVenvInstallationCreate


@pytest.mark.version_zero
def test_corrupted_settings_file_is_explicit_error(tmp_path: Path) -> None:
    settings = Settings(data_root=tmp_path, host="127.0.0.1", port=8000)
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "settings.yaml").write_text("models_root: [", encoding="utf-8")

    with pytest.raises(ValueError, match="settings.yaml"):
        build_paths(settings)


@pytest.mark.version_zero
def test_settings_patch_keeps_runtime_when_atomic_save_fails(client, settings, monkeypatch) -> None:
    import app.api.management as management

    previous = client.get("/api/v1/settings").json()["data"]["models_root"]

    def fail_save(*_args, **_kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(management, "_atomic_write_settings", fail_save)
    response = client.patch(
        "/api/v1/settings", json={"models_root": str(settings.data_root / "new-models")}
    )

    assert response.status_code == 500
    assert client.get("/api/v1/settings").json()["data"]["models_root"] == previous
    assert not (settings.data_root / "config" / "settings.yaml").exists()


@pytest.mark.version_zero
def test_hf_symlink_escape_is_rejected(tmp_path: Path) -> None:
    root = tmp_path / "models"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    (root / "org").symlink_to(outside, target_is_directory=True)

    with pytest.raises(ValueError, match="пределы models_root"):
        _safe_hf_path(root, "org/repo/model.gguf")


@pytest.mark.version_zero
def test_missing_hf_size_stays_unknown(tmp_path: Path) -> None:
    from app.models.catalogue import ModelCatalogue

    model = tmp_path / "models" / "repo"
    model.mkdir(parents=True)
    (model / "model.gguf").write_bytes(b"weights")
    (model / ".neural-deck-hf.yaml").write_text(
        "schema_version: 1\n"
        "repo_id: org/repo\n"
        "revision: abc123\n"
        "scope: snapshot\n"
        "files:\n"
        "  - path: model.gguf\n"
        "    size: null\n",
        encoding="utf-8",
    )
    settings = Settings(
        data_root=tmp_path / "data", host="127.0.0.1", port=8000, models_root=tmp_path / "models"
    )

    item = next(item for item in ModelCatalogue(build_paths(settings)).scan() if item.path == model)
    assert item.hf_match.status == "unknown"


@pytest.mark.version_zero
def test_hf_cache_snapshot_without_expected_manifest_stays_unknown(tmp_path: Path) -> None:
    from app.models.catalogue import ModelCatalogue

    cache_root = tmp_path / "cache" / "huggingface" / "hub"
    revision = "0123456789abcdef0123456789abcdef01234567"
    snapshot = cache_root / "models--org--repo" / "snapshots" / revision
    snapshot.mkdir(parents=True)
    (snapshot / "model.safetensors").write_bytes(b"partial")
    refs = cache_root / "models--org--repo" / "refs"
    refs.mkdir()
    (refs / "main").write_text(revision, encoding="utf-8")
    settings = Settings(
        data_root=tmp_path / "data",
        host="127.0.0.1",
        port=8000,
        models_root=cache_root,
    )

    items = ModelCatalogue(build_paths(settings)).scan()
    snapshot_item = next(item for item in items if item.path == snapshot)
    assert snapshot_item.hf_match.status == "unknown"


@pytest.mark.version_zero
def test_streaming_download_cleans_temp_file_on_error(tmp_path: Path) -> None:
    destination = tmp_path / "model.gguf"

    class BrokenResponse:
        async def aiter_bytes(self, _chunk_size: int):
            yield b"partial"
            raise OSError("network broke")

    with pytest.raises(OSError):
        asyncio.run(_stream_response_to_file(BrokenResponse(), destination))

    assert not destination.exists()
    assert list(tmp_path.iterdir()) == []


@pytest.mark.version_zero
def test_failed_install_marker_remains_broken_candidate(tmp_path: Path) -> None:
    settings = Settings(data_root=tmp_path / "data", host="127.0.0.1", port=8000)
    paths = build_paths(settings)
    paths.venvs_dir.mkdir(parents=True)
    failed = paths.venvs_dir / "failed"
    failed.mkdir()
    (failed / ".neural-deck-engine.yaml").write_text(
        "engine_id: vllm\nstatus: broken\n", encoding="utf-8"
    )

    adapter = PythonEngineAdapter("vllm", "vLLM", "vllm")
    assert adapter.candidates_from_venv(paths.venvs_dir, "test")
    result = DiscoveryCoordinator(settings, paths).scan()
    candidate = next(item for item in result.installations if item.root_path == failed)
    assert candidate.status == "broken"
    assert not candidate.verified


@pytest.mark.version_zero
def test_broken_manifest_overrides_passing_executable(tmp_path: Path, monkeypatch) -> None:
    settings = Settings(data_root=tmp_path / "data", host="127.0.0.1", port=8000)
    paths = build_paths(settings)
    venv = paths.venvs_dir / "stale"
    (venv / "bin").mkdir(parents=True)
    executable = venv / "bin" / "vllm"
    executable.write_text("#!/bin/sh\n", encoding="utf-8")
    executable.chmod(0o755)
    (venv / ".neural-deck-engine.yaml").write_text(
        "engine_id: vllm\nstatus: broken\nversion: null\ndiagnostic: update failed\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("app.engines.adapters._run_version", lambda command: (True, "9.9", None))

    candidate = next(
        item
        for item in DiscoveryCoordinator(settings, paths).scan().installations
        if item.root_path == venv
    )
    assert candidate.status == "broken"
    assert candidate.verified is False
    assert candidate.version is None
    assert candidate.diagnostic == "update failed"


@pytest.mark.version_zero
async def test_cancelled_install_writes_broken_marker_and_rescans(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import app.engines.service as service_module

    settings = Settings(data_root=tmp_path / "data", host="127.0.0.1", port=8000)
    paths = build_paths(settings)
    paths.venvs_dir.mkdir(parents=True)
    service = EngineService(settings, paths)
    entered = asyncio.Event()

    async def blocked_command(*_args, **_kwargs):
        entered.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(service_module, "run_command", blocked_command)
    operation = await service.create_installation(
        "vllm", PythonVenvInstallationCreate(kind="python_venv", venv_name="cancelled")
    )
    await entered.wait()
    await service.operations.cancel(operation["id"])

    marker = paths.venvs_dir / "cancelled" / ".neural-deck-engine.yaml"
    assert marker.is_file()
    assert "status: broken" in marker.read_text(encoding="utf-8")
    assert (
        service.get_installation(
            next(item.id for item in service.installations() if item.root_path.name == "cancelled")
        ).status
        == "broken"
    )
    await service.close()


@pytest.mark.version_zero
async def test_cancelled_update_marks_existing_environment_broken(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import app.engines.service as service_module

    settings = Settings(data_root=tmp_path / "data", host="127.0.0.1", port=8000)
    paths = build_paths(settings)
    venv = paths.venvs_dir / "existing"
    (venv / "bin").mkdir(parents=True)
    executable = venv / "bin" / "vllm"
    executable.write_text("#!/bin/sh\n", encoding="utf-8")
    executable.chmod(0o755)
    monkeypatch.setattr("app.engines.adapters._run_version", lambda command: (True, "1.0", None))
    service = EngineService(settings, paths)
    installation = next(item for item in service.scan().installations if item.root_path == venv)
    entered = asyncio.Event()

    async def blocked_command(*_args, **_kwargs):
        entered.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(service_module, "run_command", blocked_command)
    operation = await service.update_installation(installation.id)
    await entered.wait()
    await service.operations.cancel(operation["id"])

    marker = venv / ".neural-deck-engine.yaml"
    assert "status: broken" in marker.read_text(encoding="utf-8")
    assert service.get_installation(installation.id).status == "broken"
    await service.close()


@pytest.mark.version_zero
async def test_failed_install_rescans_and_keeps_broken_candidate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import app.engines.service as service_module

    settings = Settings(data_root=tmp_path / "data", host="127.0.0.1", port=8000)
    paths = build_paths(settings)
    paths.venvs_dir.mkdir(parents=True)
    service = EngineService(settings, paths)

    async def failed_command(*_args, **_kwargs):
        return 1, "", "install failed"

    monkeypatch.setattr(service_module, "run_command", failed_command)
    operation = await service.create_installation(
        "vllm", PythonVenvInstallationCreate(kind="python_venv", venv_name="failed-install")
    )
    await asyncio.sleep(0.05)

    assert service.operations.get(operation["id"])["state"] == "failed"
    candidate = next(
        item for item in service.installations() if item.root_path.name == "failed-install"
    )
    assert candidate.status == "broken"
    await service.close()


@pytest.mark.version_zero
async def test_failed_update_rescans_current_broken_verification(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import app.engines.service as service_module

    settings = Settings(data_root=tmp_path / "data", host="127.0.0.1", port=8000)
    paths = build_paths(settings)
    venv = paths.venvs_dir / "existing"
    (venv / "bin").mkdir(parents=True)
    executable = venv / "bin" / "vllm"
    executable.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
    executable.chmod(0o755)
    service = EngineService(settings, paths)
    initial = service.scan()
    installation = next(item for item in initial.installations if item.root_path == venv)

    async def failed_command(*_args, **_kwargs):
        return 1, "", "update failed"

    monkeypatch.setattr(service_module, "run_command", failed_command)
    operation = await service.update_installation(installation.id)
    await asyncio.sleep(0.05)

    assert service.operations.get(operation["id"])["state"] == "failed"
    assert service.get_installation(installation.id).status == "broken"
    await service.close()


@pytest.mark.version_zero
def test_extra_root_symlink_loop_is_diagnostic(tmp_path: Path) -> None:
    root = tmp_path / "loop"
    root.symlink_to(root, target_is_directory=True)
    settings = Settings(
        data_root=tmp_path / "data",
        host="127.0.0.1",
        port=8000,
        extra_venv_roots=[root],
    )
    paths = build_paths(settings)

    result = DiscoveryCoordinator(settings, paths).scan()

    assert any(item["path"] == str(root) for item in result.diagnostics)
    assert validate_extra_venv_roots(settings)[0].code == "venv_root_error"


@pytest.mark.version_zero
def test_operation_log_failure_is_visible(tmp_path: Path) -> None:
    log_path = tmp_path / "log-file"
    log_path.write_text("not a directory", encoding="utf-8")

    async def run() -> dict[str, object]:
        manager = OperationManager(log_path)
        operation = await manager.create("test", "target", lambda _: _done())
        await asyncio.sleep(0)
        await asyncio.gather(*manager._tasks)
        return operation

    async def _done() -> None:
        return None

    operation = asyncio.run(run())
    assert operation["state"] == "failed"
    assert "сохранить лог" in str(operation["error"])


@pytest.mark.parametrize("padding", ["", "x" * 5000])
def test_operation_error_keeps_context_and_final_cause(padding: str) -> None:
    message = "Загрузчик Hugging Face завершился с кодом 1: " + padding
    message += "\nRuntimeError: Internal Writer Error: No space left on device"

    async def run() -> dict[str, object]:
        async def fail(_: dict[str, object]) -> None:
            raise RuntimeError(message)

        manager = OperationManager()
        operation = await manager.create("download", "target", fail)
        await asyncio.gather(*manager._tasks)
        return operation

    error = str(asyncio.run(run())["error"])
    assert error.startswith("Загрузчик Hugging Face завершился с кодом 1:")
    assert error.endswith("Internal Writer Error: No space left on device")
    if padding:
        assert "середина сообщения сокращена" in error
        assert len(error) < 2100
    else:
        assert error == message
