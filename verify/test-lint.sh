#!/usr/bin/env bash
# Lint and types for the configured backend and frontend.
set -uo pipefail
cd "$(dirname "$0")/.."
PROJECT_ROOT="$(pwd)"
export UV_CACHE_DIR="${UV_CACHE_DIR:-${PROJECT_ROOT}/.uv-cache}"
FAILED=0

if [[ -f backend/pyproject.toml ]]; then
  echo "[lint] ruff check (backend)"
  (cd backend && uv run --extra dev ruff check .) || FAILED=1
  echo "[lint] ruff format --check (backend)"
  (cd backend && uv run --extra dev ruff format --check .) || FAILED=1
  echo "[lint] mypy strict (backend)"
  (cd backend && uv run --extra dev mypy .) || FAILED=1
fi

if [[ -f frontend/package.json ]]; then
  echo "[lint] eslint (frontend)"
  (cd frontend && pnpm run lint) || FAILED=1
  echo "[lint] tsc --noEmit (frontend)"
  (cd frontend && pnpm run typecheck) || FAILED=1
fi

exit "$FAILED"
