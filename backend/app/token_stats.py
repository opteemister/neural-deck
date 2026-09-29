"""Request-journal persistence and token aggregation."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from .journal import RequestJournalRecord
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
    ) -> None:
        async with self._session_factory() as session:
            session.add(
                RequestJournalRecord(
                    # The client supplied request ID is correlation metadata,
                    # not a database identity: retries and concurrent clients
                    # are allowed to reuse it.
                    id=f"journal_{uuid4().hex}",
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
                )
            )
            await session.commit()

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
            self.last_error = str(exc)
            logger.exception("Request journal write failed")
            return False
        self.last_error = None
        return True


__all__ = ["RequestJournalService"]
