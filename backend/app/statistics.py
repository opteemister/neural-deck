"""UTC request statistics for the observability page."""

from __future__ import annotations

import asyncio
import math
from calendar import monthrange
from collections import defaultdict
from datetime import UTC, date, datetime, timedelta
from typing import Any

from .journal import RequestJournalRecord
from .profiles.service import ProfileService
from .schemas.profiles import ProfileData
from .token_stats import RequestJournalService

DEFAULT_PERIOD_DAYS = 30
PERIOD_DAYS = {"week": 7, "month": 30}


def as_utc(value: datetime) -> datetime:
    """Treat legacy SQLite-naive timestamps as UTC and normalize all others."""

    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def period_window(
    *, now: datetime | None = None, days: int = DEFAULT_PERIOD_DAYS
) -> tuple[datetime, datetime]:
    current = as_utc(now or datetime.now(UTC))
    end = current
    start = current.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=days - 1)
    return start, end


def _event_time(row: RequestJournalRecord) -> datetime:
    """Put terminal requests in the finish bucket and unfinished ones in start."""

    if row.finished_at is not None:
        return as_utc(row.finished_at)
    return as_utc(row.started_at or row.created_at)


def _profile_key(row: RequestJournalRecord) -> str:
    return row.profile_name or "external"


def _token_metric(
    rows: list[RequestJournalRecord], field: str, *, subset_of: str | None = None
) -> dict[str, int | None]:
    values = [getattr(row, field) for row in rows]
    known: list[int] = []
    unknown = 0
    for row, value in zip(rows, values, strict=True):
        valid = isinstance(value, int) and value >= 0
        if valid and subset_of is not None:
            parent = getattr(row, subset_of)
            valid = isinstance(parent, int) and value <= parent
        if valid:
            known.append(value)
        else:
            unknown += 1
    return {
        "known_total": sum(known) if known or not rows else None,
        "unknown_requests": unknown,
    }


def _status_counts(rows: list[RequestJournalRecord]) -> dict[str, int]:
    result = {"total": len(rows), "success": 0, "failed": 0, "interrupted": 0, "unknown": 0}
    for row in rows:
        if row.error_code == "interrupted":
            result["interrupted"] += 1
        elif row.error_code or (row.status_code is not None and row.status_code >= 400):
            result["failed"] += 1
        elif row.status_code is None:
            result["unknown"] += 1
        else:
            result["success"] += 1
    return result


def _average(rows: list[RequestJournalRecord], field: str) -> float | None:
    values = [getattr(row, field) for row in rows]
    numbers = [float(value) for value in values if isinstance(value, (int, float))]
    return sum(numbers) / len(numbers) if numbers else None


def _max_concurrency(rows: list[RequestJournalRecord], profile: str) -> int:
    events: list[tuple[datetime, int]] = []
    now = datetime.now(UTC)
    for row in rows:
        if _profile_key(row) != profile or row.started_at is None:
            continue
        started = as_utc(row.started_at)
        finished = as_utc(row.finished_at) if row.finished_at else now
        if finished <= started:
            continue
        events.extend(((started, 1), (finished, -1)))
    active = maximum = 0
    for _, delta in sorted(events, key=lambda item: (item[0], item[1])):
        active += delta
        maximum = max(maximum, active)
    return maximum


def _load(
    rows: list[RequestJournalRecord],
    profile: str,
    start: datetime,
    end: datetime,
    maximum: int,
) -> dict[str, Any]:
    if maximum <= 0 or end <= start:
        return {
            "percent": 0.0,
            "active_seconds": 0.0,
            "period_seconds": max(0.0, (end - start).total_seconds()),
            "max_concurrency": maximum,
            "basis": "active_requests",
            "state": "ok",
        }
    events: list[tuple[datetime, int]] = [(start, 0), (end, 0)]
    now = datetime.now(UTC)
    for row in rows:
        if _profile_key(row) != profile or row.started_at is None:
            continue
        left = max(start, as_utc(row.started_at))
        right = min(end, as_utc(row.finished_at) if row.finished_at else now)
        if right > left:
            events.extend(((left, 1), (right, -1)))
    active = 0
    active_seconds = 0.0
    previous = start
    for at, delta in sorted(events, key=lambda item: (item[0], item[1])):
        if at > previous:
            active_seconds += (at - previous).total_seconds() * active
        active += delta
        previous = at
    period_seconds = (end - start).total_seconds()
    percent = active_seconds / period_seconds / maximum * 100 if period_seconds else 0.0
    return {
        "percent": round(percent, 3),
        "active_seconds": round(active_seconds, 3),
        "period_seconds": round(period_seconds, 3),
        "max_concurrency": maximum,
        "basis": "active_requests",
        "state": "ok",
    }


def _prices(profile: ProfileData | None) -> dict[str, float | None]:
    if profile is None:
        return {"input": None, "cached_input": None, "output": None}
    return {
        "input": profile.input_price_usd_per_million_tokens,
        "cached_input": profile.cached_input_price_usd_per_million_tokens,
        "output": profile.output_price_usd_per_million_tokens,
    }


def _cost(rows: list[RequestJournalRecord], profile: ProfileData | None) -> dict[str, Any]:
    rates = _prices(profile)
    total = 0.0
    unknown_requests = 0
    for row in rows:
        if (
            row.input_tokens is None
            or row.cached_input_tokens is None
            or row.output_tokens is None
            or row.cached_input_tokens < 0
            or row.cached_input_tokens > row.input_tokens
        ):
            unknown_requests += 1
            continue
        if any(rate is None for rate in rates.values()):
            return {
                "usd": None,
                "state": "unavailable",
                "unknown_requests": unknown_requests,
                "prices": rates,
            }
        total += (
            max(0, row.input_tokens - row.cached_input_tokens)
            * float(rates["input"] or 0)
            / 1_000_000
            + row.cached_input_tokens * float(rates["cached_input"] or 0) / 1_000_000
            + row.output_tokens * float(rates["output"] or 0) / 1_000_000
        )
    if unknown_requests:
        return {
            "usd": None,
            "state": "unknown",
            "unknown_requests": unknown_requests,
            "prices": rates,
        }
    return {"usd": round(total, 8), "state": "ok", "unknown_requests": 0, "prices": rates}


def _cost_by_profile(
    rows: list[RequestJournalRecord], profiles: dict[str, ProfileData]
) -> dict[str, Any]:
    """Calculate a mixed-profile total without pretending one price is global."""

    total = 0.0
    unknown_requests = 0
    for row in rows:
        profile = profiles.get(_profile_key(row))
        rates = _prices(profile)
        if (
            row.input_tokens is None
            or row.cached_input_tokens is None
            or row.output_tokens is None
            or row.cached_input_tokens < 0
            or row.cached_input_tokens > row.input_tokens
        ):
            unknown_requests += 1
            continue
        if any(rate is None for rate in rates.values()):
            return {
                "usd": None,
                "state": "unavailable",
                "unknown_requests": unknown_requests,
                "prices": {"input": None, "cached_input": None, "output": None},
            }
        total += (
            max(0, row.input_tokens - row.cached_input_tokens)
            * float(rates["input"] or 0)
            / 1_000_000
            + row.cached_input_tokens * float(rates["cached_input"] or 0) / 1_000_000
            + row.output_tokens * float(rates["output"] or 0) / 1_000_000
        )
    return {
        "usd": None if unknown_requests else round(total, 8),
        "state": "unknown" if unknown_requests else "ok",
        "unknown_requests": unknown_requests,
        "prices": {"input": None, "cached_input": None, "output": None},
    }


def _aggregate(rows: list[RequestJournalRecord], profile: ProfileData | None) -> dict[str, Any]:
    latencies = sorted(row.latency_ms for row in rows if row.finished_at is not None and row.latency_ms >= 0)
    return {
        "requests": _status_counts(rows),
        "tokens": {
            "input": _token_metric(rows, "input_tokens"),
            "cached_input": _token_metric(
                rows, "cached_input_tokens", subset_of="input_tokens"
            ),
            "output": _token_metric(rows, "output_tokens"),
        },
        "performance": {
            "latency_p95_ms": latencies[math.ceil(len(latencies) * 0.95) - 1] if latencies else None,
            "latency_ms": sum(latencies) / len(latencies) if latencies else None,
            "prefill_tps": _average(rows, "prefill_tps"),
            "decode_tps": _average(rows, "decode_tps"),
        },
        "cost": _cost(rows, profile),
    }


def _activity(
    rows: list[RequestJournalRecord], start: datetime, end: datetime
) -> list[dict[str, Any]]:
    first = start.date()
    last = (end - timedelta(microseconds=1)).date()
    buckets: dict[date, list[RequestJournalRecord]] = defaultdict(list)
    for row in rows:
        at = _event_time(row)
        if start <= at < end:
            buckets[at.date()].append(row)
    items: list[dict[str, Any]] = []
    cursor = first
    while cursor <= last:
        bucket = buckets.get(cursor, [])
        aggregate = _aggregate(bucket, None)
        items.append({"date": cursor.isoformat(), **aggregate, "benchmark": False})
        cursor += timedelta(days=1)
    return items


class StatisticsService:
    def __init__(self, journal: RequestJournalService, profiles: ProfileService) -> None:
        self.journal = journal
        self.profiles = profiles

    async def read(
        self,
        *,
        period: str = "month",
        from_: datetime | None = None,
        to: datetime | None = None,
        model_name: str | None = None,
        profile_name: str | None = None,
    ) -> dict[str, Any]:
        if period == "custom" and from_ is not None and to is not None:
            start, end = as_utc(from_), as_utc(to)
        elif period == "year":
            end = datetime.now(UTC)
            today = end.replace(hour=0, minute=0, second=0, microsecond=0)
            start = today - timedelta(days=today.weekday() + 52 * 7)
        elif period in {"quarter", "half_year"}:
            end = datetime.now(UTC)
            months = 3 if period == "quarter" else 6
            index = end.year * 12 + end.month - 1 - months
            year, month = divmod(index, 12)
            start = end.replace(year=year, month=month + 1, day=min(end.day, monthrange(year, month + 1)[1]), hour=0, minute=0, second=0, microsecond=0)
        else:
            start, end = period_window(days=PERIOD_DAYS.get(period, DEFAULT_PERIOD_DAYS))
        if end <= start:
            raise ValueError("граница to должна быть позже from")

        all_rows = await self.journal.records()
        configured = await asyncio.to_thread(self.profiles.list)
        profile_map = {profile.name: profile for profile in configured.items}
        period_rows = [
            row for row in all_rows
            if start <= _event_time(row) < end
            and (model_name is None or row.model_name == model_name)
            and (profile_name is None or _profile_key(row) == profile_name)
        ]
        all_names = set(profile_map)
        all_names.update(_profile_key(row) for row in all_rows)
        names = set(all_names)
        if model_name is not None:
            names = {
                name for name, profile in profile_map.items() if profile.model_name == model_name
            }
            names.update(_profile_key(row) for row in period_rows)
        if profile_name is not None:
            names = {profile_name}
        profiles: list[dict[str, Any]] = []
        for name in sorted(names):
            rows = [row for row in period_rows if _profile_key(row) == name]
            profile = profile_map.get(name)
            item = _aggregate(rows, profile)
            item.update({
                "profile_name": name,
                "model_name": (
                    profile.model_name
                    if profile
                    else sorted({row.model_name for row in rows})[0]
                    if rows
                    else None
                ),
                "load": _load(all_rows, name, start, end, _max_concurrency(all_rows, name)),
            })
            profiles.append(item)

        client_rows: dict[str, list[RequestJournalRecord]] = defaultdict(list)
        for row in period_rows:
            client_rows[str(row.client_id) if row.client_id is not None else "unknown"].append(row)
        clients = []
        for client_id, rows in sorted(client_rows.items()):
            item = _aggregate(rows, None)
            item["cost"] = _cost_by_profile(rows, profile_map)
            item.update({
                "client_id": None if client_id == "unknown" else int(client_id),
                "label": "Неизвестный клиент" if client_id == "unknown" else f"Клиент #{client_id}",
                "profiles": sorted({_profile_key(row) for row in rows}),
            })
            clients.append(item)

        totals = _aggregate(period_rows, None)
        totals["cost"] = _cost_by_profile(period_rows, profile_map)
        activity = _activity(period_rows, start, end)
        active_dates = {_event_time(row).date() for row in period_rows}
        cursor = end.date()
        if cursor not in active_dates:
            cursor -= timedelta(days=1)
        streak = 0
        while cursor in active_dates:
            streak += 1
            cursor -= timedelta(days=1)
        return {
            "period": {
                "name": period,
                "from": start,
                "to": end,
                "timezone": "UTC",
                "boundary": "[from,to)",
                "bucket": "day",
                "request_event_time": "finished_at_else_started_at",
            },
            "filters": {"model_name": model_name, "profile_name": profile_name},
            "filters_options": {
                "models": sorted({row.model_name for row in all_rows}),
                "profiles": sorted(all_names),
            },
            "totals": totals,
            "summary": {
                "sessions": None,
                "sessions_state": "not_recorded",
                "active_days": len(active_dates),
                "period_days": len(activity),
                "streak_days": streak,
                "success_rate": totals["requests"]["success"] / len(period_rows) * 100 if period_rows else None,
            },
            "activity": activity,
            "profiles": profiles,
            "clients": clients,
            "benchmarks": {"intervals": [], "traffic_included": True, "state": "not_recorded"},
            "source": "sqlite.request_journal",
        }


__all__ = ["StatisticsService", "period_window"]
