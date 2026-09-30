"""Browser-facing API for application, profile and operation logs."""

from __future__ import annotations

import re
from urllib.parse import quote

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import PlainTextResponse

from ..logs import LogsService
from ..paths import DataPaths
from ..schemas.common import SuccessEnvelope
from ..schemas.logs import LogData, LogDeleteData, LogSourceList
from .dependencies import get_paths
from .management import success

router = APIRouter(prefix="/api/v1/logs", tags=["logs"])


def _service(paths: DataPaths) -> LogsService:
    return LogsService(paths)


@router.get("", response_model=SuccessEnvelope[LogSourceList])
async def list_logs(
    request: Request, paths: DataPaths = Depends(get_paths)
) -> object:
    return success(request, _service(paths).list_sources())


@router.get("/{source_id:path}/download")
async def download_log(
    source_id: str, paths: DataPaths = Depends(get_paths)
) -> PlainTextResponse:
    source, body = _service(paths).download(source_id)
    safe_name = re.sub(r"[^A-Za-z0-9._-]+", "-", source.label).strip("-") or "log"
    filename = f"{safe_name}.log"
    return PlainTextResponse(
        body,
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"},
    )


@router.get("/{source_id:path}", response_model=SuccessEnvelope[LogData])
async def read_log(
    request: Request,
    source_id: str,
    before: int | None = Query(default=None, ge=0),
    limit: int = Query(default=500, ge=1, le=5_000),
    paths: DataPaths = Depends(get_paths),
) -> object:
    return success(request, _service(paths).read(source_id, before=before, limit=limit))


@router.delete("/{source_id:path}", response_model=SuccessEnvelope[LogDeleteData])
async def delete_log(
    request: Request, source_id: str, paths: DataPaths = Depends(get_paths)
) -> object:
    return success(request, _service(paths).delete(source_id))


__all__ = ["router"]
