"""Management API schemas for local model catalogue and Hugging Face."""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..operations import ensure_safe_relative_path


class HfMatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["downloaded", "incomplete", "unknown"]
    repo_id: str | None = None
    revision: str | None = None


class LocalModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    path: Path
    kind: Literal["file", "directory"]
    format: str
    compatibility: str
    # A stat failure is a real unknown; zero is a valid size and cannot be
    # used as an error sentinel.
    size_bytes: int | None = Field(default=None, ge=0)
    hf_match: HfMatch


class LocalModelList(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[LocalModel]


class ModelRegistryItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    upstream_model: str | None = None
    instance_id: str = Field(min_length=1)
    engine_id: str = Field(min_length=1)
    profile_name: str | None = None
    observed_at: datetime


class ModelRegistryData(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[ModelRegistryItem]


class HfSearchItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    repo_id: str
    revision: str | None = None
    description: str | None = None
    tags: list[str] = Field(default_factory=list)


class HfSearchData(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[HfSearchItem]


class HfFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str
    # Hub tree responses may omit size for LFS/metadata entries.  Preserve
    # that uncertainty so catalogue matching cannot claim a false download.
    size: int | None = Field(default=None, ge=0)
    etag: str | None = None
    sha256: str | None = None

    @field_validator("path")
    @classmethod
    def validate_path(cls, value: str) -> str:
        return ensure_safe_relative_path(value)


class HfRepoData(BaseModel):
    model_config = ConfigDict(extra="forbid")

    repo_id: str
    revision: str
    files: list[HfFile]


class DownloadRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    repo_id: str = Field(min_length=3, max_length=256)
    revision: str
    files: list[str] | None = None

    @field_validator("repo_id")
    @classmethod
    def validate_repo_id(cls, value: str) -> str:
        if value.count("/") != 1 or any(
            not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", part) for part in value.split("/")
        ):
            raise ValueError("repo_id must be namespace/name")
        return value

    @field_validator("files")
    @classmethod
    def validate_files(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        return [ensure_safe_relative_path(item) for item in value]


__all__ = [
    "DownloadRequest",
    "HfFile",
    "HfMatch",
    "HfRepoData",
    "HfSearchData",
    "LocalModel",
    "LocalModelList",
    "ModelRegistryData",
    "ModelRegistryItem",
]
