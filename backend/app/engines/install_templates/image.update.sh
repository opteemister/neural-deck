set -euo pipefail
source_dir={{source}}
git -C "$source_dir" pull --ff-only
ln -sfn "$source_dir" {{target}}/ComfyUI
uv pip install -U --python {{python}} -r "$source_dir/requirements.txt"
