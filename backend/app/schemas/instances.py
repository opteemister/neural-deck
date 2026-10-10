"""Schemas for managed and externally observed inference processes."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

InstanceState = Literal[
    "starting",
    "ready",
    "running",
    "stopping",
    "exited",
    "error",
    "unknown",
]
MetricState = Literal["ok", "unavailable", "stale"]


class Instance(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    source: Literal["managed", "external"]
    profile_name: str | None = None
    profile_id: str | None = None
    engine_id: str = Field(min_length=1)
    pid: int = Field(gt=0)
    process_start_time_ns: int | None = Field(default=None, gt=0)
    identity_verified: bool = False
    can_stop: bool = False
    port: int | None = Field(default=None, ge=1, le=65535)
    model_name: str | None = None
    state: InstanceState
    observed_at: datetime


class InstanceList(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[Instance]


class Metric(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    value: float | int | str | None = None
    unit: str = ""
    source: str = Field(min_length=1)
    observed_at: datetime
    state: MetricState
    metadata: dict[str, Any] = Field(default_factory=dict)


class MetricList(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[Metric]
    source: str = "local"
    peaks: dict[str, float] = Field(default_factory=dict)
    distributions: dict[str, Any] = Field(default_factory=dict, exclude=True)


class LogsData(BaseModel):
    model_config = ConfigDict(extra="forbid")

    instance_id: str = Field(min_length=1)
    lines: list[str]
    truncated: bool
    available: bool = True


class TokenCounts(BaseModel):
    model_config = ConfigDict(extra="forbid")

    known_total: int | None = Field(default=None, ge=0)
    unknown_requests: int = Field(default=0, ge=0)


class TokenStats(BaseModel):
    model_config = ConfigDict(extra="forbid")

    profile_id: str | None = None
    profile_name: str = Field(min_length=1)
    requests: int = Field(ge=0)
    input_tokens: TokenCounts
    output_tokens: TokenCounts
    source: str = Field(min_length=1)


class TokenStatsData(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[TokenStats]


class ProcessOperation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    kind: str = Field(min_length=1)
    state: Literal["queued", "running", "succeeded", "failed"]
    target_id: str = Field(min_length=1)
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    stdout: str = ""
    stderr: str = ""
    exit_code: int | None = None
    error: str | None = None
    resolved_packages: dict[str, str] | None = None
    checked_at: datetime | None = None


__all__ = [
    "Instance",
    "InstanceList",
    "InstanceState",
    "LogsData",
    "Metric",
    "MetricList",
    "MetricState",
    "ProcessOperation",
    "TokenCounts",
    "TokenStats",
    "TokenStatsData",
]
