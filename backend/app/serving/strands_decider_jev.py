"""Executable entrypoint for the optional Strands Decider System One server.

The external ``strands-decider`` package owns model loading and its native
System One HTTP API. This module only provides a stable local-checkpoint entrypoint;
it does not reimplement inference or silently resolve Hub model IDs.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
from typing import Sequence


def local_checkpoint(value: str) -> Path:
    checkpoint = Path(value).expanduser().resolve()
    if not checkpoint.is_dir():
        raise SystemExit(f"--model must be an existing local checkpoint directory: {checkpoint}")
    return checkpoint


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", required=True, help="Existing local Strands Decider checkpoint")
    parser.add_argument("--model-name", help="Optional name returned by the native Jev API")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=18104)
    parser.add_argument("--device", choices=("cuda", "mps", "cpu", "mlx"), default="cuda")
    parser.add_argument("--vision", action="store_true", help="Enable the checkpoint's vision path")
    args = parser.parse_args(argv)
    checkpoint = local_checkpoint(args.model)

    # Match the local serving adapters: a missing base checkpoint is an
    # explicit setup problem, not an implicit model download at process start.
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    try:
        from strands_decider.server import create_app
    except ImportError as exc:
        raise SystemExit(
            "Strands Decider serving dependency is unavailable; install the "
            "official strands-decider package in this profile's environment"
        ) from exc
    try:
        import uvicorn
    except ImportError as exc:
        raise SystemExit(
            "uvicorn is required to run the Strands Decider HTTP entrypoint"
        ) from exc

    app = create_app(
        str(checkpoint),
        device=args.device,
        model_name=args.model_name,
        vision=args.vision,
    )
    uvicorn.run(app, host=args.host, port=args.port, workers=1)


if __name__ == "__main__":
    main()


__all__ = ["local_checkpoint", "main"]
