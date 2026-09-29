"""Engine metric providers with conservative unavailable/stale semantics."""

from __future__ import annotations

import math
import re
from datetime import UTC, datetime
from typing import Any, Protocol

import httpx

from ..schemas.instances import Instance, Metric

_PROMETHEUS_LINE = re.compile(
    r"^([a-zA-Z_:][a-zA-Z0-9_:]*)(?:\{[^}]*\})?\s+([-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?)$"
)

# Endpoints are intentionally explicit per engine.  ``None`` means that the
# Version Zero adapter has no documented/observable Prometheus source and must
# not guess by probing a conventional URL.
ENGINE_METRIC_ENDPOINTS: dict[str, str | None] = {
    "vllm": "/metrics",
    "sglang": "/metrics",
    "llama.cpp": "/metrics",
    "ninfer": None,
    "freetoken": "/v1/stats",
}


class MetricProvider(Protocol):
    engine_id: str

    async def collect(self, instance: Instance) -> list[Metric]: ...


class PrometheusMetricProvider:
    """Read an engine's documented Prometheus endpoint when present."""

    def __init__(
        self,
        engine_id: str,
        *,
        client: httpx.AsyncClient | None = None,
        client_factory: Any | None = None,
        endpoint: str | None = None,
        stale_after: float = 60.0,
        names: set[str] | None = None,
    ) -> None:
        self.engine_id = engine_id
        self.client = client
        self.client_factory = client_factory
        self.endpoint = endpoint if endpoint is not None else ENGINE_METRIC_ENDPOINTS.get(engine_id)
        self.stale_after = stale_after
        self.names = names
        self._cache: dict[str, list[Metric]] = {}

    async def collect(self, instance: Instance) -> list[Metric]:
        now = datetime.now(UTC)
        if (
            not isinstance(instance.port, int)
            or not 1 <= instance.port <= 65535
            or not self.endpoint
            or not self.endpoint.startswith("/")
        ):
            return [self._unavailable(now)]
        own_client = self.client is None
        client = self.client or (
            self.client_factory()
            if self.client_factory is not None
            else httpx.AsyncClient(timeout=2.0)
        )
        try:
            response = await client.get(f"http://127.0.0.1:{instance.port}{self.endpoint}")
            if response.status_code >= 400:
                raise httpx.HTTPStatusError(
                    "metrics endpoint returned an error",
                    request=response.request,
                    response=response,
                )
            values = _parse_prometheus(response.text, self.names, now, self.engine_id)
            if values:
                self._cache[instance.id] = values
                return values
            raise ValueError("empty metrics response")
        except (httpx.HTTPError, ValueError, UnicodeError):
            previous = self._cache.get(instance.id)
            if previous:
                age = max(
                    0.0,
                    (now - max(item.observed_at for item in previous)).total_seconds(),
                )
                if age >= self.stale_after:
                    return [item.model_copy(update={"state": "stale"}) for item in previous]
                return previous
            return [self._unavailable(now)]
        finally:
            if own_client:
                await client.aclose()

    def _unavailable(self, observed_at: datetime) -> Metric:
        return Metric(
            name=f"{self.engine_id}:metrics",
            value=None,
            unit="",
            source=f"{self.engine_id}:prometheus",
            observed_at=observed_at,
            state="unavailable",
        )


class VllmMetricsProvider(PrometheusMetricProvider):
    def __init__(self, **kwargs: Any) -> None:
        super().__init__("vllm", **kwargs)


class SglangMetricsProvider(PrometheusMetricProvider):
    def __init__(self, **kwargs: Any) -> None:
        super().__init__("sglang", **kwargs)


class LlamaCppMetricsProvider(PrometheusMetricProvider):
    def __init__(self, **kwargs: Any) -> None:
        super().__init__("llama.cpp", **kwargs)


class NInferMetricsProvider(PrometheusMetricProvider):
    def __init__(self, **kwargs: Any) -> None:
        super().__init__("ninfer", **kwargs)


class FreeTokenMetricsProvider(PrometheusMetricProvider):
    def __init__(self, **kwargs: Any) -> None:
        super().__init__("freetoken", **kwargs)

    async def collect(self, instance: Instance) -> list[Metric]:
        """Collect the documented JSON stats without inventing values.

        FreeToken exposes JSON at ``/v1/stats`` rather than Prometheus text.
        Only fields with a known meaning are emitted.  A malformed or
        unsupported value becomes an explicit unavailable metric and is never
        coerced to zero.
        """

        now = datetime.now(UTC)
        if not isinstance(instance.port, int) or not 1 <= instance.port <= 65535:
            return [self._stats_unavailable(now)]
        own_client = self.client is None
        client = self.client or (
            self.client_factory()
            if self.client_factory is not None
            else httpx.AsyncClient(timeout=2.0)
        )
        try:
            response = await client.get(f"http://127.0.0.1:{instance.port}/v1/stats")
            if response.status_code >= 400:
                return [self._stats_unavailable(now)]
            payload = response.json()
            if not isinstance(payload, dict):
                raise ValueError("FreeToken stats response is not an object")
            values = _parse_freetoken_stats(payload, now)
            return values or [self._stats_unavailable(now)]
        except (httpx.HTTPError, ValueError, TypeError, UnicodeError):
            return [self._stats_unavailable(now)]
        finally:
            if own_client:
                await client.aclose()

    def _stats_unavailable(self, observed_at: datetime) -> Metric:
        return Metric(
            name="freetoken:stats",
            value=None,
            unit="",
            source="freetoken:stats",
            observed_at=observed_at,
            state="unavailable",
        )


# Names and units are intentionally an allowlist.  Nested aliases account for
# stable FreeToken response groupings while keeping arbitrary JSON invisible.
_FREETOKEN_STATS_FIELDS: dict[str, tuple[str, ...]] = {
    "uptime_s": ("uptime_s",),
    "decode_tps": ("decode_tps", "throughput.decode_tps"),
    "prefill_tps": ("prefill_tps", "throughput.prefill_tps"),
    "active_requests": ("active_requests", "requests.active"),
    "completed_requests": ("completed_requests", "requests.completed"),
    "p95_ms": ("p95_ms", "latency.p95_ms", "requests.p95_ms"),
    "ttft_mean_ms": ("ttft_mean_ms", "latency.ttft_mean_ms", "requests.ttft_mean_ms"),
    "prompt_tokens_total": ("prompt_tokens_total", "requests.prompt_tokens_total"),
    "completion_tokens_total": (
        "completion_tokens_total",
        "requests.completion_tokens_total",
    ),
    "vram_bytes": ("vram_bytes", "memory.vram_bytes"),
    "kv_pages_used": ("kv_pages_used", "kv.used_pages"),
    "kv_pages_total": ("kv_pages_total", "kv.total_pages"),
    "kv_page_size": ("kv_page_size", "kv.page_size"),
    "mamba_slots_used": ("mamba_slots_used", "mamba.used_slots"),
    "mamba_slots_total": ("mamba_slots_total", "mamba.total_slots"),
    "swa_pages_used": ("swa_pages_used", "swa.used_pages"),
    "swa_pages_total": ("swa_pages_total", "swa.total_pages"),
    "swa_page_size": ("swa_page_size", "swa.page_size"),
}
_FREETOKEN_STATS_UNITS = {
    "uptime_s": "s",
    "decode_tps": "tokens/s",
    "prefill_tps": "tokens/s",
    "p95_ms": "ms",
    "ttft_mean_ms": "ms",
    "vram_bytes": "bytes",
    "kv_page_size": "tokens",
    "swa_page_size": "tokens",
}


def _lookup_path(payload: dict[str, Any], path: str) -> tuple[bool, Any]:
    value: Any = payload
    for part in path.split("."):
        if not isinstance(value, dict) or part not in value:
            return False, None
        value = value[part]
    return True, value


def _parse_freetoken_stats(payload: dict[str, Any], observed_at: datetime) -> list[Metric]:
    result: list[Metric] = []
    for name, aliases in _FREETOKEN_STATS_FIELDS.items():
        found = False
        raw: Any = None
        for alias in aliases:
            found, raw = _lookup_path(payload, alias)
            if found:
                break
        if not found:
            continue
        valid = (
            isinstance(raw, (int, float))
            and not isinstance(raw, bool)
            and math.isfinite(float(raw))
            and float(raw) >= 0
        )
        result.append(
            Metric(
                name=name,
                value=float(raw) if valid and isinstance(raw, float) else raw if valid else None,
                unit=_FREETOKEN_STATS_UNITS.get(name, ""),
                source="freetoken:stats",
                observed_at=observed_at,
                state="ok" if valid else "unavailable",
            )
        )
    return result


def default_providers(**kwargs: Any) -> dict[str, MetricProvider]:
    classes = (
        VllmMetricsProvider,
        SglangMetricsProvider,
        LlamaCppMetricsProvider,
        NInferMetricsProvider,
        FreeTokenMetricsProvider,
    )
    return {provider.engine_id: provider for provider in (klass(**kwargs) for klass in classes)}


def _parse_prometheus(
    body: str,
    names: set[str] | None,
    observed_at: datetime,
    engine_id: str,
) -> list[Metric]:
    result: list[Metric] = []
    for line in body.splitlines():
        match = _PROMETHEUS_LINE.match(line.strip())
        if match is None:
            continue
        name, raw_value = match.groups()
        if names is not None and name not in names:
            continue
        try:
            value = float(raw_value)
        except ValueError:
            continue
        result.append(
            Metric(
                name=name,
                value=value,
                unit="",
                source=f"{engine_id}:prometheus",
                observed_at=observed_at,
                state="ok",
            )
        )
    return result


__all__ = [
    "ENGINE_METRIC_ENDPOINTS",
    "FreeTokenMetricsProvider",
    "LlamaCppMetricsProvider",
    "MetricProvider",
    "NInferMetricsProvider",
    "PrometheusMetricProvider",
    "SglangMetricsProvider",
    "VllmMetricsProvider",
    "default_providers",
]
