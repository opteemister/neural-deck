"""Engine-specific discovery and verification adapters.

Adapters are intentionally small.  Discovery owns candidate enumeration while
an adapter decides how a candidate proves that a particular engine is really
installed.  This prevents a Python environment containing only vLLM from
being reported as an SGLang installation.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, cast

import yaml  # type: ignore[import-untyped]


@dataclass(frozen=True, slots=True)
class Candidate:
    engine_id: str
    kind: str
    root_path: Path
    executable_path: Path
    source: str


@dataclass(frozen=True, slots=True)
class Verification:
    verified: bool
    version: str | None
    diagnostic: str | None = None


@dataclass(frozen=True, slots=True)
class EngineManifest:
    """Durable result of the last install/update verification.

    The manifest is evidence about an operation, not a replacement for a
    live executable check.  Discovery uses its broken state as an explicit
    override so a failed update cannot be hidden by an old executable that
    still happens to answer ``--version``.
    """

    engine_id: str
    status: str | None
    version: str | None
    diagnostic: str | None


class EngineAdapter(Protocol):
    engine_id: str
    display_name: str
    kind: str
    installation_kinds: tuple[str, ...]

    def candidates_from_venv(self, root: Path, source: str) -> list[Candidate]: ...

    def candidates_from_path(self) -> list[Candidate]: ...

    def verify(self, candidate: Candidate) -> Verification: ...


def _run_version(command: list[str]) -> tuple[bool, str | None, str | None]:
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
            env=os.environ.copy(),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return False, None, str(exc)
    output = (result.stdout or "").strip() or (result.stderr or "").strip()
    if result.returncode != 0:
        return False, None, output or f"команда завершилась с кодом {result.returncode}"
    return True, _extract_version(output), None


def _extract_version(output: str) -> str | None:
    # Keep the original text when an upstream tool uses a non-semver format;
    # an empty result remains an honest unknown version.
    for token in output.replace("=", " ").split():
        if any(character.isdigit() for character in token):
            return token.strip("()[]{}:,;")
    return output.splitlines()[0][:200] if output else None


class PythonEngineAdapter:
    kind = "python_venv"

    def __init__(self, engine_id: str, display_name: str, command: str) -> None:
        self.engine_id = engine_id
        self.display_name = display_name
        self.command = command
        self.installation_kinds = (self.kind,)

    def candidates_from_venv(self, root: Path, source: str) -> list[Candidate]:
        if not root.is_dir():
            return []
        candidates: list[Candidate] = []
        roots = [root]
        try:
            roots.extend(sorted(path for path in root.iterdir() if path.is_dir()))
        except OSError:
            return []
        for venv in roots:
            executable = venv / "bin" / self.command
            if executable.is_file() or _manifest_engine_id(venv) == self.engine_id:
                candidates.append(Candidate(self.engine_id, self.kind, venv, executable, source))
        return candidates

    def candidates_from_path(self) -> list[Candidate]:
        path = shutil.which(self.command)
        if not path:
            return []
        executable = Path(path).resolve()
        # An executable found in PATH can be a script in a virtualenv.  The
        # environment root is inferred only when the standard layout exists.
        root = executable.parent.parent if executable.parent.name == "bin" else executable
        return [Candidate(self.engine_id, self.kind, root, executable, "PATH")]

    def verify(self, candidate: Candidate) -> Verification:
        if not candidate.executable_path.is_file() or not os.access(
            candidate.executable_path, os.X_OK
        ):
            return Verification(False, None, "исполняемый файл движка отсутствует или недоступен")
        command = [str(candidate.executable_path)]
        if self.engine_id == "sglang":
            command.append("version")
        else:
            command.append("--version")
        ok, version, diagnostic = _run_version(command)
        return Verification(ok, version, diagnostic if not ok else None)


class BinaryEngineAdapter:
    kind = "binary"

    def __init__(self, engine_id: str, display_name: str, command: str, *, version: bool) -> None:
        self.engine_id = engine_id
        self.display_name = display_name
        self.command = command
        self.check_version = version
        self.installation_kinds = (self.kind,)

    def candidates_from_venv(self, root: Path, source: str) -> list[Candidate]:
        return []

    def candidates_from_path(self) -> list[Candidate]:
        path = shutil.which(self.command)
        if not path:
            return []
        executable = Path(path).resolve()
        return [Candidate(self.engine_id, self.kind, executable, executable, "PATH")]

    def verify(self, candidate: Candidate) -> Verification:
        executable = candidate.executable_path
        if not executable.is_file() or not os.access(executable, os.X_OK):
            return Verification(False, None, "исполняемый файл отсутствует или недоступен")
        if self.check_version:
            ok, version, diagnostic = _run_version([str(executable), "--version"])
            return Verification(ok, version, diagnostic if not ok else None)
        ok, _version, diagnostic = _run_version([str(executable), "--help"])
        return Verification(ok, None, diagnostic if not ok else None)


ADAPTERS = (
    PythonEngineAdapter("vllm", "vLLM", "vllm"),
    PythonEngineAdapter("sglang", "SGLang", "sglang"),
    PythonEngineAdapter("freetoken", "FreeToken", "ft"),
    BinaryEngineAdapter("llama.cpp", "llama.cpp", "llama-server", version=True),
    BinaryEngineAdapter("ninfer", "NInfer", "ninfer-serve", version=False),
)

ADAPTER_BY_ID = {adapter.engine_id: adapter for adapter in ADAPTERS}


def _manifest_engine_id(root: Path) -> str | None:
    """Read the small install manifest used to retain failed candidates."""

    manifest = root / ".llm-studio-engine.yaml"
    try:
        value = yaml.safe_load(manifest.read_text(encoding="utf-8"))
    except (OSError, ValueError, yaml.YAMLError):
        return None
    if not isinstance(value, dict) or not isinstance(value.get("engine_id"), str):
        return None
    return cast(str, value["engine_id"])


def read_engine_manifest(root: Path) -> EngineManifest | None:
    """Read the durable install/update marker when it is well formed."""

    manifest = root / ".llm-studio-engine.yaml"
    try:
        value = yaml.safe_load(manifest.read_text(encoding="utf-8"))
    except (OSError, ValueError, yaml.YAMLError):
        return None
    if not isinstance(value, dict) or not isinstance(value.get("engine_id"), str):
        return None
    status = value.get("status")
    return EngineManifest(
        engine_id=cast(str, value["engine_id"]),
        status=status if isinstance(status, str) else None,
        version=value.get("version") if isinstance(value.get("version"), str) else None,
        diagnostic=(value.get("diagnostic") if isinstance(value.get("diagnostic"), str) else None),
    )


__all__ = [
    "ADAPTERS",
    "ADAPTER_BY_ID",
    "Candidate",
    "EngineAdapter",
    "EngineManifest",
    "Verification",
    "read_engine_manifest",
]
