#!/usr/bin/env bash
set -euo pipefail

script_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
root=$(cd "$script_dir/.." && pwd)
sandbox=$(mktemp -d "${TMPDIR:-/tmp}/neural-deck-runtime-test.XXXXXX")
trap 'rm -rf -- "$sandbox"' EXIT

test_root="$sandbox/checkout"
bin_dir="$sandbox/bin"
event_log="$sandbox/events.log"
archive="$sandbox/source.tar"
mkdir -p "$test_root/scripts" "$test_root/deploy/systemd" "$test_root/docker" \
    "$test_root/backend" "$test_root/frontend" "$bin_dir" "$sandbox/fake-rootfs"
: > "$event_log"

# Run the real runtime scripts from an isolated miniature checkout. The fake
# Git index below lets this test include a tracked filename with whitespace and
# a force-added runtime path without staging or mutating the developer's repo.
cp "$root/scripts/common.sh" "$root/scripts/deploy.sh" "$root/scripts/status.sh" \
    "$test_root/scripts/"
cp "$root/deploy/install.sh" "$root/deploy/run-service.sh" \
    "$test_root/deploy/"
# Keep command stubs first in the isolated installer. Production normalizes
# PATH, which would otherwise invoke real sudo/package managers in this harness.
sed '/^export PATH=/d' "$test_root/deploy/install.sh" > "$test_root/deploy/install.sandbox.sh"
mv "$test_root/deploy/install.sandbox.sh" "$test_root/deploy/install.sh"
cp "$root/deploy/systemd/neural-deck.service.template" "$test_root/deploy/systemd/"
mkdir -p "$test_root/frontend/public" "$test_root/docs/design"
cp "$root/frontend/public/neural-deck.svg" "$test_root/frontend/public/"
cp "$root/docs/design/neural-deck-reference.html" "$test_root/docs/design/"
cp "$root/docker/entrypoint.sh" "$test_root/docker/"
printf 'tracked\n' > "$test_root/AGENTS.md"
printf 'tracked with spaces\n' > "$test_root/tracked file.txt"
printf 'tracked local contract\n' > "$test_root/.env.local"
mkdir -p "$test_root/data"
printf 'must be excluded\n' > "$test_root/data/forced.dat"
mkdir -p "$test_root/frontend/src"
printf 'new source\n' > "$test_root/frontend/src/new-untracked.ts"
printf '%s\n' \
    '[project]' \
    'name = "runtime-harness-backend"' \
    'version = "0.0.0"' \
    'requires-python = ">=3.12"' > "$test_root/backend/pyproject.toml"
printf '[tool]\n' > "$test_root/backend/alembic.ini"
: > "$test_root/backend/uv.lock"
printf '{"name":"runtime-harness-frontend","private":true}\n' > "$test_root/frontend/package.json"
: > "$test_root/frontend/pnpm-lock.yaml"
runtime_data_root="$sandbox/persistent-data"
printf '%s\n' \
    "NEURAL_DECK_DATA_ROOT=$runtime_data_root" \
    'HOST=127.0.0.1' \
    'PORT=3000' \
    'MODELS_ROOT=' \
    'LOG_ROOT=' > "$test_root/.env"
printf 'DEPLOY_TARGET=\nDEPLOY_PATH=/opt/neural-deck\n' > "$test_root/deploy.env"
printf 'untracked secret\n' > "$test_root/untracked-secret.txt"

write_stub() {
    local path=$1
    shift
    printf '%s\n' "$@" > "$path"
    chmod 0755 "$path"
}

write_stub "$bin_dir/git" '#!/bin/bash' '
set -euo pipefail
case " $* " in
    *" ls-files -c -z -- "*)
        printf "%s\0" "AGENTS.md" "tracked file.txt" ".env.local" "data/forced.dat" "removed-tracked.txt"
        ;;
    *" ls-files -o --exclude-standard -z -- "*)
        [[ " $* " == *" deploy/systemd/neural-deck.service.template "* ]] || exit 91
        [[ " $* " == *" docs/design/neural-deck-reference.html "* ]] || exit 92
        printf "%s\0" "frontend/src/new-untracked.ts"
        printf "%s\0" "frontend/public/neural-deck.svg" "deploy/systemd/neural-deck.service.template" "docs/design/neural-deck-reference.html"
        ;;
    *" rev-parse --show-toplevel "*)
        printf "%s\n" "$PWD"
        ;;
    *)
        printf "unexpected fake git invocation: %s\n" "$*" >&2
        exit 90
        ;;
esac
'

real_tar=$(command -v tar)
export REAL_TAR="$real_tar"
write_stub "$bin_dir/tar" '#!/bin/bash
set -euo pipefail
printf "tar %s\n" "$*" >> "$FAKE_EVENT_LOG"
exec "$REAL_TAR" "$@"
'

write_stub "$bin_dir/bash" '#!/bin/bash' '
set -euo pipefail
exec /bin/bash "$@"
'

write_stub "$bin_dir/uname" '#!/bin/bash' '
set -euo pipefail
if [[ "${1:-}" == "-s" ]]; then
    printf "Linux\n"
else
    /usr/bin/uname "$@"
fi
'

write_stub "$bin_dir/sudo" '#!/bin/bash' '
set -euo pipefail
printf "sudo %s\n" "$*" >> "$FAKE_EVENT_LOG"
[[ "${1:-}" == -v ]] && exit 0
exec "$@"
'

write_stub "$bin_dir/install" '#!/bin/bash' '
set -euo pipefail
printf "install %s\n" "$*" >> "$FAKE_EVENT_LOG"
mode=copy
paths=()
while (($#)); do
    case "$1" in
        -d) mode=directory; shift ;;
        -m|-o|-g) shift 2 ;;
        --) shift; paths+=("$@"); break ;;
        -*) shift ;;
        *) paths+=("$1"); shift ;;
    esac
done
rewrite_path() {
    case "$1" in
        /etc/*|/usr/local/*) printf "%s%s" "$FAKE_ROOTFS" "$1" ;;
        *) printf "%s" "$1" ;;
    esac
}
if [[ "$mode" == directory ]]; then
    for path in "${paths[@]}"; do
        mkdir -p "$(rewrite_path "$path")"
    done
else
    (( ${#paths[@]} >= 2 ))
    source_path=${paths[0]}
    destination=$(rewrite_path "${paths[1]}")
    mkdir -p "$(dirname "$destination")"
    cp "$source_path" "$destination"
fi
'

write_stub "$bin_dir/ssh" '#!/bin/bash' '
set -euo pipefail
count_file="$FAKE_SSH_COUNT_FILE"
count=0
[[ -f "$count_file" ]] && count=$(<"$count_file")
count=$((count + 1))
printf "%s" "$count" > "$count_file"
printf "ssh[%s] %s\n" "$count" "$*" >> "$FAKE_EVENT_LOG"
if [[ "${FAKE_SSH_FAIL_ON:-}" == "$count" ]]; then
    exit 42
fi
if (( count == 1 )); then
    cat > "$FAKE_SSH_ARCHIVE"
fi
'

write_stub "$bin_dir/journalctl" '#!/bin/bash' 'exit 0'

write_stub "$bin_dir/loginctl" '#!/bin/bash' '
set -euo pipefail
printf "loginctl %s\n" "$*" >> "$FAKE_EVENT_LOG"
'

write_stub "$bin_dir/systemctl" '#!/bin/bash' '
set -euo pipefail
printf "systemctl %s\n" "$*" >> "$FAKE_EVENT_LOG"
if [[ "${1:-}" == is-active ]]; then
    [[ "${FAKE_SYSTEMCTL_ACTIVE:-1}" == 1 ]]
fi
if [[ "${1:-}" == status ]]; then
printf "fake systemd status\n"
fi
'

write_stub "$bin_dir/uv" '#!/bin/bash' '
set -euo pipefail
printf "uv %s\n" "$*" >> "$FAKE_EVENT_LOG"
'

write_stub "$bin_dir/pnpm" '#!/bin/bash' '
set -euo pipefail
printf "pnpm %s\n" "$*" >> "$FAKE_EVENT_LOG"
[[ "${1:-}" != --version ]] || printf "9.15.9\n"
'

write_stub "$bin_dir/curl" '#!/bin/bash' '
set -euo pipefail
printf "curl %s\n" "$*" >> "$FAKE_EVENT_LOG"
if [[ "${FAKE_CURL_OK:-1}" != 1 ]]; then
    exit 7
fi
printf "{\"status\":\"ok\"}\n"
'

write_stub "$bin_dir/apt-get" '#!/bin/bash' 'exit 99'
write_stub "$bin_dir/dpkg-query" '#!/bin/bash' 'printf "install ok installed"'
write_stub "$bin_dir/node" '#!/bin/bash' 'exit 0'
write_stub "$bin_dir/npm" '#!/bin/bash' 'exit 0'
write_stub "$bin_dir/chown" '#!/bin/bash' 'exit 0'

write_stub "$bin_dir/sleep" '#!/bin/bash' '
set -euo pipefail
printf "sleep %s\n" "$*" >> "$FAKE_EVENT_LOG"
'

export PATH="$bin_dir:$PATH"
export FAKE_EVENT_LOG="$event_log"
export FAKE_SSH_COUNT_FILE="$sandbox/ssh-count"
export FAKE_SSH_ARCHIVE="$archive"
export REAL_TAR="$real_tar"
export FAKE_ROOTFS="$sandbox/fake-rootfs"
export NEURAL_DECK_SERVICE_USER="$(id -un)"

if grep -Eq 'uv[[:space:]]+sync|pnpm([^[:alnum:]]|.*[[:space:]])install|pnpm.*run[[:space:]]+build' \
    "$test_root/docker/entrypoint.sh"; then
    printf 'container startup must not install dependencies or perform the initial frontend build\n' >&2
    exit 1
fi

grep -Fq 'ARG VLLM_VERSION=0.11.2' "$root/docker/Dockerfile" \
    || { printf 'Docker image does not pin vLLM 0.11.2\n' >&2; exit 1; }
grep -Fq 'ARG LLAMA_CPP_VERSION=b5046' "$root/docker/Dockerfile" \
    || { printf 'Docker image does not pin llama.cpp b5046\n' >&2; exit 1; }
grep -Fq '/opt/venvs/vllm' "$root/docker/Dockerfile" \
    || { printf 'Docker image does not install a discoverable vLLM environment\n' >&2; exit 1; }
grep -Fq '/usr/local/bin/llama-server' "$root/docker/Dockerfile" \
    || { printf 'Docker image does not expose llama-server\n' >&2; exit 1; }

# The container entrypoint must reject an unmounted persistence root before it
# can launch uv, pnpm, or the application. The temporary host directory deliberately is
# not a mount target, so this exercises the loud failure path without Docker.
mkdir -p "$runtime_data_root/models" "$runtime_data_root/logs"
set +e
NEURAL_DECK_WORKSPACE="$test_root" \
NEURAL_DECK_PERSISTENT_ROOTS="$runtime_data_root" \
NEURAL_DECK_DATA_ROOT="$runtime_data_root" \
MODELS_ROOT="$runtime_data_root/models" \
LOG_ROOT="$runtime_data_root/logs" \
HOST=0.0.0.0 \
PORT=3000 \
    /bin/bash "$test_root/docker/entrypoint.sh" >"$sandbox/entrypoint.out" 2>&1
entrypoint_status=$?
set -e
[[ "$entrypoint_status" != 0 ]] \
    || { printf 'entrypoint accepted an unmounted persistence root\n' >&2; exit 1; }
grep -q 'persistent mount target is not mounted' "$sandbox/entrypoint.out" \
    || { printf 'entrypoint did not explain the unmounted persistence root\n' >&2; exit 1; }

files_before=$(find "$test_root" -type f -print | sort)
# Verify the cancellation branch before any installer command is reached.
set +e
printf 'N\n' | (cd "$test_root" && /bin/bash scripts/deploy.sh)
cancel_status=$?
set -e
[[ "$cancel_status" == 0 ]] || { printf 'local N path failed\n' >&2; exit 1; }
files_after=$(find "$test_root" -type f -print | sort)
[[ "$files_before" == "$files_after" ]] || { printf 'local N mutated checkout\n' >&2; exit 1; }
! grep -q '^local-install$' "$event_log" || { printf 'local N invoked installer\n' >&2; exit 1; }

printf 'Y\n' | (cd "$test_root" && /bin/bash scripts/deploy.sh)
grep -q '^uv sync ' "$event_log" || { printf 'local Y did not run backend install\n' >&2; exit 1; }
grep -q '^pnpm --dir .* install ' "$event_log" || { printf 'local Y did not run frontend install\n' >&2; exit 1; }
grep -q '^pnpm --dir .* run build$' "$event_log" || { printf 'local Y did not build frontend\n' >&2; exit 1; }
grep -q '^loginctl enable-linger ' "$event_log" || { printf 'user manager linger missing\n' >&2; exit 1; }
grep -q '^systemctl start user@.*\.service$' "$event_log" || { printf 'user manager was not started\n' >&2; exit 1; }
grep -q '^systemctl daemon-reload$' "$event_log" || { printf 'local Y did not reload systemd\n' >&2; exit 1; }
grep -q '^systemctl restart neural-deck.service$' "$event_log" || { printf 'local Y did not restart service\n' >&2; exit 1; }
grep -q '^KillMode=control-group$' "$FAKE_ROOTFS/etc/systemd/system/neural-deck.service"
grep -q '^Environment=NEURAL_DECK_MODEL_LAUNCHER=systemd$' "$FAKE_ROOTFS/etc/systemd/system/neural-deck.service"
grep -q '^After=.*user@[0-9].*\.service' "$FAKE_ROOTFS/etc/systemd/system/neural-deck.service"
! grep -q '__SERVICE_UID__' "$FAKE_ROOTFS/etc/systemd/system/neural-deck.service"
! grep -q '^systemctl enable neural-deck-model-' "$event_log"
[[ -f "$FAKE_ROOTFS/etc/systemd/system/neural-deck.service" ]] \
    || { printf 'local Y did not install systemd unit\n' >&2; exit 1; }

set +e
export FAKE_CURL_OK=0
(cd "$test_root" && NEURAL_DECK_INSTALL_CONFIRMED=1 bash deploy/install.sh >/dev/null 2>&1)
health_failure_status=$?
set -e
[[ "$health_failure_status" != 0 ]] \
    || { printf 'health failure was swallowed by installer\n' >&2; exit 1; }
grep -q '^systemctl status neural-deck.service' "$event_log" \
    || { printf 'health failure did not report service status\n' >&2; exit 1; }
export FAKE_CURL_OK=1

printf 'DEPLOY_TARGET=fake-host\nDEPLOY_PATH=/opt/neural-deck\n' > "$test_root/deploy.env"
rm -f "$FAKE_SSH_COUNT_FILE" "$FAKE_SSH_ARCHIVE"
(cd "$test_root" && /bin/bash scripts/deploy.sh)
[[ -f "$archive" ]] || { printf 'remote archive was not captured\n' >&2; exit 1; }
archive_listing=$(tar -tf "$archive")
grep -Fxq 'AGENTS.md' <<<"$archive_listing"
grep -Fxq 'tracked file.txt' <<<"$archive_listing"
grep -Fxq '.env.local' <<<"$archive_listing"
grep -Fxq '.env' <<<"$archive_listing"
grep -Fxq 'frontend/src/new-untracked.ts' <<<"$archive_listing"
grep -Fxq 'frontend/public/neural-deck.svg' <<<"$archive_listing"
grep -Fxq 'deploy/systemd/neural-deck.service.template' <<<"$archive_listing"
grep -Fxq 'docs/design/neural-deck-reference.html' <<<"$archive_listing"
! grep -Fxq 'removed-tracked.txt' <<<"$archive_listing"
! grep -Fxq 'data/forced.dat' <<<"$archive_listing"
! grep -Fxq 'untracked-secret.txt' <<<"$archive_listing"
grep -q 'ssh\[1\].*mkdir -p' "$event_log"
grep -q 'ssh\[2\].*deploy/install.sh' "$event_log"

printf 'DEPLOY_TARGET=\nDEPLOY_PATH=/opt/neural-deck\n' > "$test_root/deploy.env"
rm -f "$FAKE_SSH_COUNT_FILE"
export FAKE_SYSTEMCTL_ACTIVE=1 FAKE_CURL_OK=1
(cd "$test_root" && /bin/bash scripts/status.sh --local)
grep -q '^systemctl is-active' "$event_log"
grep -q '^curl ' "$event_log"

printf 'DEPLOY_TARGET=fake-host\nDEPLOY_PATH=/opt/neural-deck\n' > "$test_root/deploy.env"
rm -f "$FAKE_SSH_COUNT_FILE"
(cd "$test_root" && /bin/bash scripts/status.sh)
grep -q 'ssh\[1\].*scripts/status.sh --local' "$event_log"

set +e
export FAKE_SSH_FAIL_ON=1
rm -f "$FAKE_SSH_COUNT_FILE"
(cd "$test_root" && /bin/bash scripts/deploy.sh >/dev/null 2>&1)
failure_status=$?
set -e
[[ "$failure_status" != 0 ]] || { printf 'remote failure was swallowed\n' >&2; exit 1; }

printf 'runtime shell tests passed\n'
