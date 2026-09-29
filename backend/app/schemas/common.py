"""Common success and error envelopes."""

from __future__ import annotations

from typing import TypeVar

from pydantic import BaseModel, ConfigDict, Field

DataT = TypeVar("DataT")


class SuccessEnvelope[DataT](BaseModel):
    model_config = ConfigDict(extra="forbid")

    data: DataT
    request_id: str = Field(min_length=1)


class ErrorBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str = Field(min_length=1)
    message: str = Field(min_length=1)
    details: dict[str, object] = Field(default_factory=dict)
    request_id: str = Field(min_length=1)
    retryable: bool = False


class ErrorEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    error: ErrorBody
