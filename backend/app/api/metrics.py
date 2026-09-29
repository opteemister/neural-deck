"""Management routes for engine metrics and proxy token statistics."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse

from ..metrics.service import MetricsService
from ..schemas.common import SuccessEnvelope
from ..schemas.instances import MetricList, TokenStatsData
from ..token_stats import RequestJournalService
from .dependencies import get_journal_service, get_metrics_service
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


__all__ = ["get_journal_service", "get_metrics_service", "router"]
