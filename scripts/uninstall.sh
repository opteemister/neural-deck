#!/usr/bin/env bash
set -euo pipefail

# This function runs on Linux; common.sh is sent before this script over SSH.
uninstall_remote() {
    local mode=$1 project=$2 keep_source=$3 approved_plan=${4:-}
    local service=neural-deck.service unit=/etc/systemd/system/neural-deck.service
    local config=/etc/neural-deck/neural-deck.env
    local load user uid working path name value mounts plan skip reason
    local protected=() remove=() keep=() models=()
    trap 'printf "\n[✗] Шаг завершился ошибкой. Очистка остановлена.\n" >&2' ERR
    [[ $(uname -s) == Linux && $(id -u) == 0 ]] || die 'target requires Linux/systemd and root or sudo -n'
    validate_deploy_path "$project"
    [[ $(realpath -m -- "$project") == "$project" ]] || die 'DEPLOY_PATH must be a canonical path, not a symlink'
    case "$project" in /opt|/srv|/home|/root|/usr|/usr/local|/etc|/var|/var/lib|/tmp|/mnt|/media)
        die "refusing a shared directory: $project" ;; esac

    property() { systemctl show "$service" --property="$1" --value; }
    load=$(property LoadState)
    user=$(property User); user=${user:-root}
    if [[ "$load" != not-found ]]; then
        [[ "$load" == loaded && $(property FragmentPath) == "$unit" && ! -L "$unit" ]] || die 'unexpected service unit'
        [[ $(property WorkingDirectory) == "$project" ]] || die 'service WorkingDirectory does not match DEPLOY_PATH'
        [[ -z $(property DropInPaths) ]] || die 'custom service drop-ins require manual review'
        [[ $(property EnvironmentFiles) == "$config (ignore_errors=no)" ]] || die 'unexpected service EnvironmentFile'
    fi

    # Only these deployment paths are read. Dotenv is never executed.
    protect() {
        local canonical
        [[ "$1" == /* && "$1" != *$'\n'* && "$1" != *$'\r'* ]] || die 'data paths must be absolute, single-line paths'
        canonical=$(realpath -m -- "$1"); protected+=("$1")
        [[ "$canonical" == "$1" ]] || protected+=("$canonical")
    }
    if [[ -f "$config" ]]; then
        value=$(require_dotenv_value "$config" NEURAL_DECK_DATA_ROOT); protect "$value"
        path=$(dotenv_value "$config" MODELS_ROOT || true); protect "${path:-$value/models}"
        path=$(dotenv_value "$config" LOG_ROOT || true); protect "${path:-$value/logs}"
        value=$(dotenv_value "$config" EXTRA_VENV_ROOTS || true)
        local extras=(); IFS=',' read -r -a extras <<< "$value"
        for path in "${extras[@]:-}"; do
            path=${path#"${path%%[![:space:]]*}"}; path=${path%"${path##*[![:space:]]}"}
            [[ -z "$path" ]] || protect "$path"
        done
    elif [[ "$load" != not-found ]]; then die "missing installed config: $config"; fi
    # Preserve bind mounts too, even if they use the same underlying filesystem.
    mounts=$(findmnt --raw --noheadings --output TARGET)
    while IFS= read -r path; do
        path=$(printf '%b' "$path")
        [[ "$path" != "$project" && "$path" != "$project/"* ]] || protect "$path"
    done <<< "$mounts"

    # A fixed list, not a repository-wide wipe. Unknown root entries stay.
    for name in .git backend frontend docs scripts deploy docker verify README.md AGENTS.md \
        DEVELOPMENT_RULES.md DEPLOYMENT_RULES.md .gitignore .dockerignore \
        docker-compose.yml package-lock.json .env .env.example deploy.env.example; do
        path="$project/$name"
        [[ -e "$path" || -L "$path" ]] || continue
        skip=0; reason=''
        if [[ "$keep_source" == 1 || "$load" == not-found ]]; then skip=1; reason='код сохраняется'; fi
        if [[ -L "$path" ]]; then skip=1; reason='ссылка'; fi
        for value in "${protected[@]:-}"; do
            [[ -n "$value" ]] || continue
            if [[ "$value" == / || "$path" == "$value" || "$path" == "$value/"* || "$value" == "$path/"* ]]; then
                skip=1; reason='содержит рабочие данные / точку монтирования'
            fi
        done
        if [[ "$skip" == 1 ]]; then keep+=("$path — $reason"); else remove+=("$path"); fi
    done

    uid=$(id -u "$user")
    userctl() {
        runuser -u "$user" -- env XDG_RUNTIME_DIR="/run/user/$uid" \
            DBUS_SESSION_BUS_ADDRESS="unix:path=/run/user/$uid/bus" systemctl --user "$@"
    }
    find_models() {
        models=()
        if [[ -S "/run/user/$uid/bus" ]]; then
            value=$(userctl list-units --all --no-legend --plain 'neural-deck-model-*.service')
            while read -r name working; do
                [[ "$name" == neural-deck-model-*.service ]] || continue
                working=$(userctl show "$name" --property=WorkingDirectory --value)
                [[ "$working" != "$project" && "$working" != "$project/backend" ]] || models+=("$name")
            done <<< "$value"
        fi
    }
    find_models
    plan=$(printf 'Служба: %s (%s), пользователь: %s\nUnit: %s\nCheckout: %s\n' "$service" "$load" "$user" "$unit" "$project"
        printf '\n[ ] Остановить backend и его model units:\n  %s\n' "${models[@]:-нет}"
        printf '[ ] Отключить автозапуск, удалить unit, выполнить daemon-reload\n'
        printf '[ ] Удалить %s; удалить /etc/neural-deck, если пустой\n' "$config"
        printf '[ ] Удалить следующие файлы/каталоги:\n  %s\n' "${remove[@]:-нет}"
        printf '\nСОХРАНИТЬ рабочие пути:\n  %s\n' "${protected[@]:-не определены}"
        printf '  Системные пакеты, движки, пользователь, user manager и linger\n'
        printf 'Оставляемые серверные копии кода:\n  %s\n' "${keep[@]:-нет}"
        printf 'Корень checkout и прочие его файлы/папки не удаляются.\n')
    [[ "$mode" != scan ]] || { printf '%s\n' "$plan"; return; }
    [[ "$mode" == apply && "$plan" == "$approved_plan" ]] || die 'plan changed; run the scan again'

    printf '[▶] 1/3 Остановка служб…\n'
    if [[ "$load" != not-found ]]; then systemctl stop "$service"; fi
    find_models  # Backend can no longer launch more models during the cleanup.
    for name in "${models[@]:-}"; do [[ -z "$name" ]] || userctl stop "$name"; done
    if systemctl is-active --quiet "$service"; then die 'backend is still active'; fi
    for name in "${models[@]:-}"; do
        [[ -n "$name" ]] || continue
        if userctl is-active --quiet "$name"; then die "model is still active: $name"; fi
    done
    printf '[✓] 1/3 Службы остановлены\n[▶] 2/3 Удаление регистрации…\n'
    if [[ "$load" != not-found ]]; then
        systemctl disable "$service"
        rm -- "$unit"
    fi
    systemctl daemon-reload
    [[ $(property LoadState) == not-found ]] || die 'service is still registered; source cleanup cancelled'
    [[ ! -e "$config" && ! -L "$config" ]] || rm -- "$config"
    if [[ -d /etc/neural-deck ]] && ! rmdir /etc/neural-deck 2>/dev/null; then
        printf '  Оставлен /etc/neural-deck: каталог непустой или не может быть удалён.\n'
    fi
    printf '[✓] 2/3 Регистрация удалена\n[▶] 3/3 Очистка серверных копий…\n'
    for path in "${remove[@]:-}"; do
        [[ -n "$path" ]] || continue
        [[ ! -L "$path" && $(realpath -m -- "$path") == "$path" ]] || die "path changed: $path"
        rm -rf --one-file-system -- "$path"
        printf '  [✓] %s\n' "$path"
    done
    printf '[✓] 3/3 Готово. Серверные копии удалены, рабочие данные сохранены.\n'
    printf 'Исходники на локальной машине не изменялись; данные не переносились.\n'
}

if [[ ${1:-} == --remote ]]; then shift; uninstall_remote "$@"; exit; fi

script_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
source "$script_dir/common.sh"
root=$(repo_root)
scan_only=0; keep_source=0
for argument in "$@"; do
    case "$argument" in
        --scan) scan_only=1 ;;
        --keep-source) keep_source=1 ;;
        --help|-h) printf 'Usage: bash scripts/uninstall.sh [--scan] [--keep-source]\n'; exit 0 ;;
        *) die "unknown argument: $argument" ;;
    esac
done
load_deploy_config "${DEPLOY_ENV_FILE:-$root/deploy.env}"
[[ -n "$DEPLOY_TARGET" && -n "$DEPLOY_PATH" ]] || die 'DEPLOY_TARGET and DEPLOY_PATH are required in deploy.env'
validate_deploy_path "$DEPLOY_PATH"
remote() {
    local command="bash -s -- --remote $(shell_quote "$1") $(shell_quote "$DEPLOY_PATH") $keep_source $(shell_quote "${2:-}")"
    cat "$script_dir/common.sh" "$script_dir/uninstall.sh" | ssh -o BatchMode=yes -- "$DEPLOY_TARGET" \
        "if [ \"\$(id -u)\" = 0 ]; then $command; else sudo -n $command; fi"
}
printf 'SSH: %s\n[▶] Проверка установленной службы и путей…\n' "$DEPLOY_TARGET"
plan=$(remote scan)
printf '\nПлан:\n%s\n' "$plan"
[[ "$scan_only" == 0 ]] || exit 0
printf '\nВыполнить этот план? Серверные копии удаляются без корзины. [y/N] '
answer=''; IFS= read -r answer || answer=''
case "$answer" in y|Y) remote apply "$plan" ;; *) printf '\nОтменено. Ничего не удалено.\n' ;; esac
