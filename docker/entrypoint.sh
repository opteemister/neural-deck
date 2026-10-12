#!/usr/bin/env bash
set -euo pipefail

workspace=${NEURAL_DECK_WORKSPACE:-/workspace}
backend_dir=${NEURAL_DECK_BACKEND_DIR:-"$workspace/backend"}
frontend_dir=${NEURAL_DECK_FRONTEND_DIR:-"$workspace/frontend"}
backend_module=${NEURAL_DECK_APP_MODULE:-app.main:app}

: "${HOST:?HOST is required}"
: "${PORT:?PORT is required}"

die_runtime() {
    printf 'Neural Deck runtime configuration error: %s\n' "$*" >&2
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
    local roots=${NEURAL_DECK_PERSISTENT_ROOTS-}
    [[ -n "$roots" ]] || die_runtime 'NEURAL_DECK_PERSISTENT_ROOTS is required by the Compose runtime'

    local -a persistent_roots=()
    local root_path resolved_root path resolved_path
    IFS=: read -r -a persistent_roots <<< "$roots"
    (( ${#persistent_roots[@]} > 0 )) || die_runtime 'NEURAL_DECK_PERSISTENT_ROOTS is empty'

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

    for path in NEURAL_DECK_DATA_ROOT MODELS_ROOT LOG_ROOT; do
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
    printf 'Neural Deck backend directory is missing: %s\n' "$backend_dir" >&2
    exit 1
fi
if [[ ! -d "$frontend_dir" ]]; then
    printf 'Neural Deck frontend directory is missing: %s\n' "$frontend_dir" >&2
    exit 1
fi
if [[ ! -f "$frontend_dir/dist/index.html" ]]; then
    printf 'Neural Deck frontend build is missing: %s. Rebuild the image.\n' "$frontend_dir/dist/index.html" >&2
    exit 1
fi

start_backend() {
    if [[ -n "${NEURAL_DECK_BACKEND_DEV_COMMAND:-}" ]]; then
        bash -lc "$NEURAL_DECK_BACKEND_DEV_COMMAND"
        return
    fi

    if [[ -x "$backend_dir/.venv/bin/uvicorn" ]]; then
        if [[ "${NEURAL_DECK_FRONTEND_WATCH:-0}" == "1" ]]; then
            "$backend_dir/.venv/bin/uvicorn" "$backend_module" \
                --app-dir "$backend_dir" \
                --host "$HOST" \
                --port "$PORT"
        else
            exec "$backend_dir/.venv/bin/uvicorn" "$backend_module" \
                --app-dir "$backend_dir" \
                --host "$HOST" \
                --port "$PORT"
        fi
    fi

    printf 'Backend runtime is missing: %s. Rebuild the image.\n' "$backend_dir/.venv/bin/uvicorn" >&2
    return 1
}

watch_frontend() {
    exec pnpm --dir "$frontend_dir" exec vite build --watch
}

if [[ "${NEURAL_DECK_FRONTEND_WATCH:-0}" == "1" ]]; then
    frontend_watch_pid=""
    backend_pid=""

    watch_frontend &
    frontend_watch_pid=$!

    stop_children() {
        if [[ -n "${backend_pid:-}" ]]; then
            kill -TERM "$backend_pid" 2>/dev/null || true
        fi
        if [[ -n "${frontend_watch_pid:-}" ]]; then
            kill -TERM "$frontend_watch_pid" 2>/dev/null || true
        fi
    }

    forward_signal() {
        stop_children
    }

    cleanup() {
        local status=$?
        trap - EXIT INT TERM
        stop_children
        if [[ -n "${backend_pid:-}" ]]; then
            wait "$backend_pid" 2>/dev/null || true
        fi
        if [[ -n "${frontend_watch_pid:-}" ]]; then
            wait "$frontend_watch_pid" 2>/dev/null || true
        fi
        exit "$status"
    }

    # The shell is PID 1 in the watcher mode. Forward Docker's TERM to both
    # children so uvicorn can close its lifespan instead of being hard-killed
    # after Docker's stop timeout.
    trap forward_signal INT TERM
    trap cleanup EXIT
    start_backend &
    backend_pid=$!
    if wait "$backend_pid"; then
        backend_status=0
    else
        backend_status=$?
    fi
    exit "$backend_status"
else
    start_backend
fi
