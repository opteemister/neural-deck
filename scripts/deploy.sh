#!/usr/bin/env bash
set -euo pipefail

script_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=common.sh
source "$script_dir/common.sh"

root=$(repo_root)
deploy_file=${DEPLOY_ENV_FILE:-"$root/deploy.env"}
runtime_file=${LLM_STUDIO_ENV_FILE:-"$root/.env"}
load_deploy_config "$deploy_file"

if [[ -z "$DEPLOY_TARGET" ]]; then
    info "This will install and start systemd service llm-studio on the current machine."
    info "Exact target: ${root} (native Linux, persistent data from .env)"
    printf 'Continue with this local installation? [y/N] '
    answer=''
    IFS= read -r answer || answer=''
    case "$answer" in
        y|Y) ;;
        *) info "Local installation cancelled."; exit 0 ;;
    esac
    [[ -f "$runtime_file" ]] \
        || die "runtime config is missing: ${runtime_file}; copy .env.example to .env"
    LLM_STUDIO_INSTALL_CONFIRMED=1 LLM_STUDIO_ENV_FILE="$runtime_file" \
        exec bash "$root/deploy/install.sh"
fi

[[ -n "$DEPLOY_PATH" ]] || die "DEPLOY_PATH is required when DEPLOY_TARGET is set"
validate_deploy_path "$DEPLOY_PATH"
[[ "$runtime_file" == "$root/.env" ]] \
    || die "remote deployment requires the populated root .env: ${root}/.env"
[[ -f "$runtime_file" ]] \
    || die "runtime config is missing: ${runtime_file}; copy .env.example to .env"

info "Deploy target: ${DEPLOY_TARGET}:${DEPLOY_PATH}"
info "The build, dependency installation, migration, and service restart will run on the target."

archive_dir=$(mktemp -d "${TMPDIR:-/tmp}/llm-studio-deploy.XXXXXX")
archive_tar="$archive_dir/source.tar"
manifest="$archive_dir/manifest"
cleanup() {
    rm -rf -- "$archive_dir"
}
trap cleanup EXIT

git -C "$root" rev-parse --show-toplevel >/dev/null 2>&1 \
    || die "deployment must run from a Git checkout"

# The index is the default deployment source. Runtime source directories also
# include non-ignored untracked files so a newly created module cannot be
# omitted silently before its first commit. Do not use a whole-checkout walk:
# arbitrary untracked files must never enter a release archive. The populated
# root .env is appended explicitly below as the one intentional runtime-file
# exception.
runtime_source_roots=(
    backend/app
    backend/alembic
    frontend/src
    frontend/public
)
: > "$manifest"
append_manifest() {
    local exclude_environment_files=${1:-0}
    while IFS= read -r -d '' item_path; do
        case "$item_path" in
            .DS_Store|*/.DS_Store|._*|*/._*|__MACOSX|__MACOSX/*|*/__MACOSX|*/__MACOSX/*|\
            .Spotlight-V100|.Spotlight-V100/*|.Trashes|.Trashes/*|.fseventsd|.fseventsd/*)
                continue
                ;;
            .env|*/.env|deploy.env|*/deploy.env|.git|.git/*|\
            data|data/*|models|models/*|logs|logs/*|\
            backend/.venv|backend/.venv/*|backend/__pycache__|backend/__pycache__/*|\
            backend/.pytest_cache|backend/.pytest_cache/*|backend/.mypy_cache|backend/.mypy_cache/*|\
            backend/.ruff_cache|backend/.ruff_cache/*|\
            frontend/node_modules|frontend/node_modules/*|frontend/dist|frontend/dist/*|\
            frontend/build|frontend/build/*|frontend/.vite|frontend/.vite/*|\
            node_modules|node_modules/*|dist|dist/*|build|build/*|coverage|coverage/*|\
            .vite|.vite/*|.pytest_cache|.pytest_cache/*|.mypy_cache|.mypy_cache/*|\
            .ruff_cache|.ruff_cache/*|__pycache__|__pycache__/*|*.egg-info|*.egg-info/*)
                continue
                ;;
        esac
        if [[ "$exclude_environment_files" == 1 && ( "$item_path" == .env.* || "$item_path" == */.env.* ) ]]; then
            continue
        fi
        printf '%s\0' "$item_path" >> "$manifest"
    done
}
git -C "$root" ls-files -c -z -- | append_manifest
git -C "$root" ls-files -o --exclude-standard -z -- "${runtime_source_roots[@]}" | append_manifest 1

# The archive is written in a private temporary directory and is never sent
# until both archive steps succeed. ``--null`` keeps tracked paths containing
# spaces, tabs, or newlines unambiguous on GNU and BSD tar.
tar_command=(tar)
if [[ "$(uname -s)" == Darwin ]]; then
    # macOS bsdtar otherwise stores xattrs in LIBARCHIVE headers and creates
    # AppleDouble entries even for an explicit list of tracked source files.
    tar_command+=(--no-xattrs --no-acls --no-fflags --no-mac-metadata)
fi
if ! COPYFILE_DISABLE=1 "${tar_command[@]}" -C "$root" \
    --null --files-from="$manifest" \
    -cf "$archive_tar"; then
    die "could not create deployment archive"
fi

# .env is intentionally copied separately from the tracked transfer set. It is
# checked above and is never accepted from an arbitrary path.
if ! COPYFILE_DISABLE=1 "${tar_command[@]}" -C "$root" --append -f "$archive_tar" .env; then
    die "could not append the populated root .env to deployment archive"
fi

remote_path=$(shell_quote "$DEPLOY_PATH")
remote_command="mkdir -p ${remote_path} && tar -xpf - -C ${remote_path}"
if ! ssh -- "$DEPLOY_TARGET" "$remote_command" < "$archive_tar"; then
    die "could not copy checkout to ${DEPLOY_TARGET}:${DEPLOY_PATH}"
fi

# The target extracts the stream directly; the local archive is no longer
# needed while dependencies are installed and the service is restarted.
cleanup

install_command="cd ${remote_path} && LLM_STUDIO_INSTALL_CONFIRMED=1 bash deploy/install.sh"
if ! ssh -- "$DEPLOY_TARGET" "$install_command"; then
    die "remote installation failed on ${DEPLOY_TARGET}"
fi

info "Deployment completed on ${DEPLOY_TARGET}:${DEPLOY_PATH}"
