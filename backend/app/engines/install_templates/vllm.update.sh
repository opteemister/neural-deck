set -euo pipefail
uv pip install -U --python {{python}} vllm --torch-backend=auto
