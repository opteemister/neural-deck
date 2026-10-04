"""FastAPI application entrypoint and process-scoped service composition."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Annotated, Any, cast
from uuid import uuid4

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, Response
from sqlalchemy.ext.asyncio import AsyncEngine
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.base import BaseHTTPMiddleware

from .api.dependencies import get_engine, get_runtime
from .api.instances import router as instances_router
from .api.logs import router as logs_router
from .api.management import router as management_router
from .api.metrics import export_router as metrics_export_router
from .api.metrics import router as metrics_router
from .api.profiles import router as profiles_router
from .api.proxy_routes import router as proxy_routes_router
from .config import Settings, load_settings
from .container import AppContainer
from .db import check_database, create_engine, create_session_factory, migrate_database
from .engines.service import EngineService
from .errors import (
    AppError,
    app_error_handler,
    http_error_handler,
    unhandled_error_handler,
    validation_error_handler,
)
from .mcp.server import MCPDependencies, mcp_lifespan, mount_mcp
from .metrics.service import MetricsService
from .models.catalogue import ModelCatalogue
from .operations import OperationManager
from .paths import build_paths, prepare_data_dirs, validate_extra_venv_roots
from .process.discovery import discover_external
from .process.launcher import SystemdLauncher
from .process.manager import ProcessManager
from .process.registry import ModelRegistry
from .process.runs import RunStore
from .profiles.repository import ProfileRepository
from .profiles.service import ProfileService
from .proxy.listener import ApplicationCORSMiddleware, model_api_listener
from .proxy.router import internal_router as internal_proxy_router
from .proxy.router import router as proxy_router
from .proxy.routes import RouteManager
from .proxy.service import ProxyService
from .schemas.common import SuccessEnvelope
from .schemas.system import HealthData
from .system import collect_status, collect_system_data, health_from_status
from .token_stats import RequestJournalService

# The application logger is also used as the parent for ``app.*`` modules.
# Explicit handlers send records to Docker's console and persist them under LOG_ROOT.
logger = logging.getLogger("app")
_MUTATING_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
_NON_MUTATING_ACTIONS = {"/api/v1/profiles/format-script"}


def _service_url(host: str, port: int) -> str:
    """Return the configured bind address in a copyable URL form."""

    display_host = host
    if ":" in display_host and not display_host.startswith("["):
        display_host = f"[{display_host}]"
    return f"http://{display_host}:{port}"


def _configure_application_logging(log_path: Path) -> None:
    """Configure one rotating application log without duplicating handlers."""

    formatter = logging.Formatter(
        fmt="%(asctime)s %(levelname)s %(name)s %(message)s",
        datefmt="%d.%m %H:%M:%S",
    )
    logger.disabled = False
    logger.propagate = False
    access_logger = logging.getLogger("uvicorn.access")
    access_logger.disabled = True
    access_logger.propagate = False
    for handler in list(access_logger.handlers):
        access_logger.removeHandler(handler)
        handler.close()
    if not any(
        getattr(handler, "_llm_studio_console_handler", False) for handler in logger.handlers
    ):
        console_handler = logging.StreamHandler()
        console_handler.setLevel(logging.INFO)
        console_handler.setFormatter(formatter)
        console_handler._llm_studio_console_handler = True  # type: ignore[attr-defined]
        logger.addHandler(console_handler)
    loggers = (logger, logging.getLogger("uvicorn.error"))
    for target in loggers:
        target.setLevel(logging.INFO)
        matching = [
            handler
            for handler in target.handlers
            if getattr(handler, "_llm_studio_file_handler", False)
        ]
        for handler in matching:
            if Path(getattr(handler, "baseFilename", "")) != log_path:
                target.removeHandler(handler)
                handler.close()
            else:
                handler.setFormatter(formatter)

        if any(
            Path(getattr(handler, "baseFilename", "")) == log_path
            for handler in target.handlers
            if getattr(handler, "_llm_studio_file_handler", False)
        ):
            continue

        handler = RotatingFileHandler(
            log_path,
            maxBytes=10 * 1024 * 1024,
            backupCount=5,
            encoding="utf-8",
        )
        handler.setFormatter(formatter)
        handler.setLevel(logging.INFO)
        handler._llm_studio_file_handler = True  # type: ignore[attr-defined]
        target.addHandler(handler)


class RequestIdMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        request.state.request_id = request.headers.get("X-Request-ID") or f"req_{uuid4().hex}"
        started_at = time.perf_counter()
        is_mutating_action = (
            request.method in _MUTATING_METHODS
            and request.url.path.startswith("/api/v1/")
            and request.url.path not in _NON_MUTATING_ACTIONS
        )
        if is_mutating_action:
            logger.info(
                "API mutation request: %s %s request_id=%s",
                request.method,
                request.url.path,
                request.state.request_id,
            )
        try:
            response = await call_next(request)
        except Exception:
            logger.exception(
                "HTTP %s %s failed request_id=%s duration_ms=%.1f",
                request.method,
                request.url.path,
                request.state.request_id,
                (time.perf_counter() - started_at) * 1000,
            )
            raise
        if response.status_code >= 400:
            log_method = logger.error if response.status_code >= 500 else logger.warning
            log_method(
                "HTTP %s %s status=%s request_id=%s duration_ms=%.1f",
                request.method,
                request.url.path,
                response.status_code,
                request.state.request_id,
                (time.perf_counter() - started_at) * 1000,
            )
        return response


def _default_frontend_dist() -> Path:
    return Path(__file__).resolve().parents[2] / "frontend" / "dist"


def _publish_runtime(app: FastAPI, runtime: AppContainer) -> None:
    """Publish the typed container and compatibility aliases on app.state."""

    app.state.runtime = runtime
    app.state.container = runtime
    app.state.settings = runtime.settings
    app.state.paths = runtime.paths
    app.state.db_engine = runtime.db_engine
    app.state.session_factory = runtime.session_factory
    app.state.operation_manager = runtime.operation_manager
    app.state.engine_service = runtime.engine_service
    app.state.installation_resolver = runtime.engine_service.resolve_installation
    app.state.model_catalogue = runtime.model_catalogue
    app.state.profile_repository = runtime.profile_repository
    app.state.profile_service = runtime.profile_service
    app.state.model_registry = runtime.model_registry
    app.state.process_manager = runtime.process_manager
    app.state.external_discovery = runtime.external_discovery
    app.state.journal_service = runtime.journal_service
    app.state.proxy_service = runtime.proxy_service
    app.state.metrics_service = runtime.metrics_service
    app.state.mcp_server = runtime.mcp_server
    app.state.runtime_diagnostics = runtime.diagnostics
    venv_diagnostics = validate_extra_venv_roots(runtime.settings)
    app.state.venv_root_diagnostics = venv_diagnostics
    for diagnostic in venv_diagnostics:
        runtime.add_diagnostic("venv_roots", diagnostic.code, diagnostic.message)


async def _scan_startup(runtime: AppContainer) -> None:
    """Run best-effort discovery in the background, preserving dependency order."""

    logger.info("Startup: обнаружение установленных движков")
    try:
        await asyncio.to_thread(runtime.engine_service.scan)
    except Exception as exc:  # noqa: BLE001 - scanner boundary is non-fatal
        runtime.engine_service.mark_scan_failed(exc)
        runtime.add_diagnostic("engine_discovery", "scan_failed", str(exc))

    logger.info("Startup: обнаружение процессов")
    try:
        await runtime.process_manager.refresh()
    except Exception as exc:  # noqa: BLE001 - scanner boundary is non-fatal
        runtime.add_diagnostic("process_discovery", "scan_failed", str(exc))
    if runtime.process_manager.discovery_error:
        runtime.add_diagnostic(
            "process_discovery", "scan_failed", runtime.process_manager.discovery_error
        )

    logger.info("Startup: однократный автозапуск профилей")
    try:
        await runtime.profile_service.autostart_once()
    except Exception as exc:  # noqa: BLE001 - autostart boundary is non-fatal
        runtime.add_diagnostic("profile_autostart", "autostart_failed", str(exc))

    for issue in runtime.profile_service.autostart_issues:
        runtime.add_diagnostic(
            "profile_autostart",
            issue.get("code", "autostart_failed"),
            f"{issue.get('profile', 'unknown')}: {issue.get('message', '')}".strip(),
        )

    logger.info("Фоновое сканирование моделей: повторное использование сохранённых хешей")
    try:
        await asyncio.to_thread(runtime.model_catalogue.scan)
    except Exception as exc:  # noqa: BLE001 - scanner boundary is non-fatal
        runtime.model_catalogue.mark_scan_failed(exc)
        runtime.add_diagnostic("model_catalogue", "scan_failed", str(exc))


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    app.state.started_monotonic = time.monotonic()
    settings: Settings = getattr(app.state, "settings", None) or load_settings()
    paths = build_paths(settings)
    # These paths are mandatory application prerequisites.  A failure here or
    # in database migration must prevent a partially usable service from
    # starting; optional scanners below are deliberately isolated.
    prepare_data_dirs(paths)
    _configure_application_logging(paths.studio_log_dir / "app.log")
    service_url = _service_url(settings.host, settings.port)
    logger.info("LLM Studio backend: запуск %s", service_url)
    logger.info(
        "LLM Studio backend: data_root=%s models_root=%s", paths.data_root, paths.models_root
    )
    db_engine = create_engine(paths.sqlite_path)
    runtime: AppContainer | None = None
    try:
        logger.info("LLM Studio backend: подготовка каталогов и базы данных")
        await migrate_database(settings, paths)
        await check_database(db_engine)
        operation_manager = OperationManager(paths.operations_log_dir)
        model_registry = ModelRegistry()
        external_discovery = discover_external
        launch_mode = os.environ.get("LLM_STUDIO_MODEL_LAUNCHER", "process")
        if launch_mode not in {"process", "systemd"}:
            raise ValueError("LLM_STUDIO_MODEL_LAUNCHER must be process or systemd")
        process_manager = ProcessManager(
            log_root=paths.profiles_log_dir,
            run_store=RunStore(db_engine),
            launcher=(SystemdLauncher(paths.runtime_dir) if launch_mode == "systemd" else None),
            registry=model_registry,
            external_discovery=external_discovery,
        )
        engine_service = EngineService(
            settings,
            paths,
            operations=operation_manager,
            installation_in_use=process_manager.is_installation_running,
        )
        model_catalogue = ModelCatalogue(paths, operations=operation_manager)
        profile_repository = ProfileRepository(paths.profiles_dir)
        profile_service = ProfileService(
            paths.profiles_dir,
            repository=profile_repository,
            process_manager=process_manager,
            installation_resolver=engine_service.resolve_installation,
        )
        session_factory = create_session_factory(db_engine)
        journal_service = RequestJournalService(session_factory=session_factory)
        route_manager = RouteManager(paths.config_dir, profile_repository)
        proxy_service = ProxyService(
            process_manager,
            journal=journal_service,
            route_manager=route_manager,
        )
        import secrets

        from .metrics.storage import MetricsStorage

        identity_path = paths.data_root / "studio-id"
        if not identity_path.exists():
            identity_path.write_text(secrets.token_hex(16), encoding="utf-8")
        await journal_service.interrupt_pending()
        metrics_service = MetricsService(
            process_manager,
            journal=journal_service,
            storage=MetricsStorage(session_factory),
            prometheus_url=settings.prometheus_url,
            prometheus_token=settings.prometheus_token,
            studio_id=identity_path.read_text().strip(),
        )
        runtime = AppContainer(
            settings=settings,
            paths=paths,
            db_engine=db_engine,
            session_factory=session_factory,
            operation_manager=operation_manager,
            engine_service=engine_service,
            model_catalogue=model_catalogue,
            profile_repository=profile_repository,
            profile_service=profile_service,
            model_registry=model_registry,
            process_manager=process_manager,
            journal_service=journal_service,
            proxy_service=proxy_service,
            metrics_service=metrics_service,
            external_discovery=external_discovery,
            mcp_server=getattr(app.state, "mcp_server", None),
        )
        _publish_runtime(app, runtime)
        model_catalogue.prepare_background_scan()
        await model_catalogue.start_update_scheduler()
        await metrics_service.start()
        logger.info("LLM Studio backend: готов принимать запросы %s", service_url)
    except BaseException:
        logger.exception("LLM Studio backend: запуск не выполнен")
        if runtime is None:
            await db_engine.dispose()
        else:
            await runtime.close()
        raise

    try:
        async with contextlib.AsyncExitStack() as stack:
            if getattr(app.state, "serve_model_api", False):
                proxy_app = create_proxy_app(runtime)
                await stack.enter_async_context(
                    model_api_listener(proxy_app, settings.host, settings.openai_port)
                )
                logger.info("OpenAI API: %s/v1", _service_url(settings.host, settings.openai_port))
            if runtime.mcp_server is not None:
                await stack.enter_async_context(mcp_lifespan(runtime.mcp_server))
            runtime.startup_task = asyncio.create_task(
                _scan_startup(runtime), name="studio-startup-discovery"
            )
            yield
    finally:
        await runtime.close()
        logger.info("LLM Studio backend: остановлен")


def _mcp_dependencies(app: FastAPI) -> MCPDependencies:
    async def get_system_state() -> object:
        runtime = app.state.runtime
        return await collect_system_data(runtime.process_manager)

    async def list_models() -> object:
        runtime = app.state.runtime
        await runtime.process_manager.refresh()
        return {
            "items": [
                {
                    "id": item.id,
                    "upstream_model": item.upstream_model,
                    "instance_id": item.instance_id,
                    "engine_id": item.engine_id,
                    "profile_name": item.profile_name,
                    "observed_at": item.observed_at,
                }
                for item in runtime.model_registry.list()
            ]
        }

    async def list_profiles() -> object:
        return await asyncio.to_thread(app.state.runtime.profile_service.list)

    async def start_profile(profile_name: str) -> object:
        return await app.state.runtime.profile_service.start(profile_name)

    async def stop_instance(instance_id: str) -> object:
        return await app.state.runtime.process_manager.stop(instance_id)

    return MCPDependencies(
        get_system_state=get_system_state,
        list_models=list_models,
        list_profiles=list_profiles,
        start_profile=start_profile,
        stop_instance=stop_instance,
    )


def _add_frontend_route(app: FastAPI, frontend_dist: Path) -> None:
    """Serve built frontend files and route client-side paths to index.html."""

    reserved_prefixes = ("/api", "/v1", "/mcp")
    resolved_dist = frontend_dist.expanduser().resolve(strict=False)

    @app.get("/{frontend_path:path}", include_in_schema=False)
    async def frontend_fallback(frontend_path: str) -> FileResponse:
        request_path = "/" + frontend_path
        if any(
            request_path == prefix or request_path.startswith(f"{prefix}/")
            for prefix in reserved_prefixes
        ):
            raise HTTPException(status_code=404, detail="Not Found")
        if not resolved_dist.is_dir():
            raise HTTPException(status_code=404, detail="Frontend build is not available")
        candidate = (resolved_dist / frontend_path).resolve(strict=False)
        try:
            candidate.relative_to(resolved_dist)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail="Not Found") from exc
        if candidate.is_file():
            return FileResponse(candidate)
        index = resolved_dist / "index.html"
        if index.is_file():
            return FileResponse(index)
        raise HTTPException(status_code=404, detail="Frontend build is not available")


def create_app(
    settings: Settings | None = None,
    *,
    frontend_dist: Path | None = None,
    serve_model_api: bool = False,
) -> FastAPI:
    app = FastAPI(
        title="LLM Studio",
        version="0.1.0",
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.serve_model_api = serve_model_api
    app.add_middleware(RequestIdMiddleware)
    app.add_exception_handler(AppError, cast(Any, app_error_handler))
    app.add_exception_handler(RequestValidationError, cast(Any, validation_error_handler))
    app.add_exception_handler(HTTPException, cast(Any, http_error_handler))
    app.add_exception_handler(StarletteHTTPException, cast(Any, http_error_handler))
    app.add_exception_handler(Exception, unhandled_error_handler)

    # Keep each management router registered once.  Future proxy/MCP routers
    # must be included before the frontend fallback route as well.
    app.include_router(management_router)
    app.include_router(proxy_routes_router)
    app.include_router(internal_proxy_router)
    app.include_router(profiles_router)
    # Register metrics before the greedy /instances/{instance_id:path}
    # detail route, which would otherwise consume the /metrics suffix.
    app.include_router(metrics_router)
    app.include_router(metrics_export_router)
    app.include_router(instances_router)
    app.include_router(logs_router)

    # The MCP server is mounted before the SPA fallback.  Its session manager
    # is entered from the host lifespan below, so the mounted Starlette app
    # remains stateless and does not create independent application services.
    mount_mcp(app, _mcp_dependencies(app))

    @app.get("/healthz", response_model=SuccessEnvelope[HealthData], include_in_schema=False)
    async def root_health(
        request: Request,
        engine: Annotated[AsyncEngine, Depends(get_engine)],
        runtime: Annotated[AppContainer, Depends(get_runtime)],
    ) -> dict[str, object]:
        model_count = runtime.model_count_for_status()
        data = health_from_status(
            await collect_status(
                engine,
                diagnostics=runtime.diagnostic_dicts(),
                openai_api_available=runtime.proxy_service is not None,
                openai_api_models=model_count,
                journal_error=runtime.journal_service.last_error,
            ),
            started_monotonic=getattr(request.app.state, "started_monotonic", None),
        )
        return {"data": data, "request_id": request.state.request_id}

    @app.get("/health", response_model=SuccessEnvelope[HealthData], include_in_schema=False)
    async def root_health_alias(
        request: Request,
        engine: Annotated[AsyncEngine, Depends(get_engine)],
        runtime: Annotated[AppContainer, Depends(get_runtime)],
    ) -> dict[str, object]:
        model_count = runtime.model_count_for_status()
        data = health_from_status(
            await collect_status(
                engine,
                diagnostics=runtime.diagnostic_dicts(),
                openai_api_available=runtime.proxy_service is not None,
                openai_api_models=model_count,
                journal_error=runtime.journal_service.last_error,
            ),
            started_monotonic=getattr(request.app.state, "started_monotonic", None),
        )
        return {"data": data, "request_id": request.state.request_id}

    # Use an ordinary FastAPI catch-all instead of a root mount so reserved API
    # prefixes keep the shared error envelope when an endpoint is unknown.
    _add_frontend_route(app, frontend_dist or _default_frontend_dist())
    return app


def create_proxy_app(runtime: AppContainer) -> FastAPI:
    app = FastAPI(title="LLM Studio OpenAI API", version="0.1.0")
    app.state.runtime = runtime
    app.add_middleware(RequestIdMiddleware)
    app.add_middleware(ApplicationCORSMiddleware, application_port=runtime.settings.port)
    app.add_exception_handler(AppError, cast(Any, app_error_handler))
    app.add_exception_handler(RequestValidationError, cast(Any, validation_error_handler))
    app.add_exception_handler(HTTPException, cast(Any, http_error_handler))
    app.add_exception_handler(StarletteHTTPException, cast(Any, http_error_handler))
    app.add_exception_handler(Exception, unhandled_error_handler)
    app.include_router(proxy_router)
    return app


app = create_app(serve_model_api=True)

__all__ = ["app", "create_app", "lifespan"]
