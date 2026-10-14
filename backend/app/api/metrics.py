"""Management routes for engine metrics and proxy token statistics."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse, Response

from ..metrics.service import MetricsService
from ..schemas.common import SuccessEnvelope
from ..schemas.instances import MetricList, TokenStatsData
from ..statistics import StatisticsService
from ..token_stats import RequestJournalService
from .dependencies import get_journal_service, get_metrics_service, get_statistics_service
from .management import success

router = APIRouter(prefix="/api/v1", tags=["metrics"])


@router.get(
    "/instances/{instance_id:path}/metrics",
    response_model=SuccessEnvelope[MetricList],
)
async def instance_metrics(
    request: Request,
    instance_id: str,
    service: MetricsService = Depends(get_metrics_service),
) -> JSONResponse:
    return success(request, await service.collect(instance_id))


@router.get("/token-stats", response_model=SuccessEnvelope[TokenStatsData])
async def token_stats(
    request: Request,
    profile_name: str | None = None,
    from_: datetime | None = Query(default=None, alias="from"),
    to: datetime | None = None,
    service: RequestJournalService = Depends(get_journal_service),
) -> JSONResponse:
    return success(
        request,
        await service.stats(profile_name=profile_name, from_=from_, to=to),
    )


@router.get("/statistics")
async def statistics(
    request: Request,
    period: Literal["week", "month", "quarter", "half_year", "year", "custom"] = "month",
    model_name: str | None = None,
    profile_name: str | None = None,
    from_: datetime | None = Query(default=None, alias="from"),
    to: datetime | None = None,
    service: StatisticsService = Depends(get_statistics_service),
) -> JSONResponse:
    return success(
        request,
        await service.read(
            period=period,
            from_=from_,
            to=to,
            model_name=model_name,
            profile_name=profile_name,
        ),
    )


__all__ = ["get_journal_service", "get_metrics_service", "router"]


@router.get("/metrics")
async def metrics(
    request: Request,
    mode: Literal[
        "current", "bootstrap", "history", "peaks", "usage", "token-history", "aggregates"
    ] = "current",
    profile_name: str | None = None,
    pid: int | None = Query(None, gt=0),
    scope: Literal["profile", "all"] = "profile",
    from_: datetime | None = Query(None, alias="from"),
    to: datetime | None = None,
    limit: int = Query(120, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    client_id: int | None = Query(None, gt=0),
    service: MetricsService = Depends(get_metrics_service),
) -> JSONResponse:
    return success(
        request,
        await service.read(
            mode=mode,
            profile_name=profile_name,
            pid=pid,
            scope=scope,
            from_=from_,
            to=to,
            limit=limit,
            offset=offset,
            client_id=client_id,
        ),
    )


export_router = APIRouter()


@router.get("/metrics/daily")
@router.get("/metrics/concurrency")
async def historical_metrics(
    request: Request,
    profile_name: str | None = None,
    days: int = Query(7, ge=1, le=3650),
    service: MetricsService = Depends(get_metrics_service),
) -> JSONResponse:
    if service.storage is None:
        return success(request, {"items": [], "days": days, "source": "unavailable"})
    return success(request, await service.storage.historical(
        profile_name=profile_name, days=days,
        curve=request.url.path.endswith("/concurrency"),
    ))


@router.get("/metrics/runs")
async def daily_metric_runs(
    request: Request,
    service: MetricsService = Depends(get_metrics_service),
) -> JSONResponse:
    items = await service.storage.daily_runs() if service.storage else []
    return success(request, {"items": items})


@export_router.get("/metrics", include_in_schema=False)
async def export_metrics(service: MetricsService = Depends(get_metrics_service)) -> Response:
    return Response(service.export(), media_type="text/plain; version=0.0.4; charset=utf-8")
