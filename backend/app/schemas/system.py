"""Schemas for the system, settings and status management endpoints."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class CpuData(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cores: int = Field(ge=1)
    usage_percent: float | None = Field(default=None, ge=0, le=100)


class RamData(BaseModel):
    model_config = ConfigDict(extra="forbid")

    used_bytes: int = Field(ge=0)
    total_bytes: int = Field(ge=0)


class GpuData(BaseModel):
    model_config = ConfigDict(extra="forbid")

    index: int = Field(ge=0)
    uuid: str = Field(min_length=1)
    name: str = Field(min_length=1)
    memory_used_bytes: int = Field(ge=0)
    memory_total_bytes: int = Field(ge=0)
    usage_percent: float | None = Field(default=None, ge=0, le=100)
    temperature_celsius: float | None = None


InstanceState = Literal[
    "starting",
    "ready",
    "running",
    "stopping",
    "exited",
    "error",
    "unknown",
]


class InstanceData(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    source: Literal["managed", "external"]
    profile_name: str | None = None
    engine_id: str = Field(min_length=1)
    pid: int = Field(gt=0)
    process_start_time_ns: int | None = Field(default=None, gt=0)
    identity_verified: bool = False
    can_stop: bool = False
    port: int | None = Field(default=None, ge=1, le=65535)
    model_name: str | None = None
    state: InstanceState
    observed_at: datetime


class SystemData(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cpu: CpuData
    ram: RamData
    gpus: list[GpuData]
    instances: list[InstanceData]


ComponentState = Literal["ok", "degraded", "error"]


class ComponentStatus(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    state: ComponentState
    message: str | None = None
    checked_at: datetime


class StatusData(BaseModel):
    model_config = ConfigDict(extra="forbid")

    overall: ComponentState
    components: list[ComponentStatus]


class HealthData(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["ok", "degraded"]


class SettingsData(BaseModel):
    model_config = ConfigDict(extra="forbid")

    data_root: Path
    models_root: Path
    log_root: Path
    extra_venv_roots: list[Path]
    host: str
    port: int = Field(ge=1, le=65535)
    openai_port: int = Field(ge=1, le=65535)


class SettingsPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    models_root: Path
