"""Build the read-only data shown on profile cards."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, time
from pathlib import Path
from typing import Any

from ..metrics.service import MetricsService
from ..models.catalogue import ModelCatalogue
from ..schemas.instances import Instance, MetricList
from ..schemas.profile_summary import (
    ProfileConcurrencySummary,
    ProfileEndpointSummary,
    ProfileLoadSummary,
    ProfileMemorySummary,
    ProfilePerformanceSummary,
    ProfileSummary,
    ProfileSummaryData,
    SummaryMetric,
    SummaryPeriod,
    SummaryState,
)
from ..schemas.profiles import ProfileData
from .ports import PortResolution, resolve_effective_port


def utc_day_window(now: datetime | None = None) -> tuple[datetime, datetime]:
    end = (now or datetime.now(UTC)).astimezone(UTC)
    return datetime.combine(end.date(), time.min, tzinfo=UTC), end


def _metric(
    value: float | None,
    *,
    unit: str,
    state: SummaryState,
    source: str,
    reason: str | None = None,
) -> SummaryMetric:
    return SummaryMetric(
        value=value,
        unit=unit,
        state=state,
        source=source,
        reason=reason,
    )


def _aggregate_metric(
    aggregates: dict[str, dict[str, float]], name: str, *, unit: str
) -> SummaryMetric:
    value = aggregates.get(name, {}).get("avg")
    return _metric(
        value,
        unit=unit,
        state="ok" if value is not None else "unavailable",
        source="sqlite" if value is not None else "unavailable",
        reason=None if value is not None else "no_valid_samples",
    )


def _max_metric(
    aggregates: dict[str, dict[str, float]], name: str, *, unit: str
) -> SummaryMetric:
    value = aggregates.get(name, {}).get("max")
    return _metric(
        value,
        unit=unit,
        state="ok" if value is not None else "unavailable",
        source="sqlite" if value is not None else "unavailable",
        reason=None if value is not None else "no_valid_samples",
    )


def _disk_metric(profile: ProfileData, catalogue_items: list[Any]) -> SummaryMetric:
    model = next(
        (item for item in catalogue_items if Path(item.path) == profile.model.path),
        None,
    )
    size = getattr(model, "size_bytes", None)
    return _metric(
        float(size) if size is not None else None,
        unit="bytes",
        state="ok" if size is not None else "unavailable",
        source="model_catalogue" if size is not None else "unavailable",
        reason=None if size is not None else "model_not_in_catalogue",
    )


def _current_metric(
    current: MetricList | None, name: str, *, unit: str
) -> SummaryMetric:
    if current is None:
        return _metric(
            None, unit=unit, state="unavailable", source="unavailable", reason="no_active_process"
        )
    item = next((candidate for candidate in current.items if candidate.name == name), None)
    if item is None or not isinstance(item.value, (int, float)):
        return _metric(
            None, unit=unit, state="unavailable", source="unavailable", reason="metric_unavailable"
        )
    return _metric(
        float(item.value),
        unit=unit,
        state=item.state,
        source=item.source,
        reason=None if item.state == "ok" else "metric_stale",
    )


def _port_summary(
    resolution: PortResolution, conflicts: dict[int, list[str]], profile_name: str
) -> ProfileEndpointSummary:
    peers = [name for name in conflicts.get(resolution.port or -1, []) if name != profile_name] if resolution.port is not None else []
    return ProfileEndpointSummary(
        port=resolution.port,
        state="ok" if resolution.state == "ok" else "unknown",
        source=resolution.source,
        conflict=bool(peers),
        conflict_with=peers,
        reason=resolution.reason,
    )


async def build_profile_summary(
    profiles: list[ProfileData],
    *,
    metrics: MetricsService,
    catalogue: ModelCatalogue,
    start: datetime,
    end: datetime,
) -> ProfileSummaryData:
    """Build all requested cards in one backend operation."""

    catalogue_items = await asyncio.to_thread(catalogue.items)
    resolutions = {
        profile.name: resolve_effective_port(profile.engine.id, profile.command)
        for profile in profiles
    }
    by_port: dict[int, list[str]] = {}
    for profile_name, resolution in resolutions.items():
        if resolution.port is not None:
            by_port.setdefault(resolution.port, []).append(profile_name)

    instances: list[Instance] = []
    try:
        observed = await metrics.process_manager.refresh(probe_models=False)
        instances = [
            item
            for item in observed.items
            if item.profile_name is not None
            and item.state in {"starting", "ready", "running"}
        ]
    except Exception:
        # A summary must remain useful when process discovery is temporarily
        # unavailable; historical values and catalogue size are independent.
        instances = []

    active_by_profile: dict[str, Instance] = {}
    for instance in instances:
        if instance.profile_name is not None:
            active_by_profile[instance.profile_name] = instance
    current_results = await asyncio.gather(
        *(metrics.local(instance.id) for instance in active_by_profile.values()),
        return_exceptions=True,
    )
    current_by_profile: dict[str, MetricList] = {
        name: result
        for name, result in zip(active_by_profile, current_results, strict=True)
        if isinstance(result, MetricList)
    }

    async def aggregates_for(profile_name: str) -> dict[str, dict[str, float]]:
        if metrics.storage is None:
            return {}
        try:
            result = await metrics.storage.aggregates(
                start=start, end=end, profile_name=profile_name
            )
            peak = await metrics.storage.all_time_active_requests_max(profile_name)
            result.pop("active_requests", None)
            if peak is not None:
                result["active_requests"] = {"max": peak}
            return result
        except Exception:
            # A card can still show model size, endpoint and unavailable
            # states when the historical store is temporarily unreadable.
            return {}

    async def last_vram_for(profile_name: str) -> SummaryMetric:
        value = None
        if metrics.storage is not None:
            try:
                value = await metrics.storage.last_vram(profile_name)
            except Exception:
                logging.getLogger(__name__).exception("Не удалось прочитать сохранённый VRAM профиля %s", profile_name)
        return _metric(
            value, unit="bytes", state="ok" if value is not None else "unavailable",
            source="sqlite" if value is not None else "unavailable",
            reason="last_observed" if value is not None else "no_valid_samples",
        )

    last_vram_results = await asyncio.gather(*(last_vram_for(p.name) for p in profiles))
    aggregate_results = await asyncio.gather(
        *(aggregates_for(profile.name) for profile in profiles)
    )
    period = SummaryPeriod.model_validate({"from": start, "to": end, "timezone": "UTC"})
    result: list[ProfileSummary] = []
    for profile, aggregates, last_vram in zip(
        profiles, aggregate_results, last_vram_results, strict=True
    ):
        current_vram = _current_metric(
            current_by_profile.get(profile.name), "vram_bytes", unit="bytes"
        )
        vram_avg = _aggregate_metric(aggregates, "vram_bytes", unit="bytes")
        vram_peak = _max_metric(aggregates, "vram_bytes", unit="bytes")
        limit = _max_metric(aggregates, "active_requests", unit="requests")
        if limit.value is not None:
            limit.value = float(int(limit.value))
        result.append(
            ProfileSummary(
                profile_name=profile.name,
                period=period,
                performance=ProfilePerformanceSummary(
                    prefill_tps=_aggregate_metric(aggregates, "prefill_tps", unit="tok/s"),
                    decode_tps=_aggregate_metric(aggregates, "decode_tps", unit="tok/s"),
                ),
                memory=ProfileMemorySummary(
                    disk_bytes=_disk_metric(profile, catalogue_items),
                    vram_current_bytes=current_vram,
                    vram_last_bytes=last_vram,
                    vram_avg_bytes=vram_avg,
                    vram_peak_bytes=vram_peak,
                ),
                concurrency=ProfileConcurrencySummary(
                    optimal=_metric(
                        None,
                        unit="requests",
                        state="not_measured",
                        source="benchmark",
                        reason="benchmark_not_measured",
                    ),
                    limit=limit,
                    observed_max=limit.model_copy(deep=True),
                    benchmark_state="not_measured",
                ),
                load=ProfileLoadSummary(
                    percent=_metric(
                        None,
                        unit="%",
                        state="unavailable",
                        source="metrics_interval",
                        reason="daily_load_not_collected",
                    )
                ),
                endpoint=_port_summary(resolutions[profile.name], by_port, profile.name),
            )
        )
    source = "sqlite" if metrics.storage is not None else "unavailable"
    return ProfileSummaryData(items=result, period=period, source=source)


__all__ = ["build_profile_summary", "utc_day_window"]
