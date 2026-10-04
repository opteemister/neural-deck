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

export PATH="/usr/local/bin:/usr/local/sbin:/usr/sbin:/usr/bin:/sbin:/bin:$PATH"

prepare_system_packages() {
    local package
    local missing=()
    if command -v apt-get >/dev/null 2>&1; then
        for package in ca-certificates curl tar gzip coreutils git libgomp1 util-linux dbus-user-session; do
            if [[ "$(dpkg-query -W -f='${Status}' "$package" 2>/dev/null || true)" != 'install ok installed' ]]; then
                missing+=("$package")
            fi
        done
        if (( ${#missing[@]} )); then
            info "Installing system packages: ${missing[*]}"
            as_root apt-get update
            as_root env DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends "${missing[@]}"
        fi
    elif command -v dnf >/dev/null 2>&1; then
        for package in ca-certificates curl tar gzip coreutils git libgomp util-linux dbus-daemon; do
            if ! rpm -q "$package" >/dev/null 2>&1; then
                missing+=("$package")
            fi
        done
        if (( ${#missing[@]} )); then
            info "Installing system packages: ${missing[*]}"
            as_root dnf install -y "${missing[@]}"
        fi
    else
        die "automatic dependency installation requires apt-get or dnf on a Linux system with systemd"
    fi
}

prepare_uv() (
    if command -v uv >/dev/null 2>&1; then
        # Keep uv available to systemd even if the operator uses ~/.local/bin.
        if [[ "$(command -v uv)" != /usr/local/bin/uv ]]; then
            as_root install -m 0755 "$(command -v uv)" /usr/local/bin/uv
        fi
        return
    fi
    info "Installing uv into /usr/local/bin"
    uv_installer=$(mktemp)
    trap 'rm -f -- "$uv_installer"' EXIT
    curl --fail --silent --show-error --location \
        https://astral.sh/uv/install.sh -o "$uv_installer"
    as_root env UV_UNMANAGED_INSTALL=/usr/local/bin sh "$uv_installer"
)

prepare_node() (
    if command -v node >/dev/null 2>&1 && command -v npm >/dev/null 2>&1 \
        && node -e 'process.exit(Number(process.versions.node.split(".")[0]) >= 20 ? 0 : 1)'; then
        return
    fi
    case "$(uname -m)" in
        x86_64) node_arch=x64 ;;
        aarch64|arm64) node_arch=arm64 ;;
        *) die "unsupported architecture for automatic Node.js installation: $(uname -m)" ;;
    esac
    node_version=v22.23.3
    node_archive="node-${node_version}-linux-${node_arch}.tar.gz"
    node_url="https://nodejs.org/dist/${node_version}"
    node_tmp=$(mktemp -d)
    trap 'rm -rf -- "$node_tmp"' EXIT
    info "Installing Node.js ${node_version} and npm into /usr/local"
    curl --fail --silent --show-error --location \
        "$node_url/$node_archive" -o "$node_tmp/$node_archive"
    curl --fail --silent --show-error --location \
        "$node_url/SHASUMS256.txt" -o "$node_tmp/SHASUMS256.txt"
    awk -v archive="$node_archive" '$2 == archive { print }' \
        "$node_tmp/SHASUMS256.txt" > "$node_tmp/checksum"
    [[ -s "$node_tmp/checksum" ]] || die "Node.js archive checksum is missing"
    (cd "$node_tmp" && sha256sum --check checksum)
    as_root tar --extract --gzip --file "$node_tmp/$node_archive" \
        --directory /usr/local --strip-components=1 --no-same-owner
)

as_service() {
    if [[ "$(id -un)" == "$service_user" ]]; then
        "$@"
    else
        as_root runuser -u "$service_user" -- env PATH="$PATH" "$@"
    fi
}

info "Installing LLM Studio at ${project_root} for ${service_user}:${service_group}"
info "Persistent data: ${data_root}"
info "Persistent logs: ${log_root}"

[[ -f "$project_root/backend/pyproject.toml" && -f "$project_root/backend/uv.lock" ]] \
    || die "backend pyproject.toml and uv.lock are required"
[[ -f "$project_root/frontend/package.json" && -f "$project_root/frontend/pnpm-lock.yaml" ]] \
    || die "frontend package.json and pnpm-lock.yaml are required"
if [[ $EUID -ne 0 ]]; then
    command -v sudo >/dev/null 2>&1 || die "run the installer as root or install sudo"
    sudo -v
fi
prepare_system_packages
prepare_uv
prepare_node
if ! command -v pnpm >/dev/null 2>&1 \
    || [[ "$(pnpm --version)" != 9.15.9 ]]; then
    info "Installing pnpm 9.15.9 into /usr/local"
    as_root env PATH="$PATH" npm install --global --prefix /usr/local pnpm@9.15.9
fi
hash -r

# Earlier macOS archives may have left binary AppleDouble files such as
# alembic/versions/._0001_initial.py. Alembic treats them as Python migrations.
# Excluding metadata from new archives does not remove those existing files.
for source_dir in \
    "$project_root/backend/app" \
    "$project_root/backend/alembic" \
    "$project_root/frontend/src" \
    "$project_root/frontend/public"; do
    if [[ -d "$source_dir" ]]; then
        info "Removing leftover macOS metadata from ${source_dir}"
        as_root find "$source_dir" -type f \
            \( -name '._*' -o -name '.DS_Store' \) -print -delete
    fi
done

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
    "$log_root/profiles" \
    "$log_root/api"

as_root chown -R "$service_user:$service_group" "$project_root/backend" "$project_root/frontend"
info "Preparing Python 3.12 and backend dependencies for ${service_user}"
as_service uv sync --project "$project_root/backend" --python 3.12 --no-dev --locked
info "Installing frontend dependencies and building frontend"
as_service pnpm --dir "$project_root/frontend" install --frozen-lockfile
as_service pnpm --dir "$project_root/frontend" run build

if [[ -f "$project_root/backend/alembic.ini" ]]; then
    if [[ -x "$project_root/backend/.venv/bin/alembic" ]]; then
        (cd "$project_root/backend" && as_service .venv/bin/alembic upgrade head)
    else
        as_service uv run --project "$project_root/backend" --no-sync \
            alembic -c "$project_root/backend/alembic.ini" upgrade head
    fi
elif [[ -f "$project_root/alembic.ini" ]]; then
    as_service uv run --project "$project_root/backend" --no-sync \
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
service_uid=$(id -u "$service_user")
uv_replacement=$(escape_sed_replacement "$uv_bin")
data_replacement=$(escape_sed_replacement "$data_root")
log_replacement=$(escape_sed_replacement "$log_root")

service_tmp=$(mktemp)
trap 'rm -f -- "$service_tmp"' EXIT
sed \
    -e "s|__PROJECT_PATH__|${project_replacement}|g" \
    -e "s|__SERVICE_USER__|${user_replacement}|g" \
    -e "s|__SERVICE_GROUP__|${group_replacement}|g" \
    -e "s|__SERVICE_UID__|${service_uid}|g" \
    -e "s|__UV_BIN__|${uv_replacement}|g" \
    -e "s|__DATA_ROOT__|${data_replacement}|g" \
    -e "s|__LOG_ROOT__|${log_replacement}|g" \
    "$project_root/deploy/systemd/llm-studio.service.template" > "$service_tmp"

as_root install -m 0644 -o root -g root "$service_tmp" \
    /etc/systemd/system/llm-studio.service
as_root install -m 0640 -o "$service_user" -g "$service_group" \
    "$runtime_file" /etc/llm-studio/llm-studio.env
# The user manager owns transient model units independently of the backend.
# Linger starts only the manager after boot; no model unit is enabled.
as_root loginctl enable-linger "$service_user"
as_root systemctl start "user@${service_uid}.service"
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
