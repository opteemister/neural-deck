"""Run official Hub snapshot downloads in a cancellable child process.

Stdout carries structured progress; Hub diagnostics remain on stderr.
"""

from __future__ import annotations

import json
import sys
import threading
import time
from pathlib import Path
from typing import Any

from huggingface_hub import snapshot_download
from tqdm.auto import tqdm  # type: ignore[import-untyped]


def main() -> None:
    plan = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    files = plan["files"]
    total = sum(item["size"] for item in files)
    progress = {
        "files_total": len(files),
        "files_completed": 0,
        "bytes_total": total,
        "bytes_completed": 0,
        "current_file": None,
    }
    lock = threading.RLock()
    last_report = 0.0
    cached_bytes = 0
    # Hugging Face can expose one byte-progress bar per file. Keep each bar's
    # cumulative contribution and aggregate them instead of replacing the
    # operation total with whichever bar reported last.
    byte_bars: dict[object, int] = {}

    def report(*, force: bool = False) -> None:
        nonlocal last_report
        now = time.monotonic()
        if not force and now - last_report < 0.25:
            return
        last_report = now
        print(json.dumps(progress), flush=True)

    class Progress(tqdm):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            kwargs.pop("name", None)
            kwargs["disable"] = False
            self.byte_progress = str(kwargs.get("desc", "")).startswith("Reconstructing")
            self.file_progress = str(kwargs.get("desc", "")).startswith("Fetching")
            super().__init__(*args, **kwargs)
            if self.byte_progress:
                with lock:
                    byte_bars[self] = 0

        def update(self, n: float = 1) -> Any:
            result = super().update(n)
            with lock:
                if self.byte_progress:
                    byte_bars[self] = max(byte_bars.get(self, 0), int(self.n))
                    aggregated = cached_bytes + sum(byte_bars.values())
                    progress["bytes_completed"] = max(
                        progress["bytes_completed"], min(total, aggregated)
                    )
                if self.file_progress:
                    progress["files_completed"] = max(progress["files_completed"], int(self.n))
                report()
            return result

        def close(self) -> None:
            if hasattr(self, "n"):
                with lock:
                    if self.file_progress:
                        progress["files_completed"] = max(progress["files_completed"], int(self.n))
                    if self.byte_progress:
                        byte_bars[self] = max(byte_bars.get(self, 0), int(self.n))
                        progress["bytes_completed"] = max(
                            progress["bytes_completed"],
                            min(total, cached_bytes + sum(byte_bars.values())),
                        )
                    report(force=True)
            super().close()

    # Escape wildcard characters because these are exact paths selected by
    # the UI, not user-supplied glob expressions.
    patterns = ["".join({"[": "[[]", "*": "[*]", "?": "[?]"}.get(c, c) for c in item["path"]) for item in files]
    options: dict[str, Any] = {
        "repo_id": plan["repo_id"],
        "revision": plan["revision"],
        "local_dir": plan["destination"],
        "allow_patterns": patterns,
        "force_download": plan.get("force", False),
    }
    preview = snapshot_download(**options, dry_run=True)
    cached_bytes = sum(item.file_size for item in preview if not item.will_download)
    progress["bytes_completed"] = cached_bytes
    report(force=True)
    snapshot_download(**options, tqdm_class=Progress)
    progress["files_completed"] = len(files)
    progress["bytes_completed"] = total
    report(force=True)


if __name__ == "__main__":
    main()
