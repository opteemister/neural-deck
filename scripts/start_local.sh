#!/usr/bin/env bash
# Start both development processes in the single local application container.
set -euo pipefail

cd "$(dirname "$0")/.."
mkdir -p data

cleanup() {
  kill "${BACKEND_PID:-}" "${FRONTEND_PID:-}" 2>/dev/null || true
}
trap cleanup INT TERM EXIT

(
  cd backend
  uv run --extra dev uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
) &
BACKEND_PID=$!

(
  cd frontend
  pnpm run dev --host 0.0.0.0 --port 5173
) &
FRONTEND_PID=$!

wait "$BACKEND_PID" "$FRONTEND_PID"
