"""Minimal management endpoints available in the backend foundation."""

from __future__ import annotations

import asyncio
import contextlib
import os
import tempfile
from pathlib import Path

import yaml  # type: ignore[import-untyped]
from fastapi import APIRouter, Depends, Request, status
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncEngine

from ..config import Settings
from ..engines.service import EngineService, operation_data
from ..errors import AppError
from ..models.catalogue import ModelCatalogue
from ..paths import DataPaths, effective_settings, prepare_data_dirs, validate_models_root
from ..process.manager import ProcessManager
from ..process.registry import ModelRegistry
from ..schemas.common import SuccessEnvelope
from ..schemas.engines import (
    EngineList,
    InstallationCreate,
    InstallationList,
    MaintenanceRequest,
    Operation,
)
from ..schemas.models import (
    DownloadRequest,
    HfRepoData,
    HfSearchData,
    LocalModelList,
    ModelRegistryData,
)
from ..schemas.system import HealthData, SettingsData, SettingsPatch, StatusData, SystemData
from ..system import collect_status, collect_system_data, health_from_status
from .dependencies import (
    get_engine,
    get_engine_service,
    get_model_catalogue,
    get_model_registry,
    get_paths,
    get_process_manager,
    get_runtime,
    get_settings,
)

router = APIRouter(prefix="/api/v1", tags=["management"])


def success(
    request: Request, data: object, *, status_code: int = status.HTTP_200_OK
) -> JSONResponse:
    envelope = SuccessEnvelope(data=data, request_id=request.state.request_id)
    return JSONResponse(status_code=status_code, content=envelope.model_dump(mode="json"))


@router.get("/system", response_model=SuccessEnvelope[SystemData])
async def system(request: Request) -> JSONResponse:
    process_manager = get_process_manager(request)
    try:
        data = await collect_system_data(process_manager)
    except Exception as exc:  # noqa: BLE001 - process scanner is best effort
        runtime = get_runtime(request)
        runtime.add_diagnostic("process_discovery", "scan_failed", str(exc))
        data = await collect_system_data()
    else:
        runtime = get_runtime(request)
        if process_manager.discovery_error:
            runtime.add_diagnostic(
                "process_discovery", "scan_failed", process_manager.discovery_error
            )
    return success(request, data)


@router.get("/status", response_model=SuccessEnvelope[StatusData])
async def status_endpoint(
    request: Request,
    engine: AsyncEngine = Depends(get_engine),
) -> JSONResponse:
    runtime = get_runtime(request)
    model_count = await runtime.refresh_for_status()
    return success(
        request,
        await collect_status(
            engine,
            diagnostics=runtime.diagnostic_dicts(),
            openai_api_available=runtime.proxy_service is not None,
            openai_api_models=model_count,
            journal_error=runtime.journal_service.last_error,
        ),
    )


@router.get("/health", response_model=SuccessEnvelope[HealthData])
@router.get("/healthz", response_model=SuccessEnvelope[HealthData], include_in_schema=False)
async def health(request: Request, engine: AsyncEngine = Depends(get_engine)) -> JSONResponse:
    runtime = get_runtime(request)
    model_count = await runtime.refresh_for_status()
    return success(
        request,
        health_from_status(
            await collect_status(
                engine,
                diagnostics=runtime.diagnostic_dicts(),
                openai_api_available=runtime.proxy_service is not None,
                openai_api_models=model_count,
                journal_error=runtime.journal_service.last_error,
            )
        ),
    )


@router.get("/settings", response_model=SuccessEnvelope[SettingsData])
async def settings(
    request: Request,
    app_settings: Settings = Depends(get_settings),
    paths: DataPaths = Depends(get_paths),
) -> JSONResponse:
    return success(request, effective_settings(app_settings, paths))


@router.patch("/settings", response_model=SuccessEnvelope[SettingsData])
async def patch_settings(
    request: Request,
    payload: SettingsPatch,
    app_settings: Settings = Depends(get_settings),
    paths: DataPaths = Depends(get_paths),
) -> JSONResponse:
    try:
        models_root = validate_models_root(payload.models_root)
    except ValueError as exc:
        raise AppError(
            "invalid_models_root",
            str(exc),
            status_code=422,
        ) from exc

    # Build and prepare every effective path before touching settings.yaml or
    # app state.  A failed prerequisite therefore leaves both unchanged.
    candidate_settings = app_settings.model_copy(update={"models_root": models_root})
    from ..paths import build_paths

    updated = build_paths(candidate_settings, include_saved_settings=False)
    try:
        prepare_data_dirs(updated)
    except OSError as exc:
        raise AppError(
            "invalid_models_root",
            "Новые пути приложения недоступны",
            status_code=422,
            details={"reason": str(exc)[:200]},
        ) from exc

    catalogue = ModelCatalogue(updated, operations=get_runtime(request).operation_manager)
    await asyncio.to_thread(catalogue.scan)

    settings_path = paths.settings_path
    try:
        _atomic_write_settings(settings_path, models_root)
    except OSError as exc:
        raise AppError(
            "settings_save_failed",
            "Не удалось атомарно сохранить settings.yaml",
            status_code=500,
            details={"reason": str(exc)[:200]},
        ) from exc

    app_settings.models_root = models_root
    # Publish the validated bundle only after the atomic settings replacement.
    runtime = get_runtime(request)
    runtime.replace_paths(updated, catalogue)
    request.app.state.paths = updated
    request.app.state.model_catalogue = catalogue
    return success(request, effective_settings(app_settings, updated))


def _atomic_write_settings(path: Path, models_root: Path) -> None:
    """Write settings beside the destination and replace it atomically."""

    payload = yaml.safe_dump({"models_root": str(models_root)}, sort_keys=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: str | None = None
    try:
        descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        temporary = None
        try:
            directory_fd = os.open(path.parent, os.O_DIRECTORY)
        except OSError:
            directory_fd = None
        if directory_fd is not None:
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
    finally:
        if temporary is not None:
            with contextlib.suppress(OSError):
                os.unlink(temporary)


@router.get("/engines", response_model=SuccessEnvelope[EngineList])
async def engines(
    request: Request, service: EngineService = Depends(get_engine_service)
) -> JSONResponse:
    return success(request, {"items": await asyncio.to_thread(service.engines)})


@router.post("/engines/scan", response_model=SuccessEnvelope[EngineList])
async def scan_engines(
    request: Request, service: EngineService = Depends(get_engine_service)
) -> JSONResponse:
    result = await asyncio.to_thread(service.scan)
    return success(request, {"items": result.engines})


@router.get("/engines/{engine_id}", response_model=SuccessEnvelope[object])
async def engine_detail(
    request: Request, engine_id: str, service: EngineService = Depends(get_engine_service)
) -> JSONResponse:
    return success(request, await asyncio.to_thread(service.get_engine, engine_id))


@router.get("/installations", response_model=SuccessEnvelope[InstallationList])
async def installations(
    request: Request, service: EngineService = Depends(get_engine_service)
) -> JSONResponse:
    return success(request, {"items": await asyncio.to_thread(service.installations)})


@router.get("/installations/{installation_id}", response_model=SuccessEnvelope[object])
async def installation_detail(
    request: Request, installation_id: str, service: EngineService = Depends(get_engine_service)
) -> JSONResponse:
    return success(
        request,
        await asyncio.to_thread(service.get_installation, installation_id),
    )


@router.post(
    "/engines/{engine_id}/installations",
    response_model=SuccessEnvelope[Operation],
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_installation(
    request: Request,
    engine_id: str,
    payload: InstallationCreate,
    service: EngineService = Depends(get_engine_service),
) -> JSONResponse:
    operation = await service.create_installation(engine_id, payload)
    return success(request, operation_data(operation), status_code=status.HTTP_202_ACCEPTED)


@router.post(
    "/installations/{installation_id}/update",
    response_model=SuccessEnvelope[Operation],
    status_code=status.HTTP_202_ACCEPTED,
)
async def update_installation(
    request: Request,
    installation_id: str,
    service: EngineService = Depends(get_engine_service),
) -> JSONResponse:
    operation = await service.update_installation(installation_id)
    return success(request, operation_data(operation), status_code=status.HTTP_202_ACCEPTED)


@router.post(
    "/installations/{installation_id}/maintenance",
    response_model=SuccessEnvelope[Operation],
    status_code=status.HTTP_202_ACCEPTED,
)
async def maintenance(
    request: Request,
    installation_id: str,
    payload: MaintenanceRequest,
    service: EngineService = Depends(get_engine_service),
) -> JSONResponse:
    operation = await service.maintenance(installation_id, payload.command)
    return success(request, operation_data(operation), status_code=status.HTTP_202_ACCEPTED)


@router.get("/operations/{operation_id}", response_model=SuccessEnvelope[Operation])
async def operation_detail(
    request: Request,
    operation_id: str,
    service: EngineService = Depends(get_engine_service),
    catalogue: ModelCatalogue = Depends(get_model_catalogue),
    process_manager: ProcessManager = Depends(get_process_manager),
) -> JSONResponse:
    try:
        value = service.operations.get(operation_id)
    except AppError:
        try:
            value = catalogue.operations.get(operation_id)
        except AppError:
            value = process_manager.get_operation(operation_id).model_dump(mode="json")
    return success(request, operation_data(value))


@router.get("/models/local", response_model=SuccessEnvelope[LocalModelList])
async def local_models(
    request: Request, catalogue: ModelCatalogue = Depends(get_model_catalogue)
) -> JSONResponse:
    return success(request, {"items": await asyncio.to_thread(catalogue.items)})


@router.get("/models", response_model=SuccessEnvelope[ModelRegistryData])
async def models_registry(
    request: Request,
    process_manager: ProcessManager = Depends(get_process_manager),
    registry: ModelRegistry = Depends(get_model_registry),
) -> JSONResponse:
    await process_manager.refresh()
    return success(
        request,
        {
            "items": [
                {
                    "id": item.id,
                    "upstream_model": item.upstream_model,
                    "instance_id": item.instance_id,
                    "engine_id": item.engine_id,
                    "profile_name": item.profile_name,
                    "observed_at": item.observed_at,
                }
                for item in registry.list()
            ]
        },
    )


@router.post("/models/local/scan", response_model=SuccessEnvelope[LocalModelList])
async def scan_local_models(
    request: Request, catalogue: ModelCatalogue = Depends(get_model_catalogue)
) -> JSONResponse:
    return success(request, {"items": await asyncio.to_thread(catalogue.scan)})


@router.get("/models/hf/search", response_model=SuccessEnvelope[HfSearchData])
async def hf_search(
    request: Request, q: str, catalogue: ModelCatalogue = Depends(get_model_catalogue)
) -> JSONResponse:
    return success(request, await catalogue.hf_search(q))


@router.get("/models/hf/repos/{namespace}/{name}", response_model=SuccessEnvelope[HfRepoData])
async def hf_repo(
    request: Request,
    namespace: str,
    name: str,
    revision: str | None = None,
    catalogue: ModelCatalogue = Depends(get_model_catalogue),
) -> JSONResponse:
    return success(request, await catalogue.hf_repo(f"{namespace}/{name}", revision))


@router.get("/models/hf/repos/{namespace}/{name}/files", response_model=SuccessEnvelope[HfRepoData])
async def hf_repo_files(
    request: Request,
    namespace: str,
    name: str,
    revision: str | None = None,
    catalogue: ModelCatalogue = Depends(get_model_catalogue),
) -> JSONResponse:
    return success(request, await catalogue.hf_files(f"{namespace}/{name}", revision))


@router.post(
    "/models/downloads",
    response_model=SuccessEnvelope[Operation],
    status_code=status.HTTP_202_ACCEPTED,
)
async def download_models(
    request: Request,
    payload: DownloadRequest,
    catalogue: ModelCatalogue = Depends(get_model_catalogue),
) -> JSONResponse:
    operation = await catalogue.download(payload)
    return success(request, operation_data(operation), status_code=status.HTTP_202_ACCEPTED)
