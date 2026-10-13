"""Engine catalogue and asynchronous installation operations."""

from __future__ import annotations

import asyncio
import os
import shlex
import subprocess
import tempfile
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped]
from pydantic import ValidationError

from ..errors import AppError
from ..operations import OperationManager, run_command, safe_target_name, utc_now
from ..paths import DataPaths
from ..schemas.engines import (
    Engine,
    EngineDefinition,
    EngineDefinitionList,
    GenericInstallationCreate,
    Installation,
    Operation,
    ResolvedEngineProfile,
)
from ..schemas.profiles import ProfileData
from .adapters import ADAPTER_BY_ID
from .definition_registry import EngineDefinitionRegistry
from .discovery import DiscoveryCoordinator, DiscoveryResult, installation_id
from .installation_templates import installation_template
from ..profiles.formatting import substitute_placeholders

VERSION_COMMANDS = {"vllm": "vllm", "sglang": "sglang", "freetoken": "ft", "image": "comfyui"}


class EngineService:
    def __init__(
        self,
        settings: Any,
        paths: DataPaths,
        *,
        operations: OperationManager | None = None,
        installation_in_use: Callable[[str], bool] | None = None,
        definition_registry: EngineDefinitionRegistry | None = None,
    ) -> None:
        self.settings = settings
        self.paths = paths
        self.operations = operations or OperationManager(paths.operations_log_dir)
        self.installation_in_use = installation_in_use
        self.definition_registry = definition_registry or EngineDefinitionRegistry(
            self.paths.data_root / "engines"
        )
        self._result: DiscoveryResult | None = None
        self.scan_error: str | None = None
        self._binary_registry = self.paths.data_root / 'config' / 'engine-binaries.yaml'
        self._installation_registry = self.paths.data_root / "config" / "engine-installations.yaml"
        self._custom_installations: dict[str, dict[str, str]] = {}
        self._pending_definition_installations: dict[str, int] = {}
        if self._installation_registry.exists():
            loaded = yaml.safe_load(self._installation_registry.read_text(encoding="utf-8"))
            if not isinstance(loaded, dict):
                raise ValueError("Неверный реестр установок движков")
            for key, item in loaded.items():
                if (not isinstance(key, str) or not isinstance(item, dict)
                        or not isinstance(item.get("engine_id"), str)
                        or not isinstance(item.get("root_path"), str)
                        or not isinstance(item.get("executable_path"), str)):
                    raise ValueError("Неверная запись установки движка")
                self._custom_installations[key] = item
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
            found = DiscoveryCoordinator(self.settings, self.paths).scan(
                explicit=[(engine, Path(path)) for path, engine in self._binary_paths.items()]
            )
            installations: dict[str, Installation] = {}
            for item in found.installations:
                if item.engine_id == "image":
                    item.engine_id = "comfyui"
                    item.id = installation_id("comfyui", item.kind, item.root_path)
                installations[item.id] = item
            for registry_key, entry in self._custom_installations.items():
                try:
                    definition = self.get_definition(entry["engine_id"])
                    root = Path(entry["root_path"]).expanduser().resolve(strict=False)
                    executable = Path(entry["executable_path"]).expanduser().resolve(strict=False)
                    manifest = self._read_install_manifest(root)
                    diagnostic = None
                    verified = executable.is_file() and os.access(executable, os.X_OK)
                    status = "ready" if verified else "broken"
                    if manifest and manifest.get("status") == "broken":
                        verified = False
                        status = "broken"
                        diagnostic = str(manifest.get("diagnostic") or "Последняя операция завершилась ошибкой")
                    elif not verified:
                        diagnostic = "Исполняемый файл отсутствует или недоступен"
                    kind = "custom"
                    item = Installation(
                        id=registry_key, engine_id=definition.id, kind=kind,
                        root_path=root, executable_path=executable,
                        version=(manifest.get("version") if manifest else None),
                        status=status, verified=verified, diagnostic=diagnostic,
                        registered=True,
                    )
                    installations[item.id] = item
                except (KeyError, OSError, ValueError):
                    continue
            definitions = self.definition_registry.list()
            by_engine: dict[str, list[Installation]] = {definition.id: [] for definition in definitions}
            for item in installations.values():
                if item.engine_id in by_engine:
                    by_engine[item.engine_id].append(item)
            engines: list[Engine] = []
            for definition in definitions:
                adapter = ADAPTER_BY_ID.get(definition.id)
                engines.append(Engine(
                    id=definition.id,
                    display_name=definition.name,
                    command_template=definition.command,
                    installation_template=(definition.install_script or ""),
                    installation_root=str(self.paths.venvs_dir),
                    installation_kinds=(list(adapter.installation_kinds) if adapter else ["custom"]),
                    installations=sorted(by_engine[definition.id], key=lambda value: str(value.root_path)),
                    types=definition.types,
                    api=definition.api,
                    ui_url=definition.ui_url,
                ))
            self._result = DiscoveryResult(
                engines=engines,
                installations=sorted(installations.values(), key=lambda value: (value.engine_id, str(value.root_path))),
                diagnostics=found.diagnostics,
            )
            registered = {str(Path(path).resolve(strict=False)) for path in self._binary_paths}
            for item in self._result.installations:
                item.registered = item.registered or str(item.executable_path) in registered
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
                    id=definition.id,
                    display_name=definition.name,
                    command_template=definition.command,
                    installation_template=definition.install_script or "",
                    installation_root=str(self.paths.venvs_dir),
                    installation_kinds=(list(ADAPTER_BY_ID[definition.id].installation_kinds)
                                        if definition.id in ADAPTER_BY_ID else ["custom"]),
                    installations=[],
                    types=definition.types,
                    api=definition.api,
                    ui_url=definition.ui_url,
                )
                for definition in self.definition_registry.list()
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

    def list_definitions(self) -> EngineDefinitionList:
        items = self.definition_registry.list()
        return EngineDefinitionList(items=items, issues=list(self.definition_registry.issues))

    def get_definition(self, engine_id: str) -> EngineDefinition:
        try:
            return self.definition_registry.get(engine_id)
        except KeyError as exc:
            raise AppError("engine_not_found", "Определение движка не найдено", status_code=404) from exc
        except (OSError, ValueError) as exc:
            raise AppError("engine_definition_invalid", str(exc), status_code=422) from exc

    def create_definition(self, definition: EngineDefinition) -> EngineDefinition:
        try:
            return self.definition_registry.create(definition)
        except FileExistsError as exc:
            raise AppError("engine_definition_conflict", "Определение с таким ID уже существует", status_code=409) from exc
        except (OSError, ValueError) as exc:
            raise AppError("engine_definition_invalid", str(exc), status_code=422) from exc
        finally:
            self._result = None

    def update_definition(self, engine_id: str, definition: EngineDefinition) -> EngineDefinition:
        try:
            return self.definition_registry.put(engine_id, definition)
        except KeyError as exc:
            raise AppError("engine_not_found", "Определение движка не найдено", status_code=404) from exc
        except (OSError, ValueError, PermissionError) as exc:
            raise AppError("engine_definition_invalid", str(exc), status_code=422) from exc
        finally:
            self._result = None

    def delete_definition(self, engine_id: str) -> None:
        try:
            definition_path = self.definition_registry.custom_path(engine_id)
            if definition_path.is_file() and not definition_path.is_symlink():
                installation_refs = sorted(
                    installation_id
                    for installation_id, entry in self._custom_installations.items()
                    if entry.get("engine_id") == engine_id
                )
                pending_installations = self._pending_definition_installations.get(engine_id, 0)
                profile_refs: list[str] = []
                unreadable_profiles: list[str] = []
                for profile_path in sorted(self.paths.profiles_dir.glob("*.yaml")):
                    if profile_path.is_symlink():
                        unreadable_profiles.append(profile_path.name)
                        continue
                    if not profile_path.is_file():
                        continue
                    try:
                        raw = yaml.safe_load(profile_path.read_text(encoding="utf-8"))
                        profile = ProfileData.model_validate(raw)
                        if profile.name != profile_path.stem:
                            raise ValueError("имя в профиле не совпадает с именем файла")
                    except (
                        OSError,
                        UnicodeError,
                        yaml.YAMLError,
                        ValidationError,
                        TypeError,
                        ValueError,
                    ):
                        unreadable_profiles.append(profile_path.name)
                        continue
                    if profile.engine.id == engine_id:
                        profile_refs.append(profile.name)
                if installation_refs or pending_installations or profile_refs or unreadable_profiles:
                    reasons = []
                    if installation_refs:
                        reasons.append(f"установок: {len(installation_refs)}")
                    if pending_installations:
                        reasons.append(f"установок выполняется: {pending_installations}")
                    if profile_refs:
                        reasons.append(f"профилей: {len(profile_refs)}")
                    if unreadable_profiles:
                        reasons.append("есть профили, чьи ссылки нельзя проверить")
                    raise AppError(
                        "engine_definition_in_use",
                        "Нельзя удалить определение: " + ", ".join(reasons),
                        status_code=409,
                        details={
                            "installation_ids": installation_refs,
                            "pending_installations": pending_installations,
                            "profile_names": profile_refs,
                            "unreadable_profiles": unreadable_profiles,
                        },
                    )
            self.definition_registry.delete(engine_id)
        except KeyError as exc:
            raise AppError("engine_definition_not_found", "Пользовательское определение не найдено", status_code=404) from exc
        except (OSError, PermissionError, ValueError) as exc:
            raise AppError("engine_definition_delete_failed", str(exc), status_code=409) from exc
        finally:
            self._result = None

    def resolve_profile(
        self, profile: ProfileData, installation: Installation | None = None
    ) -> ResolvedEngineProfile:
        definition = self.get_definition(profile.engine.id)
        chosen = installation or self.get_installation(profile.engine.installation_id)
        if chosen.engine_id != definition.id or not chosen.verified or chosen.status != "ready":
            raise AppError("installation_unavailable", chosen.diagnostic or "Установка недоступна", status_code=409)
        target = chosen.root_path
        executable = chosen.executable_path.resolve(strict=False)
        if not executable.is_file() or not os.access(executable, os.X_OK):
            raise AppError("installation_unavailable", "Executable из определения недоступен", status_code=409)
        default_working_directory = target if target.is_dir() else target.parent
        work_text = (definition.working_directory or str(default_working_directory)).replace("{{target}}", str(target))
        work_text = work_text.replace("{{engine}}", str(executable))
        working_directory = Path(work_text).expanduser()
        if not working_directory.is_absolute():
            working_directory = target / working_directory
        if not working_directory.is_dir():
            raise AppError("installation_unavailable", "Рабочая папка установки недоступна", status_code=409)
        model_path = profile.model.path if profile.model else None
        model_name = profile.model_name or None
        types = list(definition.types)
        if profile.types is not None:
            if not set(profile.types).issubset(types):
                raise AppError("profile_types_invalid", "Типы профиля не могут расширять определение движка", status_code=422)
            types = list(profile.types)
        protocol = definition.api.protocol if definition.api else "none"
        base_url = definition.api.base_url if definition.api else None
        if profile.api is not None:
            if profile.api.protocol is not None:
                protocol = profile.api.protocol
            if "base_url" in profile.api.model_fields_set:
                base_url = profile.api.base_url
        ui_url = profile.ui_url if profile.ui_url is not None else definition.ui_url
        environment = {**definition.environment, **profile.environment}
        return ResolvedEngineProfile(
            definition=definition, installation=chosen, executable_path=executable,
            working_directory=working_directory, command=profile.command,
            environment=environment, types=types, api_protocol=protocol,
            api_base_url=base_url, ui_url=ui_url,
            healthcheck=definition.healthcheck, model_path=model_path, model_name=model_name,
        )

    @staticmethod
    def _read_install_manifest(root: Path) -> dict[str, Any] | None:
        try:
            value = yaml.safe_load((root / ".neural-deck-engine.yaml").read_text(encoding="utf-8"))
        except (OSError, UnicodeError, yaml.YAMLError):
            return None
        return value if isinstance(value, dict) else None

    def _save_installation_registry(self) -> None:
        self._installation_registry.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(
            prefix=f".{self._installation_registry.name}.",
            suffix=".tmp",
            dir=self._installation_registry.parent,
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                yaml.safe_dump(self._custom_installations, handle, allow_unicode=True, sort_keys=True)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self._installation_registry)
        except BaseException:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass
            raise

    @staticmethod
    def _write_yaml_atomic(path: Path, payload: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                yaml.safe_dump(payload, handle, allow_unicode=True, sort_keys=False)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        except BaseException:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass
            raise

    async def _record_generic_installation(
        self,
        definition: EngineDefinition,
        target: Path,
        executable: Path,
        operation: dict[str, Any],
    ) -> None:
        executable = executable.resolve(strict=False)
        if not executable.is_file() or not os.access(executable, os.X_OK):
            raise RuntimeError("Executable отсутствует или не имеет права на запуск")
        version: str | None = None
        if definition.version_command:
            argv = [
                part.replace("{{target}}", str(target)).replace("{{engine}}", str(executable))
                for part in definition.version_command
            ]
            try:
                result = await asyncio.to_thread(
                    subprocess.run, argv, capture_output=True, text=True,
                    timeout=30, check=False, cwd=str(target), env=os.environ.copy(),
                )
            except (OSError, subprocess.SubprocessError) as exc:
                raise RuntimeError(f"Проверка версии не выполнена: {exc}") from exc
            output = (result.stdout or "").strip() or (result.stderr or "").strip()
            if result.returncode:
                raise RuntimeError(output or f"version_command завершилась с кодом {result.returncode}")
            version = output.splitlines()[0][:200] if output else None
        snapshot_path = target / ".neural-deck-definition.yaml"
        self._write_yaml_atomic(snapshot_path, definition.model_dump(mode="json", exclude_none=True))
        self._write_yaml_atomic(
            target / ".neural-deck-engine.yaml",
            {
                "schema_version": 1,
                "engine_id": definition.id,
                "version": version,
                "checked_at": utc_now(),
                "status": "ready",
                "diagnostic": None,
                "definition_snapshot": snapshot_path.name,
            },
        )
        key = installation_id(definition.id, "custom", target)
        self._custom_installations[key] = {
            "engine_id": definition.id,
            "root_path": str(target),
            "executable_path": str(executable),
        }
        self._save_installation_registry()
        operation["checked_at"] = utc_now()

    def _generic_paths(self, definition: EngineDefinition, request: GenericInstallationCreate) -> tuple[Path, Path]:
        target = request.target_path.expanduser()
        if not target.is_absolute() or target == Path("/"):
            raise AppError("invalid_installation_path", "Укажите абсолютную папку установки", status_code=422)
        target = target.resolve(strict=False)
        executable_value = request.executable_path.expanduser() if request.executable_path else Path(
            definition.executable.replace("{{target}}", str(target))
        )
        if not executable_value.is_absolute():
            executable_value = target / executable_value
        return target, executable_value.resolve(strict=False)

    def _render_definition_script(
        self, definition: EngineDefinition, script: str, target: Path, executable: Path
    ) -> str:
        build_root = self.paths.runtime_dir / "builds"
        source_id = "image" if definition.id == "comfyui" else definition.id
        source = build_root / source_id / (
            "source" if definition.id in {"llama.cpp", "comfyui"} else str(target).lstrip("/")
        )
        template = script.replace("{{source}}", shlex.quote(str(source)))
        template = template.replace("{{target_dir}}", shlex.quote(str(target.parent)))
        template = template.replace("{{python}}", shlex.quote(str(target / "bin" / "python")))
        template = template.replace("{{models_root}}", shlex.quote(str(self.paths.models_root)))
        return substitute_placeholders(
            template, engine=str(executable), target=str(target), model=None,
            model_name=None, model_draft=None, port=None, models_root=None,
            extra_model_paths_config=None,
        )

    async def _create_generic_installation(
        self, engine_id: str, request: GenericInstallationCreate
    ) -> dict[str, Any]:
        definition = self.get_definition(engine_id)
        target, executable = self._generic_paths(definition, request)
        install_script = request.command if request.command is not None else definition.install_script
        if install_script is not None and (not install_script.strip() or "\x00" in install_script):
            raise AppError("invalid_command", "Пустой или недопустимый скрипт установки", status_code=422)
        if request.run_install and not install_script:
            raise AppError("install_script_missing", "В определении нет скрипта установки", status_code=422)
        target_id = installation_id(definition.id, "custom", target)
        if self.installation_in_use and self.installation_in_use(target_id):
            raise AppError("installation_in_use", "Нельзя менять установку, пока из неё запущена модель", status_code=409)

        async def install(operation: dict[str, Any]) -> None:
            try:
                if self.installation_in_use and self.installation_in_use(target_id):
                    raise AppError("installation_in_use", "Установка используется запущенной моделью", status_code=409)
                target.mkdir(parents=True, exist_ok=True)
                if request.run_install:
                    script = self._render_definition_script(definition, install_script or "", target, executable)
                    self.operations.append_output(operation, "stdout", "$ bash -c '<definition install script>'\n")
                    code, _out, _err = await run_command(
                        ["/bin/bash", "-c", script], cwd=str(target), timeout_seconds=14400,
                        env={**os.environ, "UV_PYTHON_INSTALL_DIR": str(self.paths.runtime_dir / "uv-python")},
                        on_stdout_line=lambda line: self.operations.append_output(operation, "stdout", line),
                        on_stderr_line=lambda line: self.operations.append_output(operation, "stderr", line),
                    )
                    operation["exit_code"] = code
                    if code:
                        raise RuntimeError(f"Команда установки завершилась с кодом {code}")
                used_definition = definition.model_copy(
                    update={"install_script": install_script if request.run_install else definition.install_script}
                )
                await self._record_generic_installation(used_definition, target, executable, operation)
            except BaseException as exc:
                if target.is_dir() and not isinstance(exc, AppError):
                    self._write_yaml_atomic(target / ".neural-deck-engine.yaml", {
                        "schema_version": 1, "engine_id": definition.id, "version": None,
                        "checked_at": utc_now(), "status": "broken", "diagnostic": str(exc)[:500],
                    })
                raise
            finally:
                try:
                    await self._rescan_after_operation()
                finally:
                    self._release_pending_definition_installation(definition.id)

        self._pending_definition_installations[definition.id] = (
            self._pending_definition_installations.get(definition.id, 0) + 1
        )
        try:
            return await self.operations.create("install", target_id, install)
        except BaseException:
            self._release_pending_definition_installation(definition.id)
            raise

    def _release_pending_definition_installation(self, engine_id: str) -> None:
        pending = self._pending_definition_installations.get(engine_id, 0)
        if pending <= 1:
            self._pending_definition_installations.pop(engine_id, None)
        else:
            self._pending_definition_installations[engine_id] = pending - 1

    def forget_installation(self, value: str) -> None:
        installation = self.get_installation(value)
        if self.installation_in_use and self.installation_in_use(value):
            raise AppError('installation_in_use', 'Установка используется запущенной моделью', status_code=409)
        if value in self._custom_installations:
            updated = dict(self._custom_installations)
            del updated[value]
            self._custom_installations = updated
            self._save_installation_registry()
            self.scan()
            return
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
        if installation.engine_id not in {engine_id, "comfyui" if engine_id == "image" else engine_id}:
            raise ValueError("Установка принадлежит другому движку")
        if installation.status != "ready" or not installation.verified:
            raise ValueError(installation.diagnostic or "Установка движка недоступна")
        return installation.executable_path

    def installation_target(self, engine_id: str, request: Any) -> Path:
        adapter_id = "image" if engine_id == "comfyui" else engine_id
        adapter = ADAPTER_BY_ID.get(adapter_id)
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
        adapter_id = "image" if engine_id == "comfyui" else engine_id
        return {'command': installation_template(adapter_id, target, self.paths.data_root / 'runtime' / 'builds')}

    async def create_installation(self, engine_id: str, request: Any) -> dict[str, Any]:
        if isinstance(request, GenericInstallationCreate):
            return await self._create_generic_installation(engine_id, request)
        adapter_id = "image" if engine_id == "comfyui" else engine_id
        target = self.installation_target(engine_id, request)
        command = request.command if request.command is not None else self.preview_installation(adapter_id, request)['command']
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
                    await self._verify_after_install(adapter_id, target, operation)
                else:
                    result = await asyncio.to_thread(DiscoveryCoordinator(self.settings, self.paths).scan, explicit=[(adapter_id, target)])
                    found = next((item for item in result.installations if item.engine_id == adapter_id and item.executable_path == target.resolve(strict=False)), None)
                    if found is None or not found.verified:
                        raise RuntimeError(found.diagnostic if found else 'Бинарник не прошёл проверку')
                    self._binary_paths[str(target)] = adapter_id
                    self._binary_registry.parent.mkdir(parents=True, exist_ok=True)
                    temporary = self._binary_registry.with_suffix('.tmp')
                    temporary.write_text(yaml.safe_dump(self._binary_paths), encoding='utf-8')
                    temporary.replace(self._binary_registry)
                operation['checked_at'] = utc_now()
            except BaseException as exc:
                if request.kind == 'python_venv' and target.is_dir() and not isinstance(exc, AppError):
                    self._write_broken_manifest(target, adapter_id, str(exc) or 'Операция отменена')
                raise
            finally:
                await self._rescan_after_operation()

        operation_target = 'build:llama.cpp' if engine_id == 'llama.cpp' else target_id if request.kind == 'python_venv' else f'binary:{target}'
        return await self.operations.create('install', operation_target, install)

    async def update_installation(self, installation_id: str) -> dict[str, Any]:
        installation = self.get_installation(installation_id)
        if installation.kind == "custom":
            definition = self.get_definition(installation.engine_id)
            if not definition.update_script:
                raise AppError("unsupported_operation", "В определении нет скрипта обновления")
            if self.installation_in_use is not None and self.installation_in_use(installation_id):
                raise AppError("installation_in_use", "Нельзя обновить окружение, пока из него запущена модель", status_code=409)

            async def update_custom(operation: dict[str, Any]) -> None:
                try:
                    script = self._render_definition_script(
                        definition, definition.update_script or "", installation.root_path,
                        installation.executable_path,
                    )
                    code, _out, _err = await run_command(
                        ["/bin/bash", "-c", script], cwd=str(installation.root_path),
                        timeout_seconds=14400, env=os.environ.copy(),
                        on_stdout_line=lambda line: self.operations.append_output(operation, "stdout", line),
                        on_stderr_line=lambda line: self.operations.append_output(operation, "stderr", line),
                    )
                    operation["exit_code"] = code
                    if code:
                        raise RuntimeError(f"Команда обновления завершилась с кодом {code}")
                    await self._record_generic_installation(
                        definition, installation.root_path, installation.executable_path, operation
                    )
                except Exception as exc:
                    if not isinstance(exc, AppError):
                        self._write_yaml_atomic(installation.root_path / ".neural-deck-engine.yaml", {
                            "schema_version": 1, "engine_id": definition.id, "version": None,
                            "checked_at": utc_now(), "status": "broken", "diagnostic": str(exc)[:500],
                        })
                    raise
                finally:
                    await self._rescan_after_operation()

            return await self.operations.create("update", installation_id, update_custom)
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
        adapter_id = "image" if installation.engine_id == "comfyui" else installation.engine_id

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
                command = installation_template(adapter_id, installation.root_path, self.paths.runtime_dir / 'builds', update=True)
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
                    adapter_id, installation.root_path, operation
                )
                operation["checked_at"] = utc_now()
            except asyncio.CancelledError:
                self._write_broken_manifest(
                    installation.root_path, adapter_id, "Операция отменена"
                )
                raise
            except Exception as exc:
                if not isinstance(exc, AppError):
                    self._write_broken_manifest(
                        installation.root_path, adapter_id, str(exc)
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
        try:
            definition = self.get_definition(engine_id)
        except AppError:
            definition = None
        if definition is not None:
            self._write_yaml_atomic(
                root / ".neural-deck-definition.yaml",
                definition.model_dump(mode="json", exclude_none=True),
            )
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
