"""Small schemas for the OpenAI-compatible model list."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictStr


class OpenAIModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    object: str = "model"
    created: int
    owned_by: str = "neural-deck"
    engine_id: str | None = None
    context_length: int | None = None
    reasoning_capabilities: dict[str, Any] | None = None
    capabilities: dict[str, Any] | None = None


class OpenAIModelList(BaseModel):
    model_config = ConfigDict(extra="forbid")

    object: str = "list"
    data: list[OpenAIModel]


class RouteCandidateData(BaseModel):
    model_config = ConfigDict(extra="forbid")

    profile_id: str = Field(min_length=1)
    profile_name: str | None = None
    instance_id: str | None = None
    upstream_model: str | None = None
    engine_id: str | None = None
    port: int | None = None
    state: str = Field(min_length=1)
    available: bool = False
    reason: str | None = None


class ProxyRouteData(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    kind: Literal["auto", "alias"]
    candidate_profile_ids: list[str]
    candidates: list[RouteCandidateData]
    available: bool = False
    diagnostics: list[str] = Field(default_factory=list)


class ProxyRoutesData(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[ProxyRouteData]


class RouteOrderRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    profile_ids: list[StrictStr] = Field(default_factory=list)


class AliasCreateRequest(RouteOrderRequest):
    name: StrictStr = Field(min_length=1, max_length=128)


__all__ = [
    "AliasCreateRequest",
    "OpenAIModel",
    "OpenAIModelList",
    "ProxyRouteData",
    "ProxyRoutesData",
    "RouteCandidateData",
    "RouteOrderRequest",
]
