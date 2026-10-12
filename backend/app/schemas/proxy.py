"""Small schemas for the OpenAI-compatible model list."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict


class OpenAIModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    object: str = "model"
    created: int
    owned_by: str = "llm-studio"
    engine_id: str | None = None
    context_length: int | None = None
    reasoning_capabilities: dict[str, Any] | None = None
    capabilities: dict[str, Any] | None = None


class OpenAIModelList(BaseModel):
    model_config = ConfigDict(extra="forbid")

    object: str = "list"
    data: list[OpenAIModel]


__all__ = ["OpenAIModel", "OpenAIModelList"]
