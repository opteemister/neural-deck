"""Base launch commands for the executable discovered for each engine."""

LAUNCH_TEMPLATES = {
    "llama.cpp": "{{engine}} --model {{model}} --host 127.0.0.1 --port 8000",
    "vllm": "{{engine}} serve {{model}} --host 127.0.0.1 --port 8000",
    "sglang": "{{engine}} serve --model-path {{model}} --host 127.0.0.1 --port 8000",
    "ninfer": "{{engine}} {{model}} --host 127.0.0.1 --port 8000",
    "freetoken": "{{engine}} serve --model {{model}} --host 127.0.0.1 --port 8000",
}
