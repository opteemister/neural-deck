"""The provider only prepares inputs and transports opaque workflow results."""

import base64
import copy
import json

import httpx
import pytest

from app.proxy.comfy_provider import ComfyProviderService
from tests.test_comfy_contract import package


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["generate", "edit", "edit_masked", "variations"])
async def test_images_preserve_graph_and_return_results_without_media_audits(operation):
    graph, model = package("image", operation)
    graph["n"] = {"class_type": "AnyInteger", "inputs": {"value": 1}, "_meta": {"title": "ND.v1.n"}}
    graph["unrelated"] = {"class_type": "OtherParallelWork", "inputs": {"anything": "keep me"}}
    graph["result"]["class_type"] = "AnyCustomOutput"
    original = copy.deepcopy(graph)
    sent, uploaded = [], []
    native_bytes = b"opaque workflow result, not audited by the transport"

    def upstream(request):
        if request.url.path.startswith("/userdata/"):
            return httpx.Response(200, json=graph)
        if request.url.path == "/upload/image":
            uploaded.append(request.content)
            return httpx.Response(200, json={"name": f"input-{len(uploaded)}.png"})
        if request.url.path == "/prompt":
            sent.append(json.loads(request.content))
            return httpx.Response(200, json={"prompt_id": "image-job"})
        if request.url.path == "/history/image-job":
            return httpx.Response(200, json={"image-job": {"status": {"status_str": "success"}, "outputs": {"result": {"images": [{"filename": "native.png"}]}, "unrelated": {"files": [{"filename": "other.bin"}]}}}})
        if request.url.path == "/view":
            return httpx.Response(200, content=native_bytes, headers={"content-type": "image/png"})
        raise AssertionError(f"Unexpected inspection: {request.url}")

    service = ComfyProviderService({"sources": {"local": {"base_url": "http://comfy.test"}}, "models": {"image": model}}, client_factory=lambda: httpx.AsyncClient(transport=httpx.MockTransport(upstream)), poll_interval=0)
    params = {"model": "image", "prompt": "my prompt", "size": "768x640", "seed": 0, "n": 2, "response_format": "b64_json"}
    if operation != "generate":
        params["image"] = b"source"
    if operation == "edit_masked":
        params["mask"] = b"mask"
    result = await service.images.generate(params, operation=operation)
    assert result["data"][0]["b64_json"] == base64.b64encode(native_bytes).decode()
    assert len(result["data"]) == 1  # No output-count gate despite requested n=2.
    assert len(sent) == 1
    assert "partial_execution_targets" not in sent[0]
    assert sent[0]["prompt"]["unrelated"] == graph["unrelated"]
    assert sent[0]["prompt"]["seed"]["inputs"]["noise_seed"] == 0
    assert graph == original
    assert len(uploaded) == (2 if operation == "edit_masked" else 0 if operation == "generate" else 1)


@pytest.mark.asyncio
async def test_video_resumes_from_original_history_without_codec_or_resolution_inspection(tmp_path):
    graph, model = package("video", "edit")
    submitted, uploads = [], []
    config = {"sources": {"local": {"base_url": "http://comfy.test"}}, "models": {"video": model}}

    def upstream(request):
        assert request.url.host == "comfy.test"
        if request.url.path.startswith("/userdata/"):
            return httpx.Response(200, json=graph)
        if request.url.path == "/upload/image":
            uploads.append(request.content)
            return httpx.Response(200, json={"name": "uploaded.png"})
        if request.url.path == "/prompt":
            submitted.append(json.loads(request.content)["prompt"])
            return httpx.Response(200, json={"prompt_id": "video-job"})
        if request.url.path == "/history/video-job":
            return httpx.Response(200, json={"video-job": {"prompt": [1, "video-job", submitted[0]], "status": {"status_str": "success"}, "outputs": {"result": {"videos": [{"filename": "native.webm"}]}, "other": {"files": [{"filename": "unrelated.bin"}]}}}})
        if request.url.path == "/view":
            return httpx.Response(200, content=b"native opaque video", headers={"content-type": "video/webm"})
        raise AssertionError(request.url)

    factory = lambda: httpx.AsyncClient(transport=httpx.MockTransport(upstream))
    path = tmp_path / "media.sqlite3"
    service = ComfyProviderService(config, path, client_factory=factory)
    job = await service.create_video({"model": "video", "prompt": "animate", "seconds": 15, "size": "768x640", "seed": 0, "input_reference": b"source"})
    assert job["status"] == "queued"
    assert submitted[0]["image"]["inputs"]["image"] == "uploaded.png"
    await service.close()
    config["sources"]["local"]["base_url"] = "http://different.test"
    config["models"].clear()
    restarted = ComfyProviderService(config, path, client_factory=factory)
    assert (await restarted.video_status(job["id"]))["status"] == "completed"
    assert await restarted.video_content(job["id"]) == (b"native opaque video", "video/webm")
    assert len(submitted) == len(uploads) == 1
    await restarted.close()
