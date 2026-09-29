# spec: version-zero
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from app.process.discovery import identify_engine, process_port
from app.process.manager import ProcessManager
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
    manager = ProcessManager(log_root=tmp_path, username="studio", http_client=FakeHTTPClient())
    value = ProfileData.model_validate(
        {
            "version": 1,
            "name": "demo",
            "model_name": "demo-model",
            "engine": {"id": "vllm", "installation_id": "i"},
            "model": {"path": "/models/x"},
            "command": "{{engine}} serve --model {{model}} --port 1901",
        }
    )

    instance = await manager.start_profile(value, "/bin/echo")
    assert instance.id == "managed/demo/22001-123"
    assert instance.port == 1901
    assert calls[0][:3] == ("/bin/bash", "-c", "/bin/echo serve --model /models/x --port 1901")
    await asyncio.sleep(0.1)


async def _empty_refresh(self: ProcessManager):
    from app.schemas.instances import InstanceList

    return InstanceList(items=[])


def test_external_engine_detection_requires_supported_command() -> None:
    assert identify_engine(["python", "-m", "vllm.entrypoints.openai.api_server"]) == "vllm"
    assert identify_engine(["python", "-m", "arbitrary.server"]) is None
    assert process_port(object(), ["vllm", "serve", "--port", "1901"]) == 1901
