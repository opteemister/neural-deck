"""Management API schemas for engine discovery and operations."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

EngineId = Literal["vllm", "llama.cpp", "sglang", "ninfer", "freetoken"]
InstallationKind = Literal["python_venv", "binary"]
InstallationStatus = Literal["ready", "broken", "candidate"]
OperationState = Literal["queued", "running", "succeeded", "failed"]


class Installation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    engine_id: EngineId
    kind: InstallationKind
    root_path: Path
    executable_path: Path
    version: str | None = None
    status: InstallationStatus
    verified: bool
    diagnostic: str | None = None


class Engine(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: EngineId
    display_name: str
    installation_kinds: list[InstallationKind]
    installations: list[Installation]


class EngineList(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[Engine]


class InstallationList(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[Installation]


class PythonVenvInstallationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["python_venv"]
    venv_name: str = Field(min_length=1, max_length=128)


class BinaryInstallationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["binary"]
    path: Path


InstallationCreate = Annotated[
    PythonVenvInstallationCreate | BinaryInstallationCreate,
    Field(discriminator="kind"),
]


class MaintenanceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    command: str = Field(min_length=1, max_length=4096)


class Operation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    kind: str = Field(min_length=1)
    state: OperationState
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
    "BinaryInstallationCreate",
    "Engine",
    "EngineId",
    "EngineList",
    "Installation",
    "InstallationCreate",
    "InstallationList",
    "MaintenanceRequest",
    "Operation",
    "PythonVenvInstallationCreate",
]
