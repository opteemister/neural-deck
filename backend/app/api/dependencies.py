"""FastAPI dependencies for the application runtime."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncEngine

from ..config import Settings
from ..container import AppContainer
from ..engines.service import EngineService
from ..models.catalogue import ModelCatalogue
from ..operations import OperationManager
from ..paths import DataPaths
from ..process.manager import ProcessManager
from ..process.registry import ModelRegistry
from ..profiles.repository import ProfileRepository
from ..profiles.service import ProfileService

if TYPE_CHECKING:
    from ..metrics.service import MetricsService
    from ..proxy.service import ProxyService
    from ..token_stats import RequestJournalService


def get_runtime(request: Request) -> AppContainer:
    return cast(AppContainer, request.app.state.runtime)


def get_settings(request: Request) -> Settings:
    return cast(Settings, request.app.state.settings)


def get_paths(request: Request) -> DataPaths:
    return cast(DataPaths, request.app.state.paths)


def get_engine(request: Request) -> AsyncEngine:
    return cast(AsyncEngine, request.app.state.db_engine)


def get_engine_service(request: Request) -> EngineService:
    return cast(EngineService, request.app.state.engine_service)


def get_model_catalogue(request: Request) -> ModelCatalogue:
    return cast(ModelCatalogue, request.app.state.model_catalogue)


def get_operation_manager(request: Request) -> OperationManager:
    return get_runtime(request).operation_manager


def get_model_registry(request: Request) -> ModelRegistry:
    return get_runtime(request).model_registry


def get_process_manager(request: Request) -> ProcessManager:
    return get_runtime(request).process_manager


def get_profile_repository(request: Request) -> ProfileRepository:
    return get_runtime(request).profile_repository


def get_profile_service(request: Request) -> ProfileService:
    return get_runtime(request).profile_service


def get_journal_service(request: Request) -> RequestJournalService:
    return get_runtime(request).journal_service


def get_proxy_service(request: Request) -> ProxyService:
    return get_runtime(request).proxy_service


def get_metrics_service(request: Request) -> MetricsService:
    return get_runtime(request).metrics_service
