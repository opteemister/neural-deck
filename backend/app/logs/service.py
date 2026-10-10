"""Read and manage the persistent logs exposed by the browser UI."""

from __future__ import annotations

import contextlib
import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import quote, unquote

from ..errors import AppError
from ..paths import DataPaths
from ..schemas.logs import LogData, LogDeleteData, LogKind, LogLine, LogSource, LogSourceList

_PROFILE_DIR_SUFFIX = re.compile(r"^(?P<label>.+)-[0-9a-f]{12}$")


@dataclass(frozen=True, slots=True)
class _LogGroup:
    source: LogSource
    paths: tuple[Path, ...]
    metadata_dirs: tuple[Path, ...] = ()


class LogsService:
    """Discover log files without coupling the UI to their filesystem layout."""

    def __init__(self, paths: DataPaths) -> None:
        self.paths = paths

    def list_sources(self) -> LogSourceList:
        return LogSourceList(items=[group.source for group in self._groups()])

    def read(self, source_id: str, *, before: int | None = None, limit: int = 500) -> LogData:
        if limit < 1 or limit > 5_000:
            raise AppError(
                "invalid_log_limit", "Параметр limit должен быть от 1 до 5000", status_code=422
            )
        group = self._find(source_id)
        lines = self._read_lines(group.paths)
        total = len(lines)
        end = total if before is None else min(max(before, 0), total)
        start = max(0, end - limit)
        return LogData(
            source=group.source,
            lines=[
                LogLine(number=index, text=text)
                for index, text in enumerate(lines[start:end], start)
            ],
            total_lines=total,
            start_line=start,
            end_line=end,
            has_older=start > 0,
        )

    def download(self, source_id: str, *, latest: bool = False) -> tuple[LogSource, str]:
        group = self._find(source_id)
        if latest:
            if not group.paths:
                return group.source, ""
            try:
                with group.paths[-1].open(encoding="utf-8", errors="replace", newline="") as handle:
                    return group.source, handle.read()
            except OSError as exc:
                raise AppError("logs_unavailable", str(exc), status_code=503) from exc
        lines = self._read_lines(group.paths)
        body = "\n".join(lines)
        if lines:
            body += "\n"
        return group.source, body

    def delete(self, source_id: str) -> LogDeleteData:
        group = self._find(source_id)
        deleted = 0
        paths = group.paths
        if group.source.kind == "application" and not paths:
            paths = (self.paths.neural_deck_log_dir / "app.log",)
        for path in paths:
            if group.source.kind == "application" and path.name == "app.log":
                try:
                    path.write_text("", encoding="utf-8")
                except OSError as exc:
                    raise AppError("log_delete_failed", str(exc), status_code=500) from exc
                deleted += 1
                continue
            try:
                path.unlink()
            except FileNotFoundError:
                continue
            except OSError as exc:
                raise AppError("log_delete_failed", str(exc), status_code=500) from exc
            deleted += 1

        if group.source.kind == "application":
            # Truncating the active file keeps the current logging handler
            # attached to the visible path while rotated history is removed.
            application_path = self.paths.neural_deck_log_dir / "app.log"
            application_path.parent.mkdir(parents=True, exist_ok=True)
            application_path.touch(exist_ok=True)
        elif group.source.kind == "profile":
            for directory in group.metadata_dirs:
                with contextlib.suppress(OSError):
                    (directory / ".profile-name").unlink()
                with contextlib.suppress(OSError):
                    directory.rmdir()

        return LogDeleteData(id=source_id, deleted_files=deleted)

    def _groups(self) -> list[_LogGroup]:
        application_paths = self._application_paths()
        operations_paths = self._operation_paths()
        groups: list[_LogGroup] = [
            _LogGroup(
                source=self._source("application", "application", "Application", application_paths),
                paths=tuple(application_paths),
            ),
            _LogGroup(
                source=self._source("operations", "operations", "Operations", operations_paths),
                paths=tuple(operations_paths),
            ),
        ]

        profile_dirs: dict[str, list[Path]] = {}
        root = self.paths.profiles_log_dir
        if root.is_dir():
            for directory in sorted(root.iterdir(), key=lambda item: item.name):
                if not directory.is_dir():
                    continue
                paths = self._profile_paths(directory)
                if paths:
                    profile_dirs.setdefault(self._profile_label(directory), []).append(directory)

        for label, directories in sorted(profile_dirs.items(), key=lambda item: item[0].casefold()):
            paths = self._unique_paths(
                path for directory in directories for path in self._profile_paths(directory)
            )
            groups.append(
                _LogGroup(
                    source=self._source(
                        f"profile:{quote(label, safe='')}",
                        "profile",
                        label,
                        paths,
                        profile_name=label,
                    ),
                    paths=tuple(paths),
                    metadata_dirs=tuple(directories),
                )
            )
        return groups

    def _find(self, source_id: str) -> _LogGroup:
        for group in self._groups():
            if group.source.id == source_id:
                return group
        raise AppError("log_not_found", "Лог не найден", status_code=404)

    def _application_paths(self) -> list[Path]:
        root = self.paths.neural_deck_log_dir
        return self._unique_paths(path for path in root.glob("app.log*") if path.is_file())

    def _operation_paths(self) -> list[Path]:
        root = self.paths.operations_log_dir
        if not root.is_dir():
            return []
        return self._unique_paths(path for path in root.glob("*.log") if path.is_file())

    @staticmethod
    def _profile_paths(directory: Path) -> list[Path]:
        return sorted(
            (path for path in directory.glob("*.log") if path.is_file()),
            key=LogsService._file_sort_key,
        )

    @staticmethod
    def _file_sort_key(path: Path) -> tuple[float, str]:
        try:
            modified = path.stat().st_mtime
        except OSError:
            modified = 0.0
        return modified, path.name

    @staticmethod
    def _unique_paths(paths: Iterable[Path]) -> list[Path]:
        result: list[Path] = []
        seen: set[Path] = set()
        for path in paths:
            resolved = path.absolute()
            if resolved not in seen:
                seen.add(resolved)
                result.append(path)
        return sorted(result, key=LogsService._file_sort_key)

    @staticmethod
    def _profile_label(directory: Path) -> str:
        metadata = directory / ".profile-name"
        try:
            value = metadata.read_text(encoding="utf-8").strip()
        except (OSError, UnicodeError):
            value = ""
        if value:
            return value
        match = _PROFILE_DIR_SUFFIX.match(directory.name)
        return unquote(match.group("label") if match else directory.name).replace("_", " ")

    @staticmethod
    def _source(
        source_id: str,
        kind: LogKind,
        label: str,
        paths: list[Path],
        *,
        profile_name: str | None = None,
    ) -> LogSource:
        timestamps: list[float] = []
        for path in paths:
            with contextlib.suppress(OSError):
                timestamps.append(path.stat().st_mtime)
        updated_at = datetime.fromtimestamp(max(timestamps), UTC) if timestamps else None
        return LogSource(
            id=source_id,
            kind=kind,
            label=label,
            profile_name=profile_name,
            file_count=len(paths),
            updated_at=updated_at,
        )

    @staticmethod
    def _read_lines(paths: tuple[Path, ...]) -> list[str]:
        lines: list[str] = []
        for path in paths:
            try:
                lines.extend(path.read_text(encoding="utf-8", errors="replace").splitlines())
            except OSError as exc:
                raise AppError("logs_unavailable", str(exc), status_code=503) from exc
        return lines


__all__ = ["LogsService"]
