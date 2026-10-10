"""API contract for the read-only profile card summary."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

SummaryState = Literal["ok", "unavailable", "stale", "unknown", "not_measured"]


class SummaryMetric(BaseModel):
    model_config = ConfigDict(extra="forbid")

    value: float | None = None
    unit: str = ""
    state: SummaryState
    source: str = Field(min_length=1)
    reason: str | None = None


class SummaryPeriod(BaseModel):
    model_config = ConfigDict(extra="forbid")

    from_: datetime = Field(alias="from")
    to: datetime
    timezone: Literal["UTC"] = "UTC"


class ProfilePerformanceSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    prefill_tps: SummaryMetric
    decode_tps: SummaryMetric


class ProfileMemorySummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    disk_bytes: SummaryMetric
    vram_current_bytes: SummaryMetric
    vram_last_bytes: SummaryMetric
    vram_avg_bytes: SummaryMetric
    vram_peak_bytes: SummaryMetric


class ProfileConcurrencySummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    optimal: SummaryMetric
    limit: SummaryMetric
    observed_max: SummaryMetric
    limit_source: str = "metrics_observed_max"
    benchmark_state: Literal["not_measured", "measured"] = "not_measured"


class ProfileLoadSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    percent: SummaryMetric


class ProfileEndpointSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    port: int | None = None
    state: Literal["ok", "unknown"]
    source: str = Field(min_length=1)
    conflict: bool = False
    conflict_with: list[str] = Field(default_factory=list)
    reason: str | None = None


class ProfileSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    profile_id: str | None = None
    profile_name: str = Field(min_length=1)
    period: SummaryPeriod
    performance: ProfilePerformanceSummary
    memory: ProfileMemorySummary
    concurrency: ProfileConcurrencySummary
    load: ProfileLoadSummary
    endpoint: ProfileEndpointSummary


class ProfileSummaryData(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[ProfileSummary]
    period: SummaryPeriod
    source: str = Field(min_length=1)


__all__ = [
    "ProfileSummary",
    "ProfileSummaryData",
    "SummaryMetric",
    "SummaryPeriod",
]
