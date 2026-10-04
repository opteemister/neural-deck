"""System and status probes that do not depend on a specific inference engine."""

from __future__ import annotations

import asyncio
import csv
import io
import math
import shutil
import subprocess
import time
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Literal

import psutil
from sqlalchemy.ext.asyncio import AsyncEngine

from .db import check_database
from .schemas.system import (
    ComponentStatus,
    CpuData,
    GpuData,
    HealthData,
    InstanceData,
    RamData,
    StatusData,
    SystemData,
)

if TYPE_CHECKING:
    from .process.manager import ProcessManager


def now_utc() -> datetime:
    return datetime.now(UTC)


async def collect_system_data(process_manager: ProcessManager | None = None) -> SystemData:
    cpu = CpuData(
        cores=max(psutil.cpu_count(logical=True) or 1, 1),
        usage_percent=psutil.cpu_percent(interval=None),
    )
    memory = psutil.virtual_memory()
    instances = []
    if process_manager is not None:
        instances = [
            InstanceData.model_validate(item.model_dump())
            for item in (await process_manager.refresh()).items
        ]
    return SystemData(
        cpu=cpu,
        ram=RamData(used_bytes=memory.used, total_bytes=memory.total),
        gpus=await collect_gpus(),
        instances=instances,
    )


async def collect_gpus() -> list[GpuData]:
    executable = shutil.which("nvidia-smi")
    if executable is None:
        return []
    try:
        completed = await asyncio.to_thread(
            subprocess.run,
            [
                executable,
                "--query-gpu=index,name,memory.used,memory.total,utilization.gpu,temperature.gpu,uuid,power.draw,power.limit,fan.speed",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            check=False,
            text=True,
            timeout=2,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    if completed.returncode != 0:
        return []

    gpus: list[GpuData] = []
    for row in csv.reader(io.StringIO(completed.stdout), skipinitialspace=True):
        if len(row) != 10:
            continue
        try:
            fan = float(row[9])
            fan = fan if math.isfinite(fan) and fan >= 0 else None
        except ValueError:
            fan = None
        power_values: list[float | None] = []
        for raw in row[7:9]:
            try:
                watts = float(raw)
                power_values.append(watts if math.isfinite(watts) and watts >= 0 else None)
            except ValueError:
                power_values.append(None)
        try:
            usage = float(row[4])
            temperature = float(row[5])
            gpus.append(
                GpuData(
                    index=int(row[0]),
                    name=row[1],
                    uuid=row[6],
                    memory_used_bytes=int(float(row[2]) * 1024 * 1024),
                    memory_total_bytes=int(float(row[3]) * 1024 * 1024),
                    usage_percent=usage,
                    temperature_celsius=temperature,
                    fan_speed_percent=fan,
                    power_watts=power_values[0],
                    power_limit_watts=power_values[1],
                )
            )
        except (TypeError, ValueError):
            continue
    return gpus


async def collect_status(
    engine: AsyncEngine,
    *,
    diagnostics: Sequence[dict[str, str]] = (),
    openai_api_available: bool | None = None,
    openai_api_ready: bool | None = None,
    openai_api_models: int | None = None,
    journal_error: str | None = None,
) -> StatusData:
    checked_at = now_utc()
    # ``openai_api_available`` describes whether the endpoint can be served by
    # the application.  Readiness additionally requires at least one current
    # registered route/model; no request is made to the service itself here.
    if openai_api_available is None:
        openai_api_available = openai_api_ready
    if openai_api_available is False:
        openai_component = ComponentStatus(
            name="openai_api",
            state="error",
            message="OpenAI-совместимый API недоступен",
            checked_at=checked_at,
        )
    elif openai_api_available is True and openai_api_models is not None and openai_api_models > 0:
        openai_component = ComponentStatus(
            name="openai_api",
            state="ok",
            message="OpenAI-совместимый API работает и имеет доступный маршрут модели",
            checked_at=checked_at,
        )
    elif openai_api_available is True:
        openai_component = ComponentStatus(
            name="openai_api",
            state="degraded",
            message="OpenAI-совместимый API доступен, но зарегистрированных моделей нет",
            checked_at=checked_at,
        )
    else:
        openai_component = ComponentStatus(
            name="openai_api",
            state="degraded",
            message="OpenAI-совместимый API ещё не реализован",
            checked_at=checked_at,
        )
    components: list[ComponentStatus] = [
        ComponentStatus(name="backend", state="ok", checked_at=checked_at),
        openai_component,
    ]
    if journal_error:
        components.append(
            ComponentStatus(
                name="request_journal",
                state="degraded",
                message=f"Журнал запросов недоступен: {journal_error[:200]}",
                checked_at=checked_at,
            )
        )
    try:
        await check_database(engine)
    except Exception:
        components.append(
            ComponentStatus(
                name="sqlite",
                state="error",
                message="SQLite недоступна",
                checked_at=checked_at,
            )
        )
    else:
        components.append(ComponentStatus(name="sqlite", state="ok", checked_at=checked_at))

    for diagnostic in diagnostics:
        component = diagnostic.get("component", "runtime")
        message = diagnostic.get("message") or diagnostic.get("code") or "Диагностика недоступна"
        components.append(
            ComponentStatus(
                name=component,
                state="degraded",
                message=message,
                checked_at=checked_at,
            )
        )

    states = {component.state for component in components}
    overall: Literal["ok", "degraded", "error"] = (
        "error" if "error" in states else "degraded" if "degraded" in states else "ok"
    )
    return StatusData(overall=overall, components=components)


def health_from_status(status: StatusData, *, started_monotonic: float | None = None) -> HealthData:
    return HealthData(
        status="ok" if status.overall == "ok" else "degraded",
        uptime_seconds=None if started_monotonic is None else max(0, time.monotonic() - started_monotonic),
    )
