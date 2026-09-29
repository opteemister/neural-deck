"""Management routes for managed and externally discovered instances."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request, status
from fastapi.responses import JSONResponse

from ..process.manager import ProcessManager
from ..schemas.common import SuccessEnvelope
from ..schemas.instances import Instance, InstanceList, LogsData, ProcessOperation
from .dependencies import get_process_manager
from .management import success

router = APIRouter(prefix="/api/v1/instances", tags=["instances"])


@router.get("", response_model=SuccessEnvelope[InstanceList])
async def list_instances(
    request: Request, manager: ProcessManager = Depends(get_process_manager)
) -> JSONResponse:
    return success(request, await manager.refresh())


@router.post(
    "/{instance_id:path}/stop",
    response_model=SuccessEnvelope[ProcessOperation],
    status_code=status.HTTP_202_ACCEPTED,
)
async def stop_instance(
    request: Request,
    instance_id: str,
    manager: ProcessManager = Depends(get_process_manager),
) -> JSONResponse:
    return success(request, await manager.stop(instance_id), status_code=status.HTTP_202_ACCEPTED)


@router.get("/{instance_id:path}/logs", response_model=SuccessEnvelope[LogsData])
async def instance_logs(
    request: Request,
    instance_id: str,
    tail: int = Query(default=200, ge=1, le=10_000),
    manager: ProcessManager = Depends(get_process_manager),
) -> JSONResponse:
    return success(request, await manager.logs(instance_id, tail))


@router.get("/{instance_id:path}", response_model=SuccessEnvelope[Instance])
async def get_instance(
    request: Request,
    instance_id: str,
    manager: ProcessManager = Depends(get_process_manager),
) -> JSONResponse:
    return success(request, await manager.get(instance_id))


__all__ = ["get_process_manager", "router"]
