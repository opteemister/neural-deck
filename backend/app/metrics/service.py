"""Metric collection service over process and engine seams."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from datetime import UTC, datetime

from ..process.manager import ProcessManager
from ..schemas.instances import Metric, MetricList
from ..token_stats import RequestJournalService
from .performance import PerformanceNormalizer, process_vram
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
        self.performance = PerformanceNormalizer()

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
        vram = await asyncio.to_thread(process_vram, instance)
        if vram is not None:
            result = [item for item in result if item.name != 'vram_bytes'] + [vram]
        return MetricList(items=self.performance.collect(instance, result))


__all__ = ["MetricsService"]
