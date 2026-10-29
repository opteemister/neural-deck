"""Management endpoints for proxy auto-routes and aliases."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request, status
from fastapi.responses import JSONResponse

from ..proxy.service import ProxyService
from ..schemas.common import SuccessEnvelope
from ..schemas.proxy import (
    AliasCreateRequest,
    ProxyRoutesData,
    RouteOrderRequest,
)
from .dependencies import get_proxy_service
from .management import success

router = APIRouter(prefix="/api/v1/proxy/routes", tags=["proxy-routes"])


@router.get("", response_model=SuccessEnvelope[ProxyRoutesData])
async def list_routes(
    request: Request, service: ProxyService = Depends(get_proxy_service)
) -> JSONResponse:
    return success(request, await service.route_data())


@router.get("/choices")
async def model_choices(request: Request, route_name: str | None = None, refresh: bool = False, service: ProxyService = Depends(get_proxy_service)):
    return success(request, await service.model_choices(route_name, refresh))


@router.get("/details/{route_name:path}")
async def get_route(request: Request, route_name: str, service: ProxyService = Depends(get_proxy_service)):
    return success(request, await service.get_route(route_name))


@router.post(
    "/aliases",
    response_model=SuccessEnvelope[dict[str, object]],
    status_code=status.HTTP_201_CREATED,
)
async def create_alias(
    request: Request,
    payload: AliasCreateRequest,
    service: ProxyService = Depends(get_proxy_service),
) -> JSONResponse:
    return success(
        request,
        await service.create_alias(payload.name, payload.profile_ids, payload.remote.model_dump() if payload.remote else None, payload.candidates),
        status_code=status.HTTP_201_CREATED,
    )


@router.patch("/aliases/{alias_name:path}", response_model=SuccessEnvelope[dict[str, object]])
async def update_alias(
    request: Request,
    alias_name: str,
    payload: AliasCreateRequest,
    service: ProxyService = Depends(get_proxy_service),
) -> JSONResponse:
    return success(request, await service.update_alias(alias_name, payload.name, payload.profile_ids, payload.remote.model_dump() if payload.remote else None, payload.candidates))


@router.put("/{route_name:path}", response_model=SuccessEnvelope[dict[str, object]])
async def update_route_order(
    request: Request,
    route_name: str,
    payload: RouteOrderRequest,
    service: ProxyService = Depends(get_proxy_service),
) -> JSONResponse:
    return success(request, await service.update_route_order(route_name, payload.profile_ids, payload.candidate_ids))


@router.delete(
    "/aliases/{alias_name:path}", response_model=SuccessEnvelope[dict[str, str]]
)
async def delete_alias(
    request: Request,
    alias_name: str,
    service: ProxyService = Depends(get_proxy_service),
) -> JSONResponse:
    await service.delete_alias(alias_name)
    return success(request, {"name": alias_name})


__all__ = ["router"]
