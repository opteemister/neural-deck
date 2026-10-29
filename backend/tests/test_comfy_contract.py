"""Offline ND.v1 contract checks; no ComfyUI, model or GPU is started."""

import copy
import json

import httpx
import pytest
from pydantic import ValidationError

from app.errors import AppError
from app.proxy.comfy_contract import WorkflowContract
from app.proxy.comfy_provider import ComfyUIProvider
from app.schemas.proxy_management import MediaAliasRequest


def package(kind="image", operation="generate"):
    model = {"type": kind, "source": "local", "workflows": {operation: "workflows/test.json"}, **({"max_megapixels": 2} if kind == "image" else {"max_resolution": 768, "max_seconds": 15})}
    graph = {}
    for name, cls, field, value in [("prompt", "PrimitiveStringMultiline", "value", "native prompt"), ("width", "PrimitiveInt", "value", 640), ("height", "PrimitiveInt", "value", 640), ("seed", "RandomNoise", "noise_seed", 9)]:
        graph[name] = {"class_type": cls, "inputs": {field: value}, "_meta": {"title": f"ND.v1.{name}"}}
    if kind == "video":
        graph["seconds"] = {"class_type": "PrimitiveInt", "inputs": {"value": 5}, "_meta": {"title": "ND.v1.seconds"}}
    if operation in {"edit", "edit_masked", "variations"}:
        graph["image"] = {"class_type": "LoadImage", "inputs": {"image": "saved-test.png"}, "_meta": {"title": "ND.v1.image"}}
    if operation == "edit_masked":
        graph["mask"] = {"class_type": "LoadImageMask", "inputs": {"image": "saved-mask.png", "channel": "alpha"}, "_meta": {"title": "ND.v1.mask"}}
    graph["generator"] = {"class_type": "AnyModel", "inputs": {name: [name, 0] for name in graph}}
    graph["result"] = {"class_type": "SaveVideo" if kind == "video" else "SaveImage", "inputs": {"images": ["generator", 0]}, "_meta": {"title": "ND.v1.output"}}
    return graph, model


@pytest.mark.parametrize("kind,operation", [("image", "generate"), ("image", "edit"), ("image", "edit_masked"), ("image", "variations"), ("video", "generate"), ("video", "edit")])
def test_operations_use_markers_and_preserve_model_recipe(kind, operation):
    graph, model = package(kind, operation)
    original = copy.deepcopy(graph)
    contract = WorkflowContract.parse(graph, model, operation)
    params = {"prompt": "request", "size": "768x640", "seed": 0, "quality": "high"}
    if operation in {"edit", "edit_masked", "variations"}:
        params["input_reference" if kind == "video" else "image"] = b"image"
    if operation == "edit_masked":
        params["mask"] = b"mask"
    if kind == "video":
        params["seconds"] = 15
    result = contract.patch(graph, params, set(params), {"image": "uploaded.png", "reference": "uploaded.png", "mask": "uploaded-mask.png"})
    assert graph == original
    assert result["seed"]["inputs"]["noise_seed"] == 0
    assert result["width"]["inputs"]["value"] == 768
    assert result["generator"] == original["generator"]
    if kind == "video":
        assert result["seconds"]["inputs"]["value"] == 15


def test_missing_seed_randomized_once_within_declared_range(monkeypatch):
    graph, model = package()
    calls = []
    monkeypatch.setattr("app.proxy.comfy_contract.secrets.randbits", lambda upper: calls.append(upper) or 17)
    contract = WorkflowContract.parse(graph, model, "generate")
    assert contract.patch(graph, {}, set(), {})["seed"]["inputs"]["noise_seed"] == 17
    assert calls == [63]


@pytest.mark.parametrize("field,value", [("size", "0x640"), ("size", "900x900"), ("seed", -1), ("seed", True), ("seconds", 16), ("seconds", 5.5), ("n", 2), ("background", "transparent")])
def test_unsupported_or_out_of_range_request_is_400(field, value):
    graph, model = package("video")
    contract = WorkflowContract.parse(graph, model, "generate")
    with pytest.raises(AppError) as caught:
        contract.patch(graph, {field: value}, {field}, {})
    assert caught.value.status_code == 400
    assert caught.value.details["param"] == field


@pytest.mark.parametrize("problem", ["duplicate", "version", "missing", "field"])
def test_bad_contract_never_falls_back(problem):
    graph, model = package()
    if problem == "duplicate":
        graph["copy"] = copy.deepcopy(graph["prompt"])
    elif problem == "version":
        graph["prompt"]["_meta"]["title"] = "ND.v2.prompt"
    elif problem == "field":
        graph["prompt"]["inputs"] = {"different_field": "text"}
    else:
        del graph["prompt"]
    with pytest.raises(AppError) as caught:
        WorkflowContract.parse(graph, model, "generate")
    assert caught.value.code == "comfyui_contract_invalid"


def test_zero_defaults_only_allow_image_auto_mode():
    graph, model = package("image", "edit")
    graph["width"]["inputs"]["value"] = graph["height"]["inputs"]["value"] = 0
    contract = WorkflowContract.parse(graph, model, "edit")
    patched = contract.patch(graph, {"image": b"file"}, {"image"}, {"image": "uploaded.png"})
    assert patched["width"]["inputs"]["value"] == 0
    graph["width"]["inputs"]["value"] = 12345
    WorkflowContract.parse(graph, model, "edit")  # defaults belong to workflow


def test_graph_is_a_black_box_including_defaults_classes_and_parallel_paths():
    graph, model = package("video")
    graph["prompt"]["class_type"] = "AnotherPromptNode"
    graph["result"]["class_type"] = "AnyOutputNode"
    graph["width"]["inputs"]["value"] = 0
    graph["generator"]["inputs"].pop("prompt")
    graph["generator"]["inputs"]["saved_first_frame"] = ["saved", 0]
    graph["saved"] = {"class_type": "LoadImage", "inputs": {"image": "fixed.png"}}
    graph["unrelated"] = {"class_type": "AnyParallelOutput", "inputs": {"images": ["generator", 0]}}
    contract = WorkflowContract.parse(graph, model, "generate")
    patched = contract.patch(graph, {"prompt": "hello", "seed": 0}, {"prompt", "seed"}, {})
    assert patched["prompt"]["inputs"]["value"] == "hello"
    assert patched["unrelated"] == graph["unrelated"]
    assert patched["saved"] == graph["saved"]
    assert patched["width"] == graph["width"]


def test_history_selects_original_output_without_current_alias():
    graph, _ = package("video")
    selected = {"filename": "movie.mp4", "subfolder": "", "type": "output"}
    entry = {"prompt": [1, "original-job", graph], "outputs": {"preview": {"images": [{"filename": "preview.png"}]}, "result": {"images": [selected]}}}
    assert ComfyUIProvider._outputs(entry) == [selected]


@pytest.mark.asyncio
async def test_submission_sends_full_graph_without_inspection_or_execution_restrictions(monkeypatch):
    graph, model = package("video")
    graph["other"] = {"class_type": "SaveVideo", "inputs": {"video": ["generator", 0]}}
    sent = []
    def upstream(request):
        if request.url.path == "/object_info":
            raise AssertionError("Do not inspect workflow internals via object_info")
        if request.method == "POST":
            sent.append(json.loads(request.content))
            return httpx.Response(200, json={"prompt_id": "one-job"})
        return httpx.Response(200, json=graph)
    draws = []
    monkeypatch.setattr("app.proxy.comfy_contract.secrets.randbits", lambda upper: draws.append(upper) or 42)
    provider = ComfyUIProvider({"sources": {"local": {"base_url": "http://comfy.test"}}, "models": {"video": model}}, client_factory=lambda: httpx.AsyncClient(transport=httpx.MockTransport(upstream)))
    await provider.submit("video", "generate", {"prompt": "animate"})
    assert len(sent) == len(draws) == 1
    assert "partial_execution_targets" not in sent[0]
    assert "other" in sent[0]["prompt"]
    assert sent[0]["prompt"]["seed"]["inputs"]["noise_seed"] == 42


def test_only_minimal_alias_settings_are_required():
    request = {"name": "new", **package()[1]}
    assert MediaAliasRequest.model_validate(request).max_megapixels == 2
    for extra in [{"max_megapixels": 0}, {"max_megapixels": True}, {"limits": {}}, {"max_resolution": 1000}]:
        with pytest.raises(ValidationError):
            MediaAliasRequest.model_validate({**request, **extra})
    with pytest.raises(ValidationError):
        MediaAliasRequest.model_validate({key: value for key, value in request.items() if key != "max_megapixels"})
    # No hardcoded model maxima or pixel grid.
    huge = {**request, "max_megapixels": 1000}
    assert MediaAliasRequest.model_validate(huge).max_megapixels == 1000
    graph, model = package("video")
    patched = WorkflowContract.parse(graph, model, "generate").patch(graph, {"size": "765x641", "seconds": 1, "seed": 0}, {"size", "seconds", "seed"}, {})
    assert patched["width"]["inputs"]["value"] == 765
    assert patched["seconds"]["inputs"]["value"] == 1
