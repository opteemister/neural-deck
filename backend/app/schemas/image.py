"""OpenAI image-generation wire schemas."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, StrictStr


class ImageGenerationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    prompt: StrictStr = Field(min_length=1, max_length=20_000)
    model: StrictStr | None = Field(default=None, min_length=1, max_length=256)
    n: StrictInt = Field(default=1, ge=1, le=4)
    size: StrictStr = Field(default="1024x1024", min_length=3, max_length=11)
    response_format: Literal["url", "b64_json"] = "url"
    seed: StrictInt | None = None
    steps: StrictInt = Field(default=25, ge=1, le=100)
    transparent: StrictBool = False
    quality: Literal["auto", "low", "medium", "high"] | None = None
    background: Literal["auto", "transparent", "opaque"] | None = None
    output_format: Literal["png", "jpeg", "webp"] | None = None
    output_compression: StrictInt | None = Field(default=None, ge=0, le=100)
    # Legacy JSON inputs; the provider multipart routes use the same adapter.
    image: StrictStr | None = None
    mask: StrictStr | None = None


__all__ = ["ImageGenerationRequest"]
