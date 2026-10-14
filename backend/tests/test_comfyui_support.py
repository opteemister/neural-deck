from __future__ import annotations

import base64
from pathlib import Path

import httpx
from app.config import Settings
from app.image.service import ImageGenerationService
from app.image.workflow import build_workflow, validate_model_directory
from app.models.catalogue import ModelCatalogue
from app.paths import build_paths, prepare_data_dirs
from app.schemas.instances import Instance, InstanceList
from app.schemas.profiles import ProfileData


def _image_model(root: Path) -> Path:
    model = root / "qwen-image"
    for category in ("diffusion_models", "text_encoders", "vae"):
        directory = model / category
        directory.mkdir(parents=True)
        (directory / f"{category}.safetensors").write_bytes(b"weights")
    return model


def test_qwen_image_workflow_uses_api_nodes_and_model_root(tmp_path: Path) -> None:
    model = _image_model(tmp_path)
    validate_model_directory(model)
    workflow = build_workflow(model, prompt="a fox", size="1024x768", seed=7, steps=12)

    assert workflow["3"]["inputs"]["unet_name"] == "diffusion_models.safetensors"
    assert workflow["6"]["class_type"] == "TextEncodeQwenImage21"
    assert workflow["7"]["inputs"]["seed"] == 7
    assert workflow["7"]["inputs"]["model"] == ["10", 0]


def test_catalogue_marks_nested_huggingface_image_bundle(tmp_path: Path) -> None:
    model = _image_model(tmp_path / "models" / "org")
    settings = Settings(
        data_root=tmp_path / "data",
        host="127.0.0.1",
        port=8000,
        models_root=tmp_path / "models",
    )
    paths = build_paths(settings)
    prepare_data_dirs(paths)

    items = ModelCatalogue(paths).scan()

    assert [item.path for item in items] == [model]
    assert items[0].model_kind == "image"


async def test_image_service_queues_workflow_and_returns_openai_image(tmp_path: Path) -> None:
    model = _image_model(tmp_path)
    profile = ProfileData.model_validate(
        {
            "version": 1,
            "name": "qwen-image",
            "model_name": "qwen-image-2.1",
            "engine": {"id": "image", "installation_id": "inst-image"},
            "model": {"path": str(model)},
            "command": "{{engine}} --port 8188 --models-directory {{model}}",
            "environment": {},
            "autostart": False,
            "enable_metrics": True,
        }
    )

    instance = Instance(
        id="managed/qwen-image/1-2",
        source="managed",
        profile_name=profile.name,
        engine_id="image",
        pid=1,
        process_start_time_ns=2,
        identity_verified=True,
        can_stop=True,
        port=8188,
        model_name=profile.model_name,
        state="ready",
        observed_at="2026-10-04T00:00:00Z",
    )

    class Manager:
        async def refresh(self, *, probe_models: bool = True) -> InstanceList:
            del probe_models
            return InstanceList(items=[instance])

        def profile_runtime_dir(self, name: str) -> Path:
            return tmp_path / "runtime" / name

    class Repository:
        def get(self, name: str) -> ProfileData:
            assert name == profile.name
            return profile

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST" and request.url.path == "/prompt":
            return httpx.Response(200, json={"prompt_id": "prompt-1"})
        if request.method == "GET" and request.url.path == "/history/prompt-1":
            return httpx.Response(
                200,
                json={
                    "prompt-1": {
                        "status": {"status_str": "success"},
                        "outputs": {"9": {"images": [{"filename": "qwen.png", "subfolder": "", "type": "output"}]}},
                    }
                },
            )
        if request.method == "GET" and request.url.path == "/view":
            return httpx.Response(200, content=b"png-bytes", headers={"content-type": "image/png"})
        raise AssertionError(f"unexpected upstream request: {request.method} {request.url}")

    service = ImageGenerationService(
        Manager(),
        Repository(),
        client_factory=lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    result = await service.generate(
        {"prompt": "a fox", "model": profile.model_name, "response_format": "b64_json", "seed": 1},
        request_id="req_test",
    )

    assert result["data"][0]["b64_json"] == base64.b64encode(b"png-bytes").decode("ascii")
    assert (tmp_path / "runtime" / profile.name / "workflows" / "qwen-image-2.1-t2i.json").is_file()
