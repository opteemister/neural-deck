"""SQLite engine history; request/client tables are never pruned here."""

from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ..schemas.instances import Instance, Metric, MetricList
from .aggregation import WindowSummary, utc
from .archive import archive_before_cleanup, historical_statistics, load_and_fold
from .entities import MetricDaily, MetricLaunchExtrema, MetricSample, ProfileStatistics
from .performance import FIELDS, UNITS


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

    async def statistics_window(self, *, start: datetime, end: datetime,
                                profile_name: str) -> dict[str, Any]:
        """Merge daily engine measurements with pending raw samples, once."""
        async with self.archive_lock, self.sessions() as session:
            daily, _, _, _ = await load_and_fold(
                session, first_day=utc(start).date().isoformat(),
                profile_name=profile_name, end=end,
            )
        combined: dict[str, dict[str, float]] = {}
        active_seconds = 0.0
        load_known = True
        for (day, _, _, _), item in daily.items():
            last_day = (utc(end) - timedelta(microseconds=1)).date().isoformat()
            if not utc(start).date().isoformat() <= day <= last_day:
                continue
            stats = item["statistics"]
            for field in ("prefill_tps", "decode_tps"):
                value = stats.get(field)
                if value and value["weight"] > 0:
                    target = combined.setdefault(field, {"sum": 0.0, "weight": 0.0})
                    target["sum"] += value["sum"]
                    target["weight"] += value["weight"]
            active_time = stats.get("active_requests_time")
            if active_time:
                active_seconds += active_time["sum"]
            elif stats.get("active_requests"):
                # Old rollups have peaks, but no observed concurrency durations.
                load_known = False
        return {
            "prefill_tps": (
                combined["prefill_tps"]["sum"] / combined["prefill_tps"]["weight"]
                if "prefill_tps" in combined else None
            ),
            "decode_tps": (
                combined["decode_tps"]["sum"] / combined["decode_tps"]["weight"]
                if "decode_tps" in combined else None
            ),
            "active_seconds": active_seconds,
            "load_known": load_known,
        }

    async def aggregates(
        self, *, start: datetime, end: datetime, profile_name: str | None = None,
        pid: int | None = None, all_profiles: bool = False,
    ) -> dict[str, dict[str, float]]:
        """Use shared window rules and stream SQLite snapshots."""
        summary = WindowSummary(start, end, all_profiles)
        query = select(MetricSample).where(
            MetricSample.observed_at >= start, MetricSample.observed_at <= end,
        ).order_by(MetricSample.observed_at, MetricSample.id)
        if profile_name is not None:
            query = query.where(MetricSample.profile_name == profile_name)
        if pid is not None:
            query = query.where(MetricSample.pid == pid)
        async with self.sessions() as session:
            rows = await session.stream_scalars(query.execution_options(yield_per=256))
            async for row in rows:
                summary.add(sample_data(row))
        return summary.result()

    async def daily_runs(self) -> list[dict[str, str | None]]:
        """List today's metric owners without loading samples or folding archives."""
        now = datetime.now(UTC)
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        query = select(
            MetricSample.profile_name, MetricSample.model_name, MetricSample.engine_id,
        ).where(
            MetricSample.observed_at >= start,
            MetricSample.observed_at <= now,
            MetricSample.profile_name.is_not(None),
            (MetricSample.decode_tps.is_not(None) | MetricSample.prefill_tps.is_not(None)),
        ).distinct()
        async with self.sessions() as session:
            rows = (await session.execute(query)).all()
        return [{"profile_name": profile, "model_name": model, "engine_id": engine}
                for profile, model, engine in rows]

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
