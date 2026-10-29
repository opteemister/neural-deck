import copy

import pytest
import httpx

from app.errors import AppError
from app.proxy.comfy_provider import ComfyUIProvider
from app.proxy.routes import RouteConfigRepository
from app.proxy.comfy_contract import WorkflowContract
from tests.test_comfy_contract import package


def config():
    return {
        "sources": {"main": {"base_url": "http://comfy.test:8188"}},
        "models": {"video": {
            "source": "main", "type": "video",
            "workflows": {"generate": "workflows/video.json"},
            "max_resolution": 768, "max_seconds": 15,
        }},
    }


def test_video_alias_limits_survive_config_validation():
    assert RouteConfigRepository.validate_comfyui(config()) == config()


@pytest.mark.parametrize("field,value", [
    ("max_seconds", 0), ("max_seconds", 1.5), ("max_seconds", True),
    ("max_resolution", 0), ("max_resolution", True), ("max_resolution", 768.5),
])
def test_invalid_video_alias_limits_are_rejected(field, value):
    payload = copy.deepcopy(config())
    payload["models"]["video"][field] = value
    with pytest.raises(AppError) as error:
        RouteConfigRepository.validate_comfyui(payload)
    assert error.value.status_code == 422
    assert error.value.details["field"].endswith(field)


def test_alias_maxima_are_not_bounded_by_hardcoded_model_limits():
    payload = config()
    payload["models"]["video"].update(max_seconds=601, max_resolution=16385)
    assert RouteConfigRepository.validate_comfyui(payload) == payload


@pytest.mark.asyncio
@pytest.mark.parametrize("field,value", [("seconds", 16), ("size", "1536x1536")])
async def test_video_limits_reject_requests_before_comfy_submission(field, value):
    requests = []
    graph, _ = package("video")

    def handler(request):
        requests.append((request.method, request.url.path))
        if request.method == "GET" and request.url.path.startswith("/userdata/"):
            return httpx.Response(200, json=graph)
        raise AssertionError("An invalid request must not be submitted to ComfyUI")

    provider = ComfyUIProvider(config(), client_factory=lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    try:
        with pytest.raises(AppError) as error:
            await provider.create_video("video", {"prompt": "test", field: value})
        assert error.value.status_code == 400
        assert error.value.details["field"] == field
        assert not any(method == "POST" for method, _ in requests)
    finally:
        await provider.close()


@pytest.mark.parametrize("size", ["4096x480", "480x4096"])
def test_long_side_is_not_an_invented_alias_limit(size):
    graph, model = package("video")
    contract = WorkflowContract.parse(graph, model, "generate")
    patched = contract.patch(graph, {"size": size, "seed": 0}, {"size", "seed"}, {})
    assert (contract.value(patched, "width"), contract.value(patched, "height")) == tuple(map(int, size.split("x")))
