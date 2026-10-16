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
опрашивает `nd_get_operation`. После этого используются `nd_list_engines`,
`nd_list_installations`, `nd_validate_profile`, `nd_preview_profile`,
`nd_save_profile`, `nd_get_hardware`, `nd_list_instances` и `nd_start_profile`.

Проверить работу поднятой модели можно через `nd_list_chat_models` и `nd_chat`.
История передаётся агентом в `messages`; сервер не хранит отдельный chat_id.
`nd_chat` возвращает ответ, finish reason и доступные usage/timings. Для
проверки проблем запуска используются `nd_list_log_sources` и `nd_read_log`.

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
