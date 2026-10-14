set -euo pipefail
target={{target}}
git clone -- https://github.com/Niko1221/Strata.git "${target}"
chmod +x "${target}/setup.sh"
