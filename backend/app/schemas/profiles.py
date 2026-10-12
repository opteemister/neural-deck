"""Pydantic schemas for the Version Zero profile file contract."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

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

EngineId = Literal["vllm", "llama.cpp", "sglang", "ninfer", "freetoken", "image"]


def _profile_name(value: str) -> str:
    if not value or "/" in value or "\\" in value or ".." in value:
        raise ValueError("имя профиля содержит недопустимый путь")
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise ValueError("имя профиля содержит управляющий символ")
    return value


class ProfileEngine(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: EngineId
    installation_id: StrictStr = Field(min_length=1)


class ProfileModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: Path
    draft_path: Path | None = None

    @field_validator("path", "draft_path")
    @classmethod
    def absolute_path(cls, value: Path | None) -> Path | None:
        if value is not None and not value.is_absolute():
            raise ValueError("путь модели должен быть абсолютным")
        return value


class ProfileData(BaseModel):
    """The complete serialisable profile representation.

    Strict scalar types are intentional: YAML values such as ``"1"`` and
    ``yes`` must not silently become a different profile.
    """

    model_config = ConfigDict(extra="forbid")

    version: StrictInt
    name: StrictStr = Field(min_length=1)
    model_name: StrictStr = Field(min_length=1)
    engine: ProfileEngine
    model: ProfileModel
    command: StrictStr = Field(min_length=1)
    port: StrictInt | None = Field(default=None, ge=1, le=65535)
    environment: dict[StrictStr, StrictStr] = Field(default_factory=dict)
    autostart: StrictBool = False
    enable_metrics: StrictBool = True

    @model_validator(mode="after")
    def draft_placeholder_has_path(self) -> ProfileData:
        if "{{model_draft}}" in self.command and self.model.draft_path is None:
            raise ValueError("для {{model_draft}} необходимо выбрать draft-модель")
        return self

    @model_validator(mode="after")
    def image_profile_has_port(self) -> ProfileData:
        if self.engine.id == "image" and self.port is None:
            self.port = 8188
        return self

    @field_validator("version")
    @classmethod
    def version_one(cls, value: int) -> int:
        if value != 1:
            raise ValueError("поддерживается только версия профиля 1")
        return value

    @field_validator("name")
    @classmethod
    def valid_name(cls, value: str) -> str:
        return _profile_name(value)

    @field_validator("command")
    @classmethod
    def valid_command(cls, value: str) -> str:
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


class ProfileIssue(BaseModel):
    model_config = ConfigDict(extra="forbid")

    file: str = Field(min_length=1)
    code: str = Field(min_length=1)
    message: str = Field(min_length=1)


class ProfileListData(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[ProfileData]
    issues: list[ProfileIssue]


class FormatScriptRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    script: StrictStr


class FormatScriptResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    script: str
    changed: bool
    reason: str | None = None


# Names used by the contracts and by clients that import schemas directly.
Profile = ProfileData


__all__ = [
    "EngineId",
    "FormatScriptRequest",
    "FormatScriptResult",
    "Profile",
    "ProfileData",
    "ProfileEngine",
    "ProfileIssue",
    "ProfileListData",
    "ProfileModel",
]
