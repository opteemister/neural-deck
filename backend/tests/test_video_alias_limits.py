import copy
from unittest.mock import AsyncMock

import pytest

from app.errors import AppError
from app.proxy.comfy_provider import ComfyUIProvider
from app.proxy.routes import RouteConfigRepository


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
    ("max_seconds", 0), ("max_seconds", 601), ("max_seconds", True),
    ("max_resolution", 0), ("max_resolution", 16385), ("max_resolution", 768.5),
])
def test_invalid_video_alias_limits_are_rejected(field, value):
    payload = copy.deepcopy(config())
    payload["models"]["video"][field] = value
    with pytest.raises(AppError) as error:
        RouteConfigRepository.validate_comfyui(payload)
    assert error.value.status_code == 422
    assert error.value.details["field"].endswith(field)


@pytest.mark.asyncio
@pytest.mark.parametrize("field,value", [
    ("seconds", "16"), ("size", "1536x1536"),
    ("size", "4096x480"), ("size", "480x4096"),
])
async def test_video_limits_reject_requests_before_comfy_submission(field, value):
    provider = ComfyUIProvider(config())
    provider.submit = AsyncMock()
    try:
        with pytest.raises(AppError) as error:
            await provider.create_video("video", {"prompt": "test", field: value})
        assert error.value.status_code == 400
        assert error.value.details["field"] == field
        provider.submit.assert_not_awaited()
    finally:
        await provider.close()
