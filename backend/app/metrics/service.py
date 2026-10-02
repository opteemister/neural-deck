"""Metric collection service over process and engine seams."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any, cast

from ..process.manager import ProcessManager
from ..schemas.instances import Metric, MetricList
from ..token_stats import RequestJournalService
from .performance import PerformanceNormalizer, process_vram
from .prometheus import PrometheusReader, exposition
from .providers import MetricProvider, default_providers
from .storage import MetricsStorage, utc

logger = logging.getLogger(__name__)


class MetricsService:
    def __init__(
        self,
        process_manager: ProcessManager,
        *,
        providers: Mapping[str, MetricProvider] | None = None,
        journal: RequestJournalService | None = None,
        storage: MetricsStorage | None = None,
        prometheus_url: str = "",
        prometheus_token: str = "",
        studio_id: str = "local",
    ) -> None:
        self.process_manager = process_manager
        self.providers = dict(providers or default_providers())
        self.journal = journal
        self.performance = PerformanceNormalizer()
        self.storage = storage
        self.studio_id = studio_id
        self.prometheus = PrometheusReader(prometheus_url, prometheus_token, studio_id)
        self._tasks: list[asyncio.Task[Any]] = []
        self._locks: dict[str, asyncio.Lock] = {}
        self._cache: dict[str, tuple[float, Any, MetricList]] = {}
        self._peaks: dict[tuple[str, int], dict[str, float]] = {}
        self.last_error: str | None = None

    async def close(self) -> None:
        """Close resources owned by metric providers, when they have any."""

        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        await self.prometheus.close()
        for provider in self.providers.values():
            close = getattr(provider, "close", None)
            if close is not None:
                await close()

    async def start(self) -> None:
        self._tasks = [
            asyncio.create_task(self.prometheus.check()),
            asyncio.create_task(self._background()),
            asyncio.create_task(self._cleanup_loop()),
        ]

    async def local(self, instance_id: str) -> MetricList:
        lock = self._locks.setdefault(instance_id, asyncio.Lock())
        async with lock:
            cached = self._cache.get(instance_id)
            if cached and time.monotonic() - cached[0] < 1:
                return cached[2]
            instance = await self.process_manager.get(instance_id)
            data = await self._collect_local(instance_id)
            if instance.profile_name is not None:
                identity = (instance.profile_name, instance.pid)
                if identity not in self._peaks:
                    self._peaks[identity] = (
                        await self.storage.peaks(*identity) if self.storage else {}
                    )
                peaks = self._peaks[identity]
                for item in data.items:
                    if item.state != "ok" or not isinstance(item.value, (int, float)):
                        continue
                    if item.name in {
                        "decode_tps",
                        "prefill_tps",
                        "active_requests",
                        "queue_requests",
                        "kv_cache_percent",
                    }:
                        peaks[item.name] = max(peaks.get(item.name, item.value), item.value)
                    elif item.name == "ttft_mean_ms" and item.value > 0:
                        peaks[item.name] = min(peaks.get(item.name, item.value), item.value)
                data.peaks = dict(peaks)
            self._cache[instance_id] = (time.monotonic(), instance, data)
            return data

    async def collect(self, instance_id: str) -> MetricList:
        if not self.prometheus.url:
            return await self.local(instance_id)
        instance = await self.process_manager.get(instance_id)
        samples = await self.prometheus.read(profile_name=instance.profile_name, pid=instance.pid)
        if samples:
            data = MetricList(items=samples[-1]["items"], source="prometheus")
            if instance.profile_name is not None:
                data.peaks = (
                    dict(self._peaks.get((instance.profile_name, instance.pid), {}))
                    if instance.profile_name is not None
                    else {}
                )
            return data
        if self.storage:
            rows = await self.storage.history(
                profile_name=instance.profile_name, pid=instance.pid, limit=1
            )
            if rows:
                items = [
                    item.model_copy(update={"state": "stale"})
                    if (datetime.now(UTC) - item.observed_at).total_seconds() > 10
                    else item
                    for item in rows[-1]["items"]
                ]
                return MetricList(
                    items=items,
                    source="sqlite",
                    peaks=dict(self._peaks.get((instance.profile_name, instance.pid), {}))
                    if instance.profile_name is not None
                    else {},
                )
        return MetricList(items=[], source="sqlite")

    async def _instances(self) -> list[Any]:
        observed = await self.process_manager.refresh(probe_models=False)
        return [
            instance
            for instance in observed.items
            if instance.state in {"starting", "ready", "running"}
            or (instance.source == "external" and instance.state == "unknown")
        ]

    async def _background(self) -> None:
        while True:
            started = time.monotonic()
            try:
                instances = await self._instances()
                results = await asyncio.gather(
                    *(self.local(instance.id) for instance in instances), return_exceptions=True
                )
                samples = [
                    (instance, result)
                    for instance, result in zip(instances, results, strict=True)
                    if isinstance(result, MetricList)
                ]
                if self.storage:
                    await self.storage.write(samples, self._peaks)
                active_ids = {instance.id for instance in instances}
                self._cache = {
                    key: value for key, value in self._cache.items() if key in active_ids
                }
                self._locks = {
                    key: value
                    for key, value in self._locks.items()
                    if key in active_ids or value.locked()
                }
                for provider in self.providers.values():
                    cache = getattr(provider, "_cache", None)
                    if isinstance(cache, dict):
                        for identity in list(cache):
                            if identity not in active_ids:
                                del cache[identity]
                self.last_error = None
            except Exception as exc:
                self.last_error = type(exc).__name__
                logger.exception("Фоновый сбор метрик не выполнен")
            await asyncio.sleep(max(0.01, 5 - (time.monotonic() - started)))

    async def _cleanup_loop(self) -> None:
        while True:
            try:
                instances = await self._instances()
                active = {
                    (instance.profile_name, instance.pid)
                    for instance in instances
                    if instance.profile_name is not None
                }
                if self.storage:
                    await self.storage.cleanup(active)
                self._peaks = {key: value for key, value in self._peaks.items() if key in active}
            except Exception:
                logger.exception("Очистка истории метрик не выполнена")
            now = datetime.now().astimezone()
            next_midnight = datetime.combine(
                now.date() + timedelta(days=1), datetime.min.time()
            ).astimezone()
            await asyncio.sleep(max(1, (next_midnight - now).total_seconds()))

    async def read(
        self,
        *,
        mode: str,
        profile_name: str | None = None,
        pid: int | None = None,
        scope: str = "profile",
        from_: datetime | None = None,
        to: datetime | None = None,
        limit: int = 120,
        client_id: int | None = None,
        offset: int = 0,
    ) -> dict[str, Any]:
        if mode == "usage":
            return (
                await self.journal.usage(
                    profile_name=profile_name,
                    pid=pid,
                    client_id=client_id,
                    from_=from_,
                    to=to,
                    limit=limit,
                    offset=offset,
                )
                if self.journal
                else {"items": [], "next_offset": None}
            )
        cutoff = datetime.now(UTC) - timedelta(hours=24)
        start = max(cutoff, utc(from_)) if from_ else cutoff
        end = utc(to) if to else datetime.now(UTC)
        if scope == "all":
            profile_name, pid = None, None
        if mode == "peaks":
            stored_peaks = (
                await self.storage.peaks(profile_name, pid)
                if self.storage and profile_name is not None and pid is not None
                else {}
            )
            return {"peaks": stored_peaks}
        history: list[dict[str, Any]] = []
        source = "sqlite"
        if mode in {"bootstrap", "history"}:
            count = limit * 16 if scope == "all" else limit
            history = (
                await self.storage.history(
                    profile_name=profile_name, pid=pid, from_=start, to=end, limit=count
                )
                if self.storage
                else []
            )
            remote = await self.prometheus.read(
                profile_name=profile_name,
                pid=pid,
                from_=max(start, end - timedelta(seconds=limit * 5))
                if mode == "bootstrap"
                else start,
                to=end,
                limit=count,
            )
            if remote:
                # Keep SQLite points in remote gaps, preferring remote on identity/time collisions.
                samples = {
                    (row["profile_name"], row["pid"], row["observed_at"].isoformat()): row
                    for row in history
                }
                samples.update(
                    {
                        (row["profile_name"], row["pid"], row["observed_at"].isoformat()): row
                        for row in remote
                    }
                )
                history = sorted(samples.values(), key=lambda row: row["observed_at"])
                source = "mixed" if len(samples) > len(remote) else "prometheus"
            if scope == "all":
                history = aggregate_history(history)
            history = history[-limit:]
        current = None
        peaks: dict[str, float] = {}
        if mode in {"current", "bootstrap"}:
            instances = await self._instances()
            selected = [
                instance
                for instance in instances
                if (profile_name is None or instance.profile_name == profile_name)
                and (pid is None or instance.pid == pid)
            ]
            results = await asyncio.gather(
                *(self.collect(instance.id) for instance in selected), return_exceptions=True
            )
            rows = [
                {
                    "profile_name": instance.profile_name,
                    "pid": instance.pid,
                    "items": result.items,
                    "source": result.source,
                    "observed_at": max(
                        (item.observed_at for item in result.items), default=datetime.now(UTC)
                    ),
                }
                for instance, result in zip(selected, results, strict=True)
                if isinstance(result, MetricList)
            ]
            if scope == "all":
                current = aggregate_samples(rows)
            elif rows:
                current = rows[-1]
                peaks = dict(self._peaks.get((rows[-1]["profile_name"], rows[-1]["pid"]), {}))
        if mode == "current" and current is not None:
            source = current.get("source", "local")
        return {"history": history, "current": current, "peaks": peaks, "source": source}

    def export(self) -> str:
        samples = [
            (entry[1], entry[2])
            for entry in self._cache.values()
            if time.monotonic() - entry[0] <= 10
        ]
        return exposition(samples, self.studio_id, self.performance.samples)

    async def _collect_local(self, instance_id: str) -> MetricList:
        instance = await self.process_manager.get(instance_id)
        now = datetime.now(UTC)
        result = [
            Metric(
                name="process_state",
                value=instance.state,
                unit="",
                source="process_table",
                observed_at=now,
                state="ok" if instance.state in {"running", "ready", "starting"} else "stale",
            )
        ]
        provider = self.providers.get(instance.engine_id)
        if provider is None:
            result.append(
                Metric(
                    name="engine_metrics",
                    value=None,
                    unit="",
                    source="engine_provider",
                    observed_at=now,
                    state="unavailable",
                )
            )
        else:
            try:
                result.extend(await provider.collect(instance))
            except Exception:  # provider boundary must not break metrics API
                result.append(
                    Metric(
                        name=f"{instance.engine_id}:metrics",
                        value=None,
                        unit="",
                        source="engine_provider",
                        observed_at=now,
                        state="unavailable",
                    )
                )
        vram = await asyncio.to_thread(process_vram, instance)
        if vram is not None:
            result = [item for item in result if item.name != "vram_bytes"] + [vram]
        return MetricList(items=self.performance.collect(instance, result))


__all__ = ["MetricsService"]


def aggregate_samples(rows: list[dict[str, Any]]) -> dict[str, Any]:
    from .performance import FIELDS, UNITS

    items = []
    sums = {
        "decode_tps",
        "prefill_tps",
        "active_requests",
        "queue_requests",
        "prompt_tokens_total",
        "completion_tokens_total",
        "inflight_completion_tokens",
        "completed_requests",
        "cached_prompt_tokens_total",
    }
    for name in FIELDS:
        matches = [next((item for item in row["items"] if item.name == name), None) for row in rows]
        complete = all(
            item is not None and item.state == "ok" and isinstance(item.value, (int, float))
            for item in matches
        )
        values = [
            item.value
            for item in matches
            if item is not None and isinstance(item.value, (int, float))
        ]
        value = None
        if complete:
            if name in sums:
                value = sum(values)
            elif name == "kv_cache_percent":
                value = max(values, default=0)
            elif not rows:
                value = 0
            elif len(rows) == 1:
                value = values[0]
            else:
                weight_key = (
                    "count" if name in {"ttft_mean_ms", "queue_time_mean_ms"} else "denominator"
                )
                weights = [item.metadata.get(weight_key) if item else None for item in matches]
                if all(isinstance(weight, (int, float)) and weight > 0 for weight in weights):
                    numeric_weights = [cast(float, weight) for weight in weights]
                    value = sum(v * w for v, w in zip(values, numeric_weights, strict=True)) / sum(
                        numeric_weights
                    )
        stamp = max((item.observed_at for item in matches if item), default=datetime.now(UTC))
        items.append(
            Metric(
                name=name,
                value=value,
                unit=UNITS.get(name, ""),
                source="aggregate",
                observed_at=stamp,
                state="ok" if value is not None else "unavailable",
            )
        )
    sources = {row.get("source", "local") for row in rows}
    return {
        "items": items,
        "source": next(iter(sources)) if len(sources) == 1 else "mixed" if sources else "local",
        "observed_at": max((item.observed_at for item in items), default=datetime.now(UTC)),
    }


def aggregate_history(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[int, dict[tuple[str | None, int], dict[str, Any]]] = {}
    for row in rows:
        key = int(row["observed_at"].timestamp()) // 5
        groups.setdefault(key, {})[(row["profile_name"], row["pid"])] = row
    return [aggregate_samples(list(group.values())) for _, group in sorted(groups.items())]
