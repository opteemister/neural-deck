"""Persistent records for long running operations and model catalogue facts.

The service keeps the hot operation state in memory for immediate API reads;
these tables provide the migration seam for restart-safe history and make the
catalogue data model explicit without turning YAML profiles into database
configuration.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, BigInteger, DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base


class OperationRecord(Base):
    __tablename__ = "operations"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    kind: Mapped[str] = mapped_column(String(80), nullable=False)
    state: Mapped[str] = mapped_column(String(20), nullable=False)
    target_id: Mapped[str] = mapped_column(String(512), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    stdout: Mapped[str] = mapped_column(Text, nullable=False, default="")
    stderr: Mapped[str] = mapped_column(Text, nullable=False, default="")
    exit_code: Mapped[int | None] = mapped_column(Integer)
    error: Mapped[str | None] = mapped_column(Text)
    resolved_packages: Mapped[dict[str, str] | None] = mapped_column(JSON)
    checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ModelArtifactRecord(Base):
    __tablename__ = "model_artifacts"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    path: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    format: Mapped[str] = mapped_column(String(80), nullable=False)
    compatibility: Mapped[str] = mapped_column(String(80), nullable=False)
    size_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    hf_status: Mapped[str] = mapped_column(String(20), nullable=False)
    hf_repo_id: Mapped[str | None] = mapped_column(String(256))
    hf_revision: Mapped[str | None] = mapped_column(String(128))
    scanned_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
