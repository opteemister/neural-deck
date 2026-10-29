"""Small schemas for the OpenAI-compatible model list."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictStr


class ReasoningCapabilities(BaseModel):
    model_config = ConfigDict(extra="forbid")
    efforts: list[Literal["none", "minimal", "low", "medium", "high", "xhigh", "max"]]
    effort_parameter: Literal["reasoning_effort", "chat_template_kwargs"]
    can_disable_thinking: bool
    default_effort: str | None = None


class OpenAIModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    object: str = "model"
    created: int
    owned_by: str = "neural-deck"
    engine_id: str | None = None
    # Operation capabilities let clients such as the built-in chat select only
    # text models without inferring support from an engine's name.
    types: list[str] = Field(default_factory=list)
    context_length: int | None = None
    reasoning_capabilities: ReasoningCapabilities | None = Field(
        default=None,
        description="Neural Deck extension: confirmed reasoning levels and upstream transport. Requests use standard reasoning_effort or reasoning.effort.",
    )
    capabilities: dict[str, Any] | None = None


class OpenAIModelList(BaseModel):
    model_config = ConfigDict(extra="forbid")

    object: str = "list"
    data: list[OpenAIModel]


class RouteCandidateData(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str | None = None
    connection_id: str | None = None
    connection_name: str | None = None
    profile_id: str | None = None
    profile_name: str | None = None
    instance_id: str | None = None
    upstream_model: str | None = None
    engine_id: str | None = None
    port: int | None = None
    state: str = Field(min_length=1)
    available: bool = False
    reason: str | None = None


class RemoteAliasTarget(BaseModel):
    model_config = ConfigDict(extra="forbid")
    connection_id: StrictStr = Field(min_length=1)
    model: StrictStr = Field(min_length=1)


class ProxyRouteData(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    kind: Literal["auto", "alias"]
    candidate_profile_ids: list[str]
    candidate_ids: list[str] = Field(default_factory=list)
    remote: RemoteAliasTarget | None = None
    connection_name: str | None = None
    candidates: list[RouteCandidateData]
    available: bool = False
    diagnostics: list[str] = Field(default_factory=list)


class ProxyRoutesData(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[ProxyRouteData]


class RouteOrderRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    profile_ids: list[StrictStr] = Field(default_factory=list)
    candidate_ids: list[StrictStr] | None = None


class AliasCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    profile_ids: list[StrictStr] = Field(default_factory=list)
    candidates: list[dict[StrictStr, StrictStr]] | None = None
    remote: RemoteAliasTarget | None = None
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
