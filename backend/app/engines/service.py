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
from .discovery import DiscoveryCoordinator, DiscoveryResult, installation_id
from .installation_templates import frontend_installation_template, installation_template
from .templates import LAUNCH_TEMPLATES

VERSION_COMMANDS = {"vllm": "vllm", "sglang": "sglang", "freetoken": "ft", "image": "comfyui"}


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
        self.operations = operations or OperationManager(paths.operations_log_dir)
        self.installation_in_use = installation_in_use
        self._result: DiscoveryResult | None = None
        self.scan_error: str | None = None
        self._binary_registry = self.paths.data_root / 'config' / 'engine-binaries.yaml'
        self._binary_paths: dict[str, str] = {}
        if self._binary_registry.exists():
            entries = yaml.safe_load(self._binary_registry.read_text(encoding='utf-8'))
            if not isinstance(entries, dict) or any(not isinstance(path, str) or engine not in ADAPTER_BY_ID or ADAPTER_BY_ID[engine].kind != 'binary' for path, engine in entries.items()):
                raise ValueError('Неверный реестр бинарных движков')
            self._binary_paths = entries

    async def close(self) -> None:
        await self.operations.close()

    def scan(self) -> DiscoveryResult:
        try:
            self._result = DiscoveryCoordinator(self.settings, self.paths).scan(explicit=[(engine, Path(path)) for path, engine in self._binary_paths.items()])
            registered = {str(Path(path).resolve(strict=False)) for path in self._binary_paths}
            for item in self._result.installations:
                item.registered = str(item.executable_path) in registered
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
                    command_template=LAUNCH_TEMPLATES[adapter.engine_id],
                    installation_template=frontend_installation_template(adapter.engine_id, self.paths.runtime_dir / 'builds'),
                    installation_root=str(self.paths.venvs_dir) if adapter.kind == 'python_venv' else '',
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

    def forget_installation(self, value: str) -> None:
        installation = self.get_installation(value)
        if self.installation_in_use and self.installation_in_use(value):
            raise AppError('installation_in_use', 'Установка используется запущенной моделью', status_code=409)
        paths = [path for path, engine in self._binary_paths.items()
                 if engine == installation.engine_id and Path(path).resolve(strict=False) == installation.executable_path]
        if not paths:
            raise AppError('installation_not_registered', 'Установка найдена автоматически, сохранённой записи нет', status_code=409)
        updated = {path: engine for path, engine in self._binary_paths.items() if path not in paths}
        temporary = self._binary_registry.with_suffix('.tmp')
        temporary.write_text(yaml.safe_dump(updated), encoding='utf-8')
        temporary.replace(self._binary_registry)
        self._binary_paths = updated
        self.scan()

    def resolve_installation(self, engine_id: str, installation_id: str) -> Path:
        """Resolve only a currently verified installation for profile starts."""

        installation = self.get_installation(installation_id)
        if installation.engine_id != engine_id:
            raise ValueError("Установка принадлежит другому движку")
        if installation.status != "ready" or not installation.verified:
            raise ValueError(installation.diagnostic or "Установка движка недоступна")
        return installation.executable_path

    def installation_target(self, engine_id: str, request: Any) -> Path:
        adapter = ADAPTER_BY_ID.get(engine_id)
        if adapter is None:
            raise AppError("engine_not_found", "Движок не найден", status_code=404)
        if request.kind != adapter.kind:
            raise AppError("unsupported_operation", "Способ установки не соответствует движку", status_code=422)
        if request.kind == 'python_venv':
            return self.paths.venvs_dir / safe_target_name(request.venv_name)
        path = request.path.expanduser()
        if not path.is_absolute() or path == Path('/'):
            raise AppError("invalid_binary_path", "Укажите абсолютный путь к бинарнику", status_code=422)
        return path.parent.resolve(strict=False) / path.name

    def preview_installation(self, engine_id: str, request: Any) -> dict[str, str]:
        target = self.installation_target(engine_id, request)
        return {'command': installation_template(engine_id, target, self.paths.data_root / 'runtime' / 'builds')}

    async def create_installation(self, engine_id: str, request: Any) -> dict[str, Any]:
        target = self.installation_target(engine_id, request)
        command = request.command if request.command is not None else self.preview_installation(engine_id, request)['command']
        if not command.strip() or '\x00' in command:
            raise AppError('invalid_command', 'Пустая или недопустимая команда', status_code=422)
        target_id = installation_id(engine_id, request.kind, target)
        if self.installation_in_use and self.installation_in_use(target_id):
            raise AppError('installation_in_use', 'Нельзя менять установку, пока из неё запущена модель', status_code=409)

        async def install(operation: dict[str, Any]) -> None:
            try:
                if self.installation_in_use and self.installation_in_use(target_id):
                    raise AppError('installation_in_use', 'Установка используется запущенной моделью', status_code=409)
                target.parent.mkdir(parents=True, exist_ok=True)
                build_root = self.paths.data_root / 'runtime' / 'builds'
                build_root.mkdir(parents=True, exist_ok=True)
                self.operations.append_output(operation, 'stdout', '$ ' + command + '\n')
                code, out, err = await run_command(
                    ['/bin/bash', '-c', command], cwd=str(build_root), timeout_seconds=14400,
                    # Persist managed interpreters beside venvs in both native
                    # deployments and containers; venvs reference them by path.
                    env={**os.environ, 'UV_PYTHON_INSTALL_DIR': str(self.paths.runtime_dir / 'uv-python')},
                    on_stdout_line=lambda line: self.operations.append_output(operation, 'stdout', line),
                    on_stderr_line=lambda line: self.operations.append_output(operation, 'stderr', line),
                )
                operation['exit_code'] = code
                if code:
                    raise RuntimeError(f'Команда установки завершилась с кодом {code}')
                if request.kind == 'python_venv':
                    await self._verify_after_install(engine_id, target, operation)
                else:
                    result = await asyncio.to_thread(DiscoveryCoordinator(self.settings, self.paths).scan, explicit=[(engine_id, target)])
                    found = next((item for item in result.installations if item.engine_id == engine_id and item.executable_path == target.resolve(strict=False)), None)
                    if found is None or not found.verified:
                        raise RuntimeError(found.diagnostic if found else 'Бинарник не прошёл проверку')
                    self._binary_paths[str(target)] = engine_id
                    self._binary_registry.parent.mkdir(parents=True, exist_ok=True)
                    temporary = self._binary_registry.with_suffix('.tmp')
                    temporary.write_text(yaml.safe_dump(self._binary_paths), encoding='utf-8')
                    temporary.replace(self._binary_registry)
                operation['checked_at'] = utc_now()
            except BaseException as exc:
                if request.kind == 'python_venv' and target.is_dir() and not isinstance(exc, AppError):
                    self._write_broken_manifest(target, engine_id, str(exc) or 'Операция отменена')
                raise
            finally:
                await self._rescan_after_operation()

        operation_target = 'build:llama.cpp' if engine_id == 'llama.cpp' else target_id if request.kind == 'python_venv' else f'binary:{target}'
        return await self.operations.create('install', operation_target, install)

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
                command = installation_template(installation.engine_id, installation.root_path, self.paths.runtime_dir / 'builds', update=True)
                self.operations.append_output(operation, 'stdout', '$ ' + command + '\n')
                code, out, err = await run_command(
                    ['/bin/bash', '-c', command], cwd=str(installation.root_path),
                    env={**os.environ, 'UV_PYTHON_INSTALL_DIR': str(self.paths.runtime_dir / 'uv-python')},
                    on_stdout_line=lambda line: self.operations.append_output(operation, 'stdout', line),
                    on_stderr_line=lambda line: self.operations.append_output(operation, 'stderr', line),
                )
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
        path = root / ".neural-deck-engine.yaml"
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
