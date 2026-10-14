set -euo pipefail
target_root={{target}}
if [[ "$target_root" == */llama-server ]]; then executable="$target_root"; else executable="$target_root/bin/llama-server"; fi
if [ -f "$executable" ]; then
  echo 'Бинарник уже существует; сборка пропущена. Приложение проверит и зарегистрирует его.'
  exit 0
fi
# Debian 12 / Ubuntu 22.04, 24.04: недостающие инструменты устанавливаются через apt.
# RTX 3090: sm_86; новые Blackwell: sm_120.
as_root() {
  if [ "$(id -u)" -eq 0 ]; then
    "$@"
  elif command -v sudo >/dev/null 2>&1 && sudo -n true; then
    sudo -n "$@"
  else
    echo 'Для установки системных пакетов нужны root или sudo без запроса пароля (systemd NoNewPrivileges также запрещает повышение прав).' >&2
    exit 1
  fi
}
if ! command -v apt-get >/dev/null 2>&1; then
  echo 'Этот шаблон автоматической подготовки llama.cpp поддерживает Debian/Ubuntu с apt-get.' >&2
  exit 1
fi
missing_packages=()
for package in build-essential cmake git curl ca-certificates libcurl4-openssl-dev ccache; do
  if [ "$(dpkg-query -W -f='${Status}' "$package" 2>/dev/null || true)" != 'install ok installed' ]; then
    missing_packages+=("$package")
  fi
done
if [ "${#missing_packages[@]}" -gt 0 ]; then
  echo "Установка инструментов сборки: ${missing_packages[*]}"
  as_root apt-get update
  as_root env DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends "${missing_packages[@]}"
fi
cuda_compiler=''
for candidate in "${CUDACXX:-}" "$(command -v nvcc || true)" /usr/local/cuda/bin/nvcc /usr/local/cuda-12.8/bin/nvcc; do
  if [ -n "$candidate" ] && [ -x "$candidate" ]; then
    cuda_version=$("$candidate" --version | sed -n 's/.*release \([0-9]*\.[0-9]*\),.*/\1/p')
    if [ -n "$cuda_version" ] && dpkg --compare-versions "$cuda_version" ge 12.8; then
      cuda_compiler=$candidate
      break
    fi
  fi
done
if [ -z "$cuda_compiler" ]; then
  . /etc/os-release
  case "$ID:$VERSION_ID" in
    ubuntu:22.04) cuda_distro=ubuntu2204 ;;
    ubuntu:24.04) cuda_distro=ubuntu2404 ;;
    debian:12) cuda_distro=debian12 ;;
    *) echo "Автоустановка CUDA 12.8 не настроена для $ID $VERSION_ID." >&2; exit 1 ;;
  esac
  case "$(uname -m)" in
    x86_64) cuda_arch=x86_64 ;;
    *) echo 'Автоустановка CUDA в этом шаблоне поддерживает x86_64.' >&2; exit 1 ;;
  esac
  echo 'Установка CUDA Toolkit 12.8 с nvcc из официального репозитория NVIDIA'
  cuda_tmp=$(mktemp -d)
  trap 'rm -rf -- "$cuda_tmp"' EXIT
  curl --fail --silent --show-error --location \
    "https://developer.download.nvidia.com/compute/cuda/repos/$cuda_distro/$cuda_arch/cuda-keyring_1.1-1_all.deb" \
    -o "$cuda_tmp/cuda-keyring.deb"
  as_root dpkg -i "$cuda_tmp/cuda-keyring.deb"
  as_root apt-get update
  as_root env DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends cuda-toolkit-12-8
  cuda_compiler=/usr/local/cuda-12.8/bin/nvcc
fi
echo "CUDA compiler: $cuda_compiler"
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
  -DCMAKE_CUDA_COMPILER="$cuda_compiler" \
  -DCMAKE_CUDA_ARCHITECTURES='86;120' \
  -DBUILD_SHARED_LIBS=OFF \
  -DLLAMA_BUILD_SERVER=ON
cmake --build "$source_dir/build" --config Release --target llama-server --parallel "$(nproc)"
install -D -m 0755 "$source_dir/build/bin/llama-server" "$executable"
