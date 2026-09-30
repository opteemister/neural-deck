"""Local model catalogue and public Hugging Face client."""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import os
import shutil
import re
import tempfile
import sys
from pathlib import Path
from typing import Any

import httpx
import yaml  # type: ignore[import-untyped]

from ..errors import AppError
from ..operations import OperationManager, ensure_safe_relative_path, run_command, utc_now
from ..paths import DataPaths
from ..schemas.models import (
    HfFile,
    HfMatch,
    HfRepoData,
    HfSearchData,
    HfSearchItem,
    LocalModel,
    LocalModelFile,
    LocalModelGroup,
)

MODEL_EXTENSIONS = {
    ".gguf": "gguf",
    ".ggml": "ggml",
    ".mmproj": "mmproj",
    ".safetensors": "safetensors",
    ".bin": "pytorch",
    ".pt": "pytorch",
    ".pth": "pytorch",
    ".ckpt": "checkpoint",
    ".onnx": "onnx",
    ".nemo": "nemo",
    ".tflite": "tflite",
}
SIDECAR_DIR = ".llm-studio-hf.yaml"
_AUXILIARY_ROLE_RE = re.compile(
    r"(^|[\\/_.-])(?:mtp|mmproj|projector|projection|vision(?:[-_]?(?:encoder|tower))?"
    r"|audio(?:[-_]?(?:encoder|tower))?|adapter|d[\s_-]?spark)(?=$|[\\/_.-])",
    re.IGNORECASE,
)


class ModelCatalogue:
    def __init__(
        self,
        paths: DataPaths,
        *,
        operations: OperationManager | None = None,
    ) -> None:
        self.paths = paths
        self.operations = operations or OperationManager(paths.operations_log_dir)
        self._items: list[LocalModel] | None = None
        self.scan_error: str | None = None
        self._manifest_lock = asyncio.Lock()

    async def close(self) -> None:
        await self.operations.close()

    def scan(self, *, verify_hashes: bool = False) -> list[LocalModel]:
        root = self.paths.models_root
        try:
            if not root.exists():
                self._items = []
                return []
            paths = sorted(root.rglob("*"), key=lambda value: str(value))
        except (OSError, RuntimeError) as exc:
            self.scan_error = str(exc)
            self._items = []
            return []
        directories = [
            path
            for path in paths
            if _visible_relative(path, root) and _is_directory(path)
        ]
        model_directories = _model_directories(root, directories)
        model_directory_set = set(model_directories)
        for path in model_directories:
            _reconcile_local_manifest(root, path)
            if verify_hashes:
                _refresh_manifest_hashes(path)
        standalone_files = [
            path
            for path in paths
            if _visible_relative(path, root)
            and path.is_file()
            and path.suffix.lower() in MODEL_EXTENSIONS
            and not any(parent in model_directory_set for parent in path.parents)
        ]
        items = [
            self._describe(path, verify_hashes=verify_hashes)
            for path in [*model_directories, *standalone_files]
        ]
        self._items = items
        self.scan_error = None
        return items

    def mark_scan_failed(self, error: Exception) -> None:
        """Keep local model reads available after a non-fatal startup failure."""

        self.scan_error = str(error)
        self._items = []

    def items(self) -> list[LocalModel]:
        return self._items if self._items is not None else self.scan()

    def delete(self, model_id: str) -> list[LocalModel]:
        item = next((candidate for candidate in self.items() if candidate.id == model_id), None)
        if item is None:
            raise AppError("model_not_found", "Локальная модель не найдена", status_code=404)
        try:
            root = self.paths.models_root.resolve(strict=True)
            target = item.path.resolve(strict=True)
            target.relative_to(root)
        except (OSError, RuntimeError, ValueError) as exc:
            raise AppError("model_delete_failed", "Папка модели недоступна", status_code=409) from exc
        if target == root or item.path.is_symlink():
            raise AppError("model_delete_failed", "Удалять можно только модель внутри models_root", status_code=409)
        try:
            if item.kind == "file":
                target.unlink()
            elif target.is_dir():
                shutil.rmtree(target)
            else:
                raise OSError("model path is not a file or directory")
        except OSError as exc:
            raise AppError(
                "model_delete_failed",
                "Не удалось удалить папку модели",
                status_code=500,
                details={"reason": str(exc)[:200]},
            ) from exc
        return self.scan()

    def delete_group(self, model_id: str, group_id: str) -> list[LocalModel]:
        item = next((candidate for candidate in self.items() if candidate.id == model_id), None)
        if item is None:
            raise AppError("model_not_found", "Локальная модель не найдена", status_code=404)
        group = next(
            (candidate for candidate in [*item.groups, *item.auxiliary_blocks] if candidate.id == group_id),
            None,
        )
        if group is None:
            raise AppError("model_group_not_found", "Компонент модели не найден", status_code=404)
        if not group.deletable:
            raise AppError(
                "model_group_delete_failed",
                "Этот компонент нельзя удалить отдельно",
                status_code=409,
            )
        try:
            model_root = item.path.resolve(strict=True)
            models_root = self.paths.models_root.resolve(strict=True)
            model_root.relative_to(models_root)
        except (OSError, RuntimeError, ValueError) as exc:
            raise AppError("model_group_delete_failed", "Папка модели недоступна", status_code=409) from exc
        try:
            if group.path:
                raw_target = model_root / group.path
                if raw_target.is_symlink():
                    raise ValueError("group is a symlink")
                target = raw_target.resolve(strict=True)
                target.relative_to(model_root)
                if target == model_root or not target.is_dir():
                    raise ValueError("group is not a directory")
                shutil.rmtree(target)
            else:
                for file in group.files:
                    raw_target = model_root / file.path
                    if raw_target.is_symlink():
                        continue
                    target = raw_target.resolve(strict=True)
                    target.relative_to(model_root)
                    if not target.is_file():
                        continue
                    target.unlink()
        except (OSError, RuntimeError, ValueError) as exc:
            raise AppError(
                "model_group_delete_failed",
                "Не удалось удалить компонент модели",
                status_code=500,
                details={"reason": str(exc)[:200]},
            ) from exc
        return self.scan()

    def _describe(self, path: Path, *, verify_hashes: bool = False) -> LocalModel:
        format_name = self._format(path)
        compatibility = "unknown"
        match = self._match(path, verify_hashes=verify_hashes)
        size = _directory_size(path) if path.is_dir() else _file_size(path)
        manifest = _read_manifest(path)
        if path.is_dir():
            groups, auxiliary_blocks, variants, auxiliary, supporting = _model_groups(path, manifest)
        else:
            file = _file_description(path.parent, path)
            group_id = "group_" + hashlib.sha256(f"{path}\0variant\0{path.name}".encode()).hexdigest()[:24]
            group = LocalModelGroup(
                id=group_id,
                label=path.name,
                kind="variant",
                files=[file],
                file_count=1,
                size_bytes=file.size_bytes,
                status="unknown" if manifest is None else "complete",
                deletable=False,
            )
            groups, auxiliary_blocks, variants, auxiliary, supporting = [group], [], [file], [], 0
        try:
            canonical = str(path.resolve(strict=False))
        except (OSError, RuntimeError):
            canonical = str(path.absolute())
        identifier = "model_" + hashlib.sha256(canonical.encode()).hexdigest()[:24]
        return LocalModel(
            id=identifier,
            path=path,
            kind="directory" if path.is_dir() else "file",
            vendor=_model_vendor(path, self.paths.models_root),
            model_name=_model_name(path, self.paths.models_root),
            format=format_name,
            compatibility=compatibility,
            size_bytes=size,
            hf_match=match,
            variants=variants,
            auxiliary_files=auxiliary,
            groups=groups,
            auxiliary_blocks=auxiliary_blocks,
            supporting_files=supporting,
        )

    def _format(self, path: Path) -> str:
        try:
            is_dir = path.is_dir()
        except OSError:
            return "unknown"
        if is_dir:
            try:
                children = list(path.iterdir())
            except OSError:
                return "unknown"
            names = {item.name for item in children}
            if "config.json" in names or "model_index.json" in names:
                return "transformers"
            if any(item.is_file() and item.suffix.lower() in {".gguf", ".ggml"} for item in path.rglob("*")):
                return "gguf"
            if "model.safetensors.index.json" in names or any(
                item.name == "model.safetensors.index.json" for item in path.rglob("*")
            ):
                return "safetensors"
            return "unknown"
        return MODEL_EXTENSIONS.get(path.suffix.lower(), "unknown")

    def _match(self, path: Path, *, verify_hashes: bool = False) -> HfMatch:
        try:
            sidecar = path / SIDECAR_DIR if path.is_dir() else Path(f"{path}{SIDECAR_DIR}")
            has_sidecar = sidecar.is_file()
        except (OSError, RuntimeError):
            return HfMatch(status="unknown")
        if not has_sidecar:
            return _cache_match(path)
        try:
            raw = yaml.safe_load(sidecar.read_text(encoding="utf-8"))
            manifest = _validate_manifest(raw)
            status, missing_files, unverified_files = _manifest_status(
                path, manifest, verify_hashes=verify_hashes
            )
            return HfMatch(
                status=status,
                repo_id=manifest["repo_id"],
                revision=manifest["revision"],
                scope=manifest["scope"],
                missing_files=missing_files,
                unverified_files=unverified_files,
            )
        except (OSError, ValueError, yaml.YAMLError):
            return _cache_match(path)

    async def hf_search(self, query: str) -> HfSearchData:
        if not query.strip():
            raise AppError("invalid_query", "Параметр q не должен быть пустым", status_code=422)
        try:
            async with httpx.AsyncClient(base_url="https://huggingface.co", timeout=30) as client:
                response = await client.get("/api/models", params={"search": query, "limit": 50})
                response.raise_for_status()
                payload = response.json()
                if not isinstance(payload, list):
                    raise ValueError("Hugging Face вернул неожиданный список моделей")
        except (httpx.HTTPError, ValueError) as exc:
            raise AppError(
                "hf_unavailable",
                "Hugging Face недоступен",
                status_code=502,
                details={"reason": str(exc)[:200]},
                retryable=True,
            ) from exc
        return HfSearchData(
            items=[
                HfSearchItem(
                    repo_id=item.get("id", ""),
                    revision=item.get("sha"),
                    description=item.get("description"),
                    tags=item.get("tags", []),
                )
                for item in payload
                if isinstance(item, dict) and item.get("id")
            ]
        )

    async def hf_repo(self, repo_id: str, revision: str | None = None) -> HfRepoData:
        namespace, name = _repo_parts(repo_id)
        try:
            async with httpx.AsyncClient(base_url="https://huggingface.co", timeout=30) as client:
                params = {"revision": revision} if revision else None
                response = await client.get(f"/api/models/{namespace}/{name}", params=params)
                response.raise_for_status()
                metadata = response.json()
                if not isinstance(metadata, dict):
                    raise ValueError("Hugging Face вернул неожиданный ответ")
                resolved = metadata.get("sha") or (revision if _is_commit_sha(revision) else None)
                if not isinstance(resolved, str) or not _is_commit_sha(resolved):
                    raise AppError("hf_revision_unknown", "Hugging Face не вернул revision")
                files = await self._hf_files(client, repo_id, resolved)
                card_data = metadata.get("cardData")
                description = card_data.get("description") if isinstance(card_data, dict) else None
                if not isinstance(description, str):
                    description = None
                readme = await self._hf_readme(client, repo_id, resolved)
        except AppError:
            raise
        except (httpx.HTTPError, ValueError) as exc:
            raise AppError(
                "hf_unavailable",
                "Hugging Face недоступен",
                status_code=502,
                details={"reason": str(exc)[:200]},
                retryable=True,
            ) from exc
        return HfRepoData(
            repo_id=repo_id,
            revision=resolved,
            description=description,
            readme=readme,
            files=files,
        )

    async def hf_files(self, repo_id: str, revision: str | None = None) -> HfRepoData:
        return await self.hf_repo(repo_id, revision)

    async def hf_quantizations(self, repo_id: str) -> HfSearchData:
        _repo_parts(repo_id)
        try:
            async with httpx.AsyncClient(base_url="https://huggingface.co", timeout=30) as client:
                response = await client.get(
                    "/api/models",
                    params={
                        "filter": f"base_model:quantized:{repo_id}",
                        "limit": 1000,
                        "sort": "downloads",
                        "direction": -1,
                    },
                )
                response.raise_for_status()
                payload = response.json()
                if not isinstance(payload, list):
                    raise ValueError("Hugging Face вернул неожиданный список квантизаций")
        except (httpx.HTTPError, ValueError) as exc:
            raise AppError(
                "hf_unavailable",
                "Кванты Hugging Face недоступны",
                status_code=502,
                details={"reason": str(exc)[:200]},
                retryable=True,
            ) from exc
        return HfSearchData(items=self._parse_hf_quantizations(payload))

    async def download(self, request: Any) -> dict[str, Any]:
        # Different selections from one repository can be downloaded in
        # parallel (for example, several quantizations). Repeating the exact
        # same selection still uses one target and is rejected as a duplicate.
        selection = "*" if request.files is None else "\n".join(sorted(request.files))
        fingerprint = hashlib.sha256(f"{request.revision}\0{selection}".encode()).hexdigest()[:16]
        target_id = f"hf:{request.repo_id}:{fingerprint}"

        async def worker(operation: dict[str, Any]) -> None:
            operation["progress"] = {
                "files_total": 0,
                "files_completed": 0,
                "bytes_total": 0,
                "bytes_completed": 0,
                "current_file": "Получаем список файлов",
            }
            repo = await self.hf_repo(request.repo_id, request.revision)
            wanted = (
                repo.files
                if request.files is None
                else [item for item in repo.files if item.path in set(request.files)]
            )
            if request.files is not None and len(wanted) != len(request.files):
                missing = sorted(set(request.files) - {item.path for item in wanted})
                raise RuntimeError(f"Файлы отсутствуют в revision: {', '.join(missing)}")
            if any(item.size is None for item in wanted):
                raise RuntimeError("Hugging Face не вернул размеры всех выбранных файлов")
            operation["progress"] = {
                "files_total": len(wanted),
                "files_completed": 0,
                "bytes_total": sum(item.size or 0 for item in wanted),
                "bytes_completed": 0,
                "current_file": None,
            }
            try:
                await asyncio.to_thread(self.paths.models_root.mkdir, parents=True, exist_ok=True)
                destination_root = self.paths.models_root.resolve(strict=True)
            except (OSError, RuntimeError) as exc:
                raise RuntimeError(f"models_root недоступен: {exc}") from exc
            destination = _safe_hf_path(destination_root, request.repo_id)
            await asyncio.to_thread(destination.mkdir, parents=True, exist_ok=True)
            destination = _safe_hf_path(destination_root, request.repo_id)
            _safe_hf_path(destination_root, f"{request.repo_id}/.cache/huggingface")
            for item in wanted:
                relative = ensure_safe_relative_path(item.path)
                _safe_hf_path(destination_root, f"{request.repo_id}/{relative}")
            # Persist the expected set before downloading.  An interrupted
            # operation therefore remains visible as an incomplete component
            # on the next local catalogue scan.
            await self._merge_download_manifest(
                destination,
                request.repo_id,
                repo.revision,
                wanted,
                repo_files=repo.files,
                full_snapshot=request.files is None,
            )
            # A child process keeps the Hub library's blocking I/O off the API
            # loop and lets operation cancellation stop all download threads.
            descriptor, plan_path = tempfile.mkstemp(prefix="llm-studio-download-", suffix=".json")
            try:
                with os.fdopen(descriptor, "w", encoding="utf-8") as plan:
                    json.dump({
                        "repo_id": request.repo_id,
                        "revision": repo.revision,
                        "destination": str(destination),
                        "files": [{"path": item.path, "size": item.size} for item in wanted],
                    }, plan)

                def report(line: str) -> None:
                    operation["progress"] = json.loads(line)

                code, _, stderr = await run_command(
                    [sys.executable, "-m", "app.models.hf_download", plan_path],
                    cwd=str(Path(__file__).resolve().parents[2]),
                    timeout_seconds=7 * 24 * 3600,
                    on_stdout_line=report,
                )
                operation["stderr"] = stderr
                if code:
                    raise RuntimeError(f"Загрузчик Hugging Face завершился с кодом {code}: {stderr[-2000:]}")
                operation["stdout"] += "".join(f"downloaded {item.path}\n" for item in wanted)
            finally:
                Path(plan_path).unlink(missing_ok=True)
            operation["progress"]["current_file"] = None
            # Re-read and merge once more because another quantization from
            # the same repository may have completed while this task ran.
            await self._merge_download_manifest(
                destination,
                request.repo_id,
                repo.revision,
                wanted,
                repo_files=repo.files,
                full_snapshot=request.files is None,
            )
            operation["checked_at"] = utc_now()
            await asyncio.to_thread(self.scan)

        operation = await self.operations.create("download", target_id, worker)
        operation["label"] = request.label or request.repo_id
        return operation

    async def _merge_download_manifest(
        self,
        destination: Path,
        repo_id: str,
        revision: str,
        wanted: list[HfFile],
        *,
        repo_files: list[HfFile],
        full_snapshot: bool,
    ) -> dict[str, Any]:
        """Persist a cumulative manifest for selections from one revision.

        Quantizations are downloaded independently, but they share one Hub
        repository directory and sidecar.  Keep the previously known files
        when the repository and revision match so downloading a later
        quantization does not make earlier complete quantizations look
        unverified.
        """

        async with self._manifest_lock:
            existing = await asyncio.to_thread(_read_manifest, destination)
            files_by_path: dict[str, dict[str, Any]] = {}
            if (
                existing is not None
                and existing.get("repo_id") == repo_id
                and existing.get("revision") == revision
            ):
                for item in existing.get("files", []):
                    if isinstance(item, dict) and isinstance(item.get("path"), str):
                        files_by_path[item["path"]] = dict(item)
            for item in wanted:
                files_by_path[item.path] = item.model_dump(mode="json")
            # Recover files downloaded before the cumulative-manifest fix (or
            # copied into the repository by hand) when their Hub size matches.
            # This keeps the sidecar aligned with the actual directory on the
            # next download without treating partial files as complete.
            destination_root = destination.resolve(strict=True)
            for item in repo_files:
                if item.size is None or item.path in files_by_path:
                    continue
                candidate = _safe_hf_path(destination_root, item.path)
                try:
                    if candidate.is_file() and candidate.stat().st_size == item.size:
                        files_by_path[item.path] = item.model_dump(mode="json")
                except OSError:
                    continue
            scope = "snapshot" if full_snapshot or (
                existing is not None
                and existing.get("repo_id") == repo_id
                and existing.get("revision") == revision
                and existing.get("scope") == "snapshot"
            ) else "selection"
            manifest = {
                "schema_version": 1,
                "repo_id": repo_id,
                "revision": revision,
                "scope": scope,
                "files": list(files_by_path.values()),
            }
            sidecar = destination / SIDECAR_DIR
            await _atomic_write_text(sidecar, yaml.safe_dump(manifest, sort_keys=False))
            return manifest

    @staticmethod
    async def _hf_files(client: httpx.AsyncClient, repo_id: str, revision: str) -> list[HfFile]:
        response = await client.get(
            f"/api/models/{repo_id}/tree/{revision}", params={"recursive": "true"}
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, list):
            raise ValueError("Hugging Face вернул неожиданный список файлов")
        result: list[HfFile] = []
        for item in payload:
            if not isinstance(item, dict):
                continue
            if item.get("type") not in {None, "file"}:
                continue
            path = ensure_safe_relative_path(str(item.get("path", "")))
            raw_size = item.get("size")
            if raw_size is None and isinstance(item.get("lfs"), dict):
                raw_size = item["lfs"].get("size")
            size: int | None
            if isinstance(raw_size, int) and not isinstance(raw_size, bool) and raw_size >= 0:
                size = raw_size
            else:
                size = None
            lfs = item.get("lfs") if isinstance(item.get("lfs"), dict) else None
            lfs_sha256 = lfs.get("sha256") if lfs else None
            raw_sha256 = item.get("sha256")
            sha256 = (
                raw_sha256
                if isinstance(raw_sha256, str) and raw_sha256
                else lfs_sha256
                if isinstance(lfs_sha256, str) and lfs_sha256
                else None
            )
            raw_oid = item.get("oid")
            hash_kind = (
                "git_blob"
                if isinstance(raw_oid, str)
                and len(raw_oid) == 40
                and lfs is None
                else None
            )
            result.append(
                HfFile(
                    path=path,
                    size=size,
                    # LFS content is verifiable through lfs.sha256. Regular
                    # files use Git blob hashing for their 40-char object id.
                    etag=raw_oid if hash_kind else None,
                    sha256=sha256,
                    hash_kind=hash_kind,
                )
            )
        return result

    @staticmethod
    async def _hf_readme(client: httpx.AsyncClient, repo_id: str, revision: str) -> str | None:
        try:
            response = await client.get(f"/{repo_id}/raw/{revision}/README.md")
            if response.status_code == 404:
                return None
            response.raise_for_status()
            return response.text
        except httpx.HTTPError:
            return None

    @staticmethod
    def _parse_hf_quantizations(payload: list[Any]) -> list[HfSearchItem]:
        result: list[HfSearchItem] = []
        for item in payload:
            if not isinstance(item, dict) or not isinstance(item.get("id"), str):
                continue
            tags = item.get("tags")
            result.append(
                HfSearchItem(
                    repo_id=item["id"],
                    revision=item.get("sha") if isinstance(item.get("sha"), str) else None,
                    description=item.get("description") if isinstance(item.get("description"), str) else None,
                    tags=[tag for tag in tags if isinstance(tag, str)] if isinstance(tags, list) else [],
                )
            )
        return result


def _visible_relative(path: Path, root: Path) -> bool:
    try:
        return not any(part.startswith(".") for part in path.relative_to(root).parts)
    except ValueError:
        return False


def _model_vendor(path: Path, root: Path) -> str:
    try:
        parts = path.relative_to(root).parts
    except ValueError:
        parts = path.parts
    return parts[0] if parts else path.name


def _model_name(path: Path, root: Path) -> str:
    try:
        parts = path.relative_to(root).parts
    except ValueError:
        parts = path.parts
    return parts[1] if len(parts) > 1 else (parts[0] if parts else path.name)


def _is_directory(path: Path) -> bool:
    try:
        return path.is_dir()
    except (OSError, RuntimeError):
        return False


def _has_model_marker(path: Path) -> bool:
    try:
        children = list(path.iterdir())
    except OSError:
        return False
    names = {child.name for child in children}
    return (
        SIDECAR_DIR in names
        or "config.json" in names
        or "model_index.json" in names
        or "model.safetensors.index.json" in names
        or any(child.is_file() and child.suffix.lower() in MODEL_EXTENSIONS for child in children)
    )


def _model_directories(root: Path, directories: list[Path]) -> list[Path]:
    selected: list[Path] = []
    for path in sorted(directories, key=lambda value: (len(value.parts), str(value))):
        if any(parent == path or parent in path.parents for parent in selected):
            continue
        if _has_model_marker(path):
            selected.append(path)
    # A downloaded quantized repository commonly has the shape
    # ``namespace/model/quant-name/*.gguf`` without a marker in
    # ``namespace/model`` itself.  Treat that level as one model so its
    # children become logical groups instead of separate catalogue entries.
    for path in sorted(directories, key=lambda value: (len(value.relative_to(root).parts), str(value))):
        try:
            relative = path.relative_to(root)
        except ValueError:
            continue
        if len(relative.parts) != 2 or not _contains_model_file(path):
            continue
        selected = [candidate for candidate in selected if not (path == candidate or path in candidate.parents)]
        if not any(candidate == path or candidate in path.parents for candidate in selected):
            selected.append(path)
    try:
        top_level = [path for path in root.iterdir() if _is_directory(path) and not path.name.startswith(".")]
    except OSError:
        top_level = []
    for path in top_level:
        if not any(model == path or path in model.parents for model in selected):
            selected.append(path)
    return sorted(selected, key=lambda value: str(value))


def _file_description(root: Path, path: Path) -> LocalModelFile:
    try:
        size = path.stat().st_size
    except OSError:
        size = None
    return LocalModelFile(
        path=path.relative_to(root).as_posix(),
        format=MODEL_EXTENSIONS.get(path.suffix.lower(), "unknown"),
        size_bytes=size,
    )


def _contains_model_file(path: Path) -> bool:
    try:
        return any(
            item.is_file()
            and item.suffix.lower() in MODEL_EXTENSIONS
            and not any(part.startswith(".") for part in item.relative_to(path).parts)
            for item in path.rglob("*")
        )
    except (OSError, RuntimeError):
        return False


def _root_group_key(name: str) -> str:
    stem = Path(name).stem
    stem = re.sub(r"[-_.]\d{5}-of-\d{5}$", "", stem, flags=re.IGNORECASE)
    return stem or Path(name).name


def _is_auxiliary_path(value: str) -> bool:
    return _AUXILIARY_ROLE_RE.search(value) is not None


def _group_status(
    root: Path,
    key: str,
    files: list[Path],
    manifest: dict[str, Any] | None,
) -> str:
    if manifest is None:
        return "unknown"
    expected = []
    for item in manifest.get("files", []):
        if not isinstance(item, dict) or not isinstance(item.get("path"), str):
            continue
        relative = str(item["path"])
        is_nested = "/" in key or any(
            item.relative_to(root).parent != Path(".") for item in files
        )
        if is_nested and relative.startswith(f"{key}/"):
            expected.append(item)
        elif not is_nested and "/" not in relative and Path(relative).suffix.lower() in MODEL_EXTENSIONS:
            if _root_group_key(relative) == key:
                expected.append(item)
    if not expected:
        return "unknown"
    local = {file.relative_to(root).as_posix(): file for file in files}
    incomplete = False
    for item in expected:
        relative = str(item["path"])
        candidate = local.get(relative)
        if candidate is None:
            candidate = root / relative
        try:
            if not candidate.is_file() or (
                isinstance(item.get("size"), int) and candidate.stat().st_size != item["size"]
            ):
                incomplete = True
        except OSError:
            incomplete = True
    return "incomplete" if incomplete else "complete"


def _read_manifest(path: Path) -> dict[str, Any] | None:
    try:
        sidecar = path / SIDECAR_DIR if path.is_dir() else Path(f"{path}{SIDECAR_DIR}")
        if not sidecar.is_file():
            return None
        return _validate_manifest(yaml.safe_load(sidecar.read_text(encoding="utf-8")))
    except (OSError, ValueError, yaml.YAMLError):
        return None


def _reconcile_local_manifest(root: Path, path: Path) -> None:
    """Best-effortly attach a Hub manifest to an externally added folder.

    Local scanning must remain useful offline, so every network or metadata
    error simply leaves the folder without a manifest and therefore with an
    ``unknown`` match status.
    """

    if not path.is_dir():
        return
    existing = _read_manifest(path)
    # A snapshot already describes the complete revision.  Selection
    # manifests are refreshed so older manifests that were overwritten by a
    # later quantization can be repaired from the files currently on disk.
    if existing is not None and existing.get("scope") == "snapshot":
        return
    repo_id = _repo_id_for_path(root, path)
    if repo_id is None:
        return
    if existing is not None:
        try:
            expected_paths = {
                str(item.get("path"))
                for item in existing.get("files", [])
                if isinstance(item, dict) and isinstance(item.get("path"), str)
            }
            local_model_paths = {
                item.relative_to(path).as_posix()
                for item in path.rglob("*")
                if item.is_file()
                and item.suffix.lower() in MODEL_EXTENSIONS
                and not any(part.startswith(".") for part in item.relative_to(path).parts)
            }
            if local_model_paths and local_model_paths <= expected_paths:
                return
        except (OSError, RuntimeError):
            pass
    try:
        with httpx.Client(base_url="https://huggingface.co", timeout=8) as client:
            metadata_response = client.get(f"/api/models/{repo_id}")
            metadata_response.raise_for_status()
            metadata = metadata_response.json()
            revision = metadata.get("sha") if isinstance(metadata, dict) else None
            if not isinstance(revision, str) or not _is_commit_sha(revision):
                return
            tree_response = client.get(
                f"/api/models/{repo_id}/tree/{revision}",
                params={"recursive": "true"},
            )
            tree_response.raise_for_status()
            payload = tree_response.json()
        if not isinstance(payload, list):
            return
        hf_files: list[dict[str, Any]] = []
        for raw in payload:
            if not isinstance(raw, dict) or raw.get("type") not in {None, "file"}:
                continue
            relative = ensure_safe_relative_path(str(raw.get("path", "")))
            raw_size = raw.get("size")
            if raw_size is None and isinstance(raw.get("lfs"), dict):
                raw_size = raw["lfs"].get("size")
            if not isinstance(raw_size, int) or isinstance(raw_size, bool) or raw_size < 0:
                continue
            lfs = raw.get("lfs") if isinstance(raw.get("lfs"), dict) else None
            lfs_sha256 = lfs.get("sha256") if lfs else None
            raw_sha256 = raw.get("sha256")
            sha256 = (
                raw_sha256
                if isinstance(raw_sha256, str) and raw_sha256
                else lfs_sha256
                if isinstance(lfs_sha256, str) and lfs_sha256
                else None
            )
            raw_oid = raw.get("oid")
            hash_kind = (
                "git_blob"
                if isinstance(raw_oid, str)
                and len(raw_oid) == 40
                and lfs is None
                else None
            )
            hf_files.append(
                {
                    "path": relative,
                    "size": raw_size,
                    "etag": raw_oid if hash_kind else None,
                    "sha256": sha256,
                    "hash_kind": hash_kind,
                }
            )
        local_model_files = {
            item.relative_to(path).as_posix(): item
            for item in path.rglob("*")
            if item.is_file()
            and item.suffix.lower() in MODEL_EXTENSIONS
            and not any(part.startswith(".") for part in item.relative_to(path).parts)
        }
        matched_paths = {
            relative
            for relative, item in local_model_files.items()
            for candidate in hf_files
            if candidate["path"] == relative and item.stat().st_size == candidate["size"]
        }
        if not matched_paths:
            return
        touched_groups = {_hf_group_key(relative) for relative in matched_paths}
        selected = [
            item
            for item in hf_files
            if _hf_group_key(str(item["path"])) in touched_groups
        ]
        selected_paths = {str(item["path"]) for item in selected}
        # Common metadata is useful for validation only when it is already
        # present locally. Missing common files must not turn a hand-copied
        # quant into a falsely incomplete model.
        for item in hf_files:
            relative = str(item["path"])
            candidate = path / relative
            if relative in selected_paths or not candidate.is_file() or Path(relative).suffix.lower() in MODEL_EXTENSIONS:
                continue
            if candidate.stat().st_size == item["size"]:
                selected.append(item)
        all_model_paths = {str(item["path"]) for item in hf_files if Path(str(item["path"])).suffix.lower() in MODEL_EXTENSIONS}
        scope = "snapshot" if {str(item["path"]) for item in selected} >= all_model_paths else "selection"
        manifest = {
            "schema_version": 1,
            "repo_id": repo_id,
            "revision": revision,
            "scope": scope,
            "files": selected,
        }
        _atomic_write_text_sync(path / SIDECAR_DIR, yaml.safe_dump(manifest, sort_keys=False))
    except (httpx.HTTPError, OSError, RuntimeError, ValueError, KeyError):
        return


def _refresh_manifest_hashes(path: Path) -> None:
    """Refresh manifest hashes and verify them on an explicit local sync.

    The download cache already contains the Hub tree for files downloaded by
    this application.  It is the preferred source because a sync should also
    work when the machine is temporarily offline.  For manually copied model
    directories without that cache, fall back to the public Hub tree API.
    """

    if not path.is_dir():
        return
    manifest = _read_manifest(path)
    if manifest is None:
        return
    repo_id = manifest.get("repo_id")
    revision = manifest.get("revision")
    if not isinstance(repo_id, str) or not isinstance(revision, str):
        return
    metadata = _cached_hf_tree_metadata(path, revision)
    if metadata is None:
        metadata = _remote_hf_tree_metadata(repo_id, revision)
    if not metadata:
        return

    refreshed: list[dict[str, Any]] = []
    changed = False
    for raw_item in manifest.get("files", []):
        if not isinstance(raw_item, dict) or not isinstance(raw_item.get("path"), str):
            refreshed.append(raw_item)
            continue
        item = dict(raw_item)
        file_metadata = metadata.get(str(item["path"]))
        if file_metadata:
            if isinstance(file_metadata.get("size"), int):
                item["size"] = file_metadata["size"]
            if file_metadata.get("sha256"):
                item["sha256"] = file_metadata["sha256"]
                item.pop("etag", None)
                item.pop("hash_kind", None)
            elif file_metadata.get("hash_kind") == "git_blob":
                item["etag"] = file_metadata.get("etag")
                item["hash_kind"] = "git_blob"
                item.pop("sha256", None)
            elif file_metadata.get("etag"):
                item["etag"] = file_metadata["etag"]
                item.pop("hash_kind", None)
            changed = item != raw_item or changed
        refreshed.append(item)
    if not changed:
        return
    updated = {
        "schema_version": 1,
        "repo_id": repo_id,
        "revision": revision,
        "scope": manifest["scope"],
        "files": refreshed,
    }
    try:
        _atomic_write_text_sync(path / SIDECAR_DIR, yaml.safe_dump(updated, sort_keys=False))
    except OSError:
        return


def _cached_hf_tree_metadata(path: Path, revision: str) -> dict[str, dict[str, Any]] | None:
    cache_file = path / ".cache" / "huggingface" / "trees" / f"{revision}.json"
    try:
        payload = json.loads(cache_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    files = payload.get("files") if isinstance(payload, dict) else None
    if not isinstance(files, dict):
        return None
    result: dict[str, dict[str, Any]] = {}
    for raw_path, raw in files.items():
        if not isinstance(raw_path, str) or not isinstance(raw, dict):
            continue
        try:
            relative = ensure_safe_relative_path(raw_path)
        except ValueError:
            continue
        metadata = _tree_file_metadata(raw, cached=True)
        if metadata:
            result[relative] = metadata
    return result or None


def _remote_hf_tree_metadata(repo_id: str, revision: str) -> dict[str, dict[str, Any]] | None:
    try:
        with httpx.Client(base_url="https://huggingface.co", timeout=8) as client:
            response = client.get(
                f"/api/models/{repo_id}/tree/{revision}",
                params={"recursive": "true"},
            )
            response.raise_for_status()
            payload = response.json()
    except (httpx.HTTPError, ValueError):
        return None
    if not isinstance(payload, list):
        return None
    result: dict[str, dict[str, Any]] = {}
    for raw in payload:
        if not isinstance(raw, dict) or raw.get("type") not in {None, "file"}:
            continue
        try:
            relative = ensure_safe_relative_path(str(raw.get("path", "")))
        except ValueError:
            continue
        metadata = _tree_file_metadata(raw)
        if metadata:
            result[relative] = metadata
    return result or None


def _tree_file_metadata(raw: dict[str, Any], *, cached: bool = False) -> dict[str, Any] | None:
    lfs = raw.get("lfs") if isinstance(raw.get("lfs"), dict) else None
    lfs_sha256 = raw.get("lfs_sha256") if cached else (lfs or {}).get("sha256")
    raw_sha256 = raw.get("sha256")
    sha256 = (
        lfs_sha256
        if isinstance(lfs_sha256, str) and len(lfs_sha256) == 64
        else raw_sha256
        if isinstance(raw_sha256, str) and len(raw_sha256) == 64
        else None
    )
    raw_size = raw.get("lfs_size") if cached else (lfs or {}).get("size")
    if not isinstance(raw_size, int) or isinstance(raw_size, bool) or raw_size < 0:
        raw_size = raw.get("size")
    metadata: dict[str, Any] = {}
    if isinstance(raw_size, int) and not isinstance(raw_size, bool) and raw_size >= 0:
        metadata["size"] = raw_size
    if sha256:
        metadata["sha256"] = sha256
        return metadata
    raw_oid = raw.get("blob_id") if cached else raw.get("oid")
    if isinstance(raw_oid, str) and len(raw_oid) == 40:
        metadata.update({"etag": raw_oid, "hash_kind": "git_blob"})
    return metadata or None


def _repo_id_for_path(root: Path, path: Path) -> str | None:
    try:
        parts = path.relative_to(root).parts
    except ValueError:
        return None
    if len(parts) < 2:
        return None
    candidate = f"{parts[0]}/{parts[1]}"
    try:
        _repo_parts(candidate)
    except AppError:
        return None
    return candidate


def _hf_group_key(relative: str) -> str:
    path = Path(relative)
    return path.parent.as_posix() if path.parent != Path(".") else _root_group_key(path.name)


def _atomic_write_text_sync(destination: Path, value: str) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{destination.name}.", dir=destination.parent)
    committed = False
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(value)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, destination)
        committed = True
    finally:
        if not committed:
            with contextlib.suppress(OSError):
                os.unlink(temporary)


def _model_groups(
    path: Path,
    manifest: dict[str, Any] | None,
) -> tuple[list[LocalModelGroup], list[LocalModelGroup], list[LocalModelFile], list[LocalModelFile], int]:
    buckets: dict[str, list[Path]] = {}
    supporting = 0
    try:
        files = [
            item
            for item in path.rglob("*")
            if item.is_file() and not any(part.startswith(".") for part in item.relative_to(path).parts)
        ]
    except (OSError, RuntimeError):
        return [], [], [], [], 0
    for item in files:
        if item.name == SIDECAR_DIR:
            continue
        if item.suffix.lower() not in MODEL_EXTENSIONS:
            supporting += 1
            continue
        relative = item.relative_to(path)
        key = relative.parent.as_posix() if relative.parent != Path(".") else "."
        if key == ".":
            key = _root_group_key(item.name)
        buckets.setdefault(key, []).append(item)

    variants: list[LocalModelGroup] = []
    auxiliary: list[LocalModelGroup] = []
    flattened_variants: list[LocalModelFile] = []
    flattened_auxiliary: list[LocalModelFile] = []
    for key, group_files in sorted(buckets.items()):
        kind = "auxiliary_block" if _is_auxiliary_path("/".join([key, *(item.name for item in group_files)])) else "variant"
        relative_files = [_file_description(path, item) for item in group_files]
        size = sum(item.size_bytes or 0 for item in relative_files) if all(item.size_bytes is not None for item in relative_files) else None
        physical_path = key if any(item.relative_to(path).parent != Path(".") for item in group_files) else None
        status = _group_status(path, key, group_files, manifest)
        group_id = "group_" + hashlib.sha256(f"{path}\0{kind}\0{key}".encode()).hexdigest()[:24]
        group = LocalModelGroup(
            id=group_id,
            label=key if key != "." else "Модель",
            kind=kind,
            path=physical_path,
            files=relative_files,
            file_count=len(relative_files),
            size_bytes=size,
            status=status,
            deletable=(kind == "auxiliary_block" and bool(relative_files)) or (kind == "variant" and bool(relative_files)),
        )
        if kind == "auxiliary_block":
            auxiliary.append(group)
            flattened_auxiliary.extend(relative_files)
        else:
            variants.append(group)
            flattened_variants.extend(relative_files)
    deletable_variants = len(variants)
    if deletable_variants <= 1:
        variants = [group.model_copy(update={"deletable": False}) for group in variants]
    return variants, auxiliary, flattened_variants, flattened_auxiliary, supporting


def _directory_size(path: Path) -> int | None:
    total = 0
    try:
        for item in path.rglob("*"):
            stat = item.stat()
            if stat.st_mode & 0o170000 == 0o100000:
                total += stat.st_size
    except (OSError, RuntimeError):
        return None
    return total


def _file_size(path: Path) -> int | None:
    try:
        return path.stat().st_size
    except OSError:
        return None


def _repo_parts(repo_id: str) -> tuple[str, str]:
    parts = repo_id.split("/")
    if len(parts) != 2 or any(
        not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", part) for part in parts
    ):
        raise AppError(
            "invalid_repo_id", "repo_id должен иметь вид namespace/name", status_code=422
        )
    return parts[0], parts[1]


def _is_commit_sha(value: str | None) -> bool:
    if value is None or len(value) != 40:
        return False
    return all(character in "0123456789abcdefABCDEF" for character in value)


def _validate_manifest(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or value.get("schema_version") != 1:
        raise ValueError("unsupported manifest")
    repo_id = value.get("repo_id")
    revision = value.get("revision")
    scope = value.get("scope")
    files = value.get("files")
    if (
        not isinstance(repo_id, str)
        or not isinstance(revision, str)
        or not _is_commit_sha(revision)
        or scope not in {"snapshot", "selection"}
    ):
        raise ValueError("invalid manifest metadata")
    if not isinstance(files, list) or not files:
        raise ValueError("manifest files are required")
    parsed: list[dict[str, Any]] = []
    for item in files:
        if not isinstance(item, dict) or not isinstance(item.get("path"), str):
            raise ValueError("invalid manifest file")
        relative = ensure_safe_relative_path(item["path"])
        size = item.get("size")
        if not isinstance(size, int) or isinstance(size, bool) or size < 0:
            raise ValueError("invalid manifest size")
        parsed.append({**item, "path": relative})
    return {"repo_id": repo_id, "revision": revision, "scope": scope, "files": parsed}


def _manifest_status(
    path: Path,
    manifest: dict[str, Any],
    *,
    verify_hashes: bool = False,
) -> tuple[str, list[str], list[str]]:
    if path.is_file() and len(manifest["files"]) != 1:
        return "unknown", [str(item["path"]) for item in manifest["files"]], []
    matches = 0
    unknown_size = False
    missing_files: list[str] = []
    unverified_files: list[str] = []
    for item in manifest["files"]:
        candidate = path / item["path"] if path.is_dir() else path
        if not candidate.is_file():
            missing_files.append(str(item["path"]))
            continue
        try:
            expected_size = item.get("size")
            if expected_size is None:
                unknown_size = True
                continue
            if candidate.stat().st_size != expected_size:
                missing_files.append(str(item["path"]))
                continue
            expected_hash = item.get("sha256") or item.get("etag")
            if expected_hash:
                hash_result = _hash_matches(
                    candidate,
                    expected_hash,
                    hash_kind=item.get("hash_kind"),
                    verify_hashes=verify_hashes,
                )
                if hash_result is None:
                    unverified_files.append(str(item["path"]))
                if hash_result is False:
                    missing_files.append(str(item["path"]))
                    continue
        except OSError:
            missing_files.append(str(item["path"]))
            continue
        matches += 1
    if unknown_size:
        return "unknown", sorted(set(missing_files)), sorted(set(unverified_files))
    if matches == len(manifest["files"]):
        return "downloaded", [], sorted(set(unverified_files))
    if matches:
        return "incomplete", sorted(set(missing_files)), sorted(set(unverified_files))
    return "unknown", sorted(set(missing_files)), sorted(set(unverified_files))


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _hash_matches(
    path: Path,
    expected: str,
    *,
    hash_kind: str | None = None,
    verify_hashes: bool = False,
) -> bool | None:
    expected = expected.removeprefix("sha256:")
    if len(expected) == 64:
        return _sha256(path) == expected
    if len(expected) == 40:
        # A regular Hub file's oid is the SHA-1 of a Git blob (header plus
        # bytes), not the SHA-1 of the payload. Only calculate it for metadata
        # explicitly refreshed from a regular-file tree entry. Legacy 40-char
        # etags remain informational until the user runs an explicit sync.
        if verify_hashes and hash_kind == "git_blob":
            return _git_blob_sha1(path) == expected
        return None
    if len(expected) == 32:
        digest = hashlib.md5()  # noqa: S324 - HF ETag may be an MD5 value
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest() == expected
    # An opaque ETag can still be useful metadata but cannot be verified from
    # local bytes without provider-specific semantics.
    return None


def _git_blob_sha1(path: Path) -> str:
    size = path.stat().st_size
    digest = hashlib.sha1()  # noqa: S324 - Git blob compatibility hash
    digest.update(f"blob {size}\0".encode("ascii"))
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_hf_path(root: Path, relative: str) -> Path:
    """Resolve an HF destination while rejecting symlink escapes."""

    normalized = ensure_safe_relative_path(relative)
    candidate = root.joinpath(*normalized.split("/"))
    current = root
    for component in normalized.split("/"):
        current = current / component
        try:
            if current.is_symlink():
                resolved = current.resolve(strict=True)
                resolved.relative_to(root)
        except ValueError as exc:
            raise ValueError("путь Hugging Face выходит за пределы models_root") from exc
        except (OSError, RuntimeError) as exc:
            raise ValueError(f"небезопасный symlink в пути Hugging Face: {exc}") from exc
    try:
        candidate.resolve(strict=False).relative_to(root)
    except (ValueError, OSError, RuntimeError) as exc:
        raise ValueError("путь Hugging Face выходит за пределы models_root") from exc
    return candidate


async def _stream_response_to_file(
    response: Any,
    destination: Path,
    *,
    on_chunk: Any | None = None,
) -> None:
    """Stream one response into a same-directory temp file and replace atomically."""

    descriptor, temporary = await asyncio.to_thread(
        tempfile.mkstemp, prefix=f".{destination.name}.", dir=destination.parent
    )
    stream = os.fdopen(descriptor, "wb")
    committed = False
    try:
        async for chunk in response.aiter_bytes(1024 * 1024):
            if chunk:
                await asyncio.to_thread(stream.write, chunk)
                if on_chunk is not None:
                    on_chunk(len(chunk))
        await asyncio.to_thread(stream.flush)
        await asyncio.to_thread(os.fsync, stream.fileno())
        await asyncio.to_thread(stream.close)
        await asyncio.to_thread(os.replace, temporary, destination)
        committed = True
    finally:
        if not stream.closed:
            await asyncio.to_thread(stream.close)
        if not committed:
            with contextlib.suppress(OSError):
                await asyncio.to_thread(os.unlink, temporary)


async def _atomic_write_text(destination: Path, value: str) -> None:
    descriptor, temporary = await asyncio.to_thread(
        tempfile.mkstemp, prefix=f".{destination.name}.", dir=destination.parent
    )
    stream = os.fdopen(descriptor, "w", encoding="utf-8")
    committed = False
    try:
        await asyncio.to_thread(stream.write, value)
        await asyncio.to_thread(stream.flush)
        await asyncio.to_thread(os.fsync, stream.fileno())
        await asyncio.to_thread(stream.close)
        await asyncio.to_thread(os.replace, temporary, destination)
        committed = True
    finally:
        if not stream.closed:
            await asyncio.to_thread(stream.close)
        if not committed:
            with contextlib.suppress(OSError):
                await asyncio.to_thread(os.unlink, temporary)


def _cache_match(path: Path) -> HfMatch:
    """Use a Hub cache snapshot only when its expected file set is proven.

    A ``snapshots/<sha>`` directory and a matching ``refs/*`` file prove only
    that a revision was selected.  Interrupted downloads have the same shape,
    so a complete local file inventory is required before returning
    ``downloaded`` or ``incomplete``.
    """

    try:
        current = path if path.is_dir() else path.parent
        for snapshot in (current, *current.parents):
            if snapshot.parent.name != "snapshots":
                continue
            revision = snapshot.name
            if not _is_commit_sha(revision):
                continue
            repo_cache = snapshot.parent.parent
            if not repo_cache.name.startswith("models--"):
                continue
            refs = repo_cache / "refs"
            if not refs.is_dir() or not any(
                ref.is_file() and ref.read_text(encoding="utf-8").strip() == revision
                for ref in refs.iterdir()
            ):
                continue
            encoded = repo_cache.name.removeprefix("models--")
            repo_id = encoded.replace("--", "/", 1)
            manifest = _cache_expected_manifest(repo_cache, snapshot, repo_id, revision)
            if manifest is None:
                return HfMatch(status="unknown", repo_id=repo_id, revision=revision)
            status, missing_files, unverified_files = _manifest_status(snapshot, manifest)
            return HfMatch(
                status=status,
                repo_id=repo_id,
                revision=revision,
                scope=manifest["scope"],
                missing_files=missing_files,
                unverified_files=unverified_files,
            )
    except (OSError, RuntimeError, UnicodeError):
        return HfMatch(status="unknown")
    return HfMatch(status="unknown")


def _cache_expected_manifest(
    repo_cache: Path, snapshot: Path, repo_id: str, revision: str
) -> dict[str, Any] | None:
    """Read an explicit expected file listing exported with an HF cache.

    The ordinary Hugging Face cache does not store a complete inventory next
    to ``snapshots``.  A few cache exporters do, using one of the metadata
    names below; only those explicit listings are trusted here.
    """

    metadata_paths = (
        repo_cache / "metadata.json",
        repo_cache / ".metadata.json",
        repo_cache / "snapshot_metadata.json",
        repo_cache / "metadata.yaml",
        repo_cache / ".metadata.yaml",
        snapshot / "metadata.json",
        snapshot / ".metadata.json",
        snapshot / "metadata.yaml",
        snapshot / ".metadata.yaml",
    )
    for metadata_path in metadata_paths:
        try:
            raw_text = metadata_path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            continue
        try:
            payload: Any
            if metadata_path.suffix == ".json":
                payload = json.loads(raw_text)
            else:
                payload = yaml.safe_load(raw_text)
        except (ValueError, yaml.YAMLError):
            continue
        metadata_revision = (
            payload.get("revision", payload.get("commit_hash"))
            if isinstance(payload, dict)
            else None
        )
        if isinstance(metadata_revision, str) and metadata_revision != revision:
            continue
        entries = _cache_metadata_entries(payload)
        if not entries:
            continue
        parsed: list[dict[str, Any]] = []
        valid = True
        for item in entries:
            if not isinstance(item, dict):
                valid = False
                break
            candidate_path = item.get("path", item.get("rfilename", item.get("name")))
            if not isinstance(candidate_path, str):
                valid = False
                break
            try:
                relative = ensure_safe_relative_path(candidate_path)
            except ValueError:
                valid = False
                break
            size = item.get("size", item.get("size_bytes"))
            if not isinstance(size, int) or isinstance(size, bool) or size < 0:
                valid = False
                break
            entry: dict[str, Any] = {"path": relative, "size": size}
            for key in ("sha256", "etag", "oid"):
                value = item.get(key)
                if isinstance(value, str) and value:
                    entry["etag" if key == "oid" else key] = value
                    break
            parsed.append(entry)
        if valid and parsed:
            return {"repo_id": repo_id, "revision": revision, "scope": "snapshot", "files": parsed}
    return None


def _cache_metadata_entries(value: Any) -> list[Any] | None:
    if isinstance(value, list):
        return value
    if not isinstance(value, dict):
        return None
    for key in ("files", "siblings", "items", "entries", "expected_files"):
        entries = value.get(key)
        if isinstance(entries, list):
            return entries
        if isinstance(entries, dict):
            result: list[dict[str, Any]] = []
            for path, metadata in entries.items():
                if isinstance(metadata, dict):
                    result.append({"path": path, **metadata})
                else:
                    result.append({"path": path, "size": metadata})
            return result
    return None


__all__ = ["ModelCatalogue"]
