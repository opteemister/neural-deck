"""Management API schemas for local model catalogue and Hugging Face."""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..operations import ensure_safe_relative_path


class RemoteCheckState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    availability: Literal["success", "unavailable", "not_tracked"]
    last_attempted_at: str | None = None
    last_successful_at: str | None = None
    current_revision: str | None = None
    repository_date: str | None = None
    last_error: str | None = None
    stale: bool = False


class RemoteUpdateState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["up_to_date", "update_available", "unknown", "not_tracked"]
    changed_files: int = Field(default=0, ge=0)


class LocalIntegrityState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    baseline_status: Literal[
        "matches_downloaded", "diverged_from_downloaded", "unknown", "unavailable"
    ] = "unknown"
    remote_status: Literal[
        "matches_current_remote", "differs_from_current_remote", "unknown", "unavailable"
    ] = "unknown"


class ChangedPath(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str
    status: Literal["changed", "added", "removed", "local_divergence"]
    downloaded_file_date: str | None = None
    remote_file_date: str | None = None


class LocalFileDifference(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str
    reasons: list[Literal["size", "hash"]] = Field(default_factory=list)


class HfMatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["downloaded", "incomplete", "unknown"]
    repo_id: str | None = None
    revision: str | None = None
    scope: Literal["snapshot", "selection"] | None = None
    missing_files: list[str] = Field(default_factory=list)
    unverified_files: list[str] = Field(default_factory=list)
    local_differences: list[LocalFileDifference] = Field(default_factory=list)
    remote_check: RemoteCheckState | None = None
    remote_update: RemoteUpdateState | None = None
    local_integrity: LocalIntegrityState | None = None
    changed_paths: list[ChangedPath] = Field(default_factory=list)
    downloaded_revision_date: str | None = None
    remote_revision_date: str | None = None


class LocalModelFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str
    format: str
    size_bytes: int | None = Field(default=None, ge=0)


class LocalModelGroup(BaseModel):
    """Logical model component shown inside one local model card."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    label: str = Field(min_length=1)
    kind: Literal["variant", "auxiliary_block"]
    path: str | None = None
    files: list[LocalModelFile] = Field(default_factory=list)
    file_count: int = Field(default=0, ge=0)
    size_bytes: int | None = Field(default=None, ge=0)
    status: Literal["complete", "incomplete", "unknown"] = "unknown"
    deletable: bool = False


class LocalModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    path: Path
    kind: Literal["file", "directory"]
    model_kind: Literal["llm", "image", "unknown"] = "unknown"
    format: str
    compatibility: str
    vendor: str = ""
    model_name: str = ""
    # A stat failure is a real unknown; zero is a valid size and cannot be
    # used as an error sentinel.
    size_bytes: int | None = Field(default=None, ge=0)
    hf_match: HfMatch
    variants: list[LocalModelFile] = Field(default_factory=list)
    auxiliary_files: list[LocalModelFile] = Field(default_factory=list)
    groups: list[LocalModelGroup] = Field(default_factory=list)
    auxiliary_blocks: list[LocalModelGroup] = Field(default_factory=list)
    supporting_files: int = Field(default=0, ge=0)


class LocalModelList(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[LocalModel]
    scanning: bool = False
    scan_error: str | None = None


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
    author: str | None = None
    avatar_url: str | None = None
    downloads: int | None = Field(default=None, ge=0)
    likes: int | None = Field(default=None, ge=0)
    trending_score: float | None = None
    used_storage: int | None = Field(default=None, ge=0)
    safetensors: dict[str, Any] | None = None
    gguf: dict[str, Any] | None = None
    eval_results: list[dict[str, Any]] | None = None
    pipeline_tag: str | None = None
    library_name: str | None = None
    created_at: str | None = None
    last_modified: str | None = None


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
    # A regular Hub file exposes a Git blob id rather than a content hash.
    # Keep that distinction in manifests so an explicit sync can verify it
    # with Git's blob hashing rules without treating an LFS oid as SHA-1 data.
    hash_kind: Literal["git_blob", "md5"] | None = None
    file_date: str | None = None

    @field_validator("path")
    @classmethod
    def validate_path(cls, value: str) -> str:
        return ensure_safe_relative_path(value)


class HfRepoData(BaseModel):
    model_config = ConfigDict(extra="forbid")

    repo_id: str
    revision: str
    author: str | None = None
    avatar_url: str | None = None
    revision_date: str | None = None
    description: str | None = None
    readme: str | None = None
    files: list[HfFile]
    quantizations: list[HfSearchItem] = Field(default_factory=list)


class ModelFileUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    files: list[str] | None = Field(default=None, min_length=1)

    @field_validator("files")
    @classmethod
    def validate_files(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        return list(dict.fromkeys(ensure_safe_relative_path(item) for item in value))


class ModelFileVersion(BaseModel):
    size: int | None = None
    file_date: str | None = None
    hash: str | None = None
    hash_kind: str | None = None


class ModelFileUpdate(BaseModel):
    path: str
    status: Literal["changed", "added", "removed", "local_divergence"]
    local: ModelFileVersion
    remote: ModelFileVersion


class ModelFileUpdates(BaseModel):
    model_id: str
    repo_id: str
    revision: str | None = None
    checked_at: str | None = None
    items: list[ModelFileUpdate]


class DownloadRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    repo_id: str = Field(min_length=3, max_length=256)
    revision: str
    files: list[str] | None = None
    label: str | None = Field(default=None, max_length=512)

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
    "ChangedPath",
    "HfFile",
    "HfMatch",
    "HfRepoData",
    "HfSearchData",
    "LocalIntegrityState",
    "LocalModel",
    "LocalModelFile",
    "LocalModelGroup",
    "LocalModelList",
    "ModelRegistryData",
    "ModelRegistryItem",
    "RemoteCheckState",
    "RemoteUpdateState",
]
