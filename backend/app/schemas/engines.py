"""Management API schemas for engine discovery and operations."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Annotated, Literal

from urllib.parse import urlsplit

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    StrictStr,
    field_validator,
    model_validator,
)

from ..profiles.formatting import validate_placeholders

EngineId = Annotated[
    StrictStr,
    Field(
        min_length=1,
        max_length=64,
        pattern=r"^[a-z][a-z0-9]*(?:[.-][a-z0-9]+)*$",
    ),
]
Capability = Literal["text", "tts", "asr", "video", "image", "embeddings", "decision"]
ApiProtocol = Literal["none", "openai", "comfyui", "jev"]
InstallationKind = Literal["python_venv", "binary", "custom"]
InstallationStatus = Literal["ready", "broken", "candidate"]
OperationState = Literal["queued", "running", "succeeded", "failed"]


def _validate_http_url(value: str | None) -> str | None:
    if value is None:
        return value
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("URL должен быть абсолютным HTTP(S) адресом")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("учётные данные не должны входить в URL")
    return value


class Healthcheck(BaseModel):
    """Generic HTTP readiness probe against the effective API origin."""

    model_config = ConfigDict(extra="forbid")

    path: StrictStr = Field(min_length=1, max_length=2048)
    timeout_seconds: float = Field(default=3.0, gt=0, le=60)

    @field_validator("path")
    @classmethod
    def absolute_path(cls, value: str) -> str:
        if not value.startswith("/") or value.startswith("//") or "\x00" in value:
            raise ValueError("healthcheck.path должен начинаться с одного символа /")
        return value


class EngineApi(BaseModel):
    model_config = ConfigDict(extra="forbid")

    protocol: ApiProtocol
    base_url: StrictStr | None = None

    @field_validator("base_url")
    @classmethod
    def valid_base_url(cls, value: str | None) -> str | None:
        return _validate_http_url(value)


class ProfileApiOverride(BaseModel):
    """Optional protocol/base URL overrides; omitted values inherit."""

    model_config = ConfigDict(extra="forbid")

    protocol: ApiProtocol | None = None
    base_url: StrictStr | None = None

    @field_validator("base_url")
    @classmethod
    def valid_base_url(cls, value: str | None) -> str | None:
        return _validate_http_url(value)


class EngineDefinition(BaseModel):
    """Validated, serialisable engine definition stored as YAML."""

    model_config = ConfigDict(extra="forbid")

    version: StrictInt = 1
    id: EngineId
    name: StrictStr = Field(min_length=1, max_length=128)
    types: list[Capability] = Field(min_length=1)
    install_script: StrictStr | None = None
    update_script: StrictStr | None = None
    executable: StrictStr = Field(min_length=1, max_length=4096)
    working_directory: StrictStr | None = Field(default=None, max_length=4096)
    environment: dict[StrictStr, StrictStr] = Field(default_factory=dict)
    command: StrictStr = Field(min_length=1, max_length=32768)
    healthcheck: Healthcheck | None = None
    api: EngineApi | None = None
    ui_url: StrictStr | None = None
    version_command: list[StrictStr] | None = None

    @field_validator("types")
    @classmethod
    def unique_types(cls, value: list[Capability]) -> list[Capability]:
        if len(set(value)) != len(value):
            raise ValueError("типы движка не должны повторяться")
        return value

    @field_validator("executable", "working_directory")
    @classmethod
    def valid_path_template(cls, value: str | None) -> str | None:
        if value is not None and ("\x00" in value or not value.strip()):
            raise ValueError("путь executable или рабочей папки недопустим")
        return value

    @field_validator("command", "install_script", "update_script")
    @classmethod
    def valid_script_text(cls, value: str | None) -> str | None:
        if value is not None and "\x00" in value:
            raise ValueError("скрипт содержит NUL")
        return value

    @field_validator("command")
    @classmethod
    def valid_command_placeholders(cls, value: str) -> str:
        validate_placeholders(value)
        return value

    @field_validator("environment")
    @classmethod
    def valid_environment(cls, value: dict[str, str]) -> dict[str, str]:
        if any(not key or "=" in key or "\x00" in key for key in value):
            raise ValueError("имя переменной окружения недопустимо")
        if any("\x00" in item for item in value.values()):
            raise ValueError("значение переменной окружения содержит NUL")
        return value

    @field_validator("ui_url")
    @classmethod
    def valid_ui_url(cls, value: str | None) -> str | None:
        return _validate_http_url(value)

    @model_validator(mode="after")
    def valid_version(self) -> EngineDefinition:
        if self.version != 1:
            raise ValueError("поддерживается только версия определения 1")
        if self.healthcheck is not None and (self.api is None or self.api.base_url is None):
            raise ValueError("healthcheck требует api.base_url")
        if self.version_command is not None and not self.version_command:
            raise ValueError("version_command не может быть пустой")
        return self


class EngineDefinitionIssue(BaseModel):
    model_config = ConfigDict(extra="forbid")

    file: StrictStr = Field(min_length=1)
    code: StrictStr = Field(min_length=1)
    message: StrictStr = Field(min_length=1)


class EngineDefinitionList(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[EngineDefinition]
    issues: list[EngineDefinitionIssue] = Field(default_factory=list)


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
    registered: bool = False


class Engine(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: EngineId
    display_name: str
    command_template: str
    installation_template: str
    installation_root: str
    installation_kinds: list[InstallationKind]
    installations: list[Installation]
    types: list[Capability] = Field(default_factory=list)
    api: EngineApi | None = None
    ui_url: str | None = None


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


LegacyInstallationCreate = Annotated[
    PythonVenvInstallationCreate | BinaryInstallationCreate,
    Field(discriminator="kind"),
]


class GenericInstallationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_path: Path
    run_install: StrictBool = True
    executable_path: Path | None = None
    command: StrictStr | None = Field(default=None, min_length=1, max_length=32768)


# Keep the established installation URL backwards compatible. Generic clients
# register a target directory, while existing clients may still submit their
# explicit venv/binary kind and edited shell command.
InstallationCreate = GenericInstallationCreate | LegacyInstallationCreate


class ResolvedEngineProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    definition: EngineDefinition
    installation: Installation
    executable_path: Path
    working_directory: Path
    command: str
    environment: dict[str, str]
    types: list[Capability]
    api_protocol: ApiProtocol
    api_base_url: str | None = None
    ui_url: str | None = None
    healthcheck: Healthcheck | None = None
    model_path: Path | None = None
    model_name: str | None = None


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
    "ApiProtocol",
    "BinaryInstallationCreate",
    "Capability",
    "Engine",
    "EngineApi",
    "EngineDefinition",
    "EngineDefinitionIssue",
    "EngineDefinitionList",
    "EngineId",
    "EngineList",
    "GenericInstallationCreate",
    "Healthcheck",
    "Installation",
    "InstallationCreate",
    "LegacyInstallationCreate",
    "InstallationList",
    "MaintenanceRequest",
    "Operation",
    "OperationList",
    "OperationProgress",
    "ProfileApiOverride",
    "PythonVenvInstallationCreate",
    "ResolvedEngineProfile",
]
