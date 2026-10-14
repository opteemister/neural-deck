"""Shared window statistics for SQLite and Prometheus snapshots."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, cast

from ..schemas.instances import Metric

AGGREGATE_FIELDS = (
    "prefill_tps", "decode_tps", "active_requests", "vram_bytes", "ttft_mean_ms",
    "prefix_cache_hit_percent", "spec_acceptance_percent", "kv_cache_percent", "queue_requests",
    "per_request_tps",
)


def utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


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
        metadata: dict[str, float] = {}
        weight_key = "count" if name in {"ttft_mean_ms", "queue_time_mean_ms"} else "denominator"
        sample_weights = [item.metadata.get(weight_key) if item else None for item in matches]
        if sample_weights and all(
            isinstance(weight, (int, float)) and weight > 0 for weight in sample_weights
        ):
            metadata[weight_key] = sum(cast(float, weight) for weight in sample_weights)
        mean_pairs = [
            (item.metadata.get("mean"), item.metadata.get("mean_weight"))
            for item in matches if item is not None
        ]
        if len(mean_pairs) == len(rows) and mean_pairs and all(
            isinstance(mean, (int, float)) and isinstance(weight, (int, float)) and weight > 0
            for mean, weight in mean_pairs
        ):
            mean_weight = sum(cast(float, weight) for _, weight in mean_pairs)
            metadata.update(
                mean=sum(cast(float, mean) * cast(float, weight) for mean, weight in mean_pairs)
                / mean_weight,
                mean_weight=mean_weight,
            )
        items.append(
            Metric(
                name=name,
                value=value,
                unit=UNITS.get(name, ""),
                source="aggregate",
                observed_at=stamp,
                state="ok" if value is not None else "unavailable",
                metadata=metadata,
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


class WindowSummary:
    """Consume ordered snapshots without retaining the full history."""

    def __init__(self, start: datetime, end: datetime, all_profiles: bool) -> None:
        self.start, self.end, self.all_profiles = start, end, all_profiles
        self.seen: set[tuple[Any, ...]] = set()
        self.totals: dict[str, list[float]] = {}
        self.bucket: int | None = None
        self.pending: dict[tuple[Any, ...], dict[str, Any]] = {}

    def add(self, row: dict[str, Any]) -> None:
        if not self.all_profiles:
            self._measure(row)
            return
        bucket = int(row["observed_at"].timestamp()) // 5
        if self.bucket is not None and bucket != self.bucket:
            self._flush()
        self.bucket = bucket
        self.pending[(row["profile_name"], row["pid"])] = row

    def _flush(self) -> None:
        if self.pending:
            self._measure(aggregate_samples(list(self.pending.values())))
            self.pending.clear()

    def _measure(self, row: dict[str, Any]) -> None:
        items = list(row["items"])
        by_name = {item.name: item for item in items}
        decode, requests = by_name.get("decode_tps"), by_name.get("active_requests")
        if (
            decode is not None and requests is not None
            and decode.state == requests.state == "ok"
            and isinstance(decode.value, (int, float))
            and isinstance(requests.value, (int, float))
            and decode.value > 0 and requests.value > 0
            and abs((utc(decode.observed_at) - utc(requests.observed_at)).total_seconds()) <= 5
        ):
            items.append(Metric(
                name="per_request_tps", value=decode.value / requests.value,
                observed_at=decode.observed_at, state="ok", unit="tok/s",
                source=decode.source, metadata=decode.metadata,
            ))
        for item in items:
            value = item.value
            if (
                item.name not in AGGREGATE_FIELDS or item.state != "ok"
                or not isinstance(value, (int, float)) or value <= 0
                or not self.start <= utc(item.observed_at) <= self.end
            ):
                continue
            key = (row.get("profile_name"), row.get("pid"), item.name, item.observed_at)
            if key in self.seen:
                continue
            self.seen.add(key)
            native = item.metadata.get("count" if item.name == "ttft_mean_ms" else "denominator")
            weight = native if isinstance(native, (int, float)) and native > 0 else 1
            total = self.totals.setdefault(item.name, [0, 0, 0])
            total[0] += value * weight
            total[1] += weight
            total[2] = max(total[2], value)

    def result(self) -> dict[str, dict[str, float]]:
        self._flush()
        return {name: {"avg": total[0] / total[1], "max": total[2]}
                for name, total in self.totals.items()}
