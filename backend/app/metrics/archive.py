"""Mergeable daily statistics and original request histograms."""

from __future__ import annotations

import asyncio
import copy
import json
import math
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..schemas.instances import Metric
from .entities import MetricArchiveCheckpoint, MetricConcurrencyDaily, MetricDaily, MetricSample

FIELDS = (
    "prefill_tps", "decode_tps", "ttft_mean_ms", "active_requests", "queue_requests",
    "queue_time_mean_ms", "kv_cache_percent", "prefix_cache_hit_percent",
    "spec_acceptance_percent",
)


def capture_distributions(items: list[Metric]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for item in items:
        if item.state != "ok" or not isinstance(item.value, (int, float)):
            continue
        if item.metadata.get("type") != "histogram":
            continue
        suffix = next((s for s in ("_bucket", "_sum", "_count") if item.name.endswith(s)), None)
        if suffix is None:
            continue
        name = item.name[:-len(suffix)]
        if not any(word in name for word in (
            "time_to_first_token", "prefill_time", "queue_time", "decode_time",
            "request_prompt_tokens", "request_generation_tokens", "prefill_kv_computed_tokens",
        )):
            continue
        labels = {k: v for k, v in item.metadata.get("labels", {}).items() if k != "le"}
        key = json.dumps([name, labels], sort_keys=True)
        entry = result.setdefault(key, {
            "name": name, "labels": labels, "at": item.observed_at.isoformat(), "buckets": {},
        })
        if suffix == "_bucket":
            bound = item.metadata.get("labels", {}).get("le")
            if bound is not None:
                entry["buckets"][bound] = float(item.value)
        else:
            entry[suffix[1:]] = float(item.value)
    return {k: v for k, v in result.items() if "sum" in v and "count" in v and v["buckets"]}


def percentile(buckets: dict[str, float], q: float, *, cumulative: bool) -> float | None:
    ordered = sorted((float(bound), count) for bound, count in buckets.items())
    if not ordered:
        return None
    total = ordered[-1][1] if cumulative else sum(count for _, count in ordered)
    if total <= 0:
        return None
    rank, previous_count, lower = total * q, 0.0, 0.0
    for upper, count in ordered:
        current = count if cumulative else previous_count + count
        if current >= rank:
            if not math.isfinite(upper):
                return lower if lower > 0 else None
            if not cumulative:
                lower = upper / 1.05
            return lower + (upper - lower) * (rank - previous_count) / (current - previous_count)
        previous_count, lower = current, upper
    return None


def add_measurement(stats: dict[str, Any], value: float, weight: float) -> None:
    stats["sum"] = stats.get("sum", 0.0) + value * weight
    stats["weight"] = stats.get("weight", 0.0) + weight
    stats["count"] = stats.get("count", 0) + 1
    stats["max"] = max(stats.get("max", value), value)
    # Fixed 5% logarithmic buckets are bounded and mergeable across days.
    bound = str(1.05 ** math.ceil(math.log(value, 1.05)))
    histogram = stats.setdefault("buckets", {})
    histogram[bound] = histogram.get(bound, 0.0) + weight


def summary(stats: dict[str, Any]) -> dict[str, Any]:
    return {
        "avg": stats["sum"] / stats["weight"], "max": stats["max"],
        "count": stats["count"], "weight": stats["weight"],
        **{f"p{p}": percentile(stats["buckets"], p / 100, cumulative=False)
           for p in (10, 50, 95)},
        "percentile_source": "sample_histogram_5_percent",
    }


def fold(row: MetricSample, daily: dict, curves: dict, baselines: dict) -> None:
    if row.profile_name is None:
        return
    at = row.observed_at.replace(tzinfo=UTC) if row.observed_at.tzinfo is None else row.observed_at
    identity = (row.profile_name, row.model_name or "", row.engine_id)
    key = (at.astimezone(UTC).date().isoformat(), *identity)
    day = daily.setdefault(key, {"statistics": {}, "distributions": {}})
    run = json.dumps([*identity, row.pid])
    state = baselines.setdefault(run, {"seen": {}, "histograms": {}})
    state["last_at"] = at.isoformat()
    # Integrate engine concurrency only between fresh observations. Never
    # stretch an active sample over downtime or gaps in monitoring.
    active_meta = row.metric_meta.get("active_requests", {})
    active_at = datetime.fromisoformat(active_meta.get("observed_at", at.isoformat()))
    active_at = active_at.replace(tzinfo=UTC) if active_at.tzinfo is None else active_at.astimezone(UTC)
    previous_active = state.get("active_time")
    valid_active = (active_meta.get("state") == "ok" and row.active_requests is not None
                    and row.active_requests >= 0 and abs((at - active_at).total_seconds()) <= 5)
    if valid_active:
        if previous_active is not None:
            left = datetime.fromisoformat(previous_active["at"])
            elapsed = (active_at - left).total_seconds()
            if 0 < elapsed <= 15 and previous_active["requests"] > 0:
                while left < active_at:
                    right = min(active_at, left.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1))
                    interval_key = (left.date().isoformat(), *identity)
                    interval_day = daily.setdefault(interval_key, {"statistics": {}, "distributions": {}})
                    add_measurement(interval_day["statistics"].setdefault("active_requests_time", {}),
                                    previous_active["requests"], (right - left).total_seconds())
                    left = right
        if previous_active is None or active_at > datetime.fromisoformat(previous_active["at"]):
            state["active_time"] = {"at": active_at.isoformat(), "requests": row.active_requests}
    else:
        state.pop("active_time", None)
    for field in FIELDS:
        value = getattr(row, field)
        meta = row.metric_meta.get(field, {})
        measured = meta.get("observed_at", at.isoformat())
        if (value is None or value <= 0 or meta.get("state") != "ok"
                or state["seen"].get(field) == measured):
            continue
        state["seen"][field] = measured
        native = meta.get("metadata", {}).get("count" if field in {
            "ttft_mean_ms", "queue_time_mean_ms"
        } else "denominator")
        weight = native if isinstance(native, (int, float)) and native > 0 else 1.0
        add_measurement(day["statistics"].setdefault(field, {}), value, weight)
    for name, current in (row.distributions or {}).items():
        previous = state["histograms"].get(name)
        state["histograms"][name] = current
        if previous is None or previous["at"] == current["at"]:
            continue
        if set(current["buckets"]) != set(previous["buckets"]):
            continue
        delta = {k: current[k] - previous[k] for k in ("sum", "count")}
        buckets = {k: v - previous["buckets"][k] for k, v in current["buckets"].items()}
        counts = [count for _, count in sorted((float(k), v) for k, v in buckets.items())]
        if (delta["count"] <= 0 or delta["sum"] < 0 or any(v < 0 for v in buckets.values())
                or counts != sorted(counts) or counts[-1] != delta["count"]):
            continue
        schema = json.dumps([current["name"], sorted(buckets, key=float)])
        target = day["distributions"].setdefault(schema, {
            "name": current["name"], "sum": 0.0, "count": 0.0,
            "buckets": dict.fromkeys(buckets, 0.0),
        })
        for field in ("sum", "count"):
            target[field] += delta[field]
        for bound, value in buckets.items():
            target["buckets"][bound] += value
    current_n, speed = row.active_requests, row.decode_tps
    previous = state.get("concurrency")
    state["concurrency"] = {"at": at.isoformat(), "requests": current_n}
    speed_meta = row.metric_meta.get("decode_tps", {})
    requests_meta = row.metric_meta.get("active_requests", {})
    if (previous is None or speed is None or speed <= 0 or current_n is None
            or current_n <= 0 or not float(current_n).is_integer()
            or previous["requests"] != current_n or speed_meta.get("state") != "ok"
            or requests_meta.get("state") != "ok"):
        return
    elapsed = (at - datetime.fromisoformat(previous["at"])).total_seconds()
    measured = datetime.fromisoformat(speed_meta.get("observed_at", at.isoformat()))
    measured = measured.replace(tzinfo=UTC) if measured.tzinfo is None else measured
    requests_at = datetime.fromisoformat(requests_meta.get("observed_at", at.isoformat()))
    requests_at = requests_at.replace(tzinfo=UTC) if requests_at.tzinfo is None else requests_at
    if (elapsed <= 0 or elapsed > 15 or abs((at - measured).total_seconds()) > 5
            or abs((at - requests_at).total_seconds()) > 5
            or state.get("curve_seen") == measured.isoformat()):
        return
    state["curve_seen"] = measured.isoformat()
    stats = curves.setdefault((*key, int(current_n)), {})
    add_measurement(stats, speed, elapsed)
    add_measurement(day["statistics"].setdefault("per_request_tps", {}), speed / current_n, elapsed)


async def load_and_fold(
    session: AsyncSession, *, first_day: str | None = None, profile_name: str | None = None,
    end: datetime | None = None,
) -> tuple[dict, dict, dict, int]:
    checkpoint = await session.get(MetricArchiveCheckpoint, 1)
    cursor = checkpoint.last_sample_id if checkpoint else 0
    baselines = copy.deepcopy(checkpoint.baselines) if checkpoint else {}
    query = select(MetricSample).where(MetricSample.id > cursor).order_by(MetricSample.id)
    if profile_name is not None:
        query = query.where(MetricSample.profile_name == profile_name)
    if end is not None:
        query = query.where(MetricSample.observed_at < end)
    rows = (await session.scalars(query)).all()
    touched_days = {
        (r.observed_at.replace(tzinfo=UTC) if r.observed_at.tzinfo is None
        else r.observed_at).astimezone(UTC).date().isoformat() for r in rows
    }
    # A short observed interval can cross midnight into the preceding day.
    touched_days.update((datetime.fromisoformat(day).date() - timedelta(days=1)).isoformat()
                        for day in list(touched_days))
    daily_query, curve_query = select(MetricDaily), select(MetricConcurrencyDaily)
    if first_day is not None:
        daily_query = daily_query.where(MetricDaily.day >= first_day)
        curve_query = curve_query.where(MetricConcurrencyDaily.day >= first_day)
    else:
        daily_query = daily_query.where(MetricDaily.day.in_(touched_days))
        curve_query = curve_query.where(MetricConcurrencyDaily.day.in_(touched_days))
    if profile_name is not None:
        daily_query = daily_query.where(MetricDaily.profile_name == profile_name)
        curve_query = curve_query.where(MetricConcurrencyDaily.profile_name == profile_name)
    daily = {
        (r.day, r.profile_name, r.model_name, r.engine_id): {
            "statistics": copy.deepcopy(r.statistics),
            "distributions": copy.deepcopy(r.distributions),
        } for r in (await session.scalars(daily_query)).all()
    }
    curves = {
        (r.day, r.profile_name, r.model_name, r.engine_id, r.requests): copy.deepcopy(r.statistics)
        for r in (await session.scalars(curve_query)).all()
    }
    def fold_pending() -> int:
        last_id = cursor
        for row in rows:
            fold(row, daily, curves, baselines)
            last_id = row.id
        return last_id

    cursor = await asyncio.to_thread(fold_pending)
    cutoff = (datetime.now(UTC) - timedelta(hours=48)).isoformat()
    baselines = {k: v for k, v in baselines.items() if v.get("last_at", "") >= cutoff}
    return daily, curves, baselines, cursor


async def archive_before_cleanup(session: AsyncSession) -> int:
    daily, curves, baselines, cursor = await load_and_fold(session)
    for key, value in daily.items():
        await session.merge(MetricDaily(
            day=key[0], profile_name=key[1], model_name=key[2], engine_id=key[3], **value
        ))
    for key, stats in curves.items():
        await session.merge(MetricConcurrencyDaily(
            day=key[0], profile_name=key[1], model_name=key[2], engine_id=key[3],
            requests=key[4], statistics=stats,
        ))
    await session.merge(MetricArchiveCheckpoint(id=1, last_sample_id=cursor, baselines=baselines))
    await session.flush()
    return cursor


async def historical_statistics(session: AsyncSession, *, profile_name: str | None,
                                days: int, curve: bool) -> dict[str, Any]:
    first_day = (datetime.now(UTC).date() - timedelta(days=days - 1)).isoformat()
    daily, curves, _, _ = await load_and_fold(
        session, first_day=first_day, profile_name=profile_name,
    )
    if curve:
        groups: dict[tuple, dict] = {}
        for (day, profile, model, engine, requests), stats in curves.items():
            if day < first_day or (profile_name is not None and profile != profile_name):
                continue
            target = groups.setdefault((profile, model, engine, requests), {
                "sum": 0.0, "weight": 0.0, "count": 0, "max": 0.0,
            })
            for field in ("sum", "weight", "count"):
                target[field] += stats[field]
            target["max"] = max(target["max"], stats["max"])
        return {"items": [{
            "profile_name": p, "model_name": m or None, "engine_id": e, "requests": n,
            "total_avg": s["sum"] / s["weight"],
            "per_request_avg": s["sum"] / s["weight"] / n,
            "total_max": s["max"], "samples": s["count"], "seconds": s["weight"],
        } for (p, m, e, n), s in sorted(groups.items())], "days": days, "source": "sqlite"}
    items = []
    for (day, profile, model, engine), value in sorted(daily.items()):
        if day < first_day or (profile_name is not None and profile != profile_name):
            continue
        distributions = [{**h, "avg": h["sum"] / h["count"],
                          "p50": percentile(h["buckets"], .5, cumulative=True),
                          "p95": percentile(h["buckets"], .95, cumulative=True),
                          "percentile_source": "engine_request_histogram"}
                         for h in value["distributions"].values() if h["count"] > 0]
        items.append({"day": day, "profile_name": profile, "model_name": model or None,
                      "engine_id": engine,
                      "statistics": {k: summary(v) for k, v in value["statistics"].items()},
                      "request_distributions": distributions})
    return {"items": items, "days": days, "source": "sqlite"}
