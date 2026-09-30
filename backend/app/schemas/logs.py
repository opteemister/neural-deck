"""Schemas for the browser log viewer."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

LogKind = Literal["application", "profile", "operations"]


class LogSource(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    kind: LogKind
    label: str = Field(min_length=1)
    profile_name: str | None = None
    file_count: int = Field(ge=0)
    updated_at: datetime | None = None


class LogSourceList(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[LogSource]


class LogLine(BaseModel):
    model_config = ConfigDict(extra="forbid")

    number: int = Field(ge=0)
    text: str


class LogData(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: LogSource
    lines: list[LogLine]
    total_lines: int = Field(ge=0)
    start_line: int = Field(ge=0)
    end_line: int = Field(ge=0)
    has_older: bool


class LogDeleteData(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    deleted_files: int = Field(ge=0)


__all__ = ["LogData", "LogDeleteData", "LogKind", "LogLine", "LogSource", "LogSourceList"]
