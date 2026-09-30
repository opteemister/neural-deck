"""Management routes for YAML profiles and managed starts."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import APIRouter, Depends, Request, status
from fastapi.responses import JSONResponse

from ..profiles.service import ProfileService
from ..schemas.common import SuccessEnvelope
from ..schemas.instances import Instance
from ..schemas.profiles import (
    FormatScriptRequest,
    FormatScriptResult,
    ProfileData,
    ProfileListData,
)
from .dependencies import get_profile_service
from .management import success

router = APIRouter(prefix="/api/v1/profiles", tags=["profiles"])
logger = logging.getLogger(__name__)


@router.get("", response_model=SuccessEnvelope[ProfileListData])
async def list_profiles(
    request: Request, service: ProfileService = Depends(get_profile_service)
) -> JSONResponse:
    return success(request, await asyncio.to_thread(service.list))


@router.post("", response_model=SuccessEnvelope[ProfileData], status_code=status.HTTP_201_CREATED)
async def create_profile(
    request: Request,
    profile: ProfileData,
    service: ProfileService = Depends(get_profile_service),
) -> JSONResponse:
    created = await asyncio.to_thread(service.create, profile)
    logger.info(
        "Профиль создан: name=%s request_id=%s",
        profile.name,
        getattr(request.state, "request_id", "req_unknown"),
    )
    return success(request, created, status_code=status.HTTP_201_CREATED)


@router.post("/format-script", response_model=SuccessEnvelope[FormatScriptResult])
async def format_profile_script(
    request: Request,
    payload: FormatScriptRequest,
    service: ProfileService = Depends(get_profile_service),
) -> JSONResponse:
    return success(request, service.format_script(payload.script))


@router.post("/start", response_model=SuccessEnvelope[Instance], status_code=status.HTTP_202_ACCEPTED)
async def start_configuration(
    request: Request,
    profile: ProfileData,
    service: ProfileService = Depends(get_profile_service),
) -> JSONResponse:
    logger.info(
        "Запуск несохранённой конфигурации профиля: name=%s request_id=%s",
        profile.name,
        getattr(request.state, "request_id", "req_unknown"),
    )
    instance = await service.start_configuration(profile)
    return success(request, instance, status_code=status.HTTP_202_ACCEPTED)


@router.get("/{profile_name}", response_model=SuccessEnvelope[ProfileData])
async def get_profile(
    request: Request,
    profile_name: str,
    service: ProfileService = Depends(get_profile_service),
) -> JSONResponse:
    return success(request, await asyncio.to_thread(service.get, profile_name))


@router.put("/{profile_name}", response_model=SuccessEnvelope[ProfileData])
async def put_profile(
    request: Request,
    profile_name: str,
    profile: ProfileData,
    service: ProfileService = Depends(get_profile_service),
) -> JSONResponse:
    updated = await asyncio.to_thread(service.put, profile_name, profile)
    logger.info(
        "Профиль изменён: name=%s request_id=%s",
        profile_name,
        getattr(request.state, "request_id", "req_unknown"),
    )
    return success(request, updated)


@router.delete("/{profile_name}", response_model=SuccessEnvelope[dict[str, str]])
async def delete_profile(
    request: Request,
    profile_name: str,
    service: ProfileService = Depends(get_profile_service),
) -> JSONResponse:
    deleted_name = await asyncio.to_thread(service.delete, profile_name)
    logger.info(
        "Профиль удалён: name=%s request_id=%s",
        deleted_name,
        getattr(request.state, "request_id", "req_unknown"),
    )
    return success(request, {"name": deleted_name})


@router.post(
    "/{profile_name}/start",
    response_model=SuccessEnvelope[Instance],
    status_code=status.HTTP_202_ACCEPTED,
)
async def start_profile(
    request: Request,
    profile_name: str,
    service: ProfileService = Depends(get_profile_service),
) -> JSONResponse:
    request_id = getattr(request.state, "request_id", "req_unknown")
    logger.info("Запуск профиля: name=%s request_id=%s", profile_name, request_id)
    instance: Any = await service.start(profile_name)
    logger.info(
        "Профиль принят к запуску: name=%s instance_id=%s pid=%s request_id=%s",
        profile_name,
        getattr(instance, "id", "unknown"),
        getattr(instance, "pid", "unknown"),
        request_id,
    )
    return success(request, instance, status_code=status.HTTP_202_ACCEPTED)


__all__ = ["get_profile_service", "router"]
