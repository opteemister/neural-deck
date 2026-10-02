"""SQLite engine history; request/client tables are never pruned here."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ..schemas.instances import Instance, Metric, MetricList
from .entities import MetricLaunchExtrema, MetricSample
from .performance import FIELDS, UNITS


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

    async def write(
        self,
        samples: list[tuple[Instance, MetricList]],
        peaks: dict[tuple[str, int], dict[str, float]],
    ) -> None:
        now = datetime.now(UTC)
        async with self.sessions() as session:
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

    async def cleanup(self, active: set[tuple[str, int]]) -> None:
        cutoff = datetime.now(UTC) - timedelta(hours=24)
        async with self.sessions() as session:
            await session.execute(delete(MetricSample).where(MetricSample.observed_at < cutoff))
            rows = (
                await session.scalars(
                    select(MetricLaunchExtrema).where(MetricLaunchExtrema.observed_at < cutoff)
                )
            ).all()
            for row in rows:
                if (row.profile_name, row.pid) not in active:
                    await session.delete(row)
            await session.commit()
