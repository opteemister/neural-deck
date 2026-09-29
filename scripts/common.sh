#!/usr/bin/env bash
set -euo pipefail

die() {
    printf 'error: %s\n' "$*" >&2
    exit 1
}

info() {
    printf '%s\n' "$*"
}

warn() {
    printf 'warning: %s\n' "$*" >&2
}

repo_root() {
    cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd
}

# Read a simple dotenv assignment without executing it. Runtime .env files are
# copied to systemd and may contain comments, quoted values, and blank lines.
dotenv_value() {
    local file=$1
    local wanted_key=$2
    local line key value

    [[ -f "$file" ]] || return 1
    while IFS= read -r line || [[ -n "$line" ]]; do
        line="${line#"${line%%[![:space:]]*}"}"
        [[ -z "$line" || "${line:0:1}" == '#' ]] && continue
        [[ "$line" =~ ^[A-Za-z_][A-Za-z0-9_]*=.*$ ]] || continue
        key=${line%%=*}
        [[ "$key" == "$wanted_key" ]] || continue
        value=${line#*=}
        value="${value%"${value##*[![:space:]]}"}"
        if (( ${#value} >= 2 )); then
            if [[ "${value:0:1}" == '"' && "${value: -1}" == '"' ]] \
                || [[ "${value:0:1}" == "'" && "${value: -1}" == "'" ]]; then
                value=${value:1:${#value}-2}
            fi
        fi
        printf '%s' "$value"
        return 0
    done < "$file"
    return 1
}

require_dotenv_value() {
    local file=$1
    local key=$2
    local value
    value=$(dotenv_value "$file" "$key") || die "${key} is required in ${file}"
    [[ -n "$value" ]] || die "${key} is empty in ${file}"
    printf '%s' "$value"
}

load_deploy_config() {
    local file=$1
    DEPLOY_TARGET=''
    DEPLOY_PATH=''

    [[ -f "$file" ]] || return 0
    local line key value
    while IFS= read -r line || [[ -n "$line" ]]; do
        line="${line#"${line%%[![:space:]]*}"}"
        [[ -z "$line" || "${line:0:1}" == '#' ]] && continue
        [[ "$line" =~ ^[A-Za-z_][A-Za-z0-9_]*=.*$ ]] \
            || die "invalid line in ${file}; expected DEPLOY_TARGET= or DEPLOY_PATH="
        key=${line%%=*}
        value=${line#*=}
        value="${value%"${value##*[![:space:]]}"}"
        if (( ${#value} >= 2 )); then
            if [[ "${value:0:1}" == '"' && "${value: -1}" == '"' ]] \
                || [[ "${value:0:1}" == "'" && "${value: -1}" == "'" ]]; then
                value=${value:1:${#value}-2}
            fi
        fi
        case "$key" in
            DEPLOY_TARGET) DEPLOY_TARGET=$value ;;
            DEPLOY_PATH) DEPLOY_PATH=$value ;;
            *) die "${file} may contain only DEPLOY_TARGET and DEPLOY_PATH (got ${key})" ;;
        esac
    done < "$file"
}

validate_deploy_path() {
    local path=$1
    [[ "$path" == /* && "$path" != "/" ]] \
        || die "DEPLOY_PATH must be an absolute non-root path"
    [[ "$path" != *$'\n'* && "$path" != *$'\r'* ]] \
        || die "DEPLOY_PATH must not contain newlines"
}

shell_quote() {
    # Use single-quoted shell words so the command also works with a target's
    # default /bin/sh. Embedded quotes are closed, escaped, and reopened.
    local value=$1
    value=${value//\'/\'\\\'\'}
    printf "'%s'" "$value"
}

health_host_from_env() {
    local host=$1
    case "$host" in
        0.0.0.0|::|\[::\]) printf '127.0.0.1' ;;
        \[*\]) printf '%s' "$host" ;;
        *:*) printf '[%s]' "$host" ;;
        *) printf '%s' "$host" ;;
    esac
}

check_http_health() {
    local host=$1
    local port=$2
    local url_host
    local endpoint
    local response
    url_host=$(health_host_from_env "$host")

    command -v curl >/dev/null 2>&1 \
        || die "curl is required to verify application health"

    for endpoint in /health /api/v1/status; do
        if response=$(curl --fail --silent --show-error --max-time 5 \
            "http://${url_host}:${port}${endpoint}" 2>&1); then
            info "health: OK (${endpoint})"
            printf '%s\n' "$response"
            return 0
        fi
        warn "health check failed for ${endpoint}: ${response}"
    done
    return 1
}
