set -euo pipefail
uv venv --python 3.12 --allow-existing {{target}}
uv pip install --python {{python}} 'freetoken[accel]'
