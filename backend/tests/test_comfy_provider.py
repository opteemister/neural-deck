from __future__ import annotations

import asyncio
import copy
import json
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


def comfy_config() -> dict[str, object]:
    return {
        "sources": {"main": {"base_url": "http://comfy.test:8188", "user_context": "alice"}},
        "models": {
            "qwen-image": {
                "source": "main",
                "type": "image",
                "workflows": {"generate": "workflows/generate.json"},
                "bindings": {
                    "generate": {
                        "prompt": {"node": "6", "input": "prompt"},
                        "size": {
                            "width": {"node": "6", "input": "width"},
                            "height": {"node": "6", "input": "height"},
                        },
                        "output_format": {"node": "9", "input": "format"},
                    }
                },
            }
        },
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field,value",
    [
        ("quality", "high"),
        ("background", "transparent"),
        ("output_format", "jpeg"),
        ("output_compression", 80),
        ("mask", "Zm9v"),
    ],
)
async def test_legacy_image_adapter_does_not_silently_drop_new_parameters(field, value) -> None:
    from app.image.service import ImageGenerationService

    service = ImageGenerationService(None, None)
    with pytest.raises(AppError) as error:
        await service.generate({"prompt": "test", field: value}, request_id="legacy")
    assert error.value.status_code == 400
    assert error.value.details["field"] == field


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
    assert loaded["models"]["qwen-image"]["bindings"]["generate"]["size"]["width"] == {
        "node": "6",
        "input": "width",
    }


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
async def test_graph_is_patched_in_memory_and_unsupported_field_is_400() -> None:
    provider = ComfyUIProvider(comfy_config())
    graph = {
        "6": {"class_type": "Text", "inputs": {"prompt": "default", "width": 512, "height": 512}},
        "9": {"class_type": "SaveImage", "inputs": {"format": "png"}},
    }
    model = provider._model("qwen-image")
    patched = provider.patch_graph(
        graph,
        model,
        "generate",
        {"prompt": "a fox", "size": "1024x768"},
    )
    assert patched["6"]["inputs"] == {"prompt": "a fox", "width": 1024, "height": 768}
    assert graph["6"]["inputs"]["prompt"] == "default"
    model["bindings"]["generate"]["prompt"]["input"] = "misspelled_prompt"
    with pytest.raises(AppError) as invalid_binding:
        provider.patch_graph(graph, model, "generate", {"prompt": "must be applied"})
    assert invalid_binding.value.status_code == 400
    assert invalid_binding.value.details["field"] == "prompt"
    assert "misspelled_prompt" not in graph["6"]["inputs"]
    model["bindings"]["generate"]["prompt"]["input"] = "prompt"
    with pytest.raises(Exception) as error:
        provider.patch_graph(graph, model, "generate", {"prompt": "fox", "quality": "high"})
    assert getattr(error.value, "status_code", None) == 400
    assert getattr(error.value, "details", {}).get("field") == "quality"


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
async def test_image_generation_queues_graph_and_returns_b64_without_writing_graph(
    tmp_path: Path,
) -> None:
    route_repo = RouteConfigRepository(tmp_path)
    route_repo.save_comfyui(comfy_config())

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.startswith("/userdata/"):
            return httpx.Response(
                200,
                json={
                    "6": {
                        "class_type": "Text",
                        "inputs": {"prompt": "default", "width": 512, "height": 512},
                    },
                    "9": {"class_type": "SaveImage", "inputs": {"format": "png"}},
                },
            )
        if request.url.path == "/prompt":
            body = request.read()
            assert b'"prompt":"a fox"' in body and b'"width":1024' in body
            return httpx.Response(200, json={"prompt_id": "image-prompt"})
        if request.url.path == "/history/image-prompt":
            return httpx.Response(
                200,
                json={
                    "image-prompt": {
                        "status": {"status_str": "success"},
                        "outputs": {
                            "9": {
                                "images": [
                                    {"filename": "fox.png", "subfolder": "", "type": "output"}
                                ]
                            }
                        },
                    }
                },
            )
        if request.url.path == "/view":
            return httpx.Response(200, content=b"png", headers={"content-type": "image/png"})
        raise AssertionError(request.url)

    service = ComfyProviderService(
        route_repo,
        client_factory=lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        poll_interval=0,
    )
    result = await service.generate_image(
        {
            "model": "qwen-image",
            "prompt": "a fox",
            "size": "1024x768",
            "response_format": "b64_json",
        }
    )
    assert result["data"][0]["b64_json"] == "cG5n"
    assert not (tmp_path / "workflows" / "generate.json").exists()
    await service.close()


@pytest.mark.asyncio
async def test_video_mapping_survives_provider_restart_and_keeps_source_snapshot(
    tmp_path: Path,
) -> None:
    route_repo = RouteConfigRepository(tmp_path)
    config = comfy_config()
    config["models"]["qwen-video"] = {
        "source": "main",
        "type": "video",
        "workflows": {"generate": "workflows/video.json"},
        "bindings": {
            "generate": {
                "prompt": {"node": "6", "input": "prompt"},
                "seconds": {"node": "7", "input": "seconds"},
                "size": {"node": "8", "input": "size"},
            }
        },
    }
    route_repo.save_comfyui(config)
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.startswith("/userdata/"):
            return httpx.Response(
                200,
                json={
                    "6": {"class_type": "Text", "inputs": {"prompt": "default"}},
                    "7": {"class_type": "Primitive", "inputs": {"seconds": 4}},
                    "8": {"class_type": "Primitive", "inputs": {"size": "512x512"}},
                },
            )
        if request.url.path == "/prompt":
            return httpx.Response(200, json={"prompt_id": "video-prompt"})
        if request.url.path == "/history/video-prompt":
            return httpx.Response(
                200,
                json={
                    "video-prompt": {
                        "status": {"status_str": "success"},
                        "outputs": {
                            "1": {
                                "videos": [
                                    {"filename": "video.mp4", "subfolder": "", "type": "output"}
                                ]
                            }
                        },
                    }
                },
            )
        if request.url.path == "/view":
            return httpx.Response(200, content=b"video", headers={"content-type": "video/mp4"})
        raise AssertionError(request.url)

    engine = create_engine(tmp_path / "db.sqlite3")
    sessions = create_session_factory(engine)
    first = ComfyProviderService(
        route_repo,
        session_factory=sessions,
        client_factory=lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    job = await first.create_video(
        {"model": "qwen-video", "prompt": "a lake", "seconds": "8", "size": "768x768"}
    )
    video_id = str(job["id"])
    route_repo.save_comfyui(
        {
            **config,
            "sources": {"main": {"base_url": "http://changed.test:8188", "user_context": "bob"}},
        }
    )
    await first.close()
    restarted = ComfyProviderService(
        route_repo,
        session_factory=sessions,
        client_factory=lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    status = await restarted.video_status(video_id)
    assert status["status"] == "completed"
    assert status["model"] == "qwen-video"
    assert status["seconds"] == "8"
    assert status["size"] == "768x768"
    assert "source" not in status and "prompt_id" not in status and "result" not in status
    content, media_type = await restarted.video_content(video_id)
    assert content == b"video" and media_type == "video/mp4"
    assert all("changed.test" not in str(request.url) for request in requests)
    await restarted.close()
    await engine.dispose()


@pytest.mark.asyncio
async def test_multiple_outputs_use_explicit_batch_binding_and_keep_graph_in_memory() -> None:
    config = copy.deepcopy(comfy_config())
    config["models"]["qwen-image"]["bindings"]["generate"]["n"] = {
        "node": "7",
        "input": "batch_size",
    }
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if request.url.path.startswith("/userdata/"):
            return httpx.Response(
                200,
                json={
                    "6": {"class_type": "Text", "inputs": {"prompt": "default"}},
                    "7": {"class_type": "Batch", "inputs": {"batch_size": 1}},
                    "9": {"class_type": "SaveImage", "inputs": {"format": "png"}},
                },
            )
        if request.url.path == "/prompt":
            body = json.loads(request.content)
            assert body["prompt"]["7"]["inputs"]["batch_size"] == 2
            assert body["prompt"]["6"]["inputs"]["prompt"] == "two foxes"
            return httpx.Response(200, json={"prompt_id": "batch-prompt"})
        if request.url.path == "/history/batch-prompt":
            return httpx.Response(
                200,
                json={
                    "batch-prompt": {
                        "status": {"status_str": "success"},
                        "outputs": {
                            "9": {
                                "images": [
                                    {"filename": "one.png", "subfolder": "", "type": "output"},
                                    {"filename": "two.png", "subfolder": "", "type": "output"},
                                ]
                            }
                        },
                    }
                },
            )
        if request.url.path == "/view":
            return httpx.Response(
                200,
                content=request.url.params["filename"].encode(),
                headers={"content-type": "image/png"},
            )
        raise AssertionError(request.url)

    provider = ComfyUIProvider(
        config,
        client_factory=lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        poll_interval=0,
    )
    outputs = await provider.run_image(
        "qwen-image",
        "generate",
        {"prompt": "two foxes", "n": 2},
        provided_fields={"prompt", "n"},
    )
    assert [item.content for item in outputs] == [b"one.png", b"two.png"]
    assert sum(request.url.path == "/prompt" for request in calls) == 1
    await provider.close()


@pytest.mark.asyncio
async def test_masked_edit_uploads_image_and_mask_to_their_explicit_nodes() -> None:
    config = copy.deepcopy(comfy_config())
    model = config["models"]["qwen-image"]
    model["workflows"]["edit_masked"] = "workflows/edit-masked.json"
    model["bindings"]["edit_masked"] = {
        "prompt": {"node": "6", "input": "prompt"},
        "image": {"node": "11", "input": "image"},
        "mask": {"node": "12", "input": "mask"},
    }
    uploads = ["uploaded-image.png", "uploaded-mask.png"]
    prompts: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.startswith("/userdata/"):
            return httpx.Response(
                200,
                json={
                    "6": {"class_type": "Text", "inputs": {"prompt": "default"}},
                    "11": {"class_type": "LoadImage", "inputs": {"image": "default-image"}},
                    "12": {"class_type": "LoadMask", "inputs": {"mask": "default-mask"}},
                    "9": {"class_type": "SaveImage", "inputs": {"format": "png"}},
                },
            )
        if request.url.path == "/upload/image":
            return httpx.Response(200, json={"name": uploads.pop(0), "subfolder": ""})
        if request.url.path == "/prompt":
            body = json.loads(request.content)
            prompts.append(body["prompt"])
            return httpx.Response(200, json={"prompt_id": "masked-prompt"})
        if request.url.path == "/history/masked-prompt":
            return httpx.Response(
                200,
                json={
                    "masked-prompt": {"outputs": {"9": {"images": [{"filename": "masked.png"}]}}}
                },
            )
        if request.url.path == "/view":
            return httpx.Response(200, content=b"masked", headers={"content-type": "image/png"})
        raise AssertionError(request.url)

    provider = ComfyUIProvider(
        config,
        client_factory=lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        poll_interval=0,
    )
    outputs = await provider.run_image(
        "qwen-image",
        "edit_masked",
        {"prompt": "replace", "image": b"image", "mask": b"mask"},
        provided_fields={"prompt", "image", "mask"},
    )
    assert outputs[0].content == b"masked"
    assert prompts[0]["11"]["inputs"]["image"] == "uploaded-image.png"
    assert prompts[0]["12"]["inputs"]["mask"] == "uploaded-mask.png"
    assert uploads == []
    await provider.close()


@pytest.mark.asyncio
async def test_video_reference_selects_edit_without_waiting_or_resubmitting() -> None:
    config = copy.deepcopy(comfy_config())
    config["models"]["video"] = {
        "source": "main",
        "type": "video",
        "workflows": {
            "generate": "workflows/video-generate.json",
            "edit": "workflows/video-edit.json",
        },
        "bindings": {
            "edit": {
                "prompt": {"node": "6", "input": "prompt"},
                "reference": {"node": "11", "input": "image"},
                "seconds": {"node": "13", "input": "seconds"},
            }
        },
    }
    paths: list[bytes] = []
    prompts: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.raw_path)
        if request.url.path.startswith("/userdata/"):
            return httpx.Response(
                200,
                json={
                    "6": {"class_type": "Text", "inputs": {"prompt": "default"}},
                    "11": {"class_type": "LoadImage", "inputs": {"image": "default"}},
                    "13": {"class_type": "Primitive", "inputs": {"seconds": 8}},
                },
            )
        if request.url.path == "/upload/image":
            return httpx.Response(200, json={"name": "reference.png", "subfolder": ""})
        if request.url.path == "/prompt":
            prompts.append(json.loads(request.content)["prompt"])
            return httpx.Response(200, json={"prompt_id": "video-edit-prompt"})
        raise AssertionError(request.url)

    provider = ComfyUIProvider(
        config,
        client_factory=lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    job = await provider.create_video(
        "video",
        {"prompt": "animate this", "input_reference": b"reference", "seconds": "4"},
        provided_fields={"prompt", "input_reference", "seconds"},
    )
    assert job.status == "queued"
    assert job.as_dict() == {
        "id": job.id,
        "object": "video",
        "model": "video",
        "status": "queued",
        "seconds": "4",
        "size": None,
        "error": None,
        "created_at": int(job.created_at),
    }
    assert b"/userdata/workflows%2Fvideo-edit.json" in paths
    assert prompts[0]["11"]["inputs"]["image"] == "reference.png"
    assert prompts[0]["13"]["inputs"]["seconds"] == 4
    assert b"/history/video-edit-prompt" not in paths
    assert await provider.video_store.delete(job.id) is True
    assert b"/interrupt" not in paths
    assert await provider.video_store.get(job.id) is None
    await provider.close()


@pytest.mark.asyncio
async def test_video_status_uses_queue_running_when_history_is_not_ready() -> None:
    config = copy.deepcopy(comfy_config())
    config["models"]["video"] = {
        "source": "main",
        "type": "video",
        "workflows": {"generate": "workflows/video.json"},
        "bindings": {"generate": {"prompt": {"node": "6", "input": "prompt"}}},
    }
    paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        if request.url.path.startswith("/userdata/"):
            return httpx.Response(
                200, json={"6": {"class_type": "Text", "inputs": {"prompt": "default"}}}
            )
        if request.url.path == "/prompt":
            return httpx.Response(200, json={"prompt_id": "queue-prompt"})
        if request.url.path == "/history/queue-prompt":
            return httpx.Response(200, json={})
        if request.url.path == "/queue":
            return httpx.Response(
                200, json={"queue_pending": [], "queue_running": [[1, "queue-prompt", {}]]}
            )
        raise AssertionError(request.url)

    provider = ComfyUIProvider(
        config,
        client_factory=lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    job = await provider.create_video(
        "video", {"prompt": "still running"}, provided_fields={"prompt"}
    )
    status = await provider.video_status(job.id)
    assert status.status == "in_progress"
    assert paths.count("/prompt") == 1
    await provider.close()


@pytest.mark.asyncio
async def test_history_error_is_reported_without_retrying_prompt() -> None:
    prompts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal prompts
        if request.url.path.startswith("/userdata/"):
            return httpx.Response(
                200, json={"6": {"class_type": "Text", "inputs": {"prompt": "default"}}}
            )
        if request.url.path == "/prompt":
            prompts += 1
            return httpx.Response(200, json={"prompt_id": "failed-prompt"})
        if request.url.path == "/history/failed-prompt":
            return httpx.Response(
                200,
                json={"failed-prompt": {"status": {"status_str": "failed", "messages": ["boom"]}}},
            )
        raise AssertionError(request.url)

    provider = ComfyUIProvider(
        comfy_config(),
        client_factory=lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        poll_interval=0,
    )
    with pytest.raises(AppError, match="ошибкой") as error:
        await provider.run_image(
            "qwen-image", "generate", {"prompt": "fail"}, provided_fields={"prompt"}
        )
    assert error.value.code == "comfyui_execution_failed"
    assert prompts == 1
    await provider.close()


def test_standard_parameters_are_validated_and_not_silently_dropped() -> None:
    provider = ComfyUIProvider(comfy_config())
    graph = {
        "6": {"class_type": "Text", "inputs": {"prompt": "default", "width": 512, "height": 512}},
        "7": {"class_type": "Batch", "inputs": {"batch": 1}},
        "8": {"class_type": "Quality", "inputs": {"quality": "auto", "background": "opaque"}},
        "9": {"class_type": "SaveImage", "inputs": {"format": "png", "compression": 90}},
    }
    config = copy.deepcopy(comfy_config())
    config["models"]["qwen-image"]["bindings"]["generate"].update(
        {
            "n": {"node": "7", "input": "batch"},
            "quality": {"node": "8", "input": "quality"},
            "background": {"node": "8", "input": "background"},
            "output_compression": {"node": "9", "input": "compression"},
        }
    )
    provider = ComfyUIProvider(config)
    patched = provider.patch_graph(
        graph,
        provider._model("qwen-image"),
        "generate",
        {
            "prompt": "ok",
            "size": "1x2",
            "n": 1,
            "quality": "high",
            "background": "transparent",
            "output_format": "png",
            "output_compression": 0,
        },
        provided_fields={
            "prompt",
            "size",
            "n",
            "quality",
            "background",
            "output_format",
            "output_compression",
        },
    )
    assert patched["6"]["inputs"]["width"] == 1
    assert patched["6"]["inputs"]["height"] == 2
    assert patched["7"]["inputs"]["batch"] == 1
    assert patched["8"]["inputs"] == {"quality": "high", "background": "transparent"}
    assert patched["9"]["inputs"] == {"format": "png", "compression": 0}

    for params, field in (
        ({"prompt": "x", "size": "0x512"}, "size"),
        ({"prompt": "x", "n": 0}, "n"),
        ({"prompt": "x", "output_format": "gif"}, "output_format"),
        ({"prompt": "x", "output_compression": 101}, "output_compression"),
    ):
        with pytest.raises(AppError) as error:
            provider.patch_graph(
                graph,
                provider._model("qwen-image"),
                "generate",
                params,
                provided_fields=set(params),
            )
        assert error.value.status_code == 400
        assert error.value.details.get("field") == field


@pytest.mark.asyncio
async def test_url_asset_descriptor_survives_restart_and_source_change(tmp_path: Path) -> None:
    route_repo = RouteConfigRepository(tmp_path)
    config = comfy_config()
    route_repo.save_comfyui(config)
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.startswith("/userdata/"):
            return httpx.Response(
                200,
                json={
                    "6": {
                        "class_type": "Text",
                        "inputs": {"prompt": "default", "width": 512, "height": 512},
                    },
                    "9": {"class_type": "SaveImage", "inputs": {"format": "png"}},
                },
            )
        if request.url.path == "/prompt":
            return httpx.Response(200, json={"prompt_id": "asset-prompt"})
        if request.url.path == "/history/asset-prompt":
            return httpx.Response(
                200,
                json={
                    "asset-prompt": {
                        "status": {"status_str": "success"},
                        "outputs": {
                            "9": {
                                "images": [
                                    {"filename": "asset.png", "subfolder": "", "type": "output"}
                                ]
                            }
                        },
                    }
                },
            )
        if request.url.path == "/view":
            return httpx.Response(200, content=b"asset", headers={"content-type": "image/png"})
        raise AssertionError(request.url)

    engine = create_engine(tmp_path / "assets.sqlite3")
    sessions = create_session_factory(engine)
    first = ComfyProviderService(
        route_repo,
        session_factory=sessions,
        client_factory=lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        poll_interval=0,
    )
    result = await first.generate_image({"model": "qwen-image", "prompt": "asset"})
    asset_id = result["data"][0]["url"].rsplit("/", 1)[-1]
    route_repo.save_comfyui(
        {
            **config,
            "sources": {"main": {"base_url": "http://changed.test:8188", "user_context": "bob"}},
        }
    )
    await first.close()

    restarted = ComfyProviderService(
        route_repo,
        session_factory=sessions,
        client_factory=lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        poll_interval=0,
    )
    content, media_type, filename = await restarted.asset(asset_id)
    assert content == b"asset" and media_type == "image/png" and filename == "asset.png"
    assert all("changed.test" not in str(request.url) for request in requests)
    await restarted.close()
    await engine.dispose()


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
