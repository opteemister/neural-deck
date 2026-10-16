set -euo pipefail
if [ -f ./update.sh ]; then
  exec sh ./update.sh
fi

# Older Strata checkouts predate the upstream updater. Fetch it, then use it
# for the full dependency, engine, and installed-model settings update.
git pull --ff-only
if [ -f ./update.sh ]; then
  exec sh ./update.sh
fi

echo 'После обновления Strata не найден официальный update.sh.' >&2
exit 1
