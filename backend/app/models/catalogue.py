"""Local model catalogue and public Hugging Face client."""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any, Literal, cast

import httpx
import yaml  # type: ignore[import-untyped]

from ..errors import AppError
from ..operations import OperationManager, ensure_safe_relative_path, utc_now
from ..paths import DataPaths
from ..schemas.models import HfFile, HfMatch, HfRepoData, HfSearchData, HfSearchItem, LocalModel

MODEL_EXTENSIONS = {
    ".gguf": "gguf",
    ".ggml": "ggml",
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


class ModelCatalogue:
    def __init__(
        self,
        paths: DataPaths,
        *,
        operations: OperationManager | None = None,
    ) -> None:
        self.paths = paths
        self.operations = operations or OperationManager(paths.engines_log_dir)
        self._items: list[LocalModel] | None = None
        self.scan_error: str | None = None

    async def close(self) -> None:
        await self.operations.close()

    def scan(self) -> list[LocalModel]:
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
        items: list[LocalModel] = []
        for path in paths:
            try:
                relative_parts = path.relative_to(root).parts
            except ValueError:
                continue
            if any(part.startswith(".") for part in relative_parts):
                continue
            try:
                if path.name == SIDECAR_DIR or path.name.endswith(SIDECAR_DIR):
                    continue
                is_dir = path.is_dir()
                is_file = path.is_file()
            except (OSError, RuntimeError):
                # Keep scanning the remaining artefacts.  The individual
                # missing/stat-failed item is not represented as size zero.
                continue
            if is_dir or is_file:
                # Include recognised model artefacts and unknown files.  A
                # manually copied model must remain visible even without a
                # known extension; metadata/config sidecars are omitted only
                # when they are part of a known model directory below.
                items.append(self._describe(path))
        self._items = items
        self.scan_error = None
        return items

    def mark_scan_failed(self, error: Exception) -> None:
        """Keep local model reads available after a non-fatal startup failure."""

        self.scan_error = str(error)
        self._items = []

    def items(self) -> list[LocalModel]:
        return self._items if self._items is not None else self.scan()

    def _describe(self, path: Path) -> LocalModel:
        try:
            is_dir = path.is_dir()
        except OSError:
            is_dir = False
        format_name = self._format(path)
        compatibility = "unknown"
        match = self._match(path)
        if is_dir:
            size = _directory_size(path)
        else:
            try:
                size = path.stat().st_size
            except OSError:
                size = None
        try:
            canonical = str(path.resolve(strict=False))
        except (OSError, RuntimeError):
            canonical = str(path.absolute())
        identifier = "model_" + hashlib.sha256(canonical.encode()).hexdigest()[:24]
        return LocalModel(
            id=identifier,
            path=path,
            kind="directory" if is_dir else "file",
            format=format_name,
            compatibility=compatibility,
            size_bytes=size,
            hf_match=match,
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
            if any(item.suffix.lower() in {".gguf", ".ggml"} for item in children):
                return "gguf"
            if "model.safetensors.index.json" in names:
                return "safetensors"
            return "unknown"
        return MODEL_EXTENSIONS.get(path.suffix.lower(), "unknown")

    def _match(self, path: Path) -> HfMatch:
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
            status = cast(
                Literal["downloaded", "incomplete", "unknown"],
                _manifest_status(path, manifest),
            )
            return HfMatch(
                status=status, repo_id=manifest["repo_id"], revision=manifest["revision"]
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
        return HfRepoData(repo_id=repo_id, revision=resolved, files=files)

    async def hf_files(self, repo_id: str, revision: str | None = None) -> HfRepoData:
        return await self.hf_repo(repo_id, revision)

    async def download(self, request: Any) -> dict[str, Any]:
        # All revisions of one repository share the same destination.  They
        # therefore use one lock target to avoid interleaving writes.
        target_id = f"hf:{request.repo_id}"

        async def worker(operation: dict[str, Any]) -> None:
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
            try:
                await asyncio.to_thread(self.paths.models_root.mkdir, parents=True, exist_ok=True)
                destination_root = self.paths.models_root.resolve(strict=True)
            except (OSError, RuntimeError) as exc:
                raise RuntimeError(f"models_root недоступен: {exc}") from exc
            destination = _safe_hf_path(destination_root, request.repo_id)
            await asyncio.to_thread(destination.mkdir, parents=True, exist_ok=True)
            destination = _safe_hf_path(destination_root, request.repo_id)
            async with httpx.AsyncClient(base_url="https://huggingface.co", timeout=None) as client:
                for item in wanted:
                    relative = ensure_safe_relative_path(item.path)
                    file_path = _safe_hf_path(destination_root, f"{request.repo_id}/{relative}")
                    parent = file_path.parent
                    await asyncio.to_thread(parent.mkdir, parents=True, exist_ok=True)
                    # Re-check after mkdir, because a pre-existing symlink in
                    # any newly materialised component must still stay inside.
                    file_path = _safe_hf_path(destination_root, f"{request.repo_id}/{relative}")
                    async with client.stream(
                        "GET",
                        f"/{request.repo_id}/resolve/{repo.revision}/{relative}",
                        follow_redirects=True,
                    ) as response:
                        response.raise_for_status()
                        await _stream_response_to_file(response, file_path)
                    operation["stdout"] += f"downloaded {relative}\n"
            manifest = {
                "schema_version": 1,
                "repo_id": request.repo_id,
                "revision": repo.revision,
                "scope": "snapshot" if request.files is None else "selection",
                "files": [item.model_dump(mode="json") for item in wanted],
            }
            manifest_path = _safe_hf_path(destination_root, f"{request.repo_id}/{SIDECAR_DIR}")
            await _atomic_write_text(manifest_path, yaml.safe_dump(manifest, sort_keys=False))
            operation["checked_at"] = utc_now()
            await asyncio.to_thread(self.scan)

        return await self.operations.create("download", target_id, worker)

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
            result.append(
                HfFile(
                    path=path,
                    size=size,
                    etag=item.get("lfs", {}).get("oid")
                    if isinstance(item.get("lfs"), dict)
                    else item.get("oid"),
                    sha256=item.get("sha256"),
                )
            )
        return result


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


def _manifest_status(path: Path, manifest: dict[str, Any]) -> str:
    if path.is_file() and len(manifest["files"]) != 1:
        return "unknown"
    matches = 0
    unverifiable = False
    unknown_size = False
    for item in manifest["files"]:
        candidate = path / item["path"] if path.is_dir() else path
        if not candidate.is_file():
            continue
        try:
            expected_size = item.get("size")
            if expected_size is None:
                unknown_size = True
                continue
            if candidate.stat().st_size != expected_size:
                continue
            expected_hash = item.get("sha256") or item.get("etag")
            if expected_hash:
                hash_result = _hash_matches(candidate, expected_hash)
                if hash_result is None:
                    unverifiable = True
                    continue
                if not hash_result:
                    continue
        except OSError:
            continue
        matches += 1
    if unknown_size:
        return "unknown"
    if matches == len(manifest["files"]):
        if unverifiable:
            return "unknown"
        return "downloaded"
    if matches:
        return "incomplete"
    return "unknown"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _hash_matches(path: Path, expected: str) -> bool | None:
    expected = expected.removeprefix("sha256:")
    if len(expected) == 64:
        return _sha256(path) == expected
    if len(expected) == 32:
        digest = hashlib.md5()  # noqa: S324 - HF ETag may be an MD5 value
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest() == expected
    # An opaque ETag can still be useful metadata but cannot be verified from
    # local bytes without provider-specific semantics.
    return None


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


async def _stream_response_to_file(response: Any, destination: Path) -> None:
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
            status = cast(
                Literal["downloaded", "incomplete", "unknown"],
                _manifest_status(snapshot, manifest),
            )
            return HfMatch(status=status, repo_id=repo_id, revision=revision)
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
