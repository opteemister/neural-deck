"""Metric collection service over process and engine seams."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime

from ..process.manager import ProcessManager
from ..schemas.instances import Metric, MetricList
from ..token_stats import RequestJournalService
from .providers import MetricProvider, default_providers


class MetricsService:
    def __init__(
        self,
        process_manager: ProcessManager,
        *,
        providers: Mapping[str, MetricProvider] | None = None,
        journal: RequestJournalService | None = None,
    ) -> None:
        self.process_manager = process_manager
        self.providers = dict(providers or default_providers())
        self.journal = journal

    async def close(self) -> None:
        """Close resources owned by metric providers, when they have any."""

        for provider in self.providers.values():
            close = getattr(provider, "close", None)
            if close is not None:
                await close()

    async def collect(self, instance_id: str) -> MetricList:
        instance = await self.process_manager.get(instance_id)
        now = datetime.now(UTC)
        result = [
            Metric(
                name="process_state",
                value=instance.state,
                unit="",
                source="process_table",
                observed_at=now,
                state="ok" if instance.state in {"running", "ready", "starting"} else "stale",
            )
        ]
        provider = self.providers.get(instance.engine_id)
        if provider is None:
            result.append(
                Metric(
                    name="engine_metrics",
                    value=None,
                    unit="",
                    source="engine_provider",
                    observed_at=now,
                    state="unavailable",
                )
            )
        else:
            try:
                result.extend(await provider.collect(instance))
            except Exception:  # provider boundary must not break metrics API
                result.append(
                    Metric(
                        name=f"{instance.engine_id}:metrics",
                        value=None,
                        unit="",
                        source="engine_provider",
                        observed_at=now,
                        state="unavailable",
                    )
                )
        latency = await self.journal.latest_latency(instance_id) if self.journal else None
        result.append(
            Metric(
                name="api_latency_ms",
                value=latency,
                unit="ms",
                source="openai_proxy_journal",
                observed_at=now,
                state="ok" if latency is not None else "unavailable",
            )
        )
        return MetricList(items=result)


__all__ = ["MetricsService"]
