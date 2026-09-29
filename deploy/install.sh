#!/usr/bin/env bash
set -euo pipefail

script_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
project_root=$(cd "$script_dir/.." && pwd)
# shellcheck source=../scripts/common.sh
source "$project_root/scripts/common.sh"

# ``scripts/deploy.sh`` confirms the local/remote target before invoking this
# procedure.  Keep a second guard for direct invocation so an operator cannot
# accidentally mutate the current machine without an explicit Y/N response.
if [[ "${LLM_STUDIO_INSTALL_CONFIRMED:-}" != "1" ]]; then
    info "This will install and start systemd service llm-studio on the current machine."
    printf 'Continue with this local installation? [y/N] '
    answer=''
    IFS= read -r answer || answer=''
    case "$answer" in
        y|Y) ;;
        *) info "Local installation cancelled."; exit 0 ;;
    esac
fi

[[ "$(uname -s)" == "Linux" ]] \
    || die "native installation requires Linux with systemd"
command -v systemctl >/dev/null 2>&1 \
    || die "systemctl is required for the llm-studio service"
runtime_file=${LLM_STUDIO_ENV_FILE:-"$project_root/.env"}
[[ -f "$runtime_file" ]] \
    || die "runtime config is missing: ${runtime_file}"

if [[ -n "${LLM_STUDIO_SERVICE_USER:-}" ]]; then
    service_user=$LLM_STUDIO_SERVICE_USER
elif [[ $EUID -eq 0 ]]; then
    # Preserve the interactive operator when the script itself was entered
    # through sudo; a bare root SSH session intentionally remains root unless
    # LLM_STUDIO_SERVICE_USER is set explicitly.
    service_user=${SUDO_USER:-root}
else
    service_user=$(id -un)
fi
id "$service_user" >/dev/null 2>&1 \
    || die "service user does not exist: ${service_user}"
service_group=$(id -gn "$service_user")

data_root=$(require_dotenv_value "$runtime_file" LLM_STUDIO_DATA_ROOT)
host=$(require_dotenv_value "$runtime_file" HOST)
port=$(require_dotenv_value "$runtime_file" PORT)
models_root=$(dotenv_value "$runtime_file" MODELS_ROOT || true)
log_root=$(dotenv_value "$runtime_file" LOG_ROOT || true)
[[ -n "$models_root" ]] || models_root="$data_root/models"
[[ -n "$log_root" ]] || log_root="$data_root/logs"

validate_runtime_path() {
    local key=$1
    local path=$2
    [[ "$path" == /* && "$path" != "/" ]] \
        || die "${key} must be an absolute non-root path"
    [[ "$path" != *$'\n'* && "$path" != *$'\r'* ]] \
        || die "${key} must not contain newlines"
}

validate_runtime_path LLM_STUDIO_DATA_ROOT "$data_root"
validate_runtime_path MODELS_ROOT "$models_root"
validate_runtime_path LOG_ROOT "$log_root"
[[ "$port" =~ ^[0-9]+$ ]] || die "PORT must be numeric"

# Alembic imports the same settings boundary as the service. Export the
# already validated values so migrations work even when invoked from the
# backend directory, where the project-root .env is not auto-discovered.
export LLM_STUDIO_DATA_ROOT="$data_root"
export HOST="$host"
export PORT="$port"
export MODELS_ROOT="$models_root"
export LOG_ROOT="$log_root"

as_root() {
    if [[ $EUID -eq 0 ]]; then
        "$@"
    else
        sudo "$@"
    fi
}

info "Installing LLM Studio at ${project_root} for ${service_user}:${service_group}"
info "Persistent data: ${data_root}"
info "Persistent logs: ${log_root}"

as_root install -d -m 0750 -o "$service_user" -g "$service_group" \
    /etc/llm-studio \
    "$data_root" \
    "$data_root/config" \
    "$data_root/data" \
    "$data_root/profiles" \
    "$data_root/runtime" \
    "$data_root/runtime/venvs" \
    "$data_root/audit" \
    "$models_root" \
    "$log_root" \
    "$log_root/studio" \
    "$log_root/engines" \
    "$log_root/api"

if [[ -f "$project_root/backend/pyproject.toml" ]]; then
    command -v uv >/dev/null 2>&1 \
        || die "uv is required to install backend dependencies on the target"
    uv sync --project "$project_root/backend" --no-dev --locked
elif [[ -f "$project_root/backend/requirements.txt" ]]; then
    command -v python3 >/dev/null 2>&1 \
        || die "python3 is required to install backend dependencies"
    if [[ ! -x "$project_root/backend/.venv/bin/python" ]]; then
        python3 -m venv "$project_root/backend/.venv"
    fi
    "$project_root/backend/.venv/bin/python" -m pip install \
        --disable-pip-version-check -r "$project_root/backend/requirements.txt"
else
    die "backend dependency manifest not found under ${project_root}/backend"
fi

if [[ -f "$project_root/frontend/package.json" ]]; then
    command -v pnpm >/dev/null 2>&1 \
        || die "pnpm is required to build the frontend from pnpm-lock.yaml on the target"
    [[ -f "$project_root/frontend/pnpm-lock.yaml" ]] \
        || die "frontend lockfile not found: ${project_root}/frontend/pnpm-lock.yaml"
    (cd "$project_root/frontend" && pnpm install --frozen-lockfile)
    pnpm --dir "$project_root/frontend" run build
else
    die "frontend package manifest not found: ${project_root}/frontend/package.json"
fi

if [[ -f "$project_root/backend/alembic.ini" ]]; then
    if [[ -x "$project_root/backend/.venv/bin/alembic" ]]; then
        (cd "$project_root/backend" && .venv/bin/alembic upgrade head)
    else
        uv run --project "$project_root/backend" --no-sync \
            alembic -c "$project_root/backend/alembic.ini" upgrade head
    fi
elif [[ -f "$project_root/alembic.ini" ]]; then
    uv run --project "$project_root/backend" --no-sync \
        alembic -c "$project_root/alembic.ini" upgrade head
else
    die "Alembic configuration not found; refusing to start without migrations"
fi

uv_bin=$(command -v uv 2>/dev/null || true)
[[ -n "$uv_bin" ]] || uv_bin=uv

escape_sed_replacement() {
    printf '%s' "$1" | sed 's/[\\&|]/\\&/g'
}
project_replacement=$(escape_sed_replacement "$project_root")
user_replacement=$(escape_sed_replacement "$service_user")
group_replacement=$(escape_sed_replacement "$service_group")
uv_replacement=$(escape_sed_replacement "$uv_bin")
data_replacement=$(escape_sed_replacement "$data_root")
log_replacement=$(escape_sed_replacement "$log_root")

service_tmp=$(mktemp)
trap 'rm -f -- "$service_tmp"' EXIT
sed \
    -e "s|__PROJECT_PATH__|${project_replacement}|g" \
    -e "s|__SERVICE_USER__|${user_replacement}|g" \
    -e "s|__SERVICE_GROUP__|${group_replacement}|g" \
    -e "s|__UV_BIN__|${uv_replacement}|g" \
    -e "s|__DATA_ROOT__|${data_replacement}|g" \
    -e "s|__LOG_ROOT__|${log_replacement}|g" \
    "$project_root/deploy/systemd/llm-studio.service.template" > "$service_tmp"

as_root install -m 0644 -o root -g root "$service_tmp" \
    /etc/systemd/system/llm-studio.service
as_root install -m 0640 -o "$service_user" -g "$service_group" \
    "$runtime_file" /etc/llm-studio/llm-studio.env
as_root systemctl daemon-reload
as_root systemctl enable llm-studio.service
as_root systemctl restart llm-studio.service

info "systemd service restarted; waiting for health response"
for attempt in {1..30}; do
    if systemctl is-active --quiet llm-studio.service \
        && check_http_health "$host" "$port"; then
        info "LLM Studio installation completed successfully"
        exit 0
    fi
    sleep 1
done

systemctl status llm-studio.service --no-pager --full || true
die "service did not become healthy after 30 seconds"
