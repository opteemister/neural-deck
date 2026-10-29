"""Wire response descriptions used by the generated provider OpenAPI."""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class ImageData(BaseModel):
    url: str | None = Field(default=None, examples=["http://localhost:8080/v1/images/asset_id"])
    b64_json: str | None = Field(default=None, description="Base64-encoded image bytes.")
    revised_prompt: str | None = None


class ImagesResponse(BaseModel):
    created: int = Field(description="Unix timestamp, seconds.")
    data: list[ImageData]


class VideoError(BaseModel):
    code: str
    message: str


class VideoResponse(BaseModel):
    model_config = ConfigDict(extra="allow")
    id: str = Field(examples=["video_id"])
    object: Literal["video"] = "video"
    status: Literal["queued", "in_progress", "completed", "failed"]
    created_at: int
    model: str | None = None
    seconds: str | None = None
    size: str | None = None
    error: VideoError | None = None


class ProviderErrorDetail(BaseModel):
    message: str
    type: str
    param: str | None = None
    code: str | None = None


class ProviderError(BaseModel):
    error: ProviderErrorDetail


ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    status: {"description": description, "model": ProviderError}
    for status, description in {
        400: "Invalid or unsupported request parameter.",
        404: "Unknown model or resource.",
        502: "The serving failed or could not be reached. No prompt is resubmitted automatically.",
    }.items()
}
IMAGE_RESPONSES: dict[int | str, dict[str, Any]] = {
    **ERROR_RESPONSES,
    200: {
        "description": "Completed image generation. The HTTP request waits for the ComfyUI queue.",
        "model": ImagesResponse,
    },
}
VIDEO_RESPONSES: dict[int | str, dict[str, Any]] = {
    **ERROR_RESPONSES,
    200: {
        "description": "Video state. Poll GET /v1/videos/{video_id} until completed or failed.",
        "model": VideoResponse,
    },
}
