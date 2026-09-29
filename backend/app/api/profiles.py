"""Management routes for YAML profiles and managed starts."""

from __future__ import annotations

import asyncio
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
    return success(request, created, status_code=status.HTTP_201_CREATED)


@router.post("/format-script", response_model=SuccessEnvelope[FormatScriptResult])
async def format_profile_script(
    request: Request,
    payload: FormatScriptRequest,
    service: ProfileService = Depends(get_profile_service),
) -> JSONResponse:
    return success(request, service.format_script(payload.script))


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
    return success(request, updated)


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
    instance: Any = await service.start(profile_name)
    return success(request, instance, status_code=status.HTTP_202_ACCEPTED)


__all__ = ["get_profile_service", "router"]
