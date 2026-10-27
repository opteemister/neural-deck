from __future__ import annotations

import copy
import warnings
from types import SimpleNamespace
from typing import Any

from app.config import Settings
from app.errors import (
    AppError,
    app_error_handler,
    http_error_handler,
    unhandled_error_handler,
    validation_error_handler,
)
from app.main import create_app
from app.proxy.media_router import internal_router, management_router, router
from fastapi import FastAPI, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.testclient import TestClient
from starlette.exceptions import HTTPException as StarletteHTTPException


class FakeImages:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def generate(
        self, payload: dict[str, Any], *, operation: str, **kwargs: Any
    ) -> dict[str, Any]:
        self.calls.append({"payload": payload, "operation": operation, **kwargs})
        return {"created": 1, "operation": operation, "model": payload.get("model")}


class FakeComfy:
    def __init__(self, settings: dict[str, Any]) -> None:
        self.settings = copy.deepcopy(settings)
        self.full_config: dict[str, Any] = {
            "schema_version": 1,
            "aliases": {"text-alias": ["profile-a"]},
            "comfyui": self.settings,
        }
        self.images = FakeImages()
        self.workflow_result: dict[str, Any] = {
            "items": [],
            "available": False,
            "cached": False,
            "updated_at": None,
        }
        self.video_calls: list[dict[str, Any]] = []
        self.video_status_result: dict[str, Any] = {"id": "video_1", "status": "queued"}
        self.source_status_result: dict[str, Any] = {
            "source": "local",
            "base_url": "http://127.0.0.1:8188",
            "available": True,
            "status": "ready",
            "message": "ComfyUI доступен",
            "checked_at": 1.0,
        }

    def get_settings(self) -> dict[str, Any]:
        return copy.deepcopy(self.settings)

    def save_settings(self, value: dict[str, Any]) -> dict[str, Any]:
        self.settings = copy.deepcopy(value)
        self.full_config["comfyui"] = self.settings
        return self.get_settings()

    async def workflows(self, source: str) -> dict[str, Any]:
        self.workflow_source = source
        return copy.deepcopy(self.workflow_result)

    async def source_status(self, source: str) -> dict[str, Any]:
        self.source_status_source = source
        return copy.deepcopy(self.source_status_result)

    async def create_video(self, payload: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
        self.video_calls.append({"payload": payload, **kwargs})
        return {"id": "video_1", "object": "video", "status": "queued"}

    async def video_status(self, video_id: str) -> dict[str, Any]:
        self.video_status_id = video_id
        return copy.deepcopy(self.video_status_result)

    async def video_content(self, video_id: str) -> tuple[bytes, str]:
        self.video_content_id = video_id
        return b"fake-mp4", "video/mp4"


class FakeProxy:
    def __init__(self, route_names: list[str] | None = None) -> None:
        self.route_names = route_names or []

    async def route_data(self) -> dict[str, Any]:
        return {"items": [{"name": name} for name in self.route_names]}

    async def models(self) -> dict[str, Any]:
        return {
            "object": "list",
            "data": [
                {
                    "id": "text-model",
                    "object": "model",
                    "created": 1,
                    "owned_by": "test",
                    "types": ["text"],
                },
                {
                    "id": "ocr-model",
                    "object": "model",
                    "created": 1,
                    "owned_by": "test",
                    "types": ["ocr"],
                },
            ],
        }


def _runtime(comfy: FakeComfy, *, proxy: FakeProxy | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        settings=SimpleNamespace(port=8080),
        comfy_service=comfy,
        proxy_service=proxy or FakeProxy(),
    )


def _media_app(runtime: SimpleNamespace, *, include_internal: bool = False) -> FastAPI:
    app = FastAPI()
    app.state.runtime = runtime

    @app.middleware("http")
    async def request_id(request, call_next):
        request.state.request_id = "req_media_test"
        return await call_next(request)

    app.add_exception_handler(AppError, app_error_handler)
    app.add_exception_handler(RequestValidationError, validation_error_handler)
    app.add_exception_handler(HTTPException, http_error_handler)
    app.add_exception_handler(StarletteHTTPException, http_error_handler)
    app.add_exception_handler(Exception, unhandled_error_handler)
    app.include_router(router)
    if include_internal:
        app.include_router(internal_router)
    return app


def _management_app(runtime: SimpleNamespace) -> FastAPI:
    app = FastAPI()
    app.state.runtime = runtime

    @app.middleware("http")
    async def request_id(request, call_next):
        request.state.request_id = "req_media_test"
        return await call_next(request)

    app.add_exception_handler(AppError, app_error_handler)
    app.add_exception_handler(RequestValidationError, validation_error_handler)
    app.add_exception_handler(HTTPException, http_error_handler)
    app.add_exception_handler(StarletteHTTPException, http_error_handler)
    app.add_exception_handler(Exception, unhandled_error_handler)
    app.include_router(management_router)
    return app


def test_comfy_settings_preserve_text_aliases_and_reject_collisions() -> None:
    comfy = FakeComfy({"sources": {"local": {"base_url": "http://comfy"}}, "models": {}})
    runtime = _runtime(comfy, proxy=FakeProxy(["text-alias"]))
    app = _management_app(runtime)
    client = TestClient(app)

    payload = {
        "sources": {"remote": {"base_url": "http://comfy-remote", "user_context": "deck"}},
        "models": {
            "image": {
                "source": "remote",
                "type": "image",
                "workflows": {"generate": "workflows/image.json"},
            }
        },
    }
    response = client.put("/api/v1/proxy/comfyui", json=payload)
    assert response.status_code == 200
    assert response.json()["data"] == payload
    assert comfy.full_config["aliases"] == {"text-alias": ["profile-a"]}

    collision = client.put(
        "/api/v1/proxy/comfyui",
        json={
            "sources": {"local": {"base_url": "http://comfy"}},
            "models": {"text-alias": {"source": "local", "type": "image", "workflows": {}}},
        },
    )
    assert collision.status_code == 409
    assert collision.json()["error"]["code"] == "route_conflict"
    assert comfy.get_settings() == payload


def test_workflow_endpoint_exposes_cached_offline_catalog() -> None:
    comfy = FakeComfy({"sources": {"local": {"base_url": "http://comfy"}}, "models": {}})
    comfy.workflow_result = {
        "items": ["workflows/cached.json"],
        "available": False,
        "cached": True,
        "updated_at": 123.0,
    }
    app = _management_app(_runtime(comfy))
    response = TestClient(app).get("/api/v1/proxy/comfyui/sources/local/workflows")
    assert response.status_code == 200
    assert response.json()["data"] == comfy.workflow_result


def test_status_endpoint_returns_enveloped_backend_health() -> None:
    comfy = FakeComfy({"sources": {}, "models": {}})
    app = _management_app(_runtime(comfy))
    response = TestClient(app).get("/api/v1/proxy/comfyui/sources/local/status")
    assert response.status_code == 200
    assert response.json()["data"]["available"] is True
    assert response.json()["data"]["status"] == "ready"
    assert comfy.source_status_source == "local"


def test_models_do_not_advertise_unavailable_comfy_alias_and_internal_filter_is_preserved() -> None:
    comfy = FakeComfy(
        {
            "sources": {"offline": {"base_url": "http://comfy"}},
            "models": {
                "ghost-image": {
                    "source": "offline",
                    "type": "image",
                    "workflows": {"generate": "workflows/g.json"},
                }
            },
        }
    )
    comfy.workflow_result = {"items": [], "available": False, "cached": True, "updated_at": 1.0}
    app = _media_app(_runtime(comfy, proxy=FakeProxy()), include_internal=True)
    client = TestClient(app)
    assert [item["id"] for item in client.get("/v1/models").json()["data"]] == [
        "text-model",
        "ocr-model",
    ]
    assert [item["id"] for item in client.get("/api/proxy/models").json()["data"]] == ["text-model"]
    assert [
        item["id"] for item in client.get("/api/proxy/models?all_types=true").json()["data"]
    ] == ["text-model", "ocr-model"]
    assert client.get("/api/proxy/models/ocr-model").status_code == 404
    assert client.get("/api/proxy/models/ocr-model?all_types=true").json()["id"] == "ocr-model"


def test_image_multipart_edit_and_variation_keep_file_fields_and_select_operations() -> None:
    comfy = FakeComfy(
        {
            "sources": {"local": {"base_url": "http://comfy"}},
            "models": {
                "image": {
                    "source": "local",
                    "type": "image",
                    "workflows": {"edit": "workflows/e.json", "variations": "workflows/v.json"},
                }
            },
        }
    )
    client = TestClient(_media_app(_runtime(comfy)))
    edit = client.post(
        "/v1/images/edits",
        data={"model": "image", "prompt": "remove", "n": "2"},
        files={
            "image": ("input.png", b"image", "image/png"),
            "mask": ("mask.png", b"mask", "image/png"),
        },
    )
    variation = client.post(
        "/v1/images/variations",
        data={"model": "image"},
        files={"image": ("input.png", b"image", "image/png")},
    )
    assert edit.status_code == 200
    assert variation.status_code == 200
    assert [call["operation"] for call in comfy.images.calls] == ["edit_masked", "variations"]
    assert comfy.images.calls[0]["payload"]["image"] == b"image"
    assert comfy.images.calls[0]["payload"]["mask"] == b"mask"
    assert comfy.images.calls[0]["payload"]["n"] == 2


def test_video_create_status_and_content_routes_are_http_compatible() -> None:
    comfy = FakeComfy(
        {
            "sources": {"local": {"base_url": "http://comfy"}},
            "models": {
                "video": {
                    "source": "local",
                    "type": "video",
                    "workflows": {"generate": "workflows/v.json"},
                }
            },
        }
    )
    client = TestClient(_media_app(_runtime(comfy)))
    created = client.post(
        "/v1/videos",
        data={"model": "video", "prompt": "a river", "seconds": "4"},
        files={"input_reference": ("reference.png", b"reference", "image/png")},
    )
    status = client.get("/v1/videos/video_1")
    content = client.get("/v1/videos/video_1/content")
    assert created.status_code == 200
    assert created.json()["id"] == "video_1"
    assert status.json()["status"] == "queued"
    assert content.status_code == 200
    assert content.headers["content-type"] == "video/mp4"
    assert content.content == b"fake-mp4"
    assert comfy.video_calls[0]["payload"]["input_reference"] == b"reference"


def test_provider_openapi_contains_media_and_v2_routes_without_management_or_internal_routes(
    data_root,
) -> None:
    runtime = _runtime(FakeComfy({"sources": {}, "models": {}}))
    settings = Settings(data_root=data_root, host="127.0.0.1", port=8080, openai_port=8081)
    app = create_app(settings)
    app.state.runtime = runtime
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        schema = TestClient(app).get("/api/v1/proxy/openapi.json")
    assert schema.status_code == 200
    assert not any("Duplicate Operation ID" in str(item.message) for item in caught)
    document = schema.json()
    paths = document["paths"]
    for path in (
        "/v1/images/edits",
        "/v1/images/variations",
        "/v1/videos",
        "/v1/audio/translations",
        "/v1/ocr",
        "/v2/embed",
    ):
        assert path in paths
    assert not any(path.startswith("/api/") for path in paths)
    assert "/api/v1/proxy/comfyui" not in paths
    edit_schema = paths["/v1/images/edits"]["post"]["requestBody"]["content"][
        "multipart/form-data"
    ]["schema"]
    assert {"model", "prompt", "image", "mask"}.issubset(edit_schema["properties"])
    assert edit_schema["properties"]["image"]["format"] == "binary"
    assert "CohereEmbedRequest" in str(document["paths"]["/v2/embed"])
