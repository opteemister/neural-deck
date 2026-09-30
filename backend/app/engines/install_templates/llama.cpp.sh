set -euo pipefail
# Требуются git, CMake, C++ compiler, libcurl development и CUDA Toolkit >= 12.8.
# RTX 3090: sm_86; новые Blackwell: sm_120.
source_dir={{source}}
mkdir -p "$(dirname "$source_dir")"
if [ ! -d "$source_dir/.git" ]; then
  git clone https://github.com/ggml-org/llama.cpp.git "$source_dir"
else
  git -C "$source_dir" pull --ff-only
fi
cmake -S "$source_dir" -B "$source_dir/build" \
  -DCMAKE_BUILD_TYPE=Release \
  -DGGML_CUDA=ON \
  -DCMAKE_CUDA_ARCHITECTURES='86;120' \
  -DBUILD_SHARED_LIBS=OFF \
  -DLLAMA_BUILD_SERVER=ON
cmake --build "$source_dir/build" --config Release --target llama-server --parallel "$(nproc)"
install -D -m 0755 "$source_dir/build/bin/llama-server" {{target}}
