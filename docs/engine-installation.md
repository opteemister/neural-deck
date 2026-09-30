# Установка движков из интерфейса

Шаблоны установки находятся в `backend/app/engines/install_templates/`:

| Движок | Шаблон | Что выполняется |
| --- | --- | --- |
| vLLM | `vllm.sh` | Python 3.12 venv и установка vLLM с `--torch-backend=auto` |
| SGLang | `sglang.sh` | Python 3.12 venv и установка SGLang с `--prerelease=allow` |
| FreeToken | `freetoken.sh` | Python 3.12 venv и установка `freetoken[accel]` |
| llama.cpp | `llama.cpp.sh` | Git checkout, CUDA-сборка сервера для `86;120`, установка бинарника |
| NInfer | `ninfer.sh` | Git checkout, Ninja/CMake CUDA-сборка для `120a`, symlink на сервер в дереве сборки |

Для Python-движков также есть отдельные `*.update.sh` для кнопки обновления.

Backend передаёт шаблоны и корень venv вместе с каталогом движков.
Frontend локально подставляет пути, экранируя их для Bash.
`{{target}}` — папка venv либо путь бинарника, `{{python}}` — Python venv,
`{{source}}` — каталог исходников, `{{target_dir}}` — родитель бинарника.

## Работа формы

1. Пользователь выбирает движок, способ установки и имя venv или абсолютный
   путь бинарника. Команда формируется локально из шаблона каталога;
   изменение полей не вызывает запрос к backend.
2. Команда показывается в редактируемом поле. «Шаблон движка» возвращает
   подготовленный исходный текст. Изменение движка или пути создаёт новый шаблон.
3. «Установить» отправляет команду вместе с местом установки. Backend выполняет
   именно переданный текст через `/bin/bash -c`, с правами пользователя службы.
4. Панель логов открывается на Operations. stdout/stderr пишутся в общий
   `operations.log` сразу по мере поступления с идентификатором операции.
   Операция заканчивается записью состояния, кода выхода и ошибки.
5. После команды адаптер проверяет исполняемый файл. Python-окружение получает
   манифест и список пакетов. Бинарные пути сохраняются в
   `<LLM_STUDIO_DATA_ROOT>/config/engine-binaries.yaml` и учитываются при
   дальнейших сканированиях, включая перезапуск приложения.

Поля не очищаются после запуска. Допускается повторить установку в существующее
место, изменить параметры сборки или заменить команду на доустановку.
Одновременные операции на одну цель запрещены. Нельзя менять установку,
из которой сейчас работает управляемая модель. На выполнение установки
отведено до четырёх часов; при отмене останавливается группа дочерних процессов.

## Требования на целевой Linux-машине

Шаблоны устанавливают движки, но не драйвер NVIDIA и системный toolchain.
Служба не выполняет `sudo` и не меняет системные пакеты самостоятельно.
Нужны доступ к интернету, права записи в выбранные каталоги и `uv` в PATH.
Для сборок нужны Git, CMake, C++ compiler и CUDA Toolkit в PATH; для llama.cpp
также development-пакет libcurl. CUDA Toolkit должен поддерживать обе
архитектуры `86` и `120` (SM120 добавлен в CUDA 12.8).

NInfer upstream поддерживает только `sm_120a` / RTX 5090 и **не поддерживает
RTX 3090**. Для него требуются CMake >= 3.28, C++20 compiler, Ninja,
`pkg-config`, FFmpeg development libraries (`libavformat`, `libavcodec`,
`libavutil`, `libswscale`) и libcurl >= 7.85. Upstream использует CUDA 13.1
как проверенный development-toolkit. Изменение CMake-флага само по себе
не добавит поддержку других GPU.

Шаблоны используют актуальные пакеты и исходники без закрепления версии.
llama.cpp использует единственный checkout в
`<LLM_STUDIO_DATA_ROOT>/runtime/builds/llama.cpp/source` и его `build/`.
Если checkout уже существует, выполняется `git pull --ff-only`. CMake
пересобирает изменившиеся части и затем бинарник копируется в выбранный путь.
Параллельные установки llama.cpp запрещены, потому что используют общую сборку.
NInfer использует существующий checkout без автоматического обновления Git. Для другой ревизии пользователь явно добавляет `git fetch`
и `git checkout` в команду. Для повторной Python-установки можно добавить `-U`
или указать версию пакета прямо в поле команды.

## Официальные источники

- [llama.cpp CUDA build](https://github.com/ggml-org/llama.cpp/blob/master/docs/build.md).
- [NVIDIA RTX 3090 compute capability](https://www.nvidia.com/en-gb/geforce/graphics-cards/30-series/rtx-3090-3090ti/).
- [CUDA 12.8 features](https://docs.nvidia.com/cuda/archive/12.8.0/cuda-features-archive/index.html).
- [NInfer quick start и ограничения](https://github.com/Neroued/ninfer/blob/master/README.md).
- [vLLM NVIDIA installation](https://docs.vllm.ai/en/latest/getting_started/installation/gpu/).
- [SGLang installation](https://github.com/sgl-project/sglang/blob/main/docs/docs/get-started/install.mdx).
- [FreeToken CLI installation](https://github.com/FlashML-org/FreeToken/blob/main/README.md).

Команды собираются и выполняются только по запросу пользователя через UI;
проверка реальной сборки/GPU выполняется на целевой машине.
