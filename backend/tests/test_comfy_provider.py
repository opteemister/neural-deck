from __future__ import annotations

import asyncio
from pathlib import Path

import httpx
import pytest
from app.db import create_engine, create_session_factory
from app.errors import AppError
from app.proxy.comfy_provider import (
    ComfyProviderService,
    ComfyUIClient,
    ComfyUIProvider,
    ComfyVideoJob,
    ComfyVideoStore,
)
from app.proxy.routes import RouteConfigRepository
from tests.test_comfy_contract import package


def comfy_config():
    _, model = package()
    return {"sources": {"main": {"base_url": "http://comfy.test:8188", "user_context": "alice"}}, "models": {"qwen-image": {**model, "source": "main", "workflows": {"generate": "workflows/generate.json"}}}}


@pytest.mark.asyncio
async def test_lost_video_job_reports_unknown_without_resubmitting() -> None:
    paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        if request.url.path == "/history/lost":
            return httpx.Response(200, json={})
        if request.url.path == "/queue":
            return httpx.Response(200, json={"queue_running": [], "queue_pending": []})
        raise AssertionError(request.url)

    provider = ComfyUIProvider(
        comfy_config(),
        client_factory=lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    await provider.video_store.put(
        ComfyVideoJob(
            id="video_lost",
            source="main",
            prompt_id="lost",
            status="queued",
            source_url="http://comfy.test",
        )
    )
    with pytest.raises(AppError) as error:
        await provider.video_status("video_lost")
    assert error.value.code == "comfyui_job_unknown"
    assert error.value.status_code == 502
    assert paths == ["/history/lost", "/queue"]


@pytest.mark.asyncio
async def test_running_video_does_not_poll_queue_again() -> None:
    paths = []

    def handler(request):
        paths.append(request.url.path)
        if request.url.path == "/history/job":
            return httpx.Response(200, json={})
        if request.url.path == "/queue":
            return httpx.Response(200, json={"queue_running": [[1, "job"]], "queue_pending": []})
        raise AssertionError(request.url)

    provider = ComfyUIProvider(comfy_config(), client_factory=lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    await provider.video_store.put(ComfyVideoJob(id="video_job", source="main", prompt_id="job", status="queued", source_url="http://comfy.test"))
    assert (await provider.video_status("video_job")).status == "in_progress"
    assert (await provider.video_status("video_job")).status == "in_progress"
    assert (await provider.video_status("video_job")).status == "in_progress"
    assert paths == ["/history/job", "/queue", "/history/job", "/history/job"]


@pytest.mark.asyncio
async def test_prompt_validation_error_remains_400_and_is_not_retried() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            400,
            json={
                "error": {"type": "prompt_outputs_failed_validation"},
                "node_errors": {"6": {"errors": [{"type": "value_not_in_list"}]}},
            },
        )

    client = ComfyUIClient(
        "http://comfy.test",
        client_factory=lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    with pytest.raises(AppError) as error:
        await client.prompt({"6": {"class_type": "Text", "inputs": {"prompt": "test"}}})
    assert error.value.status_code == 400
    assert len(requests) == 1


def test_route_repository_validates_and_persists_comfyui(tmp_path: Path) -> None:
    repository = RouteConfigRepository(tmp_path)
    repository.save_comfyui(comfy_config())
    loaded = repository.load_comfyui()
    assert loaded["sources"]["main"]["base_url"] == "http://comfy.test:8188"
    assert loaded["models"]["qwen-image"]["max_megapixels"] == 2


def test_virtual_local_source_is_visible_without_being_persisted(tmp_path: Path) -> None:
    repository = RouteConfigRepository(tmp_path)
    service = ComfyProviderService(repository)
    assert service.get_settings()["sources"]["local"] == {"base_url": "http://127.0.0.1:8188"}
    assert repository.load_comfyui()["sources"] == {}

    repository.save_comfyui(
        {
            "sources": {"local": {"base_url": "http://custom.test:8188", "user_context": "deck"}},
            "models": {},
        }
    )
    assert service.get_settings()["sources"]["local"] == {
        "base_url": "http://custom.test:8188",
        "user_context": "deck",
    }


@pytest.mark.asyncio
async def test_source_status_checks_system_stats_and_unknown_source_is_404() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path == "/system_stats":
            return httpx.Response(200, json={"devices": []})
        if request.url.path.startswith("/userdata/"):
            return httpx.Response(
                200,
                json={"1": {"class_type": "CLIPTextEncode", "inputs": {"text": "default"}}},
            )
        raise AssertionError(request.url)

    service = ComfyProviderService(
        {
            "sources": {},
            "models": {
                "image": {
                    "source": "local",
                    "type": "image",
                    "workflows": {"generate": "workflows/generate.json"},
                }
            },
        },
        client_factory=lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    status = await service.source_status("local")
    assert status["available"] is True
    assert status["status"] == "ready"
    assert status["base_url"] == "http://127.0.0.1:8188"
    graph = await service.provider.load_graph("image", "generate")
    assert graph["1"]["class_type"] == "CLIPTextEncode"
    assert calls == ["/system_stats", "/userdata/workflows/generate.json"]
    with pytest.raises(AppError) as error:
        await service.source_status("missing")
    assert error.value.status_code == 404
    await service.close()


@pytest.mark.asyncio
async def test_userdata_file_rejects_paths_outside_workflows() -> None:
    called = False

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(200, json={})

    client = ComfyUIClient(
        "http://comfy.test:8188",
        client_factory=lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    with pytest.raises(AppError) as error:
        await client.userdata_file("models/checkpoint.safetensors")
    assert error.value.code == "comfyui_workflow_path_invalid"
    assert called is False


@pytest.mark.asyncio
async def test_catalog_live_then_restart_uses_only_same_source_cache(tmp_path: Path) -> None:
    cache_path = tmp_path / "workflow-cache.json"
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        if len(calls) == 1:
            return httpx.Response(200, json=["generate.json", "nested/edit.json"])
        raise httpx.ConnectError("offline", request=request)

    client = ComfyUIClient(
        "http://comfy.test:8188",
        user_context="alice",
        client_factory=lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    from app.proxy.comfy_provider import ComfyWorkflowCatalog

    catalog = ComfyWorkflowCatalog(cache_path)
    live, available = await catalog.list_workflows_with_status("main", client, user_context="alice")
    assert available is True
    assert [item["path"] for item in live] == [
        "workflows/generate.json",
        "workflows/nested/edit.json",
    ]
    restarted = ComfyWorkflowCatalog(cache_path)
    cached, available = await restarted.list_workflows_with_status(
        "main", client, user_context="alice"
    )
    assert available is False
    assert [item["path"] for item in cached] == [
        "workflows/generate.json",
        "workflows/nested/edit.json",
    ]
    other, available = await restarted.list_workflows_with_status(
        "main", client, user_context="bob"
    )
    assert available is False
    assert other == []
    assert "dir=workflows" in calls[0] and "recurse=true" in calls[0]


@pytest.mark.asyncio
async def test_provider_facade_workflow_shape_and_user_header(tmp_path: Path) -> None:
    route_repo = RouteConfigRepository(tmp_path)
    route_repo.save_comfyui(comfy_config())
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/userdata":
            return httpx.Response(200, json=["generate.json"])
        return httpx.Response(200, json={"6": {"class_type": "Text", "inputs": {}}})

    service = ComfyProviderService(
        route_repo,
        cache_path=tmp_path / "cache.json",
        client_factory=lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    listing = await service.workflows("main")
    assert listing == {
        "items": ["workflows/generate.json"],
        "available": True,
        "cached": False,
        "updated_at": listing["updated_at"],
    }
    graph = await service.provider.load_graph("qwen-image", "generate")
    assert graph["6"]["class_type"] == "Text"
    assert requests[0].headers["Comfy-User"] == "alice"
    await service.close()


@pytest.mark.asyncio
async def test_ui_workflow_returns_actionable_api_export_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/userdata/workflows/generate.json"
        return httpx.Response(
            200,
            json={
                "nodes": [{"id": 6, "type": "CLIPTextEncode", "widgets_values": ["text"]}],
                "links": [],
            },
        )

    provider = ComfyUIProvider(
        comfy_config(),
        client_factory=lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    with pytest.raises(AppError) as error:
        await provider.load_graph("qwen-image", "generate")
    assert error.value.code == "comfyui_workflow_invalid"
    assert error.value.status_code == 502
    assert error.value.message == (
        "Этот workflow сохранён для редактора ComfyUI. Выберите его API-экспорт."
    )


@pytest.mark.asyncio
async def test_validate_workflow_distinguishes_api_ui_missing_and_offline() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("api.json"):
            return httpx.Response(
                200,
                json={"6": {"class_type": "CLIPTextEncode", "inputs": {"text": "ok"}}},
            )
        if request.url.path.endswith("ui.json"):
            return httpx.Response(200, json={"nodes": [], "links": []})
        return httpx.Response(404, json={"error": "not found"})

    provider = ComfyUIProvider(
        comfy_config(),
        client_factory=lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    assert await provider.validate_workflow("main", "workflows/api.json") == {
        "path": "workflows/api.json",
        "usable": True,
    }
    ui_result = await provider.validate_workflow("main", "workflows/ui.json")
    assert ui_result["path"] == "workflows/ui.json"
    assert ui_result["usable"] is False
    assert "API-экспорт" in ui_result["message"]
    missing_result = await provider.validate_workflow("main", "workflows/missing.json")
    assert missing_result["usable"] is False
    assert "прочитать" in missing_result["message"]

    offline = ComfyUIProvider(
        comfy_config(),
        client_factory=lambda: httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda request: (_ for _ in ()).throw(
                    httpx.ConnectError("offline", request=request)
                )
            )
        ),
    )
    offline_result = await offline.validate_workflow("main", "workflows/api.json")
    assert offline_result == {
        "path": "workflows/api.json",
        "usable": None,
        "message": "ComfyUI недоступен; workflow не проверен",
    }

    with pytest.raises(AppError) as unknown:
        await provider.validate_workflow("unknown", "workflows/api.json")
    assert unknown.value.status_code == 404


@pytest.mark.asyncio
async def test_two_video_store_instances_can_initialize_same_sqlite_table(tmp_path: Path) -> None:
    engine = create_engine(tmp_path / "race.sqlite3")
    sessions = create_session_factory(engine)
    first = ComfyVideoStore(sessions)
    second = ComfyVideoStore(sessions)
    await asyncio.gather(first._ensure_table(), second._ensure_table())
    job = ComfyVideoJob(
        id="video_race",
        source="main",
        prompt_id="prompt-race",
        status="queued",
        model="main",
        created_at=1,
    )
    await first.put(job)
    assert await second.get(job.id) == job
    await engine.dispose()
