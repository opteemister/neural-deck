#!/usr/bin/env bash
# No real SSH/systemd. Deletion targets are private temporary fixtures only.
set -euo pipefail
root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
sandbox=$(mktemp -d "${TMPDIR:-/tmp}/neural-deck-uninstall-test.XXXXXX")
trap 'rm -rf -- "$sandbox"' EXIT
sandbox=$(cd "$sandbox" && pwd -P)
export TEST_SANDBOX="$sandbox" TEST_PROJECT="$sandbox/remote checkout"
export TEST_ETC="$sandbox/etc" TEST_DATA="$TEST_PROJECT/backend/storage"
export TEST_EVENTS="$sandbox/events" TEST_STATE="$sandbox/state"
mkdir -p "$sandbox/bin" "$sandbox/local/scripts" "$sandbox/run/user/1000"
cp "$root/scripts/common.sh" "$sandbox/local/scripts/"
printf 'local-source-secret\n' > "$sandbox/local/.env"
sed -e "s|/etc/systemd/system|$sandbox/etc/systemd/system|g" \
    -e "s|/etc/neural-deck|$sandbox/etc/neural-deck|g" \
    -e "s|/run/user/|$sandbox/run/user/|g" \
    -e 's/\[\[ -S /[[ -e /g' \
    "$root/scripts/uninstall.sh" > "$sandbox/local/scripts/uninstall.sh"
printf 'DEPLOY_TARGET=fake-server\nDEPLOY_PATH="%s"\n' "$TEST_PROJECT" > "$sandbox/local/deploy.env"
stub() { local name=$1; shift; printf '%s\n' '#!/bin/bash' 'set -euo pipefail' "$@" > "$sandbox/bin/$name"; chmod +x "$sandbox/bin/$name"; }
stub uname 'printf "Linux\n"'
stub id 'case "$*" in -u|"-u root") printf "0\n" ;; "-u tester") printf "1000\n" ;; *) exit 9 ;; esac'
stub findmnt '[[ ! -f "$TEST_SANDBOX/mounts" ]] || cat "$TEST_SANDBOX/mounts"; exit 0'
stub runuser 'shift 3; exec "$@"'
stub ssh '
printf "ssh\n" >> "$TEST_EVENTS"
if [[ -f "$TEST_SANDBOX/change-plan" && $(wc -l < "$TEST_EVENTS") -gt 1 ]]; then
    printf "NEURAL_DECK_DATA_ROOT=\"%s\"\nMODELS_ROOT=\"%s/changed-models\"\n" "$TEST_DATA" "$TEST_SANDBOX" > "$TEST_ETC/neural-deck/neural-deck.env"
fi
exec /bin/bash -c "${@: -1}"
'
stub systemctl '
user_mode=0
if [[ ${1:-} == --user ]]; then user_mode=1; shift; fi
command=$1; shift
case "$command" in
    show)
        unit=$1; shift; property=${1#--property=}
        case "$property" in
            LoadState) [[ -f "$TEST_ETC/systemd/system/neural-deck.service" ]] && printf "loaded\n" || printf "not-found\n" ;;
            FragmentPath) printf "%s/systemd/system/neural-deck.service\n" "$TEST_ETC" ;;
            User) printf "tester\n" ;;
            EnvironmentFiles) printf "%s/neural-deck/neural-deck.env (ignore_errors=no)\n" "$TEST_ETC" ;;
            DropInPaths) printf "%s\n" "${TEST_DROPINS:-}" ;;
            WorkingDirectory)
                if [[ "$user_mode" == 0 ]]; then printf "%s\n" "${TEST_WORKING_OVERRIDE:-$TEST_PROJECT}"
                elif [[ "$unit" == neural-deck-model-owned.service ]]; then printf "%s/backend\n" "$TEST_PROJECT"
                else printf "/other/project\n"; fi ;;
            *) exit 21 ;;
        esac ;;
    list-units) printf "neural-deck-model-owned.service loaded active running model\nneural-deck-model-other.service loaded active running model\n" ;;
    stop)
        printf "stop %s\n" "$1" >> "$TEST_EVENTS"
        [[ ! -f "$TEST_SANDBOX/fail-stop" ]] || exit 23
        touch "$TEST_STATE/$1-stopped" ;;
    is-active) [[ ! -f "$TEST_STATE/${2:-$1}-stopped" && -f "$TEST_ETC/systemd/system/neural-deck.service" ]] ;;
    disable|daemon-reload) printf "%s %s\n" "$command" "${1:-}" >> "$TEST_EVENTS" ;;
    *) exit 24 ;;
esac
'
if [[ $(/usr/bin/uname -s) == Darwin ]]; then
    stub realpath 'shift 2; node -e '\''const fs=require("fs"),p=require("path"); let s=p.resolve(process.argv[1]),tail=[];while(!fs.existsSync(s)){tail.unshift(p.basename(s));s=p.dirname(s)}console.log(p.join(fs.realpathSync(s),...tail));'\'' "$1"'
    # BSD rm has no --one-file-system flag. Still use real rm inside the sandbox.
    stub rm 'args=(); for arg in "$@"; do [[ "$arg" == --one-file-system ]] || args+=("$arg"); done; exec /bin/rm "${args[@]}"'
fi
export PATH="$sandbox/bin:$PATH"
launcher="$sandbox/local/scripts/uninstall.sh"
fixture() {
    rm -rf -- "$TEST_PROJECT" "$TEST_ETC" "$TEST_STATE"
    rm -f -- "$sandbox/change-plan" "$sandbox/fail-stop" "$sandbox/mounts"
    mkdir -p "$TEST_PROJECT/backend/app" "$TEST_DATA" "$TEST_PROJECT/frontend/src" \
        "$TEST_PROJECT/.git" "$TEST_ETC/systemd/system" "$TEST_ETC/neural-deck" "$TEST_STATE" "$sandbox/models"
    printf 'code\n' > "$TEST_PROJECT/backend/app/main.py"
    printf 'code\n' > "$TEST_PROJECT/frontend/src/app.tsx"
    printf 'git\n' > "$TEST_PROJECT/.git/HEAD"
    printf 'database\n' > "$TEST_DATA/history.sqlite3"
    printf 'secret-value\n' > "$TEST_PROJECT/.env"
    printf 'unknown\n' > "$TEST_PROJECT/user-file.txt"
    printf 'weights\n' > "$sandbox/models/model.gguf"
    printf 'unit\n' > "$TEST_ETC/systemd/system/neural-deck.service"
    printf 'NEURAL_DECK_DATA_ROOT="%s"\nMODELS_ROOT="%s/models"\nTOKEN=secret-value\n' "$TEST_DATA" "$sandbox" > "$TEST_ETC/neural-deck/neural-deck.env"
    touch "$sandbox/run/user/1000/bus"
    : > "$TEST_EVENTS"
}
exists() { [[ -f "$1" ]] || { printf 'FAIL: missing %s\n' "$1"; exit 1; }; }
absent() { [[ ! -e "$1" ]] || { printf 'FAIL: not removed %s\n' "$1"; exit 1; }; }
preserved() {
    exists "$TEST_DATA/history.sqlite3"; exists "$sandbox/local/.env"
    exists "$sandbox/local/scripts/uninstall.sh"
    exists "$TEST_PROJECT/user-file.txt"; exists "$sandbox/models/model.gguf"
    ! rg -q 'secret-value' "$sandbox/output"
    ! rg -q 'stop neural-deck-model-other|user@|loginctl' "$TEST_EVENTS"
}

fixture
bash "$launcher" --scan > "$sandbox/output"
exists "$TEST_ETC/systemd/system/neural-deck.service"
exists "$TEST_ETC/neural-deck/neural-deck.env"; exists "$TEST_PROJECT/.env"
[[ $(wc -l < "$TEST_EVENTS") == 1 ]]; preserved
printf 'PASS: scan only, no secrets or mutations\n'
for answer in N ''; do
    fixture
    printf '%s' "$answer" | bash "$launcher" > "$sandbox/output"
    [[ $(wc -l < "$TEST_EVENTS") == 1 ]]; exists "$TEST_PROJECT/frontend/src/app.tsx"
    exists "$TEST_ETC/neural-deck/neural-deck.env"; exists "$TEST_PROJECT/.env"
done
printf 'PASS: N/EOF cancel\n'

fixture
printf 'Y\n' | bash "$launcher" > "$sandbox/output"
absent "$TEST_ETC/systemd/system/neural-deck.service"; absent "$TEST_PROJECT/frontend"
absent "$TEST_ETC/neural-deck"; absent "$TEST_PROJECT/.env"
absent "$TEST_PROJECT/.git"; exists "$TEST_PROJECT/backend/app/main.py"
exists "$TEST_STATE/neural-deck-model-owned.service-stopped"; preserved
bash "$launcher" --scan > "$sandbox/output"; preserved
printf 'PASS: cleanup, nested work folder keeps whole backend, repeat scan\n'

TEST_DATA="$sandbox/external-data"; export TEST_DATA
fixture
printf 'Y\n' | bash "$launcher" > "$sandbox/output"
absent "$TEST_PROJECT/backend"; absent "$TEST_PROJECT/frontend"; preserved
TEST_DATA="$TEST_PROJECT/backend/storage"; export TEST_DATA
printf 'PASS: external work folder allows full code cleanup\n'

fixture
printf 'Y\n' | bash "$launcher" --keep-source > "$sandbox/output"
exists "$TEST_PROJECT/frontend/src/app.tsx"; absent "$TEST_ETC/systemd/system/neural-deck.service"; preserved
printf 'PASS: keep-source\n'

fixture
printf 'manual file\n' > "$TEST_ETC/neural-deck/manual.conf"
printf 'Y\n' | bash "$launcher" > "$sandbox/output"
absent "$TEST_ETC/neural-deck/neural-deck.env"; exists "$TEST_ETC/neural-deck/manual.conf"; preserved
printf 'PASS: installed env removed, unrelated config file preserved\n'

fixture
touch "$sandbox/change-plan"
if printf 'Y\n' | bash "$launcher" > "$sandbox/output" 2>&1; then exit 1; fi
! rg -q '^stop ' "$TEST_EVENTS"; exists "$TEST_ETC/systemd/system/neural-deck.service"
printf 'PASS: changed work paths reject plan\n'

fixture
touch "$sandbox/fail-stop"
if printf 'Y\n' | bash "$launcher" > "$sandbox/output" 2>&1; then exit 1; fi
exists "$TEST_ETC/systemd/system/neural-deck.service"; exists "$TEST_PROJECT/frontend/src/app.tsx"; preserved
exists "$TEST_ETC/neural-deck/neural-deck.env"
printf 'PASS: stop failure cancels deletion\n'

fixture
printf '%s/frontend/mounted\n' "$TEST_PROJECT" > "$sandbox/mounts"
printf 'Y\n' | bash "$launcher" > "$sandbox/output"
exists "$TEST_PROJECT/frontend/src/app.tsx"; preserved
printf 'PASS: mounted subtree keeps whole source directory\n'

fixture
if TEST_WORKING_OVERRIDE=/other/project bash "$launcher" --scan > "$sandbox/output" 2>&1; then exit 1; fi
exists "$TEST_ETC/systemd/system/neural-deck.service"
if TEST_DROPINS=custom.conf bash "$launcher" --scan > "$sandbox/output" 2>&1; then exit 1; fi
rm -- "$TEST_ETC/neural-deck/neural-deck.env"
if bash "$launcher" --scan > "$sandbox/output" 2>&1; then exit 1; fi
exists "$TEST_PROJECT/frontend/src/app.tsx"
printf 'PASS: wrong service/missing config refuse deletion\n'
printf 'uninstall harness completed\n'
