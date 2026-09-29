"""Application configuration loaded from the Version Zero environment contract."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any

from pydantic import AliasChoices, BaseModel, Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


def _absolute_path(value: Any) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        raise ValueError("path must be absolute")
    return path


class Settings(BaseSettings):
    """Runtime settings.

    ``LLM_STUDIO_DATA_ROOT``, ``HOST`` and ``PORT`` intentionally have no
    defaults. The service must not silently start with data in an unexpected
    location or on an unexpected network interface.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="",
        case_sensitive=True,
        extra="ignore",
    )

    data_root: Path = Field(validation_alias=AliasChoices("LLM_STUDIO_DATA_ROOT", "data_root"))
    host: str = Field(validation_alias=AliasChoices("HOST", "host"), min_length=1)
    port: int = Field(validation_alias=AliasChoices("PORT", "port"), ge=1, le=65535)
    models_root: Path | None = Field(
        default=None,
        validation_alias=AliasChoices("MODELS_ROOT", "models_root"),
    )
    log_root: Path | None = Field(
        default=None,
        validation_alias=AliasChoices("LOG_ROOT", "log_root"),
    )
    extra_venv_roots: Annotated[list[Path], NoDecode] = Field(
        default_factory=list,
        validation_alias=AliasChoices("EXTRA_VENV_ROOTS", "extra_venv_roots"),
    )

    @field_validator("data_root", mode="before")
    @classmethod
    def validate_data_root(cls, value: Any) -> Any:
        if value is None:
            return None
        return _absolute_path(value)

    @field_validator("models_root", "log_root", mode="before")
    @classmethod
    def validate_optional_path(cls, value: Any) -> Any:
        if value is None or (isinstance(value, str) and not value.strip()):
            return None
        return _absolute_path(value)

    @field_validator("extra_venv_roots", mode="before")
    @classmethod
    def parse_extra_venv_roots(cls, value: Any) -> list[Path]:
        if value is None or value == "":
            return []
        if isinstance(value, str):
            value = [item.strip() for item in value.split(",") if item.strip()]
        if not isinstance(value, (list, tuple, set)):
            raise ValueError("EXTRA_VENV_ROOTS must be a comma-separated list")
        return [_absolute_path(item) for item in value]


class EffectiveSettings(BaseModel):
    """Settings exposed by the management API, without environment secrets."""

    model_config = {"extra": "forbid"}

    data_root: Path
    models_root: Path
    log_root: Path
    extra_venv_roots: list[Path]
    host: str
    port: int


def load_settings() -> Settings:
    """Load and validate settings at the application boundary."""

    return Settings()
