set -euo pipefail
# Только sm_120a / RTX 5090; RTX 3090 не поддерживается upstream.
# Требуются CUDA Toolkit (upstream проверяет 13.1), CMake >= 3.28,
# C++20 compiler, Ninja, pkg-config, libcurl >= 7.85 и FFmpeg development libraries.
source_dir={{source}}
mkdir -p "$(dirname "$source_dir")"
if [ ! -d "$source_dir/.git" ]; then
  git clone https://github.com/Neroued/ninfer.git "$source_dir"
fi
cmake -S "$source_dir" -B "$source_dir/build" -G Ninja \
  -DCMAKE_BUILD_TYPE=Release -DCMAKE_CUDA_ARCHITECTURES=120a
cmake --build "$source_dir/build" --parallel "$(nproc)"
# Upstream запускает продукт из дерева сборки; сохраняем этот путь через symlink.
mkdir -p {{target_dir}}
ln -sfn "$source_dir/build/apps/ninfer-serve" {{target}}
