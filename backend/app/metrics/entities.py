"""Persistence for engine samples and launch extrema."""

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, Float, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base


class MetricSample(Base):
    __tablename__ = "metric_samples"
    __table_args__ = (
        Index("ix_metrics_profile_pid_time", "profile_name", "pid", "observed_at"),
        Index("ix_metrics_profile_time", "profile_name", "observed_at"),
        Index("ix_metrics_time", "observed_at"),
        {"sqlite_autoincrement": True},
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    profile_name: Mapped[str | None] = mapped_column(String(256), nullable=True)
    pid: Mapped[int] = mapped_column(Integer)
    engine_id: Mapped[str] = mapped_column(String(80))
    model_name: Mapped[str | None] = mapped_column(String(512), nullable=True)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    metric_meta: Mapped[dict[str, Any]] = mapped_column(JSON)
    distributions: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    decode_tps: Mapped[float | None] = mapped_column(Float, nullable=True)
    ttft_mean_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    prefill_tps: Mapped[float | None] = mapped_column(Float, nullable=True)
    active_requests: Mapped[float | None] = mapped_column(Float, nullable=True)
    vram_bytes: Mapped[float | None] = mapped_column(Float, nullable=True)
    power_watts: Mapped[float | None] = mapped_column(Float, nullable=True)
    kv_cache_percent: Mapped[float | None] = mapped_column(Float, nullable=True)
    queue_requests: Mapped[float | None] = mapped_column(Float, nullable=True)
    uptime_s: Mapped[float | None] = mapped_column(Float, nullable=True)
    prompt_tokens_total: Mapped[float | None] = mapped_column(Float, nullable=True)
    completion_tokens_total: Mapped[float | None] = mapped_column(Float, nullable=True)
    inflight_completion_tokens: Mapped[float | None] = mapped_column(Float, nullable=True)
    completed_requests: Mapped[float | None] = mapped_column(Float, nullable=True)
    energy_kwh: Mapped[float | None] = mapped_column(Float, nullable=True)
    queue_time_mean_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    cached_prompt_tokens_total: Mapped[float | None] = mapped_column(Float, nullable=True)
    prefix_cache_hit_percent: Mapped[float | None] = mapped_column(Float, nullable=True)
    prefix_cache_hit_mean_percent: Mapped[float | None] = mapped_column(Float, nullable=True)
    spec_acceptance_percent: Mapped[float | None] = mapped_column(Float, nullable=True)


class MetricLaunchExtrema(Base):
    __tablename__ = "metric_launch_extrema"
    profile_name: Mapped[str] = mapped_column(String(256), primary_key=True)
    pid: Mapped[int] = mapped_column(Integer, primary_key=True)
    values: Mapped[dict[str, float]] = mapped_column(JSON)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class MetricDaily(Base):
    __tablename__ = "metric_daily"
    day: Mapped[str] = mapped_column(String(10), primary_key=True)
    profile_name: Mapped[str] = mapped_column(String(256), primary_key=True)
    model_name: Mapped[str] = mapped_column(String(512), primary_key=True)
    engine_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    statistics: Mapped[dict[str, Any]] = mapped_column(JSON)
    distributions: Mapped[dict[str, Any]] = mapped_column(JSON)


class MetricConcurrencyDaily(Base):
    __tablename__ = "metric_concurrency_daily"
    day: Mapped[str] = mapped_column(String(10), primary_key=True)
    profile_name: Mapped[str] = mapped_column(String(256), primary_key=True)
    model_name: Mapped[str] = mapped_column(String(512), primary_key=True)
    engine_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    requests: Mapped[int] = mapped_column(Integer, primary_key=True)
    statistics: Mapped[dict[str, Any]] = mapped_column(JSON)


class MetricArchiveCheckpoint(Base):
    __tablename__ = "metric_archive_checkpoint"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    last_sample_id: Mapped[int] = mapped_column(Integer)
    baselines: Mapped[dict[str, Any]] = mapped_column(JSON)
