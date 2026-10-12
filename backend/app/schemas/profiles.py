"""Pydantic schemas for the Version Zero profile file contract."""

from __future__ import annotations

from pathlib import Path
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

from .engines import Capability, EngineId, ProfileApiOverride
from ..profiles.formatting import validate_placeholders


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
    model: ProfileModel | None = None
    command: StrictStr = Field(min_length=1)
    types: list[Capability] | None = None
    api: ProfileApiOverride | None = None
    ui_url: StrictStr | None = None
    port: StrictInt | None = Field(default=None, ge=1, le=65535)
    environment: dict[StrictStr, StrictStr] = Field(default_factory=dict)
    autostart: StrictBool = False
    enable_metrics: StrictBool = True
    input_price_usd_per_million_tokens: float | None = Field(default=None, ge=0)
    output_price_usd_per_million_tokens: float | None = Field(default=None, ge=0)
    cached_input_price_usd_per_million_tokens: float | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def draft_placeholder_has_path(self) -> ProfileData:
        if "{{model_draft}}" in self.command and (
            self.model is None or self.model.draft_path is None
        ):
            raise ValueError("для {{model_draft}} необходимо выбрать draft-модель")
        return self

    @model_validator(mode="after")
    def model_placeholder_has_path(self) -> ProfileData:
        if "{{model}}" in self.command and self.model is None:
            raise ValueError("для {{model}} необходимо выбрать модель")
        return self

    @model_validator(mode="after")
    def image_profile_has_port(self) -> ProfileData:
        if self.engine.id in {"image", "comfyui"} and self.port is None:
            self.port = 8188
        return self

    @field_validator("types")
    @classmethod
    def unique_types(cls, value: list[Capability] | None) -> list[Capability] | None:
        if value is not None and len(set(value)) != len(value):
            raise ValueError("типы профиля не должны повторяться")
        return value

    @field_validator("ui_url")
    @classmethod
    def valid_ui_url(cls, value: str | None) -> str | None:
        if value is None:
            return value
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("ui_url должен быть абсолютным HTTP(S) адресом")
        if parsed.username is not None or parsed.password is not None:
            raise ValueError("учётные данные не должны входить в ui_url")
        return value

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
