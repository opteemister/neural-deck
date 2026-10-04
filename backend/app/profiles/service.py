"""Application service for profile files and managed launches."""

from __future__ import annotations

import asyncio
import builtins
import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ..errors import AppError
from ..schemas.profiles import FormatScriptResult, ProfileData, ProfileListData
from .formatting import enable_command_metrics, format_script, substitute_placeholders
from .repository import ProfileRepository

logger = logging.getLogger(__name__)


class ProfileService:
    def __init__(
        self,
        profiles_dir: Path,
        *,
        process_manager: Any | None = None,
        installation_resolver: Callable[[str, str], str | Path] | None = None,
        repository: ProfileRepository | None = None,
    ) -> None:
        from ..process.manager import ProcessManager

        self.repository = repository or ProfileRepository(profiles_dir)
        self.process_manager = process_manager or ProcessManager()
        self.installation_resolver = installation_resolver
        self._autostart_ran = False
        self._autostart_attempted: set[str] = set()
        self.autostart_issues: list[dict[str, str]] = []

    def list(self) -> ProfileListData:
        return self.repository.list()

    def get(self, name: str) -> ProfileData:
        return self.repository.get(name)

    def create(self, profile: ProfileData) -> ProfileData:
        return self.repository.create(profile)

    def put(self, name: str, profile: ProfileData) -> ProfileData:
        return self.repository.put(name, profile)

    def delete(self, name: str) -> str:
        return self.repository.delete(name)

    def format_script(self, script: str) -> FormatScriptResult:
        value, changed, reason = format_script(script)
        return FormatScriptResult(script=value, changed=changed, reason=reason)

    async def start(self, name: str) -> Any:
        # YAML reads and validation are synchronous filesystem work.  Keep
        # them outside the event loop for API and MCP callers.
        profile = await asyncio.to_thread(self.repository.get, name)
        return await self.start_configuration(profile)

    async def start_configuration(self, profile: ProfileData) -> Any:
        """Launch a validated configuration without writing a profile file."""
        profile, engine_path = await self._prepare_configuration(profile)
        return await self.process_manager.start_profile(profile, engine_path)

    async def preview_configuration(self, profile: ProfileData) -> dict[str, str]:
        """Render the launch command without starting a process or changing YAML."""
        profile, engine_path = await self._prepare_configuration(profile)
        return {"command": substitute_placeholders(
            profile.command,
            engine=engine_path,
            model=str(profile.model.path),
            model_name=profile.model_name,
            model_draft=str(profile.model.draft_path) if profile.model.draft_path else None,
        )}

    async def _prepare_configuration(self, profile: ProfileData) -> tuple[ProfileData, str]:
        engine_path: str | Path | None = None
        if self.installation_resolver is not None:
            try:
                engine_path = await asyncio.to_thread(
                    self.installation_resolver,
                    profile.engine.id,
                    profile.engine.installation_id,
                )
            except KeyError as exc:
                raise AppError(
                    "installation_not_found",
                    "Установка движка не найдена",
                    status_code=404,
                    details={"installation_id": profile.engine.installation_id},
                ) from exc
            except ValueError as exc:
                raise AppError("installation_unavailable", str(exc), status_code=409) from exc
        if engine_path is None:
            # The engine catalogue can be connected later.  Do not silently
            # turn a missing installation into a PATH lookup.
            raise AppError(
                "installation_unavailable",
                "Для профиля не подтверждена установка движка",
                status_code=409,
                details={"installation_id": profile.engine.installation_id},
            )
        profile = profile.model_copy(
            update={
                "command": enable_command_metrics(
                    profile.command, profile.engine.id, profile.enable_metrics
                )
            }
        )
        return profile, str(engine_path)

    async def autostart_once(self) -> builtins.list[Any]:
        """Make one best-effort autostart pass for this app lifetime.

        The guard is set before any await so a second caller cannot start the
        same profiles again. A failed attempt is recorded and is never retried
        until a new application process is started.
        """

        if self._autostart_ran:
            return []
        self._autostart_ran = True
        result: builtins.list[Any] = []
        profiles = await asyncio.to_thread(self.repository.list)
        for profile in profiles.items:
            if not profile.autostart or profile.name in self._autostart_attempted:
                continue
            self._autostart_attempted.add(profile.name)
            try:
                instances = await self.process_manager.refresh(probe_models=False)
                if any(
                    item.source == "managed"
                    and item.profile_name == profile.name
                    and item.state not in {"exited", "error"}
                    for item in instances.items
                ):
                    continue
                logger.info("Автозапуск профиля: попытка name=%s", profile.name)
                result.append(await self.start(profile.name))
            except Exception as exc:  # noqa: BLE001 - autostart is non-fatal
                # A broken autostart profile must not prevent Studio from
                # starting. The failed attempt is never retried in this run.
                code = exc.code if isinstance(exc, AppError) else "autostart_failed"
                self.autostart_issues.append(
                    {"profile": profile.name, "code": code, "message": str(exc)}
                )
                logger.error(
                    "Автозапуск профиля не выполнен: name=%s code=%s error=%s",
                    profile.name,
                    code,
                    exc,
                )
        return result


__all__ = ["ProfileService"]
