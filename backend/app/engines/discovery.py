"""Discovery coordinator for all supported engines."""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path

from ..config import Settings
from ..paths import DataPaths
from ..schemas.engines import Engine, Installation
from .adapters import ADAPTER_BY_ID, ADAPTERS, Candidate, read_engine_manifest


def installation_id(engine_id: str, kind: str, path: Path) -> str:
    canonical = str(Path(path).expanduser().resolve(strict=False))
    digest = hashlib.sha256(f"{engine_id}\0{kind}\0{canonical}".encode()).hexdigest()[:24]
    return f"inst_{digest}"


@dataclass(slots=True)
class DiscoveryResult:
    engines: list[Engine]
    installations: list[Installation]
    diagnostics: list[dict[str, str]]


class DiscoveryCoordinator:
    """Collect candidates, deduplicate them and verify each engine pair."""

    def __init__(self, settings: Settings, paths: DataPaths) -> None:
        self.settings = settings
        self.paths = paths

    def scan(self, *, explicit: list[tuple[str, Path]] | None = None) -> DiscoveryResult:
        candidates: list[Candidate] = []
        diagnostics: list[dict[str, str]] = []
        roots = [Path("/opt/venvs"), self.paths.venvs_dir, *self.settings.extra_venv_roots]
        for root in _unique_paths(roots, diagnostics):
            try:
                canonical_root = root.resolve(strict=True)
                if not canonical_root.is_dir():
                    diagnostics.append({"path": str(root), "code": "venv_root_not_directory"})
                    continue
                if not os.access(canonical_root, os.R_OK | os.X_OK):
                    diagnostics.append({"path": str(root), "code": "venv_root_unreadable"})
                    continue
            except FileNotFoundError:
                diagnostics.append({"path": str(root), "code": "venv_root_missing"})
                continue
            except (OSError, RuntimeError) as exc:
                diagnostics.append(
                    {"path": str(root), "code": "venv_root_error", "message": str(exc)[:200]}
                )
                continue
            for adapter in ADAPTERS:
                try:
                    candidates.extend(adapter.candidates_from_venv(root, str(root)))
                except (OSError, RuntimeError) as exc:
                    diagnostics.append(
                        {"path": str(root), "code": "venv_scan_error", "message": str(exc)[:200]}
                    )
        for adapter in ADAPTERS:
            try:
                candidates.extend(adapter.candidates_from_path())
            except (OSError, RuntimeError) as exc:
                diagnostics.append(
                    {
                        "path": adapter.engine_id,
                        "code": "path_scan_error",
                        "message": str(exc)[:200],
                    }
                )
        for engine_id, path in explicit or []:
            explicit_adapter = ADAPTER_BY_ID.get(engine_id)
            if explicit_adapter is None:
                continue
            canonical = path.expanduser().resolve(strict=False)
            if explicit_adapter.kind == "binary":
                candidates.append(Candidate(engine_id, "binary", canonical, canonical, "explicit"))
            else:
                executable = canonical / "bin" / explicit_adapter.command
                candidates.append(
                    Candidate(engine_id, "python_venv", canonical, executable, "explicit")
                )

        unique: dict[tuple[str, str, str], Candidate] = {}
        for candidate in candidates:
            key = (
                candidate.engine_id,
                candidate.kind,
                str(candidate.executable_path.resolve(strict=False)),
            )
            unique.setdefault(key, candidate)

        installations: list[Installation] = []
        by_engine: dict[str, list[Installation]] = {adapter.engine_id: [] for adapter in ADAPTERS}
        for candidate in sorted(
            unique.values(), key=lambda value: (value.engine_id, str(value.root_path))
        ):
            adapter = ADAPTER_BY_ID[candidate.engine_id]
            verification = adapter.verify(candidate)
            status = "ready" if verification.verified else "broken"
            diagnostic = verification.diagnostic
            version = verification.version
            # A durable broken marker records a failed install/update.  It is
            # authoritative until a later successful operation writes a new
            # ready marker, even if an older executable still passes its
            # version probe.
            manifest = read_engine_manifest(candidate.root_path)
            if manifest is not None and manifest.engine_id == candidate.engine_id:
                if manifest.status == "broken":
                    status = "broken"
                    diagnostic = (
                        manifest.diagnostic
                        or diagnostic
                        or "Последняя операция завершилась ошибкой"
                    )
                    version = manifest.version
            item = Installation(
                id=installation_id(candidate.engine_id, candidate.kind, candidate.root_path),
                engine_id=candidate.engine_id,  # type: ignore[arg-type]
                kind=candidate.kind,  # type: ignore[arg-type]
                root_path=candidate.root_path.resolve(strict=False),
                executable_path=candidate.executable_path.resolve(strict=False),
                version=version,
                status=status,  # type: ignore[arg-type]
                verified=verification.verified and status == "ready",
                diagnostic=diagnostic,
            )
            installations.append(item)
            by_engine[candidate.engine_id].append(item)

        engines = [
            Engine(
                id=adapter.engine_id,  # type: ignore[arg-type]
                display_name=adapter.display_name,
                installation_kinds=list(adapter.installation_kinds),  # type: ignore[arg-type]
                installations=by_engine[adapter.engine_id],
            )
            for adapter in ADAPTERS
        ]
        return DiscoveryResult(engines, installations, diagnostics)


def _unique_paths(paths: list[Path], diagnostics: list[dict[str, str]] | None = None) -> list[Path]:
    seen: set[str] = set()
    result: list[Path] = []
    for path in paths:
        try:
            canonical = str(path.expanduser().resolve(strict=False))
        except (OSError, RuntimeError) as exc:
            if diagnostics is not None:
                diagnostics.append(
                    {"path": str(path), "code": "venv_root_error", "message": str(exc)[:200]}
                )
            continue
        if canonical not in seen:
            seen.add(canonical)
            result.append(path)
    return result


__all__ = ["DiscoveryCoordinator", "DiscoveryResult", "installation_id"]
