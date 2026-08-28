#!/usr/bin/env bash
# Frontend tests and production build. Network calls in tests are mocked.
set -uo pipefail
cd "$(dirname "$0")/.."

[[ -f frontend/package.json ]] || { echo "[frontend] package.json not found — skipping"; exit 0; }

FAILED=0
echo "[frontend] vitest"
(cd frontend && pnpm test) || FAILED=1
echo "[frontend] build"
(cd frontend && pnpm run build) || FAILED=1
exit "$FAILED"
