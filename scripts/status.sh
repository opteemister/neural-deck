#!/usr/bin/env bash
set -euo pipefail

script_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=common.sh
source "$script_dir/common.sh"

root=$(repo_root)
deploy_file=${DEPLOY_ENV_FILE:-"$root/deploy.env"}
load_deploy_config "$deploy_file"

[[ "${1:-}" == "--local" || -z "${1:-}" ]] \
    || die "usage: scripts/status.sh [--local]"

if [[ -n "$DEPLOY_TARGET" && "${1:-}" != "--local" ]]; then
    [[ -n "$DEPLOY_PATH" ]] || die "DEPLOY_PATH is required when DEPLOY_TARGET is set"
    validate_deploy_path "$DEPLOY_PATH"
    info "status target: ${DEPLOY_TARGET}:${DEPLOY_PATH}"
    remote_path=$(shell_quote "$DEPLOY_PATH")
    remote_script="cd ${remote_path} && bash scripts/status.sh --local"
    if ! ssh -- "$DEPLOY_TARGET" "$remote_script"; then
        die "remote status check failed for ${DEPLOY_TARGET} (SSH failure or unhealthy service; see output above)"
    fi
    exit 0
fi

env_file=${LLM_STUDIO_ENV_FILE:-"$root/.env"}
[[ -f "$env_file" ]] || die "runtime config is missing: ${env_file}; copy .env.example to .env"
host=$(require_dotenv_value "$env_file" HOST)
port=$(require_dotenv_value "$env_file" PORT)
command -v systemctl >/dev/null 2>&1 \
    || die "systemctl is required for local status checks; use SSH status for a Linux target"

info "status target: local machine (${root})"
info "Autostart (systemctl is-enabled):"
if systemctl is-enabled llm-studio.service; then
    autostart_failed=0
else
    autostart_failed=1
fi

info "Service status (systemctl status):"
if ! systemctl is-active --quiet llm-studio; then
    warn "systemd service llm-studio is not active"
    systemctl status llm-studio --no-pager --full || true
    service_failed=1
else
    service_failed=0
    systemctl status llm-studio --no-pager --full || true
    info "systemd: active"
fi

info "Last 100 log lines from the current boot (journalctl):"
if ! journalctl -u llm-studio.service -b --no-pager -n 100; then
    warn "could not read service journal; run with a user allowed to read the system journal"
    journal_failed=1
else
    journal_failed=0
fi

info "HTTP availability:"
if check_http_health "$host" "$port"; then
    health_failed=0
else
    health_failed=1
fi

if (( autostart_failed || service_failed || journal_failed || health_failed )); then
    exit 1
fi
