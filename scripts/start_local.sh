#!/usr/bin/env bash
# Build the frontend and start FastAPI as the single local application process.
set -euo pipefail

cd "$(dirname "$0")/.."
mkdir -p data

(cd frontend && pnpm run build)

cleanup() {
  kill "${FRONTEND_BUILD_PID:-}" "${BACKEND_PID:-}" 2>/dev/null || true
}
trap cleanup INT TERM EXIT

(
  cd frontend
  pnpm run build:watch
) &
FRONTEND_BUILD_PID=$!

(
  cd backend
  uv run --extra dev uvicorn app.main:app --host 0.0.0.0 --port 3000 --reload
) &
BACKEND_PID=$!

wait "$BACKEND_PID" "$FRONTEND_BUILD_PID"
