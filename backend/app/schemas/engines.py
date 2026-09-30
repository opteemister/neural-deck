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
    command_template: str
    installation_template: str
    installation_root: str
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
    command: str | None = Field(default=None, min_length=1, max_length=32768)


class BinaryInstallationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["binary"]
    path: Path
    command: str | None = Field(default=None, min_length=1, max_length=32768)


InstallationCreate = Annotated[
    PythonVenvInstallationCreate | BinaryInstallationCreate,
    Field(discriminator="kind"),
]


class MaintenanceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    command: str = Field(min_length=1, max_length=4096)


class OperationProgress(BaseModel):
    model_config = ConfigDict(extra="forbid")

    files_total: int = Field(ge=0)
    files_completed: int = Field(ge=0)
    bytes_total: int = Field(ge=0)
    bytes_completed: int = Field(ge=0)
    current_file: str | None = None


class Operation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    kind: str = Field(min_length=1)
    state: OperationState
    target_id: str = Field(min_length=1)
    label: str | None = None
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    stdout: str = ""
    stderr: str = ""
    exit_code: int | None = None
    error: str | None = None
    resolved_packages: dict[str, str] | None = None
    checked_at: datetime | None = None
    progress: OperationProgress | None = None


class OperationList(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[Operation]


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
    "OperationList",
    "OperationProgress",
    "PythonVenvInstallationCreate",
]
