#!/usr/bin/env bash
set -euo pipefail

script_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
root=$(cd "$script_dir/.." && pwd)
verify_tmp=$(mktemp -d "${TMPDIR:-/tmp}/neural-deck-verify.XXXXXX")
trap 'rm -rf -- "$verify_tmp"' EXIT

run_step() {
    printf '\n== %s ==\n' "$1"
    shift
    "$@"
}

command -v uv >/dev/null 2>&1 \
    || { printf 'error: uv is required for backend verification\n' >&2; exit 1; }
command -v pnpm >/dev/null 2>&1 \
    || { printf 'error: pnpm is required for frontend verification\n' >&2; exit 1; }

# Keep verification offline and avoid a machine-level cache. ``--no-sync``
# means this check only uses dependencies already installed in the checkout.
export UV_CACHE_DIR="$verify_tmp/uv-cache"

run_step "backend pytest" bash -c \
    "cd \"$root/backend\" && uv run --project . --offline --no-sync pytest"
run_step "backend ruff format" bash -c \
    "cd \"$root/backend\" && uv run --project . --offline --no-sync ruff format --check app tests"
run_step "backend ruff check" bash -c \
    "cd \"$root/backend\" && uv run --project . --offline --no-sync ruff check app tests"
run_step "backend mypy" bash -c \
    "cd \"$root/backend\" && uv run --project . --offline --no-sync mypy"

run_step "frontend tests" pnpm --dir "$root/frontend" test
run_step "frontend lint" pnpm --dir "$root/frontend" run lint
run_step "frontend typecheck" pnpm --dir "$root/frontend" run typecheck
run_step "frontend build" pnpm --dir "$root/frontend" run build

run_step "shell syntax" bash -c '
    set -euo pipefail
    while IFS= read -r -d "" path; do
        bash -n "$path"
    done < <(find "$1"/scripts "$1"/deploy "$1"/docker "$1"/verify \
        -type f -name "*.sh" -print0 | sort -z)
' bash "$root"

if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
    run_step "compose config" docker compose -f "$root/docker-compose.yml" config --quiet
elif command -v docker-compose >/dev/null 2>&1; then
    run_step "compose config" docker-compose -f "$root/docker-compose.yml" config --quiet
else
    printf 'error: Docker Compose v2 or docker-compose is required for config verification\n' >&2
    exit 1
fi

run_step "runtime shell harness" bash "$root/verify/test-runtime.sh"
run_step "uninstall shell harness" bash "$root/verify/test-uninstall.sh"
printf '\nverification completed\n'
