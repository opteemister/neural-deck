"""Management of remote OpenAI servers; all responses omit credentials."""

from fastapi import APIRouter, Depends, Request

from ..schemas.proxy_management import ConnectionRequest
from ..proxy.service import ProxyService
from .dependencies import get_proxy_service
from .management import success

router = APIRouter(prefix="/api/v1/proxy/openai/connections", tags=["proxy-routes"])


@router.get("")
async def list_connections(request: Request, service: ProxyService = Depends(get_proxy_service)):
    return success(request, {"items": service.connections().list()})


@router.post("", status_code=201)
async def create_connection(request: Request, payload: ConnectionRequest, service: ProxyService = Depends(get_proxy_service)):
    return success(request, service.connections().save(None, payload.name, payload.base_url, payload.api_key.get_secret_value() if payload.api_key is not None else None), status_code=201)


@router.patch("/{connection_id}")
async def update_connection(request: Request, connection_id: str, payload: ConnectionRequest, service: ProxyService = Depends(get_proxy_service)):
    return success(request, service.connections().save(connection_id, payload.name, payload.base_url, payload.api_key.get_secret_value() if payload.api_key is not None else None))


@router.get("/{connection_id}/models")
async def connection_models(request: Request, connection_id: str, refresh: bool = False, service: ProxyService = Depends(get_proxy_service)):
    return success(request, {"items": await service.connections().models(connection_id, refresh=refresh)})


@router.delete("/{connection_id}")
async def delete_connection(request: Request, connection_id: str, service: ProxyService = Depends(get_proxy_service)):
    service.delete_connection(connection_id)
    return success(request, {"id": connection_id})
