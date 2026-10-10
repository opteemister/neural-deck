---
title: Neural Deck Version Zero — контракты данных и API
status: integrated
parent: 2026-09-29-version-zero.md
date: 2026-09-29
updated: 2026-09-29
---

# Контракты Version Zero

Этот документ является частью спецификации Version Zero. Он фиксирует seams
между backend, frontend, MCP, файловыми профилями и каталогом моделей, чтобы
параллельные задачи не создавали несовместимые форматы.

## 1. Общие правила management REST API

Management API использует префикс `/api/v1`. OpenAI-совместимый API использует
отдельный префикс `/v1`. Даты management API передаются как RFC 3339 UTC.

Успешный management-ответ:

```json
{
  "data": {},
  "request_id": "req_01J..."
}
```

Ошибка:

```json
{
  "error": {
    "code": "profile_not_found",
    "message": "Профиль не найден",
    "details": {},
    "request_id": "req_01J...",
    "retryable": false
  }
}
```

Поле `code` — стабильный машинный идентификатор, `message` — текст для
оператора, `details` — структурированный контекст без traceback и секретов.
Pydantic-схемы backend являются единственным источником TypeScript-типов API.
Ошибка входа возвращает `422 validation_error`, отсутствующий объект —
`404 *_not_found`, конфликт имени, порта или операции — `409 *_conflict`,
неподдерживаемое действие — `400 unsupported_operation`, изменение
идентичности процесса — `409 instance_identity_changed`, внутренняя ошибка —
`500 internal_error`. Каждый `202` возвращает обычный success envelope, где
`data` — созданный `Operation`, либо созданный `Instance` для запуска профиля.

## 2. Маршруты management API

### Система и настройки

| Метод | Маршрут | Результат |
|---|---|---|
| `GET` | `/api/v1/system` | CPU, RAM, GPU и краткие сведения об активных экземплярах |
| `GET` | `/api/v1/status` | Состояние backend, SQLite, общего OpenAI API и обязательных компонентов |
| `GET` | `/api/v1/settings` | Эффективные пути и сетевые параметры без секретов |
| `PATCH` | `/api/v1/settings` | Изменение `models_root` и последующее сканирование моделей |

Остальные runtime-параметры меняются через `.env` и в API доступны только
для чтения. `PATCH` принимает `{ "models_root": "/absolute/path" }` и
сохраняет значение в `<data-root>/config/settings.yaml`; сохранённое значение
имеет приоритет над `MODELS_ROOT` после перезапуска.

### Движки, установки и длительные операции

| Метод | Маршрут | Результат |
|---|---|---|
| `GET` | `/api/v1/engines` | Поддерживаемые движки и найденные установки |
| `POST` | `/api/v1/engines/scan` | Повторное обнаружение и проверка |
| `GET` | `/api/v1/engines/{engine_id}` | Один движок |
| `GET` | `/api/v1/installations` | Все установки |
| `GET` | `/api/v1/installations/{installation_id}` | Одна установка |
| `POST` | `/api/v1/engines/{engine_id}/installations` | Создание Python venv либо регистрация готового бинарника; `202` |
| `POST` | `/api/v1/installations/{installation_id}/update` | Обновление выбранного venv; `202` |
| `POST` | `/api/v1/installations/{installation_id}/maintenance` | Разовая подтверждённая команда; `202` |
| `GET` | `/api/v1/operations/{operation_id}` | Состояние длительной операции |

`engine_id`: `vllm`, `llama.cpp`, `sglang`, `ninfer`, `freetoken`.
Для Python-движка запрос содержит `kind: python_venv` и `venv_name`; имя не
содержит разделителей пути, а окружение создаётся в
`<data-root>/runtime/venvs/<venv_name>`. Для llama.cpp или NInfer запрос
содержит `kind: binary` и абсолютный `path`; Neural Deck не копирует и не собирает
этот файл, а проверяет его адаптером.

Установка содержит `id`, `engine_id`, `kind`, `root_path`, `executable_path`,
`version`, `status`, `verified`, `diagnostic`. `id` детерминированно строится
из `engine_id`, `kind` и canonical real path, поэтому повторное сканирование
не ломает YAML-профили. Для venv `root_path` — корень окружения, а
`executable_path` — команда движка; для binary оба значения указывают на
проверенный файл. `status`: `ready`, `broken`, `candidate`; выбрать для
профиля можно только `ready`.

Длительная операция содержит `id`, `kind`, `state`, `target_id`, timestamps,
`stdout`, `stderr`, `exit_code`, `error`. `state`: `queued`, `running`,
`succeeded`, `failed`. Вывод ограничивается по размеру, сохраняется в логах и
не содержит значений environment профиля.
Успешная install/update operation сохраняет `resolved_packages` с точными
именами и версиями и `checked_at`. Одновременная операция над тем же target
или тем же `venv_name` возвращает `409 operation_conflict`.

### Локальные модели и Hugging Face

| Метод | Маршрут | Результат |
|---|---|---|
| `GET` | `/api/v1/models` | Модели, зарегистрированные в общем API |
| `GET` | `/api/v1/models/local` | Локальные файлы и каталоги моделей |
| `POST` | `/api/v1/models/local/scan` | Явное повторное сканирование |
| `GET` | `/api/v1/models/hf/search?q=...` | Поиск публичных моделей |
| `GET` | `/api/v1/models/hf/repos/{namespace}/{name}` | Репозиторий и выбранная ревизия |
| `GET` | `/api/v1/models/hf/repos/{namespace}/{name}/files?revision=...` | Файлы ревизии |
| `POST` | `/api/v1/models/downloads` | Скачивание полного snapshot или выбранных файлов; `202` |

Запрос скачивания содержит `repo_id`, `revision` и `files`. `files: null`
означает полный snapshot; массив относительных путей — выбранный набор.
Ревизия перед началом операции разрешается в commit SHA и сохраняется в
manifest.

Ответ локального каталога дополнительно содержит логические группы `groups`
и `auxiliary_blocks`. Группа имеет стабильный `id`, подпись `label`, тип
`variant` или `auxiliary_block`, исходный путь (если это вложенная папка),
количество и размер файлов, статус `complete|incomplete|unknown` и признак
`deletable`. UI показывает группу одной строкой; отдельные файлы остаются в
ответе только для безопасной операции удаления и диагностики. Удаление группы
выполняется через `DELETE /api/v1/models/local/{model_id}/groups/{group_id}`.

Локальный артефакт содержит `id`, `path`, `kind`, `format`, `compatibility`,
`size_bytes` и `hf_match`. `hf_match.status`: `downloaded`, `incomplete`,
`unknown`; неизвестное значение не подменяется отрицательным результатом.
Для вручную скопированного артефакта Hub-связь определяется автоматически
только из sidecar, HF cache metadata или проверяемого совпадения выбранной
ревизии. Отдельного ручного назначения repo в первой версии нет. Провал
проверки либо отсутствие доказательств даёт `unknown`; `incomplete` означает
только подтверждённое наличие правильной части ожидаемого manifest.

### Профили

| Метод | Маршрут | Результат |
|---|---|---|
| `GET` | `/api/v1/profiles` | Перечитанные YAML-профили и отдельный список ошибок файлов |
| `POST` | `/api/v1/profiles` | Создание профиля |
| `GET` | `/api/v1/profiles/{profile_name}` | Один профиль |
| `PUT` | `/api/v1/profiles/{profile_name}` | Полная запись профиля |
| `POST` | `/api/v1/profiles/format-script` | Безопасное форматирование одной строки без изменения семантики |
| `POST` | `/api/v1/profiles/{profile_name}/start` | Запуск; `202` и созданный экземпляр |

`GET /profiles` не скрывает исправные профили из-за одного сломанного файла:
ответ содержит `items` и `issues`, где issue содержит `file`, `code`, `message`.

### Экземпляры, логи, метрики и токены

| Метод | Маршрут | Результат |
|---|---|---|
| `GET` | `/api/v1/instances` | Управляемые и внешние экземпляры |
| `GET` | `/api/v1/instances/{instance_id}` | Текущее состояние экземпляра |
| `POST` | `/api/v1/instances/{instance_id}/stop` | Остановка; `202` |
| `GET` | `/api/v1/instances/{instance_id}/logs?tail=200` | Доступный хвост лога |
| `GET` | `/api/v1/instances/{instance_id}/metrics` | Текущие метрики экземпляра |
| `GET` | `/api/v1/token-stats?profile_name=...&from=...&to=...` | Статистика токенов |

Страница логов использует следующие маршруты:

| Метод | Маршрут | Результат |
|---|---|---|
| `GET` | `/api/v1/logs` | Источники Application, Operations и профилей |
| `GET` | `/api/v1/logs/{source_id}?before=...&limit=500` | Порция строк с номерами и курсором для более ранних строк |
| `GET` | `/api/v1/logs/{source_id}/download` | Объединённый текст выбранного источника для скачивания |
| `DELETE` | `/api/v1/logs/{source_id}` | Удаление истории выбранного источника |

Идентификаторы экземпляров:

```text
managed/<url-encoded-profile-name>/<pid>-<process-start-time-ns>
external/<engine-id>/<pid>-<process-start-time-ns>
```

Экземпляр содержит `id`, `source`, `profile_name`, `engine_id`, `pid`,
`process_start_time_ns`, `port`, `model_name`, `state`, `observed_at`.
`state`: `starting`, `ready`, `running`, `stopping`, `exited`, `error`,
`unknown`. Перед остановкой PID и start time проверяются повторно.

Метрика содержит `name`, `value`, `unit`, `source`, `observed_at`, `state`.
`state`: `ok`, `unavailable`, `stale`; при отсутствии значения `value: null`.
Статистика токенов хранит для входа и выхода `known_total` и
`unknown_requests`, чтобы неизвестное не превращалось в ноль.

### Канонические request/data-схемы

Все object-схемы запрещают неизвестные поля. Nullable поля явно помечены `?`:

- `SystemData`: `cpu{cores,usage_percent}`, `ram{used_bytes,total_bytes}`,
  `gpus[] {index,name,memory_used_bytes,memory_total_bytes,usage_percent?,temperature_celsius?}`,
  `instances[]: Instance`.
- `StatusData`: `overall`, `components[] {name,state,message?,checked_at}`;
  state — `ok`, `degraded`, `error`.
- `SettingsData`: `data_root`, `models_root`, `log_root`,
  `extra_venv_roots[]`, `host`, `port`, `openai_port`; `SettingsPatch` содержит только
  `models_root`.
- `EngineData`: `id`, `display_name`, `installation_kinds[]`,
  `installations[]: Installation`.
- `InstallationCreate`: одна из двух discriminated-схем:
  `{kind:"python_venv",venv_name}` или `{kind:"binary",path}`.
- `MaintenanceRequest`: `{command}`. `UpdateRequest` и scan requests не имеют
  body. Install/update/maintenance возвращают `Operation`.
- `HfSearchData`: `items[] {repo_id,revision?,description?,tags[]}`;
  `HfRepoData`: `repo_id`, `revision`, `files[]: HfFile`;
  `HfFile`: `path`, `size`, `etag?`, `sha256?`.
- `DownloadRequest`: `repo_id`, `revision`, `files: string[] | null`;
  возвращает `Operation`.
- `ProfileData` повторяет YAML-схему раздела 3. `ProfileListData` содержит
  `items[]: ProfileData` и `issues[] {file,code,message}`. POST и PUT принимают
  `ProfileData`; path `profile_name` обязан совпасть с body `name`.
- `FormatScriptRequest`: `{script}`; результат `{script,changed,reason?}`.
- `Instance`, `Metric` и `Operation` имеют поля, перечисленные выше;
  list endpoints возвращают `{items:[...]}`. Stop возвращает `Operation`,
  start возвращает `Instance`.
- `LogsData`: `instance_id`, `lines[]`, `truncated`; `TokenStatsData`:
  `items[] {profile_name,requests,input_tokens{known_total,unknown_requests},output_tokens{known_total,unknown_requests},source}`.

Значение `request_id` находится либо рядом с `data` в success envelope, либо
внутри `error` в error envelope; другие top-level формы не используются.

OpenAI API сохраняет отдельный стандартный contract:

Интерфейс и API управления обслуживаются на `PORT` (локально 3000).
Общий API моделей слушает `OPENAI_PORT` (8080 по умолчанию) и использует
тот же реестр процессов. Маршруты `/v1` не доступны на порту приложения.
Встроенный чат обращается к общему API на отдельном порту.

```text
GET  /v1/models
POST /v1/chat/completions
POST /v1/embeddings
```

## 3. YAML-профиль версии 1

Файл: `<data-root>/profiles/<name>.yaml`.

```yaml
version: 1
name: qwen3-8b
model_name: qwen3-8b

engine:
  id: vllm
  installation_id: inst_01J...

model:
  path: /models/Qwen/Qwen3-8B

command: |
  {{engine}} serve \
    --model {{model}} \
    --host 127.0.0.1 \
    --port 1919

environment:
  CUDA_VISIBLE_DEVICES: "0"

autostart: false
```

Правила:

- `version` равно целому числу `1`.
- `name` непустое, не содержит `/`, `\`, управляющие символы или `..` и
  совпадает с именем файла без `.yaml`.
- `model_name` — публичное имя модели, уникальное среди активного реестра.
- `engine.id` входит в список пяти движков.
- `engine.installation_id` ссылается на конкретную подтверждённую установку;
  исчезновение установки не вызывает автоматического переключения.
- `model.path` — абсолютный путь к локальному файлу или каталогу.
- `command` — единственный источник текста запуска.
- `{{engine}}` и `{{model}}` допустимы только как отдельные shell words.
- `environment` — карта строк, по умолчанию пустая.
- `autostart` — boolean.
- Неизвестные поля, пользовательские YAML tags и неверные типы являются
  ошибкой конкретного файла. Профиль не переносится в SQLite.

## 4. MCP Streamable HTTP

Официальный Python MCP SDK фиксируется точной версией в `uv.lock`. Endpoint
`/mcp` работает в stateless Streamable HTTP режиме и интегрирует lifespan
MCP-приложения в lifespan FastAPI/ASGI.

| Инструмент | Вход | Результат management API |
|---|---|---|
| `get_system_state` | `{}` | System response |
| `list_models` | `{}` | Model registry response |
| `list_profiles` | `{}` | Profile list response вместе с issues |
| `start_profile` | `{ "profile_name": string }` | Созданный instance |
| `stop_instance` | `{ "instance_id": string }` | Operation |

Каждый запрос независим; `Mcp-Session-Id` не создаётся и не требуется.
Успешный результат содержит полный success envelope management API в
`structuredContent`, один `content` item типа `text` с JSON-сериализацией того
же envelope и `isError: false`. Ошибка содержит полный общий error envelope в
`structuredContent`, его JSON-сериализацию в `content` и `isError: true`; она
не превращается в успешный текст. `start_profile` возвращает состояние
`starting` или `ready`, не ожидая бесконечно готовность модели.

## 5. Hugging Face manifest версии 1

Для каталога manifest хранится в `<model-dir>/.neural-deck-hf.yaml`, для
отдельного файла — в `<model-file>.neural-deck-hf.yaml`.

```yaml
schema_version: 1
repo_id: Qwen/Qwen3-8B
revision: abc1234567890abcdef1234567890abcdef123456
scope: snapshot
files:
  - path: config.json
    size: 9876
    sha256: null
    etag: "0123456789abcdef"
```

- `revision` — разрешённый commit SHA; `scope` — `snapshot` или `selection`.
- `files[].path` — относительный POSIX-путь без начального `/` и `..`.
- `size` обязателен; `sha256` и `etag` необязательны, но проверяются, когда
  сохранены.
- Manifest не содержит доверенного поля status; статус вычисляет сканер.
- `downloaded` означает наличие и совпадение всех файлов manifest.
- `incomplete` означает подтверждённое наличие только части manifest.
- `unknown` означает отсутствие достаточных метаданных или провал проверки.
- Дополнительные неизвестные файлы остаются видимыми и сами по себе не делают
  каталог недействительным.
- При отсутствии sidecar сканер использует metadata Hugging Face cache либо
  сравнение выбранной ревизии по именам, размерам и доступным hash/etag.
  Одного похожего имени недостаточно.

### Запуск несохранённой конфигурации профиля

`POST /api/v1/profiles/start` принимает `ProfileData` в JSON и возвращает
`SuccessEnvelope[Instance]` с HTTP 202. Конфигурация валидируется той же схемой,
разрешает установку движка и запускается тем же process manager, что и
сохранённый профиль. YAML-файл не записывается; автозапуск приложения продолжает
использовать сохранённый файл. Логи и состояние привязываются к имени профиля.

В режиме редактирования существующего профиля кнопка запуска в шапке карточки
отправляет текущие данные формы. Редактор остаётся открытым, изменения не
сбрасываются. В обычном режиме используется прежний
`POST /api/v1/profiles/{profile_name}/start`, читающий YAML. Сохранение независимо
от запуска записывает профиль в файл. Обе команды проверяют конфликт имени
модели, порта и уже работающего процесса того же профиля. При работающем
процессе UI показывает кнопку остановки вместо запуска.


### Панель логов поверх страницы

Кнопка «Логи» в шапке открывает выезжающую справа панель поверх текущей
страницы без навигации. Используется общий `LogsViewer`: выбор источника,
обновление, скачивание, удаление, фильтр, нумерация строк, авторефреш,
автоскролл и подгрузка предыдущих строк остаются теми же. Панель закрывается
крестиком или Escape; состояние редактора профиля сохраняется.

При успешном принятии к запуску несохранённой конфигурации панель автоматически
открывается с источником соответствующего профиля. Ссылка из статуса профиля
также открывает эту панель. Источник выбирается локально и не меняет URL
основной страницы. Панель не блокирует взаимодействие с видимой частью
страницы; на небольшом экране занимает всю ширину.
