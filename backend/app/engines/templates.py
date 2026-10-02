"""Base launch commands for the executable discovered for each engine."""

# HTTP metrics are enabled explicitly for llama.cpp and SGLang. vLLM and
# FreeToken expose their metrics/stats by default. NInfer has throughput logs
# by default, but no supported HTTP metrics endpoint; do not invent a flag.
LAUNCH_TEMPLATES = {
    "llama.cpp": "{{engine}} --model {{model}} --alias {{model_name}} --metrics --host 127.0.0.1 --port 8000",
    "vllm": "{{engine}} serve {{model}} --served-model-name {{model_name}} --host 127.0.0.1 --port 8000",
    "sglang": "{{engine}} serve --model-path {{model}} --served-model-name {{model_name}} --enable-metrics --host 127.0.0.1 --port 8000",
    "ninfer": "{{engine}} {{model}} --model-id {{model_name}} --host 127.0.0.1 --port 8000",
    "freetoken": "{{engine}} serve --model {{model}} --served-model-name {{model_name}} --host 127.0.0.1 --port 8000",
}
