"""Persistent metadata journal for OpenAI-compatible proxy requests."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, Float, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base


class ApiClientRecord(Base):
    __tablename__ = "api_clients"
    __table_args__ = {"sqlite_autoincrement": True}
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    api_key: Mapped[str | None] = mapped_column(Text, nullable=True, unique=True)
    anonymous_ip: Mapped[str | None] = mapped_column(String(64), nullable=True, unique=True)
    ip_addresses: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class RequestJournalRecord(Base):
    """One proxy request without request or response content."""

    __tablename__ = "request_journal"

    id: Mapped[str] = mapped_column(String(100), primary_key=True)
    request_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    route: Mapped[str] = mapped_column(String(120), nullable=False)
    model_name: Mapped[str] = mapped_column(String(512), nullable=False)
    profile_name: Mapped[str | None] = mapped_column(String(256), nullable=True, index=True)
    instance_id: Mapped[str | None] = mapped_column(String(512), nullable=True, index=True)
    engine_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    status_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    latency_ms: Mapped[float] = mapped_column(Float, nullable=False)
    input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    streaming: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    pid: Mapped[int | None] = mapped_column(Integer, nullable=True)
    client_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    client_ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    cached_input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    usage_received_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    usage_source: Mapped[str | None] = mapped_column(String(80), nullable=True)
    metric_meta: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    ttft_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    queue_time_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    prefill_time_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    generation_time_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    prefill_tps: Mapped[float | None] = mapped_column(Float, nullable=True)
    decode_tps: Mapped[float | None] = mapped_column(Float, nullable=True)
    prefix_cache_hit_percent: Mapped[float | None] = mapped_column(Float, nullable=True)
    spec_acceptance_percent: Mapped[float | None] = mapped_column(Float, nullable=True)
    error_code: Mapped[str | None] = mapped_column(Text, nullable=True)


def journal_record_values(record: RequestJournalRecord) -> dict[str, Any]:
    """Return safe metadata for diagnostics and tests."""

    return {
        "id": record.id,
        "request_id": record.request_id,
        "created_at": record.created_at,
        "route": record.route,
        "model_name": record.model_name,
        "profile_name": record.profile_name,
        "instance_id": record.instance_id,
        "engine_id": record.engine_id,
        "status_code": record.status_code,
        "latency_ms": record.latency_ms,
        "input_tokens": record.input_tokens,
        "output_tokens": record.output_tokens,
        "streaming": bool(record.streaming),
        "error_code": record.error_code,
    }


__all__ = ["RequestJournalRecord", "journal_record_values"]
