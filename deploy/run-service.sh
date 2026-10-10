#!/usr/bin/env bash
set -euo pipefail

: "${NEURAL_DECK_PROJECT_PATH:?NEURAL_DECK_PROJECT_PATH is required}"
: "${HOST:?HOST is required in /etc/neural-deck/neural-deck.env}"
: "${PORT:?PORT is required in /etc/neural-deck/neural-deck.env}"

backend_dir=${NEURAL_DECK_BACKEND_DIR:-"$NEURAL_DECK_PROJECT_PATH/backend"}
app_module=${NEURAL_DECK_APP_MODULE:-app.main:app}
uv_bin=${NEURAL_DECK_UV_BIN:-uv}

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
