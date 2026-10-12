# Универсальные определения движков

Определение движка — редактируемый YAML с командой установки, executable,
командой запуска и независимыми настройками capability, API и UI. Встроенные
определения поставляются с приложением; пользовательские лежат в
`<DATA_ROOT>/engines/<id>.yaml`. Редактирование встроенного ID записывает
пользовательский override. Удаление такого YAML сбрасывает override и возвращает
поставляемое приложением определение; установки и профили не удаляются.

Описанные ниже runtime-примеры и ComfyUI workflow пока **не проверены запуском
в этом проекте**. YAML и команды показывают ожидаемую конфигурацию; они не
подтверждают установку зависимостей, совместимость GPU или фактическую генерацию.

В интерфейсе «Движки» доступны создание, редактирование и удаление определения.
Скрипт установки редактируется как в определении, так и перед запуском
конкретной установки. Generic API принимает `{target_path, run_install,
executable_path?, command?}`. Необязательный `command` — текст скрипта установки:
если поле не передано, используется `install_script` определения; если передано,
оно переопределяет скрипт для этой операции. Регистрация существующего
executable не запускает скрипт. Старые варианты запроса с `kind` также
поддерживаются API.

Типы capability, API protocol/URL и UI URL в профиле находятся в раскрываемом
разделе «Переопределения профиля». Их пустое значение наследуется от движка;
профиль может сузить список типов. Модель необязательна, если команда не требует
`{{model}}`. Публичное имя по умолчанию берётся из имени профиля. Список
установок в форме профиля единый.

## Поля определения

```yaml
version: 1
id: example-engine
name: Example engine
types: [text]
install_script: |
  # Скрипт установки; {{target}} — выбранный каталог
executable: "{{target}}/bin/example"
working_directory: null
environment: {}
command: "{{engine}} serve --model {{model}} --host 127.0.0.1 --port 18120"
healthcheck:
  path: /health
  timeout_seconds: 3
api:
  protocol: openai
  base_url: http://127.0.0.1:18120/v1
ui_url: null
version_command: [--version]
```

`healthcheck.path` — путь от API origin, начинается с `/`; проверка readiness
требует `api.base_url`. `version_command` — argv без shell. Команда без
`{{model}}` подходит сервису, выбирающему модель самостоятельно. `types`
принимает `text`, `tts`, `asr`, `video`, `image`, `embeddings`, `decision`.
Протоколы: `none`, `openai`, `comfyui`, `jev`.

## Decision: Strands Decider 2B через Jev

Готовый пример определения: [strands-decider-2b-jev.yaml](examples/engine-definitions/strands-decider-2b-jev.yaml).
Он использует официальный Python-пакет Strands Decider и создаваемую им команду
`strands-decider`; отдельный несуществующий бинарник не предполагается.
Установка требует Python 3.12, создаёт venv, ставит CUDA-вариант upstream-пакета
из изменяемой ветки `main` и `causal-conv1d` (для последнего нужен совместимый
локальный CUDA Toolkit). После проверки шаблон следует закрепить на конкретном
релизе или commit.
Команда запуска соответствует upstream CLI:
`strands-decider serve <model> --device cuda --port 8000`. В качестве `<model>`
укажите локальный checkpoint. Upstream CLI также принимает опубликованный ID
`StrandsAgents/strands-decider-2B-qwen3.5-v1-2610`; при холодном cache он
загрузит checkpoint, а базовую модель `Qwen/Qwen3.5-2B-Base` (около 4.5 GB)
загрузит при первом запуске и для локального checkpoint.

`api.protocol: jev` направляет общий `POST /v1/decisions` к нативному
`POST /v1/systemone`. Наличие маршрута в проекте не подтверждает полную
совместимость ответов Strands с внешним контрактом OpenAI Decisions: upstream
отдельно указывает, что совместимость с Jev API не проверена. Этот runtime и
его round-trip в Neural Deck также не проверялись; до такой проверки пример
следует считать экспериментальным. См. [официальные инструкции сервера](https://github.com/strands-labs/strands-decider/blob/main/docs/inference.md)
и [README проекта](https://github.com/strands-labs/strands-decider).

## ComfyUI: видео Wan 2.1

Подготовленный workflow для text-to-video: [wan21-text-to-video.json](examples/comfyui/wan21-text-to-video.json).
Это граф для собственного UI ComfyUI; универсальный video endpoint Neural Deck
не добавляется. Workflow использует штатные узлы ComfyUI и следующие файлы
в соответствующих категориях моделей:

| Категория ComfyUI | Файл для примера |
|---|---|
| `diffusion_models` | `wan2.1_t2v_1.3B_fp16.safetensors` |
| `text_encoders` | `umt5_xxl_fp8_e4m3fn_scaled.safetensors` |
| `vae` | `wan_2.1_vae.safetensors` |

Поместите уже полученные файлы в каталоги этих категорий, которые видит ComfyUI,
либо настройте для него дополнительные пути моделей. Neural Deck не скачивает,
не устанавливает и не раскладывает веса автоматически. Workflow и фактическая
генерация здесь не запускались. Описание категорий и официальные примеры:
[ComfyUI Wan 2.1](https://github.com/comfyanonymous/ComfyUI_examples/blob/master/wan/README.md),
[официальный workflow](https://comfy.org/workflows/text_to_video_wan-105559626c80/).

Определение ComfyUI запускает сервер с `--listen 127.0.0.1 --port 8188`; UI URL
задан как `http://127.0.0.1:8188`. Для запуска приложения в Docker и доступа к
UI через опубликованный порт пользователь вручную меняет в команде запуска
`--listen 127.0.0.1` на `--listen 0.0.0.0` и публикует порт `8188` в Compose.
Переменная окружения и отдельное поле порта в профиле для этого не вводятся.

## Примеры serving-адаптеров

YAML в [каталоге примеров](examples/engine-definitions/) привязан к текущим
файлам `backend/app/serving/`. Скрипты предполагают checkout приложения по
пути `/opt/neural-deck`; измените этот префикс, если checkout в другом месте,
доступном процессу установки. Скрипты копируют adapter script в отдельный venv
и ставят его requirements. Установка весов в скрипты не входит.

Сверены сигнатуры в первичных документах, но checkpoint, GPU, зависимости и
полный HTTP round-trip здесь не запускались. Поэтому примеры экспериментальные:

- [Qwen3-TTS source](https://github.com/QwenLM/Qwen3-TTS/blob/main/qwen_tts/inference/qwen3_tts_model.py)
  документирует `from_pretrained`, `get_supported_speakers` и
  `generate_custom_voice`. Пакет `qwen-tts==0.1.1` закрепляет
  `transformers==4.57.3` и `accelerate==1.12.0`; Torch/torchaudio выбираются
  под целевую GPU.
- [Nemotron 3.5 ASR](https://huggingface.co/docs/transformers/en/model_doc/nemotron3_5_asr)
  показывает `AutoModelForRNNT`, `AutoProcessor`, `load_audio`, `generate` и
  `processor.decode`. Адаптер покрывает файловую транскрибацию; streaming не
  реализован.
- [EmbeddingGemma 2](https://huggingface.co/docs/transformers/model_doc/embedding_gemma2)
  документирует `SentenceTransformer.encode` для текста и multimodal dicts.
  `/v1/embeddings` принимает текст; `/embed` — частный JSON endpoint для текста
  и data URI изображения, не OpenAI endpoint.

Все адаптеры загружают только локальный каталог модели и включают offline-флаги.
TTS возвращает WAV и отвергает `speed != 1`; ASR поддерживает `json`/`text`;
embeddings отвергают requested `dimensions`, возвращая нативную размерность.
Ограничения проверяются обработчиками. До runtime-проверки не считать эти
обёртки производственно подтверждёнными.
