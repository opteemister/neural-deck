#!/usr/bin/env bash
# Install the native Linux production service from an extracted project archive.
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
INSTALL_DIR="${INSTALL_DIR:-/opt/llm-studio}"
SERVICE_NAME="${SERVICE_NAME:-llm-studio}"
SERVICE_USER="${SERVICE_USER:-llm-studio}"
ENV_DIR="/etc/${SERVICE_NAME}"

if [[ "$(id -u)" -ne 0 ]]; then
  echo "Run this script as root, for example: sudo ./scripts/install_systemd_service.sh" >&2
  exit 1
fi

id "$SERVICE_USER" >/dev/null 2>&1 || useradd --system --home-dir "$INSTALL_DIR" --shell /usr/sbin/nologin "$SERVICE_USER"
install -d -o "$SERVICE_USER" -g "$SERVICE_USER" "$INSTALL_DIR" /var/lib/llm-studio /var/log/llm-studio
if [[ "$PROJECT_ROOT" != "$INSTALL_DIR" ]]; then
  cp -R "$PROJECT_ROOT/". "$INSTALL_DIR/"
fi
chown -R "$SERVICE_USER:$SERVICE_USER" "$INSTALL_DIR"

install -d -m 0750 "$ENV_DIR"
if [[ ! -f "$ENV_DIR/llm-studio.env" ]]; then
  echo "Create $ENV_DIR/llm-studio.env before installing the service." >&2
  exit 1
fi

set -a
source "$ENV_DIR/llm-studio.env"
set +a

runuser -u "$SERVICE_USER" -- uv sync --project "$INSTALL_DIR/backend" --locked
(
  cd "$INSTALL_DIR/backend"
  runuser -u "$SERVICE_USER" -- env DATABASE_URL="$DATABASE_URL" uv run --project . --locked alembic upgrade head
)

sed -e "s|__INSTALL_DIR__|$INSTALL_DIR|g" -e "s|__SERVICE_NAME__|$SERVICE_NAME|g" -e "s|__SERVICE_USER__|$SERVICE_USER|g" \
  "$INSTALL_DIR/deploy/llm-studio.service.example" > "/etc/systemd/system/${SERVICE_NAME}.service"
systemctl daemon-reload
systemctl enable --now "$SERVICE_NAME.service"
echo "Installed and started ${SERVICE_NAME}.service"
