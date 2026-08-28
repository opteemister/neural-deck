#!/usr/bin/env bash
# Backend tests. No server startup; all external resources in tests are mocked.
set -uo pipefail
cd "$(dirname "$0")/.."
PROJECT_ROOT="$(pwd)"
export UV_CACHE_DIR="${UV_CACHE_DIR:-${PROJECT_ROOT}/.uv-cache}"

[[ -f backend/pyproject.toml ]] || { echo "[backend] pyproject.toml not found — skipping"; exit 0; }

FAILED=0
echo "[backend] compileall"
(cd backend && uv run --extra dev python -m compileall -q app tests alembic) || FAILED=1
echo "[backend] pytest"
(cd backend && uv run --extra dev pytest) || FAILED=1
exit "$FAILED"
