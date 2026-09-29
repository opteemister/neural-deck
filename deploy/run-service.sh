#!/usr/bin/env bash
set -euo pipefail

: "${LLM_STUDIO_PROJECT_PATH:?LLM_STUDIO_PROJECT_PATH is required}"
: "${HOST:?HOST is required in /etc/llm-studio/llm-studio.env}"
: "${PORT:?PORT is required in /etc/llm-studio/llm-studio.env}"

backend_dir=${LLM_STUDIO_BACKEND_DIR:-"$LLM_STUDIO_PROJECT_PATH/backend"}
app_module=${LLM_STUDIO_APP_MODULE:-app.main:app}
uv_bin=${LLM_STUDIO_UV_BIN:-uv}

[[ -d "$backend_dir" ]] || {
    printf 'Backend directory is missing: %s\n' "$backend_dir" >&2
    exit 1
}

cd "$backend_dir"

if [[ -x "$backend_dir/.venv/bin/uvicorn" ]]; then
    exec "$backend_dir/.venv/bin/uvicorn" "$app_module" \
        --host "$HOST" \
        --port "$PORT"
fi

exec "$uv_bin" run --project "$backend_dir" --no-sync \
    uvicorn "$app_module" \
    --host "$HOST" \
    --port "$PORT"
