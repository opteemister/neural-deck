# TASK-007 — результат реализации MCP

Дата: 2026-10-16.
Ветка: `codex/mcp-management`.

## Реализовано

- Streamable HTTP endpoint `/mcp` на management origin без аутентификации в
  доверенном локальном контуре.
- Каталог из legacy-инструментов и management-инструментов Neural Deck для
  системы, железа, экземпляров, движков, установок, операций, профилей,
  локальных/Hugging Face моделей, статистики, метрик, логов, прокси, настроек
  и текстового чата.
- Единый компактный envelope `data`/`error`, MCP `structuredContent` и
  совместимый текстовый JSON.
- Настраиваемые `MCP_DEFAULT_PAGE_SIZE`, `MCP_MAX_PAGE_SIZE`, TTL и opaque
  cursor-снимки с проверкой контекста фильтров и явными ошибками.
- Типизированные схемы `EngineDefinition`, `InstallationCreate` и `ProfileData`
  в `tools/list`; изменяющие инструменты имеют annotations.
- Длительные сканирования/установки/скачивания возвращают operation ID;
  состояние читается через `nd_get_operation`.
- Лог-выгрузка возвращает абсолютный URL с `expires_at`; после TTL download
  endpoint отвечает HTTP 410.

## Проверки

Успешно:

- `ruff check` для затронутых backend-модулей;
- `python -m compileall` для MCP и entrypoint;
- MCP contract tests: `3 passed`;
- MCP/logs/legacy envelope tests: `7 passed`;
- ручной Streamable HTTP smoke: `initialize`, `tools/list` (65 инструментов в
  полном приложении), `tools/call nd_get_status` — HTTP 200.

Полный текущий набор backend-тестов запускается, но содержит базовые падения,
не связанные с MCP (в частности, тестовые окружения с отключённым OpenAI
listener, фикстуры старого proxy registry и тесты с подменённой миграцией без
таблиц request journal). Они сохранены отдельно от MCP-контрактных проверок.

