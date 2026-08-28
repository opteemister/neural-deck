#!/usr/bin/env bash
# Build a project archive for native Linux service deployment.
# Usage: ./scripts/make_deploy_archive.sh
# Deploy: DEPLOY_HOST=myserver DEPLOY_PATH=/opt/llm-studio ./scripts/make_deploy_archive.sh --deploy
set -euo pipefail
cd "$(dirname "$0")/.."

PROJECT_NAME="$(basename "$(pwd)")"
STAMP="$(date '+%Y%m%d_%H%M%S')"
ARCHIVE_DIR="dist/archives"
ARCHIVE="${ARCHIVE_DIR}/${PROJECT_NAME}_${STAMP}.tar.gz"
mkdir -p "$ARCHIVE_DIR"

echo "[deploy] building frontend production assets"
pnpm --dir frontend run build

echo "[deploy] building ${ARCHIVE}"
tar -czf "$ARCHIVE" \
  --exclude='./dist/archives' \
  --exclude='./node_modules' \
  --exclude='*/node_modules' \
  --exclude='./.venv' \
  --exclude='*/.venv' \
  --exclude='./.uv-cache' \
  --exclude='./.pnpm-store' \
  --exclude='./.git' \
  --exclude='./.worktrees' \
  --exclude='./data/*' \
  --exclude='./.env' \
  --exclude='./.env.local' \
  --exclude='./.env.proxy' \
  --exclude='*/__pycache__' \
  --exclude='./.mypy_cache' \
  --exclude='./.ruff_cache' \
  --exclude='./.pytest_cache' \
  .
echo "[deploy] archive ready: ${ARCHIVE}"

if [[ "${1:-}" == "--deploy" ]]; then
  : "${DEPLOY_HOST:?Set DEPLOY_HOST (server ssh alias)}"
  DEPLOY_PATH="${DEPLOY_PATH:-/opt/${PROJECT_NAME}}"
  echo "[deploy] uploading to ${DEPLOY_HOST}:${DEPLOY_PATH}"
  ssh "$DEPLOY_HOST" "sudo mkdir -p '$DEPLOY_PATH'"
  scp "$ARCHIVE" "${DEPLOY_HOST}:/tmp/${PROJECT_NAME}.tar.gz"
  ssh "$DEPLOY_HOST" "sudo tar -xzf '/tmp/${PROJECT_NAME}.tar.gz' -C '$DEPLOY_PATH' && rm '/tmp/${PROJECT_NAME}.tar.gz' && cd '$DEPLOY_PATH' && sudo ./scripts/install_systemd_service.sh"
  echo "[deploy] native service installation requested"
fi
