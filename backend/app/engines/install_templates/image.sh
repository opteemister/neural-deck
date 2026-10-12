set -euo pipefail
uv venv --python 3.13 --allow-existing {{target}}
source_dir={{source}}
mkdir -p "$(dirname "$source_dir")"
if [ ! -d "$source_dir/.git" ]; then
  git clone https://github.com/comfyanonymous/ComfyUI.git "$source_dir"
else
  git -C "$source_dir" pull --ff-only
fi
ln -sfn "$source_dir" {{target}}/ComfyUI
uv pip install --python {{python}} -r "$source_dir/requirements.txt"
cat > {{target}}/bin/comfyui <<'WRAPPER'
#!/usr/bin/env bash
set -euo pipefail
exec "$(dirname "$0")/python" {{source}}/main.py "$@"
WRAPPER
chmod 0755 {{target}}/bin/comfyui
