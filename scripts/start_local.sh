#!/usr/bin/env bash
# Build the frontend and start FastAPI as the single local application process.
set -euo pipefail

cd "$(dirname "$0")/.."
mkdir -p data

if [[ ! -x frontend/node_modules/.bin/tsc || ! -f frontend/node_modules/react-router-dom/package.json ]]; then
  (cd frontend && CI=true pnpm install --frozen-lockfile)
fi

(cd frontend && ./node_modules/.bin/tsc -b && ./node_modules/.bin/vite build)

cleanup() {
  kill "${FRONTEND_BUILD_PID:-}" "${BACKEND_PID:-}" 2>/dev/null || true
}
trap cleanup INT TERM EXIT

(
  cd frontend
  ./node_modules/.bin/vite build --watch
) &
FRONTEND_BUILD_PID=$!

(
  cd backend
  uv run --extra dev --locked uvicorn app.main:app --host 0.0.0.0 --port 3000 --reload
) &
BACKEND_PID=$!

wait "$BACKEND_PID" "$FRONTEND_BUILD_PID"
