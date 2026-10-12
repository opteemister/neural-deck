"""Request-journal persistence and token aggregation."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from sqlalchemy import Integer, case, cast, func, select, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from .journal import ApiClientRecord, RequestJournalRecord, journal_record_values
from .schemas.instances import TokenCounts, TokenStats, TokenStatsData

logger = logging.getLogger(__name__)


class RequestJournalService:
    """Store proxy metadata and derive per-profile token statistics.

    ``None`` token values intentionally remain unknown.  Aggregation therefore
    counts unknown requests separately instead of turning them into zeroes.
    """

    def __init__(
        self,
        engine: AsyncEngine | None = None,
        *,
        session_factory: async_sessionmaker[AsyncSession] | None = None,
    ) -> None:
        if engine is None and session_factory is None:
            raise ValueError("engine or session_factory is required")
        self._session_factory = session_factory or async_sessionmaker(
            engine, expire_on_commit=False
        )
        self.last_error: str | None = None

    async def record(
        self,
        *,
        request_id: str,
        route: str,
        model_name: str,
        profile_name: str | None,
        instance_id: str | None,
        engine_id: str | None,
        status_code: int | None,
        latency_ms: float,
        input_tokens: int | None,
        output_tokens: int | None,
        streaming: bool = False,
        error_code: str | None = None,
        created_at: datetime,
        journal_id: str | None = None,
        **extra: Any,
    ) -> None:
        async with self._session_factory() as session:
            existing = await session.get(RequestJournalRecord, journal_id) if journal_id else None
            if existing is not None:
                for key, value in {
                    "profile_name": profile_name,
                    "instance_id": instance_id,
                    "engine_id": engine_id,
                    "status_code": status_code,
                    "latency_ms": max(0.0, latency_ms),
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                    "error_code": error_code,
                    "streaming": int(streaming),
                    **extra,
                }.items():
                    setattr(existing, key, value)
                await session.commit()
                return
            session.add(
                RequestJournalRecord(
                    # The client supplied request ID is correlation metadata,
                    # not a database identity: retries and concurrent clients
                    # are allowed to reuse it.
                    id=journal_id or f"journal_{uuid4().hex}",
                    request_id=request_id or f"req_{uuid4().hex}",
                    created_at=created_at,
                    route=route,
                    model_name=model_name,
                    profile_name=profile_name,
                    instance_id=instance_id,
                    engine_id=engine_id,
                    status_code=status_code,
                    latency_ms=max(0.0, latency_ms),
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    streaming=1 if streaming else 0,
                    error_code=error_code,
                    **extra,
                )
            )
            await session.commit()

    async def identify_client(self, authorization: str | None, ip: str | None) -> int | None:
        import ipaddress

        parts = (authorization or "").split(None, 1)
        key = parts[1].strip() if len(parts) == 2 and parts[0].lower() == "bearer" else None
        try:
            ip = str(ipaddress.ip_address(ip)) if ip else None
        except ValueError:
            ip = None
        if not key and not ip:
            return None
        now = datetime.now(UTC)
        async with self._session_factory() as session:
            # SQLite serializes the lookup, insert and JSON update as one write.
            await session.execute(text("BEGIN IMMEDIATE"))
            predicate = (
                ApiClientRecord.api_key == key if key else ApiClientRecord.anonymous_ip == ip
            )
            row = await session.scalar(select(ApiClientRecord).where(predicate))
            if row is None:
                row = ApiClientRecord(
                    api_key=key or None,
                    anonymous_ip=None if key else ip,
                    ip_addresses=[ip] if ip else [],
                    created_at=now,
                    last_seen_at=now,
                )
                session.add(row)
            else:
                row.last_seen_at = now
                if ip and ip not in row.ip_addresses:
                    row.ip_addresses = [*row.ip_addresses, ip]
            await session.commit()
            return row.id

    async def client_labels(self) -> dict[int, str]:
        """Resolve journal client IDs to their API token or anonymous IP."""

        async with self._session_factory() as session:
            clients = await session.scalars(select(ApiClientRecord))
            return {
                client.id: client.api_key or client.anonymous_ip or "Неизвестный клиент"
                for client in clients
            }

    async def records(self) -> list[RequestJournalRecord]:
        """Return journal rows for read-only aggregate/reporting services."""

        async with self._session_factory() as session:
            return list(
                (
                    await session.scalars(
                        select(RequestJournalRecord).order_by(RequestJournalRecord.created_at)
                    )
                ).all()
            )

    async def interrupt_pending(self) -> None:
        from sqlalchemy import update

        async with self._session_factory() as session:
            await session.execute(
                update(RequestJournalRecord)
                .where(
                    RequestJournalRecord.started_at.is_not(None),
                    RequestJournalRecord.finished_at.is_(None),
                )
                .values(error_code="interrupted", finished_at=datetime.now(UTC))
            )
            await session.commit()

    async def token_history(
        self, *, profile_name: str | None = None, pid: int | None = None
    ) -> dict[str, Any]:
        now = datetime.now(UTC)
        # 23 complete clock hours plus the current hour; boundaries stay put
        # during polling and advance only when the next hour starts.
        end = now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
        start = end - timedelta(hours=24)
        event_time = func.coalesce(
            RequestJournalRecord.usage_received_at,
            RequestJournalRecord.finished_at,
        )
        bucket = cast((func.strftime("%s", event_time) - start.timestamp()) / 3600, Integer)
        fields = {
            "input_tokens": RequestJournalRecord.input_tokens,
            "output_tokens": RequestJournalRecord.output_tokens,
            "cached_tokens": RequestJournalRecord.cached_input_tokens,
        }
        columns = [bucket.label("bucket"), func.count().label("requests")]
        for name, field in fields.items():
            columns.extend([
                func.sum(func.coalesce(field, 0)).label(name),
                func.sum(case((field.is_(None), 1), else_=0)).label(f"{name}_unknown"),
            ])
        query = select(*columns).where(
            RequestJournalRecord.finished_at.is_not(None),
            event_time >= start,
            event_time < now,
        ).group_by(bucket)
        if profile_name is not None:
            query = query.where(RequestJournalRecord.profile_name == profile_name)
        if pid is not None:
            query = query.where(RequestJournalRecord.pid == pid)
        async with self._session_factory() as session:
            rows = {row["bucket"]: dict(row) for row in (await session.execute(query)).mappings()}
        items = []
        for index in range(24):
            row = rows.get(index, {"requests": 0})
            item = {"at": start + timedelta(hours=index), "requests": row["requests"]}
            for name in fields:
                unknown = row.get(f"{name}_unknown", 0)
                item[name] = (
                    None if row["requests"] > 0 and unknown == row["requests"] else row.get(name, 0)
                )
                item[f"{name}_unknown"] = unknown
            items.append(item)
        return {
            "items": items, "from": start, "to": end,
            "bucket_minutes": 60, "source": "api-journal",
        }

    async def usage(
        self,
        *,
        profile_name: str | None = None,
        pid: int | None = None,
        client_id: int | None = None,
        from_: datetime | None = None,
        to: datetime | None = None,
        limit: int = 120,
        offset: int = 0,
    ) -> dict[str, Any]:
        event_time = func.coalesce(
            RequestJournalRecord.usage_received_at,
            RequestJournalRecord.finished_at,
            RequestJournalRecord.created_at,
        )
        query = select(RequestJournalRecord)
        for field, value in (
            ("profile_name", profile_name),
            ("pid", pid),
            ("client_id", client_id),
        ):
            if value is not None:
                query = query.where(getattr(RequestJournalRecord, field) == value)
        if from_ is not None:
            query = query.where(event_time >= from_)
        if to is not None:
            query = query.where(event_time <= to)
        async with self._session_factory() as session:
            rows = (
                await session.scalars(
                    query.order_by(event_time.desc(), RequestJournalRecord.id)
                    .offset(offset)
                    .limit(limit + 1)
                )
            ).all()
        fields = (
            "pid",
            "client_id",
            "client_ip",
            "cached_input_tokens",
            "started_at",
            "finished_at",
            "usage_received_at",
            "usage_source",
            "ttft_ms",
            "queue_time_ms",
            "prefill_time_ms",
            "generation_time_ms",
            "prefill_tps",
            "decode_tps",
            "prefix_cache_hit_percent",
            "spec_acceptance_percent",
            "metric_meta",
        )
        return {
            "items": [
                {**journal_record_values(row), **{field: getattr(row, field) for field in fields}}
                for row in rows[:limit]
            ],
            "next_offset": offset + limit if len(rows) > limit else None,
            "source": "sqlite",
        }

    async def latest_latency(self, instance_id: str) -> float | None:
        async with self._session_factory() as session:
            value = await session.scalar(
                select(RequestJournalRecord.latency_ms)
                .where(RequestJournalRecord.instance_id == instance_id)
                .order_by(RequestJournalRecord.created_at.desc())
                .limit(1)
            )
        return float(value) if value is not None else None

    async def stats(
        self,
        *,
        profile_name: str | None = None,
        from_: datetime | None = None,
        to: datetime | None = None,
    ) -> TokenStatsData:
        conditions: list[Any] = []
        if profile_name is not None:
            conditions.append(RequestJournalRecord.profile_name == profile_name)
        if from_ is not None:
            conditions.append(RequestJournalRecord.created_at >= from_)
        if to is not None:
            conditions.append(RequestJournalRecord.created_at <= to)
        profile = func.coalesce(RequestJournalRecord.profile_name, "external").label("profile_name")
        input_known = func.sum(RequestJournalRecord.input_tokens).label("input_known")
        output_known = func.sum(RequestJournalRecord.output_tokens).label("output_known")
        input_unknown = func.sum(
            case((RequestJournalRecord.input_tokens.is_(None), 1), else_=0)
        ).label("input_unknown")
        output_unknown = func.sum(
            case((RequestJournalRecord.output_tokens.is_(None), 1), else_=0)
        ).label("output_unknown")
        query = (
            select(
                profile,
                func.count(RequestJournalRecord.id).label("requests"),
                input_known,
                input_unknown,
                output_known,
                output_unknown,
            )
            .where(*conditions)
            .group_by(profile)
            .order_by(profile)
        )
        async with self._session_factory() as session:
            rows = (await session.execute(query)).all()
        return TokenStatsData(
            items=[
                TokenStats(
                    profile_name=str(row.profile_name),
                    requests=int(row.requests or 0),
                    input_tokens=TokenCounts(
                        known_total=int(row.input_known) if row.input_known is not None else None,
                        unknown_requests=int(row.input_unknown or 0),
                    ),
                    output_tokens=TokenCounts(
                        known_total=int(row.output_known) if row.output_known is not None else None,
                        unknown_requests=int(row.output_unknown or 0),
                    ),
                    source="openai_proxy",
                )
                for row in rows
            ]
        )

    async def record_safely(self, **kwargs: Any) -> bool:
        """Best-effort journal write used on the proxy response path.

        A journal outage must not fail an inference response, but it must be
        visible to operators through logs and status diagnostics.
        """

        try:
            await self.record(**kwargs)
        except Exception as exc:  # noqa: BLE001 - persistence boundary
            self.last_error = type(exc).__name__
            logger.error("Request journal write failed: %s", type(exc).__name__)
            return False
        self.last_error = None
        return True


__all__ = ["RequestJournalService"]
