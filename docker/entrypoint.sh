#!/usr/bin/env bash
set -euo pipefail

workspace=${LLM_STUDIO_WORKSPACE:-/workspace}
backend_dir=${LLM_STUDIO_BACKEND_DIR:-"$workspace/backend"}
frontend_dir=${LLM_STUDIO_FRONTEND_DIR:-"$workspace/frontend"}
backend_port=${LLM_STUDIO_BACKEND_PORT:-8000}
frontend_port=${LLM_STUDIO_FRONTEND_PORT:-5173}
backend_module=${LLM_STUDIO_APP_MODULE:-app.main:app}

die_runtime() {
    printf 'LLM Studio runtime configuration error: %s\n' "$*" >&2
    exit 1
}

require_runtime_path() {
    local key=$1
    local value=${!key-}
    [[ -n "$value" ]] || die_runtime "$key is required"
    [[ "$value" == /* && "$value" != "/" ]] \
        || die_runtime "$key must be an absolute non-root path (got: $value)"
    [[ "$value" != *:* && "$value" != *$'\n'* && "$value" != *$'\r'* ]] \
        || die_runtime "$key contains an unsupported delimiter or newline"
    [[ -d "$value" ]] \
        || die_runtime "$key does not point to an existing directory: $value"
}

mountpoint_exists() {
    local candidate=$1
    local mountpoint
    while IFS= read -r mountpoint; do
        mountpoint=${mountpoint//\\040/ }
        mountpoint=${mountpoint//\\011/$'\t'}
        mountpoint=${mountpoint//\\012/$'\n'}
        mountpoint=${mountpoint//\\134/\\}
        if [[ "$candidate" == "$mountpoint" ]]; then
            return 0
        fi
    done < <(awk '{print $5}' /proc/self/mountinfo 2>/dev/null)
    return 1
}

validate_persistent_paths() {
    local roots=${LLM_STUDIO_PERSISTENT_ROOTS-}
    [[ -n "$roots" ]] || die_runtime 'LLM_STUDIO_PERSISTENT_ROOTS is required by the Compose runtime'

    local -a persistent_roots=()
    local root_path resolved_root path resolved_path
    IFS=: read -r -a persistent_roots <<< "$roots"
    (( ${#persistent_roots[@]} > 0 )) || die_runtime 'LLM_STUDIO_PERSISTENT_ROOTS is empty'

    for root_path in "${persistent_roots[@]}"; do
        [[ "$root_path" == /* && "$root_path" != "/" ]] \
            || die_runtime "unsupported persistent mount target: $root_path"
        [[ -d "$root_path" ]] \
            || die_runtime "persistent mount target is missing: $root_path"
        resolved_root=$(readlink -f -- "$root_path") \
            || die_runtime "cannot resolve persistent mount target: $root_path"
        mountpoint_exists "$resolved_root" \
            || die_runtime "persistent mount target is not mounted: $root_path"
    done

    for path in LLM_STUDIO_DATA_ROOT MODELS_ROOT LOG_ROOT; do
        require_runtime_path "$path"
        resolved_path=$(readlink -f -- "${!path}") \
            || die_runtime "cannot resolve $path: ${!path}"
        local covered=0
        for root_path in "${persistent_roots[@]}"; do
            resolved_root=$(readlink -f -- "$root_path") \
                || die_runtime "cannot resolve persistent mount target: $root_path"
            if [[ "$resolved_path" == "$resolved_root" || "$resolved_path" == "$resolved_root"/* ]]; then
                covered=1
                break
            fi
        done
        (( covered == 1 )) \
            || die_runtime "$path resolves outside persistent mounts: ${!path}"
    done
}

validate_persistent_paths

cd "$workspace"

if [[ ! -d "$backend_dir" ]]; then
    printf 'LLM Studio backend directory is missing: %s\n' "$backend_dir" >&2
    exit 1
fi
if [[ ! -d "$frontend_dir" ]]; then
    printf 'LLM Studio frontend directory is missing: %s\n' "$frontend_dir" >&2
    exit 1
fi

start_backend() {
    if [[ -n "${LLM_STUDIO_BACKEND_DEV_COMMAND:-}" ]]; then
        bash -lc "$LLM_STUDIO_BACKEND_DEV_COMMAND"
        return
    fi

    if [[ -f "$backend_dir/pyproject.toml" ]]; then
        # The source is bind-mounted, so the venv and the dependency cache are
        # created in the container/workspace without any host installation.
        uv sync --project "$backend_dir" --locked
        exec uv run --project "$backend_dir" --no-sync \
            uvicorn "$backend_module" \
            --host 0.0.0.0 \
            --port "$backend_port" \
            --reload \
            --reload-dir "$backend_dir"
    fi

    printf 'Backend command is not configured and %s is missing. Set LLM_STUDIO_BACKEND_DEV_COMMAND or add pyproject.toml.\n' "$backend_dir/pyproject.toml" >&2
    return 1
}

start_frontend() {
    if [[ -n "${LLM_STUDIO_FRONTEND_DEV_COMMAND:-}" ]]; then
        bash -lc "$LLM_STUDIO_FRONTEND_DEV_COMMAND"
        return
    fi

    if [[ ! -f "$frontend_dir/package.json" ]]; then
        printf 'Frontend package manifest is missing: %s\n' "$frontend_dir/package.json" >&2
        return 1
    fi

    command -v pnpm >/dev/null 2>&1 || {
        printf 'pnpm is required to install the frontend dependencies from pnpm-lock.yaml.\n' >&2
        return 1
    }
    # Run the lockfile check on every start so a stale or partially populated
    # bind-mounted node_modules directory cannot mask missing dependencies.
    (cd "$frontend_dir" && pnpm install --frozen-lockfile)
    exec pnpm --dir "$frontend_dir" run dev --host 0.0.0.0 --port "$frontend_port"
}

children=()
cleanup() {
    local child
    for child in "${children[@]:-}"; do
        kill -- "$child" 2>/dev/null || true
    done
    wait 2>/dev/null || true
}
trap cleanup EXIT INT TERM

start_backend &
children+=("$!")
start_frontend &
children+=("$!")

# nginx is the single externally exposed process and joins the same lifecycle
# as the two watch processes above.
nginx -g 'daemon off;' &
children+=("$!")

wait -n "${children[@]}"
exit 1
