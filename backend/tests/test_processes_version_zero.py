# spec: version-zero
from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest
from app.process.discovery import identify_engine, process_port
from app.process.manager import ProcessManager, _snapshot_metadata
from app.schemas.profiles import ProfileData


class FakeProcess:
    pid = 22001
    returncode = None

    async def wait(self) -> int:
        await asyncio.sleep(0)
        return 0


class FakeHTTPClient:
    async def get(self, url: str):
        class Response:
            status_code = 404

        return Response()


@pytest.mark.asyncio
async def test_managed_process_uses_bash_group_and_profile_log(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[object, ...]] = []
    process = FakeProcess()

    async def fake_spawn(*args: object, **kwargs: object) -> FakeProcess:
        calls.append(args)
        assert kwargs["start_new_session"] is True
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_spawn)
    monkeypatch.setattr("app.process.manager._identity_start_ns", lambda pid: 123)
    monkeypatch.setattr("app.process.manager.ProcessManager.refresh", _empty_refresh)
    manager = ProcessManager(
        log_root=tmp_path, username="neural-deck", http_client=FakeHTTPClient()
    )
    value = ProfileData.model_validate(
        {
            "version": 1,
            "name": "demo",
            "model_name": "demo-model",
            "engine": {"id": "vllm", "installation_id": "i"},
            "model": {"path": "/models/x"},
            "command": (
                "{{engine}} serve --model {{model}} "
                "--served-model-name {{model_name}} --port 1901"
            ),
        }
    )

    instance = await manager.start_profile(value, "/bin/echo")
    assert instance.id == "managed/demo/22001-123"
    assert instance.port == 1901
    assert calls[0][:3] == (
        "/bin/bash",
        "-c",
        "/bin/echo serve --model /models/x "
        "--served-model-name demo-model --port 1901",
    )
    await asyncio.sleep(0.1)


@pytest.mark.asyncio
@pytest.mark.parametrize("engine_id", ["llama.cpp", "vllm", "custom-text"])
@pytest.mark.parametrize("port_source", ["command", "profile", "api_config"])
async def test_managed_profile_uses_rendered_command_port_for_api_url(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, engine_id: str, port_source: str
) -> None:
    class LiveProcess(FakeProcess):
        async def wait(self) -> int:
            await asyncio.Event().wait()
            return 0

    class ModelsClient:
        async def get(self, url: str):
            assert url == "http://127.0.0.1:1901/v1/models"
            return SimpleNamespace(
                status_code=200,
                json=lambda: {"data": [{"id": "llama-model"}]},
            )

    process = LiveProcess()

    async def fake_spawn(*args: object, **kwargs: object) -> FakeProcess:
        return process

    value = ProfileData.model_validate(
        {
            "version": 1,
            "name": "llama",
            "model_name": "llama-model",
            "engine": {"id": engine_id, "installation_id": "i"},
            "model": {"path": "/models/x.gguf"},
            "command": "{{engine}} --model {{model}} --host 127.0.0.1"
            + (" --port 1901" if port_source == "command" else ""),
            "port": 1901 if port_source == "profile" else None,
        }
    )
    resolved = SimpleNamespace(
        model_path=value.model.path,
        api_protocol="openai",
        command=value.command,
        executable_path=Path("/bin/echo"),
        model_name=value.model_name,
        installation=SimpleNamespace(root_path=tmp_path),
        types=["text"],
        api_base_url=(
            "http://127.0.0.1:1901/v1"
            if port_source == "api_config"
            else "http://127.0.0.1:8000/v1"
        ),
        ui_url=None,
        working_directory=tmp_path,
        healthcheck=None,
        environment={},
    )
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_spawn)
    monkeypatch.setattr("app.process.manager._identity_start_ns", lambda pid: 123)
    monkeypatch.setattr("app.process.manager.ProcessManager.refresh", _empty_refresh)
    managed = ProcessManager(
        log_root=tmp_path / "logs",
        username="neural-deck",
        http_client=ModelsClient(),
        profile_resolver=lambda profile: resolved,
    )

    instance = await managed.start_profile(value)

    assert instance.port == 1901
    assert instance.api_base_url == "http://127.0.0.1:1901/v1"
    await asyncio.sleep(0)
    assert instance.state == "ready"
    assert managed.registry.list()
    await managed.close()


@pytest.mark.parametrize("engine_id", ["llama.cpp", "vllm", "custom-text"])
def test_restore_repairs_definition_url_from_rendered_llama_port(engine_id: str) -> None:
    metadata = _snapshot_metadata(
        {
            "types": ["text"],
            "api_protocol": "openai",
            "api_base_url": "http://127.0.0.1:8000/v1",
            "ui_url": None,
        },
        engine_id,
        "/usr/bin/llama-server --model /models/x.gguf --port 1901",
        1901,
    )

    assert metadata["api_base_url"] == "http://127.0.0.1:1901/v1"


async def _empty_refresh(self: ProcessManager):
    from app.schemas.instances import InstanceList

    return InstanceList(items=[])


def test_external_engine_detection_requires_supported_command() -> None:
    assert identify_engine(["python", "-m", "vllm.entrypoints.openai.api_server"]) == "vllm"
    assert identify_engine(["python", "-m", "arbitrary.server"]) is None
    assert process_port(object(), ["vllm", "serve", "--port", "1901"]) == 1901


@pytest.mark.parametrize("command", [
    ["uv", "pip", "install", "vllm"],
    ["python3", "-m", "pip", "install", "vllm"],
    ["cmake", "--build", "/runtime/llama.cpp"],
    ["git", "clone", "https://github.com/ggml-org/llama.cpp"],
    ["bash", "-c", "/usr/bin/llama-server --model /models/test.gguf"],
    ["/usr/bin/llama-server", "--version"],
    ["/usr/bin/llama-server", "--help"],
    ["vllm", "bench", "serve"],
    ["python3", "-c", "import vllm"],
    ["python3", "setup.py", "install", "vllm"],
    ["ninfer", "install"],
    ["pip", "install", "freetoken"],
])
def test_installation_and_probe_commands_are_not_instances(command: list[str]) -> None:
    assert identify_engine(command) is None


@pytest.mark.parametrize("command,engine", [
    (["/opt/bin/llama-server", "--model", "/models/test.gguf"], "llama.cpp"),
    (["vllm", "serve", "/models/test"], "vllm"),
    (["/venv/bin/python3.12", "/venv/bin/vllm", "serve", "/models/test"], "vllm"),
    (["python3", "-u", "-m", "vllm.entrypoints.openai.api_server"], "vllm"),
    (["python3", "-m", "sglang.launch_server", "--model-path", "/models/test"], "sglang"),
    (["sglang", "serve", "--model-path", "/models/test"], "sglang"),
    (["ninfer", "/models/test"], "ninfer"),
    (["freetoken", "serve", "--model", "/models/test"], "freetoken"),
])
def test_server_entry_points_are_instances(command: list[str], engine: str) -> None:
    assert identify_engine(command) == engine
