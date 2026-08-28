"""Runtime configuration loaded from environment variables."""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Required runtime settings for the application."""

    app_name: str
    app_env: Literal["local", "production"]
    host: str
    port: int
    database_url: str
    data_dir: Path
    model_engines_dir: Path
    model_shutdown_timeout_seconds: int
    model_log_dir: Path

    model_config = SettingsConfigDict(
        env_file=(".env.local", "../.env.local"),
        env_prefix="",
        extra="ignore",
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the cached application settings."""

    return Settings()  # type: ignore[call-arg]  # BaseSettings loads required fields from the environment.
