"""Application service for profile files and managed launches."""

from __future__ import annotations

import asyncio
import builtins
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ..errors import AppError
from ..schemas.profiles import FormatScriptResult, ProfileData, ProfileListData
from .formatting import format_script
from .repository import ProfileRepository


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
        self.autostart_issues: list[dict[str, str]] = []

    def list(self) -> ProfileListData:
        return self.repository.list()

    def get(self, name: str) -> ProfileData:
        return self.repository.get(name)

    def create(self, profile: ProfileData) -> ProfileData:
        return self.repository.create(profile)

    def put(self, name: str, profile: ProfileData) -> ProfileData:
        return self.repository.put(name, profile)

    def format_script(self, script: str) -> FormatScriptResult:
        value, changed, reason = format_script(script)
        return FormatScriptResult(script=value, changed=changed, reason=reason)

    async def start(self, name: str) -> Any:
        # YAML reads and validation are synchronous filesystem work.  Keep
        # them outside the event loop for API and MCP callers.
        profile = await asyncio.to_thread(self.repository.get, name)
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
        return await self.process_manager.start_profile(profile, str(engine_path))

    async def autostart_once(self) -> builtins.list[Any]:
        if self._autostart_ran:
            return []
        self._autostart_ran = True
        result: builtins.list[Any] = []
        profiles = await asyncio.to_thread(self.repository.list)
        for profile in profiles.items:
            if profile.autostart:
                try:
                    result.append(await self.start(profile.name))
                except Exception as exc:  # noqa: BLE001 - autostart is non-fatal
                    # A broken autostart profile must not prevent Studio from
                    # starting.  The error remains visible through listing.
                    code = exc.code if isinstance(exc, AppError) else "autostart_failed"
                    self.autostart_issues.append(
                        {"profile": profile.name, "code": code, "message": str(exc)}
                    )
                    continue
        return result


__all__ = ["ProfileService"]
