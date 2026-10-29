# MCP Neural Deck

Документ описывает MCP после реализации TASK-007. Endpoint доступен на том же
origin, что и management API:

```text
http://<host>:<PORT>/mcp
```

Аутентификация в первой версии не используется. Endpoint предназначен для
доверенного локального контура. Внешний OpenAI-совместимый API продолжает
работать через отдельный `OPENAI_PORT` и `/v1`.

## Подключение клиента

Клиент должен поддерживать MCP Streamable HTTP. В конфигурации указывается
полный URL `/mcp` (также принимается `/mcp/`); credentials не требуются. Точный формат JSON-конфигурации
зависит от клиента, поэтому этот документ не задаёт один универсальный файл.

Некоторые MCP-клиенты перед подключением проверяют
`/mcp/.well-known/openid-configuration`. В первой версии аутентификация
отключена, поэтому ответ `404` на этот discovery-запрос ожидаем и не означает
ошибку MCP endpoint.

После `initialize` клиент вызывает `tools/list`. Изменяющие инструменты имеют
описание и MCP annotations; сервер всё равно проверяет каждое действие через
общие сервисы Neural Deck.

## Типы движков

В ответах `nd_list_engines`, `nd_list_engine_definitions` и
`nd_get_engine_definition` поле `types` описывает основную задачу и результат
движка, а не все модальности входа. Рядом возвращается компактный `type_legend`:

| Тип | Смысл |
| --- | --- |
| `text` | Генерация текста; мультимодальная модель может принимать изображения или видео, но выдаёт текст. |
| `tts` | Текст → аудио/речь. |
| `asr` | Аудио → текст. |
| `embeddings` | Вход → embedding-векторы. |
| `image` | Генерация или обработка изображений. |
| `video` | Генерация или обработка видео. |
| `decision` | Вход и вопросы → структурированные ответы решения. |

Поэтому движок с `types: [text]` выбирается для текстового результата, даже
если конкретная модель умеет принимать изображения или видео.

## Пагинация

Для list-инструментов доступны `page_size` и непрозрачный `cursor`:

```json
{
  "items": [],
  "page_size": 20,
  "has_more": true,
  "next_cursor": "..."
}
```

`20` — значение `MCP_DEFAULT_PAGE_SIZE`, используемое только при отсутствии
параметра `page_size`. Общего лимита числа элементов нет: агент продолжает
передавать `next_cursor`, пока он не станет `null`. Значение страницы можно
изменять между вызовами.

Операторский `MCP_MAX_PAGE_SIZE` ограничивает только размер одного ответа и
по умолчанию не задан. При превышении сервер возвращает `page_size_exceeded`,
а не обрезает результат молча. Ошибочный или устаревший cursor возвращается
как `invalid_cursor` или `cursor_expired`.

## Основные сценарии

Для подготовки модели агент обычно вызывает `nd_search_hub_models`,
`nd_get_hub_model`, `nd_list_hub_model_contents`, `nd_download_model`, затем
опрашивает `nd_get_operation`. После этого он выбирает существующий
`engine definition` через `nd_list_engine_definitions` и конкретную
`installation` через `nd_list_installations`.

`nd_install_engine` создаёт новую installation под существующим definition.
Для другой версии или ветки уже известного движка создаётся новая installation
с тем же `engine_id`; новое definition нужно только для другого шаблона движка,
команды или capability. `nd_save_engine_definition` не устанавливает движок.
После выбора installation используются `nd_validate_profile`,
`nd_preview_profile`, `nd_save_profile`, `nd_get_hardware`, `nd_list_instances`
и `nd_start_profile`.

Короткое правило workflow:

1. `engine definition` — шаблон движка: типы, install/run-команды и API/UI.
2. `installation` — конкретная установленная версия/ветка этого definition.
3. `profile` — конфигурация запуска конкретной модели на выбранной installation
   с GPU, environment, портом и автозапуском.

## Чат через MCP

`nd_chat_list` возвращает сохранённые чаты с ID, названием, моделью и датой,
как правая панель в UI. `nd_chat_delete` принимает `conversation_id` и удаляет
историю и загруженные вложения этого диалога; выполняемый запрос сначала
останавливается. Общие файлы ComfyUI не удаляются.

`nd_list_chat_models` возвращает тот же каталог поддерживаемых чатовых моделей,
что UI: текст/Vision, OCR, изображения и видео, включая алиасы ComfyUI.
`nd_chat_send`, `nd_chat_get`, `nd_chat_stop` используют общий `ChatService` и схему `ChatTurnRequest` HTTP API
`/api/v1/chat`. Поддерживаются PDF, изображения, reasoning, настройки генерации
и внутренние инструменты модели. Разделение ответственности закреплено в
[правилах чата и MCP](development/chat-and-mcp.md).

Отправка сообщения через `nd_chat_send`:

```json
{
  "conversation_id": "my-chat-unique-id",
  "request": {
    "id": "my-turn-unique-id",
    "model": "main-llm-model",
    "prompt": "Создай изображение котёнка",
    "content": "Создай изображение котёнка"
  }
}
```

`model` — выбранная модель чата. LLM может вызвать внутренний инструмент
генерации, а при выборе image/video-алиаса запрос выполняется напрямую через
настроенный workflow. Выбор операции находится в общем backend-сервисе.
Настройки передаются в `request.media_defaults` по имени алиаса, например
`{"qwen-image-turbo":{"size":"1024x1024","background":"opaque"}}` либо
`{"video-alias":{"size":"1280x720","seconds":5}}`. Reasoning передаётся
в `request.reasoning` стандартным полем `reasoning_effort`.

Для PDF `content` содержит `{"type":"file","file":{"filename":"document.pdf",
"file_data":"data:application/pdf;base64,..."}}`; для изображения —
`{"type":"image_url","image_url":{"url":"data:image/png;base64,..."}}`.
Можно добавить текстовую часть `{"type":"text","text":"Прочитай документ"}`.
Это тот же формат вложений, что отправляет UI. Пути к локальным файлам клиента
не являются путями на сервере и вместо содержимого не принимаются.

Ответ содержит `id`, `busy` и `turns`, как HTTP API. При `busy: true` вызывайте `nd_chat_get`:

```json
{"conversation_id":"my-chat-unique-id"}
```

Повторяйте чтение до `busy: false`. Итоговый `turn.state` различает `complete`,
`error`, `cancelled`; ошибка хранится в `turn.error`. `results` содержит медиа
и распознанный текст, `answer` — ответ модели, `reasoning` — её рассуждение,
`usage`/`timings` — доступную статистику. Ссылки `/api/...` разрешаются относительно
origin Neural Deck, к которому подключён MCP-клиент. Этот же диалог доступен
в UI по `/chat?chat=<id>&model=<model>`.

Для продолжения отправьте новый `request.id` с прежним `conversation_id`.
При повторе потерянного `nd_chat_send` сохраните оба исходных идентификатора — новая
генерация не запускается. Без `conversation_id` новый диалог получает
`request.id`, поэтому повтор той же отправки также идемпотентен. Остановка через `nd_chat_stop`:

```json
{"conversation_id":"my-chat-unique-id"}
```

Долгую генерацию не нужно держать внутри одного MCP-вызова. Закрытие клиента
не останавливает задачу. `nd_chat_get`/`nd_chat_stop` принимают `conversation_id` без `request`.
Прежний `nd_chat` заменён тремя явно названными инструментами общего чата; клиенту нужно
обновить каталог инструментов. Для прямого OpenAI-вызова сохраняется `/v1`.

Для проверки проблем запуска используются `nd_list_log_sources` и `nd_read_log`.

Длительные скачивания, установки и сканирования возвращают `operation_id`.
Состояние нужно проверять через `nd_get_operation`; `queued` и `running` не
означают успешное завершение.

`nd_export_log` возвращает абсолютный URL и `expires_at`. Срок действия задаётся
`MCP_EXPORT_TTL_SECONDS` (по умолчанию 3600 секунд); после него endpoint отдаёт
HTTP 410.

## Ответы и ошибки

Каждый вызов возвращает компактный `structuredContent` с `data` или `error`.
Текстовое MCP-содержимое содержит тот же JSON для клиентов без поддержки
structured output. В списках нет README, полных команд запуска, environment,
stdout/stderr или больших временных рядов. Подробный ресурс читается отдельной
get/read тулой.

Типовые коды ошибок: `not_found`, `validation_error`, `invalid_cursor`,
`page_size_exceeded`, `conflict`, `upstream_unavailable`,
`unsupported_operation`, `operation_not_cancellable`. Ошибка содержит короткое
описание, `retryable` и нужные детали без traceback и секретов.
