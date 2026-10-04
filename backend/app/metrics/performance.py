"""Engine-specific names translated into comparable performance measurements."""

from __future__ import annotations

import math
from datetime import UTC, datetime
from typing import cast

from ..schemas.instances import Instance, Metric, MetricState

# Explicit semantic mappings; unknown exporter fields are not guessed.
MAPPINGS: dict[str, dict[str, tuple[str, ...]]] = {
    "llama.cpp": {
        "decode_tps": ("llamacpp:predicted_tokens_seconds",),
        "prefill_tps": ("llamacpp:prompt_tokens_seconds",),
        "active_requests": ("llamacpp:requests_processing",),
        "queue_requests": ("llamacpp:requests_deferred",),
        "kv_cache_percent": ("llamacpp:kv_cache_usage_ratio",),
        "prompt_tokens_total": ("llamacpp:prompt_tokens_total",),
        "completion_tokens_total": ("llamacpp:tokens_predicted_total",),
    },
    "vllm": {
        "active_requests": ("vllm:num_requests_running",),
        "queue_requests": ("vllm:num_requests_waiting",),
        "kv_cache_percent": ("vllm:kv_cache_usage_perc", "vllm:gpu_cache_usage_perc"),
        "prompt_tokens_total": ("vllm:prompt_tokens_total",),
        "completion_tokens_total": ("vllm:generation_tokens_total",),
        "completed_requests": ("vllm:request_success_total",),
    },
    "sglang": {
        "active_requests": ("sglang:num_running_reqs",),
        "queue_requests": ("sglang:num_queue_reqs",),
        "kv_cache_percent": ("sglang:full_token_usage",),
        "prompt_tokens_total": ("sglang:prompt_tokens_total",),
        "completion_tokens_total": ("sglang:generation_tokens_total",),
        "completed_requests": ("sglang:num_requests_total",),
    },
}
UNITS = {
    "decode_tps": "tok/s",
    "prefill_tps": "tok/s",
    "ttft_mean_ms": "ms",
    "kv_cache_percent": "%",
    "uptime_s": "s",
    "vram_bytes": "bytes",
    "queue_time_mean_ms": "ms",
    "prefix_cache_hit_percent": "%",
    "prefix_cache_hit_mean_percent": "%",
    "spec_acceptance_percent": "%",
}
FIELDS = (
    "decode_tps",
    "ttft_mean_ms",
    "prefill_tps",
    "active_requests",
    "vram_bytes",
    "power_watts",
    "kv_cache_percent",
    "queue_requests",
    "uptime_s",
    "prompt_tokens_total",
    "completion_tokens_total",
    "inflight_completion_tokens",
    "completed_requests",
    "energy_kwh",
    "queue_time_mean_ms",
    "cached_prompt_tokens_total",
    "prefix_cache_hit_percent",
    "prefix_cache_hit_mean_percent",
    "spec_acceptance_percent",
)


class PerformanceNormalizer:
    def __init__(self) -> None:
        self.samples: dict[str, tuple[datetime, dict[str, float]]] = {}

    def collect(self, instance: Instance, raw: list[Metric]) -> list[Metric]:
        now = datetime.now(UTC)
        grouped: dict[str, list[Metric]] = {}
        for metric in raw:
            if (
                metric.state == "ok"
                and isinstance(metric.value, (int, float))
                and not isinstance(metric.value, bool)
                and math.isfinite(metric.value)
                and metric.value >= 0
            ):
                grouped.setdefault(metric.name, []).append(metric)
        metadata: dict[str, dict[str, float | str]] = {}
        values: dict[str, tuple[float, str, datetime, MetricState]] = {}
        mapping = MAPPINGS.get(instance.engine_id, {})
        for target in FIELDS:
            aliases = (target, *mapping.get(target, ()))
            for alias in aliases:
                matches = grouped.get(alias, [])
                if not matches:
                    continue
                value = (
                    max(float(cast(float, item.value)) for item in matches)
                    if target == "kv_cache_percent"
                    else sum(float(cast(float, item.value)) for item in matches)
                )
                if target == "kv_cache_percent" and alias != target:
                    value *= 100
                if target.endswith("_percent") and not 0 <= value <= 100:
                    continue
                state: MetricState = (
                    "stale" if any(item.state != "ok" for item in matches) else "ok"
                )
                values[target] = (
                    value,
                    matches[0].source,
                    min(item.observed_at for item in matches),
                    state,
                )
                break
        if (
            instance.engine_id == "freetoken"
            and "kv_pages_used" in grouped
            and "kv_pages_total" in grouped
        ):
            total = sum(float(cast(float, item.value)) for item in grouped["kv_pages_total"])
            if total > 0:
                used = sum(float(cast(float, item.value)) for item in grouped["kv_pages_used"])
                item = grouped["kv_pages_used"][0]
                values["kv_cache_percent"] = (
                    used / total * 100,
                    item.source,
                    item.observed_at,
                    item.state,
                )
        # Derive rates only from fresh, distinct exporter samples. A reset must
        # never turn into negative throughput or a fabricated zero.
        fresh = [
            item for item in raw if item.state == "ok" and isinstance(item.value, (int, float))
        ]
        if fresh:
            stamp = min(item.observed_at for item in fresh)
            counters = {key: value[0] for key, value in values.items() if value[3] == "ok"}
            prefix = {"vllm": "vllm", "sglang": "sglang"}.get(instance.engine_id)
            if prefix:
                for suffix in ("sum", "count"):
                    name = f"{prefix}:time_to_first_token_seconds_{suffix}"
                    if name in grouped:
                        counters[f"ttft_{suffix}"] = sum(
                            float(cast(float, item.value)) for item in grouped[name]
                        )
            prefix = {"vllm": "vllm", "sglang": "sglang"}.get(instance.engine_id)

            def counter_total(*names: str) -> float | None:
                for name in names:
                    if name in grouped:
                        return sum(float(cast(float, item.value)) for item in grouped[name])
                return None

            # Both histograms observe the same finished requests. Unlike the
            # live prompt counter, computed KV tokens exclude cached/transferred
            # prefixes; prefill time excludes the initial queue and decode.
            prefill_series: list[str] = []
            if instance.engine_id == "vllm":
                families = {}
                for key, family in (
                    ("tokens", "request_prefill_kv_computed_tokens_sum"),
                    ("token_count", "request_prefill_kv_computed_tokens_count"),
                    ("seconds", "request_prefill_time_seconds_sum"),
                    ("time_count", "request_prefill_time_seconds_count"),
                ):
                    families[key] = {
                        repr(sorted(item.metadata.get("labels", {}).items())): float(item.value)
                        for item in grouped.get(f"vllm:{family}", [])
                    }
                labels = set(families["tokens"])
                if (
                    labels
                    and all(set(family) == labels for family in families.values())
                    and all(
                        families["token_count"][label] == families["time_count"][label]
                        for label in labels
                    )
                ):
                    for label in sorted(labels):
                        series = f"prefill:{label}"
                        prefill_series.append(series)
                        for key, family in families.items():
                            counters[f"{series}:{key}"] = family[label]

            pairs = {
                "llama.cpp": (
                    ("llamacpp:prompt_tokens_cached_total",),
                    ("llamacpp:prompt_tokens_total",),
                ),
                "vllm": (
                    ("vllm:prefix_cache_hits_total", "vllm:prefix_cache_hits"),
                    ("vllm:prefix_cache_queries_total", "vllm:prefix_cache_queries"),
                ),
                "sglang": (("sglang:cached_tokens_total",), ("sglang:prompt_tokens_total",)),
            }
            pair = pairs.get(instance.engine_id)
            if pair:
                hits, eligible = counter_total(*pair[0]), counter_total(*pair[1])
                if instance.engine_id == "llama.cpp" and eligible is not None and hits is not None:
                    eligible += hits
                if hits is not None:
                    counters["cache_hits"] = hits
                if eligible is not None:
                    counters["cache_queries"] = eligible
                if hits is not None and eligible is not None and eligible > 0 and hits <= eligible:
                    metadata["prefix_cache_hit_mean_percent"] = {
                        "denominator": eligible,
                        "semantic": "cached/eligible tokens",
                    }
                    values["prefix_cache_hit_mean_percent"] = (
                        hits / eligible * 100,
                        f"{instance.engine_id}:cache_counters",
                        stamp,
                        "ok",
                    )
                cached = (
                    counter_total("llamacpp:prompt_tokens_cached_total")
                    if instance.engine_id == "llama.cpp"
                    else counter_total("sglang:cached_tokens_total")
                    if instance.engine_id == "sglang"
                    else counter_total(
                        "vllm:prompt_tokens_cached_total", "vllm:prompt_tokens_cached"
                    )
                )
                if cached is not None:
                    values["cached_prompt_tokens_total"] = (
                        cached,
                        f"{instance.engine_id}:cached_tokens",
                        stamp,
                        "ok",
                    )
            queue_name = {
                "vllm": "vllm:request_queue_time_seconds",
                "sglang": "sglang:queue_time_seconds",
            }.get(instance.engine_id)
            if queue_name:
                for suffix in ("sum", "count"):
                    counter_value = counter_total(f"{queue_name}_{suffix}")
                    if counter_value is not None:
                        counters[f"queue_{suffix}"] = counter_value
            draft_prefix = {"vllm": "vllm", "llama.cpp": "llamacpp"}.get(instance.engine_id)
            if draft_prefix:
                for target, token_name in (
                    ("draft_accepted", "accepted"),
                    ("draft_proposed", "draft"),
                ):
                    counter_value = counter_total(
                        f"{draft_prefix}:spec_decode_num_{token_name}_tokens_total",
                        f"{draft_prefix}:spec_decode_num_{token_name}_tokens",
                    )
                    if counter_value is not None:
                        counters[target] = counter_value
            previous = self.samples.get(instance.id)
            ttft_count, ttft_sum = counters.get("ttft_count"), counters.get("ttft_sum")
            if ttft_count is not None and ttft_count > 0 and ttft_sum is not None:
                metadata.setdefault("ttft_mean_ms", {}).update(
                    mean=ttft_sum / ttft_count * 1000, mean_weight=ttft_count
                )
            proposed, accepted = counters.get("draft_proposed"), counters.get("draft_accepted")
            if (
                proposed is not None
                and proposed > 0
                and accepted is not None
                and accepted <= proposed
            ):
                metadata.setdefault("spec_acceptance_percent", {}).update(
                    mean=accepted / proposed * 100, mean_weight=proposed
                )
            if previous and stamp > previous[0]:
                elapsed = (stamp - previous[0]).total_seconds()

                def delta(key: str) -> float | None:
                    current, old = counters.get(key), previous[1].get(key)
                    return (
                        current - old
                        if current is not None and old is not None and current >= old
                        else None
                    )

                if prefix:
                    for target, counter in (
                        ("decode_tps", "completion_tokens_total"),
                        ("prefill_tps", "prompt_tokens_total"),
                    ):
                        if target == "prefill_tps" and instance.engine_id == "vllm":
                            continue
                        change = delta(counter)
                        if change is not None:
                            values[target] = (
                                change / elapsed,
                                f"{prefix}:counter_rate",
                                stamp,
                                "ok",
                            )
                    if prefill_series:
                        computed, seconds = 0.0, 0.0
                        valid = True
                        for series in prefill_series:
                            tokens = delta(f"{series}:tokens")
                            duration = delta(f"{series}:seconds")
                            token_count = delta(f"{series}:token_count")
                            time_count = delta(f"{series}:time_count")
                            if (
                                tokens is None or duration is None
                                or token_count is None or time_count != token_count
                                or (token_count == 0 and (tokens != 0 or duration != 0))
                                or (token_count > 0 and duration <= 0)
                            ):
                                valid = False
                                break
                            computed += tokens
                            seconds += duration
                        if valid and computed > 0 and seconds > 0:
                            metadata["prefill_tps"] = {"denominator": seconds}
                            values["prefill_tps"] = (
                                computed / seconds,
                                "vllm:prefill_histograms",
                                stamp,
                                "ok",
                            )
                    count, duration = delta("ttft_count"), delta("ttft_sum")
                    if count is not None and count > 0 and duration is not None:
                        metadata.setdefault("ttft_mean_ms", {})["count"] = count
                        values["ttft_mean_ms"] = (
                            duration / count * 1000,
                            f"{prefix}:ttft_histogram",
                            stamp,
                            "ok",
                        )
            if previous and stamp > previous[0]:
                for target, numerator, denominator, multiplier in (
                    ("prefix_cache_hit_percent", "cache_hits", "cache_queries", 100),
                    ("spec_acceptance_percent", "draft_accepted", "draft_proposed", 100),
                    ("queue_time_mean_ms", "queue_sum", "queue_count", 1000),
                ):
                    count, amount = delta(denominator), delta(numerator)
                    if (
                        count is not None
                        and count > 0
                        and amount is not None
                        and (multiplier != 100 or amount <= count)
                    ):
                        metadata.setdefault(target, {})[
                            "count" if multiplier == 1000 else "denominator"
                        ] = count
                        values[target] = (
                            amount / count * multiplier,
                            f"{instance.engine_id}:counter_delta",
                            stamp,
                            "ok",
                        )
            if instance.engine_id == "sglang":
                for target, source in (
                    ("prefix_cache_hit_percent", "sglang:cache_hit_rate"),
                    ("spec_acceptance_percent", "sglang:spec_accept_rate"),
                ):
                    matches = grouped.get(source, [])
                    if len(matches) == 1 and 0 <= float(cast(float, matches[0].value)) <= 1:
                        item = matches[0]
                        values[target] = (
                            float(cast(float, item.value)) * 100,
                            item.source,
                            item.observed_at,
                            "ok",
                        )
            if previous is None or stamp > previous[0]:
                self.samples[instance.id] = (stamp, counters)
                if len(self.samples) > 256:
                    self.samples.pop(next(iter(self.samples)))
        for metric in raw:
            if metric.state == "stale" and isinstance(metric.value, (int, float)):
                for target in FIELDS:
                    if target not in values and metric.name in (target, *mapping.get(target, ())):
                        factor = (
                            100 if target == "kv_cache_percent" and metric.name != target else 1
                        )
                        values[target] = (
                            float(metric.value) * factor,
                            metric.source,
                            metric.observed_at,
                            "stale",
                        )
        if instance.process_start_time_ns is not None:
            values["uptime_s"] = (
                max(0, now.timestamp() - instance.process_start_time_ns / 1e9),
                "process_table",
                now,
                "ok",
            )
        return [
            Metric(
                name=name,
                value=values[name][0] if name in values else None,
                unit=UNITS.get(name, ""),
                source=values[name][1] if name in values else "unavailable",
                observed_at=values[name][2] if name in values else now,
                state=values[name][3] if name in values else "unavailable",
                metadata={"semantic": name, **metadata.get(name, {})},
            )
            for name in FIELDS
        ]


def process_vram(instance: Instance) -> Metric | None:
    """Sum CUDA memory only for an identified process and its current children."""
    import csv
    import io
    import shutil
    import subprocess

    import psutil

    executable = shutil.which("nvidia-smi")
    if executable is None or instance.process_start_time_ns is None:
        return None
    try:
        process = psutil.Process(instance.pid)
        if int(process.create_time() * 1e9) != instance.process_start_time_ns:
            return None
        pids = {instance.pid, *(child.pid for child in process.children(recursive=True))}
        result = subprocess.run(
            [
                executable,
                "--query-compute-apps=pid,used_gpu_memory",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
        if result.returncode != 0:
            return None
        total: float | None = None
        for row in csv.reader(io.StringIO(result.stdout), skipinitialspace=True):
            if len(row) != 2:
                continue
            try:
                if int(row[0]) not in pids:
                    continue
                memory = float(row[1])
                if not math.isfinite(memory) or memory < 0:
                    return None
            except ValueError:
                return None
            total = (total or 0) + memory * 1024 * 1024
        if total is None or int(process.create_time() * 1e9) != instance.process_start_time_ns:
            return None
        return Metric(
            name="vram_bytes",
            value=total,
            unit="bytes",
            source="nvidia-smi:process",
            observed_at=datetime.now(UTC),
            state="ok",
        )
    except (OSError, subprocess.SubprocessError, psutil.Error):
        return None
