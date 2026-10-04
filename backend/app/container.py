"""Runtime composition for one LLM Studio application process.

The domain modules are intentionally usable on their own in unit tests.  This
container is the one place where the application wires their long lived
instances together.  FastAPI dependencies expose the same objects through
``request.app.state`` for a small, stable integration seam.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from .config import Settings
from .engines.service import EngineService
from .models.catalogue import ModelCatalogue
from .operations import OperationManager
from .paths import DataPaths
from .process.discovery import ExternalObservation, discover_external
from .process.manager import ProcessManager
from .process.registry import ModelRegistry
from .profiles.repository import ProfileRepository
from .profiles.service import ProfileService
from .token_stats import RequestJournalService

if TYPE_CHECKING:
    from .metrics.service import MetricsService
    from .proxy.service import ProxyService

ExternalDiscovery = Callable[..., Awaitable[list[ExternalObservation]]]


@dataclass(frozen=True, slots=True)
class RuntimeDiagnostic:
    """A non-fatal startup/runtime problem exposed to status diagnostics."""

    component: str
    code: str
    message: str

    def as_dict(self) -> dict[str, str]:
        return {
            "component": self.component,
            "code": self.code,
            "message": self.message,
        }


@dataclass(slots=True)
class AppContainer:
    """All process-scoped services owned by a FastAPI application."""

    settings: Settings
    paths: DataPaths
    db_engine: AsyncEngine
    session_factory: async_sessionmaker[AsyncSession]
    operation_manager: OperationManager
    engine_service: EngineService
    model_catalogue: ModelCatalogue
    profile_repository: ProfileRepository
    profile_service: ProfileService
    model_registry: ModelRegistry
    process_manager: ProcessManager
    journal_service: RequestJournalService
    proxy_service: ProxyService
    metrics_service: MetricsService
    external_discovery: ExternalDiscovery = discover_external
    diagnostics: list[RuntimeDiagnostic] = field(default_factory=list)
    mcp_server: Any | None = None
    startup_task: asyncio.Task[None] | None = None

    def add_diagnostic(self, component: str, code: str, message: str) -> None:
        if any(
            item.component == component and item.code == code and item.message == message
            for item in self.diagnostics
        ):
            return
        self.diagnostics.append(RuntimeDiagnostic(component, code, message))

    def diagnostic_dicts(self) -> list[dict[str, str]]:
        # Process discovery owns its current scan failure, including SQLite
        # restore errors. A successful later scan clears the startup warning.
        result = [
            item.as_dict()
            for item in self.diagnostics
            if not (item.component == "process_discovery" and item.code == "scan_failed")
        ]
        # Scanner failures can happen after startup (for example a model root
        # becoming unreadable).  Expose the current failure on every status
        # read instead of leaving a stale green component behind.
        for component, code, error in (
            ("engine_discovery", "scan_failed", self.engine_service.scan_error),
            ("model_catalogue", "scan_failed", self.model_catalogue.scan_error),
            ("process_discovery", "scan_failed", self.process_manager.discovery_error),
        ):
            if error and not any(
                item["component"] == component and item["code"] == code for item in result
            ):
                result.append({"component": component, "code": code, "message": error})
        return result

    def model_count_for_status(self) -> int:
        """Read the current registry count without refreshing process state.

        Health and status probes are intentionally read-only. Process
        discovery and child-process reconciliation happen at startup or via
        explicit instance/system refresh endpoints.
        """

        return len(self.model_registry.list())

    def replace_paths(self, paths: DataPaths, catalogue: ModelCatalogue) -> None:
        """Update mutable path dependent services after a settings patch."""

        self.paths = paths
        self.model_catalogue = catalogue

    async def close(self) -> None:
        """Release application resources without stopping model processes."""

        # ProcessManager.close only cancels Studio watcher tasks.  It does not
        # send signals to managed processes, because shutdown must not kill
        # model servers that can outlive this web process.
        try:
            if self.startup_task is not None:
                self.startup_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await self.startup_task
                self.startup_task = None
            await self.model_catalogue.stop_update_scheduler()
            await self.metrics_service.close()
            await self.proxy_service.close()
            await self.process_manager.close()
        finally:
            try:
                await self.operation_manager.close()
            finally:
                await self.db_engine.dispose()


__all__ = ["AppContainer", "ExternalDiscovery", "RuntimeDiagnostic"]
