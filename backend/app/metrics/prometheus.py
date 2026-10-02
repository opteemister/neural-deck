"""Optional query source and Studio's pull exposition."""

from __future__ import annotations

import json
import math
import time
from datetime import UTC, datetime
from typing import Any

import httpx

from ..schemas.instances import Metric, MetricList
from .performance import FIELDS, UNITS


class PrometheusReader:
    def __init__(self, url: str, token: str, studio_id: str) -> None:
        self.url = url.rstrip("/")
        self.studio_id = studio_id
        self.client = httpx.AsyncClient(
            timeout=2, headers={"Authorization": f"Bearer {token}"} if token else {}
        )
        self.retry_after = 0.0
        self.missing_retry: dict[tuple[str | None, int | None, bool], float] = {}

    async def check(self) -> None:
        if not self.url:
            return
        try:
            response = await self.client.get(
                f"{self.url}/api/v1/query", params={"query": "vector(1)"}
            )
            response.raise_for_status()
            if response.json().get("status") != "success":
                raise ValueError("query failed")
        except (httpx.HTTPError, ValueError):
            self.retry_after = time.monotonic() + 30

    async def read(
        self,
        *,
        profile_name: str | None,
        pid: int | None,
        from_: datetime | None = None,
        to: datetime | None = None,
        limit: int = 120,
    ) -> list[dict[str, Any]]:
        if not self.url or time.monotonic() < self.retry_after:
            return []
        scope = (profile_name, pid, from_ is not None)
        if time.monotonic() < self.missing_retry.get(scope, 0):
            return []
        self.missing_retry = {
            key: until for key, until in self.missing_retry.items() if until > time.monotonic()
        }
        labels = {"studio_id": self.studio_id}
        if profile_name is not None:
            labels["profile_name"] = profile_name
        if pid is not None:
            labels["pid"] = str(pid)
        selector = (
            '{__name__=~"studio_model_.*",'
            + ",".join(f"{key}={json.dumps(value)}" for key, value in labels.items())
            + "}"
        )
        history = from_ is not None
        params: dict[str, Any] = {"query": selector}
        if from_ is not None:
            params.update(
                start=from_.timestamp(), end=(to or datetime.now(UTC)).timestamp(), step=5
            )
        try:
            response = await self.client.get(
                f"{self.url}/api/v1/{'query_range' if history else 'query'}", params=params
            )
            response.raise_for_status()
            payload = response.json()
            if payload.get("status") != "success":
                raise ValueError("query failed")
            rows = payload["data"]["result"]
            # Pair each field with its exporter-observed timestamp at the same evaluation.
            grouped: dict[tuple[str | None, int, str, float], dict[str, Any]] = {}
            for row in rows:
                label = row["metric"]
                if label.get("studio_id") != self.studio_id:
                    continue
                profile = label.get("profile_name") or None
                row_pid = int(label["pid"])
                if (profile_name is not None and profile != profile_name) or (
                    pid is not None and row_pid != pid
                ):
                    continue
                engine = label["engine_id"]
                name = label["__name__"].removeprefix("studio_model_")
                for at, raw in row.get("values", [row.get("value")]):
                    value = float(raw)
                    if not math.isfinite(value) or value < 0:
                        continue
                    key = (profile, row_pid, engine, float(at))
                    group = grouped.setdefault(key, {"values": {}, "times": {}, "weights": {}})
                    if name == "observed_timestamp_seconds":
                        group["times"][label.get("field")] = value
                    elif name == "weight":
                        group["weights"][label.get("field")] = (label.get("kind"), value)
                    elif name in FIELDS:
                        group["values"][name] = value
            result: dict[tuple[str | None, int, str], dict[str, Any]] = {}
            now = datetime.now(UTC).timestamp()
            for (profile, row_pid, engine, _), group in grouped.items():
                items = []
                for name, value in group["values"].items():
                    stamp = group["times"].get(name)
                    if stamp is None or stamp > now + 1 or (not history and now - stamp > 10):
                        continue
                    items.append(
                        Metric(
                            name=name,
                            value=value,
                            unit=UNITS.get(name, ""),
                            source="prometheus",
                            observed_at=datetime.fromtimestamp(stamp, UTC),
                            state="ok",
                            metadata={group["weights"][name][0]: group["weights"][name][1]}
                            if name in group["weights"]
                            else {},
                        )
                    )
                if not items:
                    continue
                stamp = max(item.observed_at for item in items)
                if from_ is not None and (stamp < from_ or (to and stamp > to)):
                    continue
                result[(profile, row_pid, stamp.isoformat())] = {
                    "profile_name": profile,
                    "pid": row_pid,
                    "engine_id": engine,
                    "source": "prometheus",
                    "observed_at": stamp,
                    "items": items,
                }
            if not result:
                self.missing_retry[scope] = time.monotonic() + 30
            else:
                self.missing_retry.pop(scope, None)
            return sorted(result.values(), key=lambda sample: sample["observed_at"])[-limit:]
        except (httpx.HTTPError, ValueError, KeyError, TypeError, AttributeError, IndexError):
            self.retry_after = time.monotonic() + 30
            return []

    async def close(self) -> None:
        await self.client.aclose()


def exposition(
    samples: list[tuple[Any, MetricList]], studio_id: str, baselines: dict[str, Any]
) -> str:
    lines = []
    for instance, data in samples:
        labels = {
            "studio_id": studio_id,
            "profile_name": instance.profile_name or "",
            "pid": str(instance.pid),
            "engine_id": instance.engine_id,
        }

        def render(extra: dict[str, str] | None = None, labels: dict[str, str] = labels) -> str:
            return (
                "{"
                + ",".join(
                    f"{key}={json.dumps(value, ensure_ascii=False)}"
                    for key, value in {**labels, **(extra or {})}.items()
                )
                + "}"
            )

        for item in data.items:
            if (
                item.state != "ok"
                or not isinstance(item.value, (int, float))
                or not math.isfinite(item.value)
            ):
                continue
            lines.append(f"studio_model_{item.name}{render()} {item.value}")
            for kind in ("count", "denominator"):
                weight = item.metadata.get(kind)
                if isinstance(weight, (int, float)) and math.isfinite(weight) and weight >= 0:
                    weight_labels = render({"field": item.name, "kind": kind})
                    lines.append(f"studio_model_weight{weight_labels} {weight}")
            lines.append(
                f"studio_model_observed_timestamp_seconds{render({'field': item.name})} "
                f"{item.observed_at.timestamp()}"
            )
        baseline = baselines.get(instance.id)
        if baseline:
            for name in ("cache_hits", "cache_queries", "draft_accepted", "draft_proposed"):
                if name in baseline[1]:
                    lines.append(f"studio_raw_{name}{render()} {baseline[1][name]}")
    return "\n".join(lines) + "\n"
