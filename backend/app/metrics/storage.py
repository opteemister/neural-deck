"""SQLite engine history; request/client tables are never pruned here."""

from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import case, delete, func, select
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ..schemas.instances import Instance, Metric, MetricList
from .archive import archive_before_cleanup, historical_statistics
from .entities import MetricDaily, MetricLaunchExtrema, MetricSample, ProfileStatistics
from .performance import FIELDS, UNITS

AGGREGATE_FIELDS = (
    "prefill_tps", "decode_tps", "active_requests", "vram_bytes", "ttft_mean_ms",
    "prefix_cache_hit_percent", "spec_acceptance_percent", "kv_cache_percent", "queue_requests",
)


def utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def sample_data(row: MetricSample) -> dict[str, Any]:
    items = []
    for name in FIELDS:
        meta = row.metric_meta.get(name, {})
        items.append(
            Metric(
                name=name,
                value=getattr(row, name),
                unit=UNITS.get(name, ""),
                source=meta.get("source", "sqlite"),
                observed_at=utc(datetime.fromisoformat(meta["observed_at"]))
                if meta.get("observed_at")
                else utc(row.observed_at),
                state=meta.get("state", "unavailable"),
                metadata=meta.get("metadata", {}),
            )
        )
    return {
        "profile_name": row.profile_name,
        "pid": row.pid,
        "engine_id": row.engine_id,
        "source": "sqlite",
        "observed_at": utc(row.observed_at),
        "items": items,
    }


class MetricsStorage:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self.sessions = sessions
        self.archive_lock = asyncio.Lock()
        self._historical_cache: dict[tuple, tuple[float, dict[str, Any]]] = {}

    async def historical(
        self, *, profile_name: str | None, days: int, curve: bool,
    ) -> dict[str, Any]:
        key = (profile_name, days, curve)
        async with self.archive_lock:
            now = time.monotonic()
            cached = self._historical_cache.get(key)
            if cached and now - cached[0] < 30:
                return cached[1]
            async with self.sessions() as session:
                result = await historical_statistics(
                    session, profile_name=profile_name, days=days, curve=curve,
                )
            self._historical_cache = {
                k: v for k, v in self._historical_cache.items() if now - v[0] < 30
            }
            self._historical_cache[key] = (now, result)
            return result

    async def write(
        self,
        samples: list[tuple[Instance, MetricList]],
        peaks: dict[tuple[str, int], dict[str, float]],
    ) -> None:
        now = datetime.now(UTC)
        async with self.archive_lock, self.sessions() as session:
            for instance, data in samples:
                meta = {
                    item.name: {
                        "state": item.state,
                        "source": item.source,
                        "observed_at": item.observed_at.isoformat(),
                        "metadata": item.metadata,
                    }
                    for item in data.items
                }
                values = {
                    item.name: item.value
                    if item.state == "ok" and isinstance(item.value, (int, float))
                    else None
                    for item in data.items
                    if item.name in FIELDS
                }
                stamp = max(
                    (
                        item.observed_at
                        for item in data.items
                        if item.state == "ok" and item.name != "uptime_s"
                    ),
                    default=now,
                )
                session.add(
                    MetricSample(
                        profile_name=instance.profile_name,
                        pid=instance.pid,
                        engine_id=instance.engine_id,
                        model_name=instance.model_name,
                        recorded_at=now,
                        observed_at=stamp,
                        metric_meta=meta,
                        distributions=data.distributions or None,
                        **values,
                    )
                )
                if instance.profile_name is not None:
                    identity = (instance.profile_name, instance.pid)
                    statement = insert(MetricLaunchExtrema).values(
                        profile_name=identity[0],
                        pid=identity[1],
                        values=peaks.get(identity, {}),
                        observed_at=now,
                    )
                    await session.execute(
                        statement.on_conflict_do_update(
                            index_elements=["profile_name", "pid"],
                            set_={"values": statement.excluded["values"], "observed_at": now},
                        )
                    )
            await session.commit()

    async def history(
        self,
        *,
        profile_name: str | None = None,
        pid: int | None = None,
        from_: datetime | None = None,
        to: datetime | None = None,
        limit: int = 120,
    ) -> list[dict[str, Any]]:
        cutoff = datetime.now(UTC) - timedelta(hours=24)
        query = select(MetricSample).where(
            MetricSample.observed_at >= max(cutoff, utc(from_))
            if from_
            else MetricSample.observed_at >= cutoff
        )
        if profile_name is not None:
            query = query.where(MetricSample.profile_name == profile_name)
        if pid is not None:
            query = query.where(MetricSample.pid == pid)
        if to is not None:
            query = query.where(MetricSample.observed_at <= utc(to))
        async with self.sessions() as session:
            rows = (
                await session.scalars(
                    query.order_by(MetricSample.observed_at.desc(), MetricSample.id.desc()).limit(
                        limit
                    )
                )
            ).all()
        return [sample_data(row) for row in reversed(rows)]

    async def peaks(self, profile_name: str, pid: int) -> dict[str, float]:
        async with self.sessions() as session:
            row = await session.get(MetricLaunchExtrema, (profile_name, pid))
            return dict(row.values) if row else {}

    async def remember_vram(self, profile_name: str, item: Metric) -> None:
        if item.state != "ok" or not isinstance(item.value, (int, float)) or item.value <= 0:
            return
        async with self.archive_lock, self.sessions() as session:
            previous = await session.get(ProfileStatistics, profile_name)
            if previous is not None and (
                previous.vram_bytes == item.value
                or utc(previous.vram_observed_at) > utc(item.observed_at)
            ):
                return
            await session.merge(ProfileStatistics(
                profile_name=profile_name, vram_bytes=float(item.value),
                vram_observed_at=item.observed_at,
            ))
            await session.commit()

    async def last_vram(self, profile_name: str) -> float | None:
        async with self.sessions() as session:
            row = await session.get(ProfileStatistics, profile_name)
            return row.vram_bytes if row else None

    async def all_time_active_requests_max(self, profile_name: str) -> float | None:
        """Preserve the observed peak across days, launches and raw-sample pruning."""
        async with self.archive_lock, self.sessions() as session:
            raw = await session.scalar(select(func.max(MetricSample.active_requests)).where(
                MetricSample.profile_name == profile_name,
                MetricSample.active_requests > 0,
                func.json_extract(MetricSample.metric_meta, "$.active_requests.state") == "ok",
            ))
            archived = await session.scalar(select(func.max(func.json_extract(
                MetricDaily.statistics, "$.active_requests.max",
            ))).where(MetricDaily.profile_name == profile_name))
        values = [float(value) for value in (raw, archived) if value is not None]
        return max(values) if values else None

    async def aggregates(
        self, *, start: datetime, end: datetime, profile_name: str | None = None,
        pid: int | None = None, all_profiles: bool = False,
    ) -> dict[str, dict[str, float]]:
        """Aggregate the entire window in SQL, deduplicating retained measurements."""
        result = {}
        async with self.sessions() as session:
            for name in AGGREGATE_FIELDS:
                value = getattr(MetricSample, name)
                observed = func.coalesce(
                    func.json_extract(MetricSample.metric_meta, f"$.{name}.observed_at"),
                    MetricSample.observed_at,
                )
                weight_key = "count" if name == "ttft_mean_ms" else "denominator"
                native_weight = func.json_extract(
                    MetricSample.metric_meta, f"$.{name}.metadata.{weight_key}"
                )
                weight = case((native_weight > 0, native_weight), else_=1.0)
                query = select(
                    func.max(value).label("value"), func.max(weight).label("weight"),
                    observed.label("at"),
                ).where(
                    MetricSample.observed_at >= start, MetricSample.observed_at <= end,
                    func.julianday(observed) >= func.julianday(start),
                    func.julianday(observed) <= func.julianday(end), value > 0,
                    func.json_extract(MetricSample.metric_meta, f"$.{name}.state") == "ok",
                )
                if profile_name is not None:
                    query = query.where(MetricSample.profile_name == profile_name)
                if pid is not None:
                    query = query.where(MetricSample.pid == pid)
                samples = query.group_by(
                    MetricSample.profile_name, MetricSample.pid, observed
                ).subquery()
                if all_profiles:
                    # Align the five-second snapshots before summing concurrent rates.
                    combined = (
                        func.sum(samples.c.value)
                        if name in {
                            "prefill_tps", "decode_tps", "active_requests", "queue_requests"
                        }
                        else func.max(samples.c.value) if name == "kv_cache_percent"
                        else func.sum(samples.c.value * samples.c.weight)
                        / func.sum(samples.c.weight)
                    )
                    samples = select(
                        combined.label("value"), func.sum(samples.c.weight).label("weight")
                        if name in {
                            "ttft_mean_ms", "prefix_cache_hit_percent", "spec_acceptance_percent"
                        }
                        else func.max(1.0).label("weight"),
                    ).group_by(
                        func.floor(func.strftime("%s", samples.c.at) / 5.0)
                    ).subquery()
                row = (await session.execute(select(
                    func.max(samples.c.value),
                    func.sum(samples.c.value * samples.c.weight) / func.sum(samples.c.weight),
                ))).one()
                if row[0] is not None:
                    result[name] = {"max": float(row[0]), "avg": float(row[1])}
        return result

    async def cleanup(self, active: set[tuple[str, int]]) -> None:
        cutoff = datetime.now(UTC) - timedelta(hours=24)
        async with self.archive_lock, self.sessions() as session:
            cursor = await archive_before_cleanup(session)
            await session.execute(delete(MetricSample).where(
                MetricSample.observed_at < cutoff, MetricSample.id <= cursor,
            ))
            rows = (
                await session.scalars(
                    select(MetricLaunchExtrema).where(MetricLaunchExtrema.observed_at < cutoff)
                )
            ).all()
            for row in rows:
                if (row.profile_name, row.pid) not in active:
                    await session.delete(row)
            await session.commit()
            self._historical_cache.clear()
