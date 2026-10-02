"""Engine-specific names translated into comparable performance measurements."""
from __future__ import annotations

import math
from datetime import UTC, datetime

from ..schemas.instances import Instance, Metric

# Explicit semantic mappings; unknown exporter fields are not guessed.
MAPPINGS = {
    'llama.cpp': {
        'decode_tps': ('llamacpp:predicted_tokens_seconds',),
        'prefill_tps': ('llamacpp:prompt_tokens_seconds',),
        'active_requests': ('llamacpp:requests_processing',),
        'queue_requests': ('llamacpp:requests_deferred',),
        'kv_cache_percent': ('llamacpp:kv_cache_usage_ratio',),
        'prompt_tokens_total': ('llamacpp:prompt_tokens_total',),
        'completion_tokens_total': ('llamacpp:tokens_predicted_total',),
    },
    'vllm': {
        'active_requests': ('vllm:num_requests_running',),
        'queue_requests': ('vllm:num_requests_waiting',),
        'kv_cache_percent': ('vllm:kv_cache_usage_perc', 'vllm:gpu_cache_usage_perc'),
        'prompt_tokens_total': ('vllm:prompt_tokens_total',),
        'completion_tokens_total': ('vllm:generation_tokens_total',),
        'completed_requests': ('vllm:request_success_total',),
    },
    'sglang': {
        'active_requests': ('sglang:num_running_reqs',),
        'queue_requests': ('sglang:num_queue_reqs',),
        'kv_cache_percent': ('sglang:token_usage',),
        'prompt_tokens_total': ('sglang:prompt_tokens_total',),
        'completion_tokens_total': ('sglang:generation_tokens_total',),
        'completed_requests': ('sglang:num_requests_total',),
    },
}
UNITS = {'decode_tps': 'tok/s', 'prefill_tps': 'tok/s', 'ttft_mean_ms': 'ms',
         'kv_cache_percent': '%', 'uptime_s': 's', 'vram_bytes': 'bytes'}
FIELDS = ('decode_tps', 'ttft_mean_ms', 'prefill_tps', 'active_requests', 'vram_bytes',
          'power_watts', 'kv_cache_percent', 'queue_requests', 'uptime_s',
          'prompt_tokens_total', 'completion_tokens_total', 'inflight_completion_tokens', 'completed_requests', 'energy_kwh')


class PerformanceNormalizer:
    def __init__(self) -> None:
        self.samples: dict[str, tuple[datetime, dict[str, float]]] = {}

    def collect(self, instance: Instance, raw: list[Metric]) -> list[Metric]:
        now = datetime.now(UTC)
        grouped: dict[str, list[Metric]] = {}
        for metric in raw:
            if isinstance(metric.value, (int, float)) and not isinstance(metric.value, bool) and math.isfinite(metric.value) and metric.value >= 0:
                grouped.setdefault(metric.name, []).append(metric)
        values: dict[str, tuple[float, str, datetime, str]] = {}
        mapping = MAPPINGS.get(instance.engine_id, {})
        for target in FIELDS:
            aliases = (target, *mapping.get(target, ()))
            for alias in aliases:
                matches = grouped.get(alias, [])
                if not matches:
                    continue
                value = max(float(item.value) for item in matches) if target == 'kv_cache_percent' else sum(float(item.value) for item in matches)
                if target == 'kv_cache_percent':
                    value *= 100
                state = 'stale' if any(item.state != 'ok' for item in matches) else 'ok'
                values[target] = (value, matches[0].source, min(item.observed_at for item in matches), state)
                break
        if instance.engine_id == 'freetoken' and 'kv_pages_used' in grouped and 'kv_pages_total' in grouped:
            total = sum(float(item.value) for item in grouped['kv_pages_total'])
            if total > 0:
                used = sum(float(item.value) for item in grouped['kv_pages_used'])
                item = grouped['kv_pages_used'][0]
                values['kv_cache_percent'] = (used / total * 100, item.source, item.observed_at, item.state)
        # Derive rates only from fresh, distinct exporter samples. A reset must
        # never turn into negative throughput or a fabricated zero.
        fresh = [item for item in raw if item.state == 'ok' and isinstance(item.value, (int, float))]
        if fresh:
            stamp = min(item.observed_at for item in fresh)
            counters = {key: value[0] for key, value in values.items() if value[3] == 'ok'}
            prefix = {'vllm': 'vllm', 'sglang': 'sglang'}.get(instance.engine_id)
            if prefix:
                for suffix in ('sum', 'count'):
                    name = f'{prefix}:time_to_first_token_seconds_{suffix}'
                    if name in grouped:
                        counters[f'ttft_{suffix}'] = sum(float(item.value) for item in grouped[name])
            previous = self.samples.get(instance.id)
            if previous and stamp > previous[0]:
                elapsed = (stamp - previous[0]).total_seconds()
                def delta(key: str) -> float | None:
                    current, old = counters.get(key), previous[1].get(key)
                    return current - old if current is not None and old is not None and current >= old else None
                if prefix:
                    for target, counter in (('decode_tps', 'completion_tokens_total'), ('prefill_tps', 'prompt_tokens_total')):
                        change = delta(counter)
                        if change is not None:
                            values[target] = (change / elapsed, f'{prefix}:counter_rate', stamp, 'ok')
                    count, duration = delta('ttft_count'), delta('ttft_sum')
                    if count is not None and count > 0 and duration is not None:
                        values['ttft_mean_ms'] = (duration / count * 1000, f'{prefix}:ttft_histogram', stamp, 'ok')
            if previous is None or stamp > previous[0]:
                self.samples[instance.id] = (stamp, counters)
                if len(self.samples) > 256:
                    self.samples.pop(next(iter(self.samples)))
        if instance.process_start_time_ns is not None:
            values['uptime_s'] = (max(0, now.timestamp() - instance.process_start_time_ns / 1e9), 'process_table', now, 'ok')
        return [Metric(name=name, value=values[name][0] if name in values else None,
                       unit=UNITS.get(name, ''), source=values[name][1] if name in values else 'unavailable',
                       observed_at=values[name][2] if name in values else now,
                       state=values[name][3] if name in values else 'unavailable') for name in FIELDS]


def process_vram(instance: Instance) -> Metric | None:
    """Sum CUDA memory only for an identified process and its current children."""
    import csv
    import io
    import shutil
    import subprocess
    import psutil

    executable = shutil.which('nvidia-smi')
    if executable is None or instance.process_start_time_ns is None:
        return None
    try:
        process = psutil.Process(instance.pid)
        if int(process.create_time() * 1e9) != instance.process_start_time_ns:
            return None
        pids = {instance.pid, *(child.pid for child in process.children(recursive=True))}
        result = subprocess.run([executable, '--query-compute-apps=pid,used_gpu_memory',
                                 '--format=csv,noheader,nounits'], capture_output=True,
                                text=True, timeout=2, check=False)
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
        return Metric(name='vram_bytes', value=total, unit='bytes', source='nvidia-smi:process',
                      observed_at=datetime.now(UTC), state='ok')
    except (OSError, subprocess.SubprocessError, psutil.Error):
        return None
