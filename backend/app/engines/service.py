"""Engine catalogue and asynchronous installation operations."""

from __future__ import annotations

import asyncio
import os
import shlex
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped]

from ..errors import AppError
from ..operations import OperationManager, run_command, safe_target_name, utc_now
from ..paths import DataPaths
from ..schemas.engines import Engine, Installation, Operation
from .adapters import ADAPTER_BY_ID, ADAPTERS
from .discovery import DiscoveryCoordinator, DiscoveryResult

PYTHON_PACKAGES = {
    "vllm": ("vllm", "--torch-backend=auto"),
    "sglang": ("sglang", "--prerelease", "allow"),
    "freetoken": ("freetoken[accel]",),
}
VERSION_COMMANDS = {"vllm": "vllm", "sglang": "sglang", "freetoken": "ft"}


class EngineService:
    def __init__(
        self,
        settings: Any,
        paths: DataPaths,
        *,
        operations: OperationManager | None = None,
        installation_in_use: Callable[[str], bool] | None = None,
    ) -> None:
        self.settings = settings
        self.paths = paths
        self.operations = operations or OperationManager(paths.engines_log_dir)
        self.installation_in_use = installation_in_use
        self._result: DiscoveryResult | None = None
        self.scan_error: str | None = None

    async def close(self) -> None:
        await self.operations.close()

    def scan(self) -> DiscoveryResult:
        try:
            self._result = DiscoveryCoordinator(self.settings, self.paths).scan()
        except Exception as exc:  # noqa: BLE001 - discovery boundary
            self.mark_scan_failed(exc)
            raise
        self.scan_error = None
        return self._result

    def mark_scan_failed(self, error: Exception) -> None:
        """Keep a usable empty catalogue after a non-fatal startup failure."""

        self.scan_error = str(error)
        self._result = DiscoveryResult(
            engines=[
                Engine(
                    id=adapter.engine_id,  # type: ignore[arg-type]
                    display_name=adapter.display_name,
                    installation_kinds=list(adapter.installation_kinds),  # type: ignore[arg-type]
                    installations=[],
                )
                for adapter in ADAPTERS
            ],
            installations=[],
            diagnostics=[{"code": "scan_failed", "message": str(error)}],
        )

    def result(self) -> DiscoveryResult:
        return self._result or self.scan()

    def engines(self) -> list[Engine]:
        return self.result().engines

    def installations(self) -> list[Installation]:
        return self.result().installations

    def get_installation(self, value: str) -> Installation:
        for installation in self.installations():
            if installation.id == value:
                return installation
        raise AppError("installation_not_found", "Установка движка не найдена", status_code=404)

    def get_engine(self, value: str) -> Engine:
        for engine in self.engines():
            if engine.id == value:
                return engine
        raise AppError("engine_not_found", "Движок не найден", status_code=404)

    def resolve_installation(self, engine_id: str, installation_id: str) -> Path:
        """Resolve only a currently verified installation for profile starts."""

        installation = self.get_installation(installation_id)
        if installation.engine_id != engine_id:
            raise ValueError("Установка принадлежит другому движку")
        if installation.status != "ready" or not installation.verified:
            raise ValueError(installation.diagnostic or "Установка движка недоступна")
        return installation.executable_path

    async def create_installation(self, engine_id: str, request: Any) -> dict[str, Any]:
        adapter = ADAPTER_BY_ID.get(engine_id)
        if adapter is None:
            raise AppError("engine_not_found", "Движок не найден", status_code=404)
        if request.kind == "binary":
            if adapter.kind != "binary":
                raise AppError("unsupported_operation", "Для этого движка нужен Python venv")
            path = request.path.expanduser()
            if not path.is_absolute():
                raise AppError(
                    "invalid_binary_path",
                    "Путь к бинарнику должен быть абсолютным",
                    status_code=422,
                )
            # Registration is still asynchronous so the caller gets the same
            # operation contract as managed venv creation.
            target_id = str(path.resolve(strict=False))

            async def register(operation: dict[str, Any]) -> None:
                result = await asyncio.to_thread(
                    DiscoveryCoordinator(self.settings, self.paths).scan,
                    explicit=[(engine_id, path)],
                )
                found = [
                    item
                    for item in result.installations
                    if item.root_path == path.resolve(strict=False)
                ]
                if not found or not found[0].verified:
                    raise RuntimeError(
                        found[0].diagnostic if found else "Бинарник не прошёл проверку"
                    )
                operation["stdout"] = "Бинарник зарегистрирован"
                operation["checked_at"] = utc_now()
                self._result = result

            return await self.operations.create("register_binary", target_id, register)
        if adapter.kind != "python_venv":
            raise AppError("unsupported_operation", "Для этого движка нужен бинарник")
        name = safe_target_name(request.venv_name)
        target_path = self.paths.venvs_dir / name
        if target_path.exists():
            raise AppError(
                "installation_conflict", "Окружение с таким именем уже существует", status_code=409
            )

        async def install(operation: dict[str, Any]) -> None:
            try:
                target_path.mkdir(parents=True, exist_ok=False)
                python = target_path / "bin" / "python"
                code, out, err = await run_command(
                    ["uv", "venv", "--python", "3.12", str(target_path)]
                )
                operation["stdout"] += out
                operation["stderr"] += err
                if code:
                    raise RuntimeError("uv venv не завершился успешно")
                package = PYTHON_PACKAGES[engine_id]
                install_command = ["uv", "pip", "install", "--python", str(python), *package]
                code, out, err = await run_command(install_command, cwd=str(target_path))
                operation["stdout"] += out
                operation["stderr"] += err
                if code:
                    raise RuntimeError("установка пакета движка завершилась ошибкой")
                await self._verify_after_install(engine_id, target_path, operation)
                operation["checked_at"] = utc_now()
            except asyncio.CancelledError:
                # Cancellation after the directory/venv exists is still a
                # failed installation.  Persist that fact before propagating
                # cancellation so the next scan keeps the environment visible
                # as a broken candidate.
                self._write_broken_manifest(target_path, engine_id, "Операция отменена")
                raise
            except Exception as exc:
                if not isinstance(exc, AppError):
                    self._write_broken_manifest(target_path, engine_id, str(exc))
                raise
            finally:
                await self._rescan_after_operation()

        # The destination is shared by all engines, so equal venv names must
        # conflict even when two requests target different engine packages.
        return await self.operations.create("install", f"venv:{name}", install)

    async def update_installation(self, installation_id: str) -> dict[str, Any]:
        installation = self.get_installation(installation_id)
        if installation.kind != "python_venv":
            raise AppError("unsupported_operation", "Обновление бинарного движка не поддерживается")
        if self.installation_in_use is not None and self.installation_in_use(installation_id):
            raise AppError(
                "installation_in_use",
                "Нельзя обновить окружение, пока из него запущена модель",
                status_code=409,
                details={"installation_id": installation_id},
            )
        target_id = installation_id

        async def update(operation: dict[str, Any]) -> None:
            try:
                if self.installation_in_use is not None and self.installation_in_use(
                    installation_id
                ):
                    raise AppError(
                        "installation_in_use",
                        "Нельзя обновить окружение, пока из него запущена модель",
                        status_code=409,
                        details={"installation_id": installation_id},
                    )
                python = installation.root_path / "bin" / "python"
                package = PYTHON_PACKAGES[installation.engine_id]
                command = ["uv", "pip", "install", "-U", "--python", str(python), *package]
                code, out, err = await run_command(command, cwd=str(installation.root_path))
                operation["stdout"] = out
                operation["stderr"] = err
                operation["exit_code"] = code
                if code:
                    raise RuntimeError("обновление пакета движка завершилось ошибкой")
                await self._verify_after_install(
                    installation.engine_id, installation.root_path, operation
                )
                operation["checked_at"] = utc_now()
            except asyncio.CancelledError:
                self._write_broken_manifest(
                    installation.root_path, installation.engine_id, "Операция отменена"
                )
                raise
            except Exception as exc:
                if not isinstance(exc, AppError):
                    self._write_broken_manifest(
                        installation.root_path, installation.engine_id, str(exc)
                    )
                raise
            finally:
                await self._rescan_after_operation()

        return await self.operations.create("update", target_id, update)

    async def maintenance(self, installation_id: str, command: str) -> dict[str, Any]:
        installation = self.get_installation(installation_id)
        if installation.kind != "python_venv":
            raise AppError("unsupported_operation", "Команды обслуживания доступны только для venv")
        try:
            parts = shlex.split(command)
        except ValueError as exc:
            raise AppError(
                "invalid_command", "Команда обслуживания имеет неверный синтаксис", status_code=422
            ) from exc
        if not parts or any("\x00" in item for item in parts):
            raise AppError("invalid_command", "Пустая команда обслуживания", status_code=422)

        async def maintenance_worker(operation: dict[str, Any]) -> None:
            environment = os.environ.copy()
            environment["PATH"] = (
                f"{installation.root_path / 'bin'}{os.pathsep}{environment.get('PATH', '')}"
            )
            code, out, err = await run_command(
                parts,
                cwd=str(installation.root_path),
                env=environment,
            )
            operation["stdout"] = out
            operation["stderr"] = err
            operation["exit_code"] = code
            if code:
                raise RuntimeError(f"команда завершилась с кодом {code}")

        return await self.operations.create("maintenance", installation_id, maintenance_worker)

    async def _verify_after_install(
        self, engine_id: str, root: Path, operation: dict[str, Any]
    ) -> None:
        executable = root / "bin" / VERSION_COMMANDS[engine_id]
        candidate = type(
            "Candidate",
            (),
            {
                "engine_id": engine_id,
                "kind": "python_venv",
                "root_path": root,
                "executable_path": executable,
                "source": "operation",
            },
        )()
        verification = await asyncio.to_thread(ADAPTER_BY_ID[engine_id].verify, candidate)
        if not verification.verified:
            raise RuntimeError(verification.diagnostic or "повторная проверка движка не прошла")
        packages = await self._resolved_packages(root)
        operation["resolved_packages"] = packages
        self._write_manifest(root, engine_id, verification.version, packages)

    async def _rescan_after_operation(self) -> None:
        """Finish the catalogue refresh even when the worker is cancelled."""

        scan_task = asyncio.create_task(asyncio.to_thread(self.scan))
        try:
            await asyncio.shield(scan_task)
        except asyncio.CancelledError:
            # The marker is already durable.  Wait for the thread to publish
            # the new result before letting OperationManager observe the
            # cancellation, so callers do not see a stale catalogue.
            await asyncio.gather(scan_task, return_exceptions=True)
            raise

    async def _resolved_packages(self, root: Path) -> dict[str, str]:
        python = root / "bin" / "python"
        code, out, _err = await run_command(
            ["uv", "pip", "freeze", "--python", str(python)], cwd=str(root)
        )
        if code:
            raise RuntimeError("не удалось получить список разрешённых пакетов")
        packages: dict[str, str] = {}
        for line in out.splitlines():
            if "==" in line:
                name, version = line.split("==", 1)
                packages[name.strip()] = version.strip()
        return packages

    @staticmethod
    def _write_manifest(
        root: Path,
        engine_id: str,
        version: str | None,
        packages: dict[str, str],
        *,
        status: str = "ready",
        diagnostic: str | None = None,
    ) -> None:
        path = root / ".llm-studio-engine.yaml"
        payload = {
            "schema_version": 1,
            "engine_id": engine_id,
            "version": version,
            "checked_at": datetime.now(UTC).isoformat(),
            "resolved_packages": packages,
            "status": status,
            "diagnostic": diagnostic,
        }
        path.write_text(yaml.safe_dump(payload, sort_keys=True), encoding="utf-8")

    @classmethod
    def _write_broken_manifest(cls, root: Path, engine_id: str, diagnostic: str) -> None:
        try:
            cls._write_manifest(root, engine_id, None, {}, status="broken", diagnostic=diagnostic)
        except OSError:
            # The operation error remains authoritative; discovery will still
            # inspect an executable if one was left in the environment.
            pass


def operation_data(value: dict[str, Any]) -> Operation:
    return Operation.model_validate(value)


__all__ = ["EngineService", "operation_data"]
