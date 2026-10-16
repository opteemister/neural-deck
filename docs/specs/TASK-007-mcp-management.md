# TASK-007 — MCP для управления Neural Deck

Статус: **Согласовано, реализовано**.
Дата: 2026-10-16.
Реализация начата и завершена после явного утверждения пользователем этой спецификации.
Работа ведётся в текущем репозитории, в ветке `codex/mcp-management`, без worktree.

## 1. Цель и границы

Агент подключается к одному MCP endpoint Neural Deck и выполняет основные
действия текущего UI: работает с каталогом моделей, определениями и установками
движков, профилями, запущенными процессами, статистикой, метриками, логами,
маршрутами прокси, настройками и текстовым чатом.

Основной сценарий: найти и скачать модель, подготовить движок нужной версии,
при необходимости выполнить патчи, создать профиль с выбранными GPU,
проверить текущую нагрузку, запустить профиль и выполнить запрос к модели.

Первая версия работает без аутентификации в доверенном локальном контуре.
Бенчмарки, judge/evals, автоматическая оптимизация конфигураций и новый UI
для MCP в эту задачу не входят. MCP не должен зависеть от утверждения других
черновиков TASK-001/003/004/005: используется фактически реализованный service layer.

## 2. Архитектура и транспорт

- Один endpoint `/mcp` на backend origin интерфейса, транспорт Streamable HTTP.
- MCP размещается на порту управления приложением. Внешний `/v1`, `OPENAI_PORT`
  и `VITE_OPENAI_API_BASE_URL` сохраняют существующее назначение.
- Реализация использует поддерживаемый Python MCP SDK; совместимую версию SDK
  и протокола выбирают по актуальной официальной документации и фиксируют
  в зависимостях при реализации. Нельзя привязываться к непроверенной будущей
  редакции протокола из переписки.
- Обязательны initialize, tools/list, tools/call, согласование версии,
  корректные protocol errors и отмена запросов согласно выбранной версии SDK.
  Семантика HTTP, включая GET/SSE и session headers, следует выбранному SDK;
  прикладная логика не полагается на существование MCP-сессии.
- Проверка Origin и настройка разрешённых host/origin следуют требованиям
  выбранного транспорта. Это не добавляет пользовательскую аутентификацию.
- MCP вызывает общие сервисы приложения, а не делает HTTP-запросы к самому
  себе. Валидация и бизнес-правила UI/API остаются общими.
- Нет универсальной тулы произвольного SQL, shell или чтения файлов.
  Команды установки и maintenance ограничены существующей моделью управления
  движками и выполняются в её контексте, как в UI.
- Для каждого инструмента публикуются строгие input/output JSON Schema,
  краткое описание и корректные readOnly/destructive/idempotent annotations.
  Annotations не заменяют серверную проверку входных данных.

Исходные точки интеграции: `backend/app/api/management.py`, `profiles.py`,
`instances.py`, `metrics.py`, `logs.py`, `proxy_routes.py`, соответствующие
сервисы и схемы; для чата — текущий proxy service. Структура MCP-модулей
создаётся отдельно от HTTP management routes.

## 3. Компактные ответы

1. Результат данных передаётся в `structuredContent`. Текстовый блок содержит
   короткое описание результата, а не копию всего JSON. Совместимость целевых
   MCP-клиентов с этим форматом проверяется перед завершением реализации.
2. Списки содержат краткие записи; полная конфигурация доступна через get-тулу.
   В списках нет README, команд установки/запуска, environment, stdout/stderr,
   аватаров, raw metadata и временных рядов.
3. Значения имеют явные единицы: `_bytes`, `_ms`, `_percent`, `_tps`, `_watts`.
   Время — ISO 8601 UTC. Отсутствующее измерение — `null`, а не `0`.
4. Диагностические данные выдаются по запросу; ошибки не содержат traceback
   и содержимое `.env`. Конфигурации профилей/движков показывают их собственные
   сохранённые поля, необходимые для редактирования; глобальное окружение
   процесса к ним не подмешивается.
5. Запись возвращает ID, изменённые поля/результат и предупреждения, если есть.
   Полный список объектов после удаления, скачивания или сохранения не выдаётся.
6. Ответ не обрезается молча. Большие коллекции и тексты имеют продолжение.
   Контекст модели не урезается автоматически. Превышение поддерживаемых
   размеров запроса возвращает явную ошибку с ограничением источника.

## 4. Пагинация без общего предела

В таблицах ниже `Page` в параметрах означает `page_size?: integer > 0,
cursor?: string`. В ответе `Page<T>` означает:

```json
{
  "items": [],
  "page_size": 20,
  "has_more": true,
  "next_cursor": "opaque-cursor"
}
```

- `MCP_DEFAULT_PAGE_SIZE=20` — конфигурируемый default, не общий лимит записей.
  `page_size` можно менять при каждом вызове, включая продолжение по курсору.
- `MCP_MAX_PAGE_SIZE` — необязательный операторский предел одной страницы.
  По умолчанию не задан. При превышении — `page_size_exceeded` с полями
  `requested_page_size` и `max_page_size`; запрещено молча уменьшать страницу.
- Никакого ограничения числа страниц или общего числа доступных элементов.
  `has_more=false` эквивалентно `next_cursor=null` и означает конец выборки.
- `total_count` по умолчанию отсутствует. Пустая выборка — `items: []`.
- Сортировка детерминирована, с уникальным ключом при равенстве значений.
  Курсор связан с фильтрами, порядком и ресурсом; другой фильтр с прежним
  курсором даёт `invalid_cursor`. Некорректный курсор не запускает чтение заново.
- Для локальных изменяемых коллекций курсор использует снимок кратких записей
  на момент первой страницы. Добавления после снимка видны в новой выборке;
  удалённые элементы могут присутствовать в снимке, get вернёт not_found.
  TTL снимка задаётся `MCP_CURSOR_TTL_SECONDS=900`. Истечение/перезапуск
  сервера возвращает `cursor_expired` и возможность начать новую выборку.
- Для Hub сохраняется реальный upstream continuation и порядок источника.
  Если upstream не даёт консистентного снимка, это отмечается в поле
  `consistency: "live"` первой и последующих страниц. Нельзя изображать
  исчерпание каталога после первых 50 результатов: продолжение должно реально
  получать следующие upstream-страницы. Недоступное продолжение — ошибка,
  а не успешный конец. Полнота — в границах опубликованного каталога источника.
- Страницы для файлов, квантований, диагностик, исторических метрик,
  строк статистики и логов подчиняются тем же правилам. Скалярные снимки
  CPU/RAM/GPU и небольшой фиксированный список компонентов не разбиваются.

Существующие unpaginated API не доказывают наличие пагинации MCP. Адаптер
должен её реализовать, а upstream-сервисы при необходимости — расшириться
без поломки существующих HTTP-клиентов.

## 5. Общие структуры и ошибки

`InstanceSummary`: `id, source, profile_id, profile_name, engine_id,
model_name, state, pid, port, can_stop, observed_at`.

`OperationSummary`: `id, kind, state, target_id, created_at, started_at,
finished_at, progress`. `progress` — известные счётчики файлов/байтов или null;
процент не выдумывается при неизвестном общем размере.

`Metric`: `value, unit, state, source`; `reason` только при проблеме.
У текущих/исторических точек дополнительно `observed_at`.

Успешный запуск длительной работы возвращает `{operation_id,state}`.
`nd_get_operation` показывает её результат. `queued/running` не означают успех.
Состояния MCP операций: `queued|running|succeeded|failed|cancelled`.
Реальные ошибки/отмена существующих сервисов переводятся явно, без вывода
по произвольному тексту stderr. Если исходный service не отличает отмену,
его контракт расширяется совместимо. Ошибка отмены не превращается в успех.

Ошибки tool call: `isError=true`, данные
`{code,message,retryable,details?}`. Некорректные protocol-вызовы — protocol
errors SDK. Типовые коды: `not_found`, `validation_error`, `invalid_cursor`,
`cursor_expired`, `page_size_exceeded`, `conflict`, `upstream_unavailable`,
`operation_not_cancellable`, `unsupported_capability`, `version_mismatch`.
`details` содержит конкретное поле/ID/ограничение и не дублирует весь запрос.
Результаты пустого поиска и отсутствующих измерений не считаются ошибкой.

## 6. Каталог инструментов

Все поля параметров без `?` обязательны. `mode=create|update` обязателен для
save-тулов; создание не перезаписывает существующий объект, обновление
несуществующего объекта возвращает not_found. Это не неявный upsert.

### 6.1. Система и процессы

| Инструмент | Вход | Поля ответа |
|---|---|---|
| `nd_get_status` | нет | `overall: ok\|degraded\|error`, `components[{name,state,message?,checked_at}]` |
| `nd_get_hardware` | нет | `sampled_at`, `cpu{cores,usage_percent}`, `ram{used_bytes,total_bytes}`, `gpus[{uuid,index,name,memory_used_bytes,memory_total_bytes,usage_percent,temperature_celsius,fan_speed_percent,power_watts,power_limit_watts}]` |
| `nd_list_instances` | `profile_id?`, `state?`, Page | Page<InstanceSummary> |
| `nd_get_instance` | `instance_id` | InstanceSummary, `types`, `types_note`, `type_legend`, `api_protocol, api_base_url, ui_url, identity_verified` |
| `nd_stop_instance` | `instance_id` | `instance_id,state`; соблюдает существующие проверки идентичности процесса и can_stop |

Телеметрия не включает список instances и историю графиков. Нельзя объявлять
систему healthy только по факту работающего backend. Запуск процесса ещё
не означает readiness API: состояние ready определяется существующей проверкой.

### 6.2. Движки, определения и установки

Определение движка — YAML-шаблон, установка — конкретный executable/окружение.
ID определения, ID установки, версия схемы определения и версия пакета —
разные сущности. Поле `version: 1` определения не задаёт версию vLLM/llama.cpp.

| Инструмент | Вход | Поля ответа |
|---|---|---|
| `nd_list_engines` | Page | Page<{id,display_name,types,installation_kinds,installation_count}>, `types_note`, `type_legend` |
| `nd_list_engine_definitions` | Page | Page<{id,name,types}>, `issue_count`, `types_note`, `type_legend` |
| `nd_list_engine_definition_issues` | Page | Page<{file,code,message}> |
| `nd_get_engine_definition` | `engine_id` | Полный EngineDefinition, описанный ниже |
| `nd_save_engine_definition` | `mode`, `definition: EngineDefinition` | `engine_id,created,changed_fields[]` |
| `nd_delete_engine_definition` | `engine_id` | `engine_id,deleted`; существующие ограничения зависимостей не обходятся |
| `nd_list_installations` | `engine_id?`, Page | Page<{id,engine_id,kind,version,status,verified,root_path,executable_path,diagnostic?}> |
| `nd_install_engine` | `engine_id`, `installation: InstallationCreate`, `expected_version?` | `operation_id,state` |
| `nd_update_installation` | `installation_id` | `operation_id,state` |
| `nd_run_engine_maintenance` | `installation_id`, `command`, `expected_version?` | `operation_id,state` |
| `nd_delete_installation` | `installation_id` | `installation_id,deleted`; та же семантика файлов и зависимостей, что у UI |
| `nd_scan_engines` | нет | `operation_id,state`; повторный поиск установок как в UI |

`EngineDefinition` использует существующий строгий контракт:
`version,id,name,types,executable,command`, необязательные
`install_script,update_script,working_directory,environment,healthcheck,
api,ui_url,version_command`. SDK публикует эту схему полностью; объект
не объявляется как произвольный JSON. Встроенные шаблоны подчиняются тем же
ограничениям редактирования, что текущий service.

`InstallationCreate` — существующий discriminated/validated контракт:
generic `{target_path,run_install?,executable_path?,command?}` либо legacy
`{kind: python_venv,venv_name,command?}` / `{kind: binary,path,command?}`.
При генерации SDK schema варианты должны быть однозначны и проверяться теми
же моделями; generic не должен молча поглощать ошибочный legacy-ввод.

Точная версия задаётся явно pinned-командой установки или install_script
определения. Параметр `expected_version` — проверка результата через механизм
version detection после успешной установки/maintenance, а не универсальная
подстановка версии в неизвестный shell-script. При несовпадении — failed /
version_mismatch; если определить версию невозможно — явный verification error.
Точное сравнение нормализует только формат, согласованный для детектора;
нельзя считать совпадением произвольный substring. Произвольный `version`
без pinned-команды не должен создавать ложного обещания установки этой версии.
Патчи задаются maintenance-командой, её stdout/stderr доступны через логи.
Update использует update_script определения и сам не обещает pin на версию.

### 6.3. Операции

| Инструмент | Вход | Поля ответа |
|---|---|---|
| `nd_list_operations` | `kind?`, `state?`, Page | Page<OperationSummary> всех подсистем |
| `nd_get_operation` | `operation_id` | OperationSummary, `result?`, `error?`, `exit_code?`, `log_source_id?` |
| `nd_cancel_operation` | `operation_id` | `operation_id,state,cancel_requested`; терминальное состояние только после фактической отмены |

Общий registry/adaptor охватывает движки, скачивания, сканирования и новые
асинхронные MCP-задачи. Коллизии внутренних ID устраняются namespace mapping.
`result` — типизированный краткий результат операции: созданные installation/
model IDs, фактическая версия, счётчики сканирования. Ни raw stdout, ни полный
каталог пакетов по умолчанию не возвращаются. Уже завершённая операция не
перезапускается из-за повторного запроса статуса/отмены.

### 6.4. Профили

| Инструмент | Вход | Поля ответа |
|---|---|---|
| `nd_list_profiles` | `query?`, Page | Page<{profile_id,name,model_name,engine_id,installation_id,port,autostart,runtime_state}>, `issue_count` |
| `nd_list_profile_issues` | Page | Page<{file,code,message}> |
| `nd_get_profile` | `profile_id` | Полный ProfileData |
| `nd_save_profile` | `mode`, `profile: ProfileData`, `profile_id?` для update | `profile_id,name,created,changed_fields[],restart_required` |
| `nd_delete_profile` | `profile_id` | `profile_id,deleted` |
| `nd_validate_profile` | `profile: ProfileData` | `valid`, `issues[{field,code,message,severity}]` |
| `nd_preview_profile` | `profile: ProfileData` | `command,working_directory,installation_id` |
| `nd_format_profile_script` | `script` | `script,changed,reason?`, как текущий formatter UI |
| `nd_start_profile` | `profile_id` | InstanceSummary |

ProfileData — существующая файловая схема: `version,name,model_name,engine
{id,installation_id},command`, опциональные `profile_id,model{path,draft_path},
types,api,ui_url,port,environment,autostart,enable_metrics` и существующие три
поля цен за миллион токенов. Полная схема доступна в tools/list. Сохранённые
профили остаются в файлах; SQLite не становится источником конфигурации.
Профили без стабильного ID разрешаются/мигрируют существующим механизмом;
MCP не генерирует новый ID при каждом чтении.

Выбор GPU хранится в `environment.CUDA_VISIBLE_DEVICES`: UUID из
`nd_get_hardware`, несколько — через запятую. Отсутствие переменной означает
все доступные GPU, `-1` — без GPU; `CUDA_DEVICE_ORDER` согласуется с UI.
MCP не вводит второй независимый список GPU, который мог бы расходиться с env.

Validate выполняет schema и доступные preflight-проверки без запуска,
скачивания и изменения профиля: модель/установка доступны, порт не конфликтует,
GPU существуют. Статическая проверка не обещает, что модель точно поместится
в VRAM; наблюдаемая свободная память не является резервированием.
Preview разрешает шаблоны без запуска. Ошибки повреждённых YAML не скрываются
пустым успешным списком. Обновление активного профиля не перезапускает процесс
самостоятельно; `restart_required` означает расхождение с запущенной конфигурацией.

### 6.5. Модели

| Инструмент | Вход | Поля ответа |
|---|---|---|
| `nd_list_local_models` | `query?`, `format?`, `model_kind?`, Page | Page<{id,model_name,path,kind,model_kind,format,compatibility,size_bytes}>, `scanning,scan_error?` |
| `nd_get_local_model` | `model_id` | Краткая запись, `hf_match,group_count,variant_count,auxiliary_count` |
| `nd_list_local_model_contents` | `model_id`, `kind: groups\|variants\|auxiliary`, Page | Страница групп/файлов с `id?,path,format?,size_bytes,role?` |
| `nd_search_hub_models` | `mode: search\|trending`, `query?`, `group?`, Page | Page<{repo_id,description?,downloads?,likes?,used_storage?,pipeline_tag?}> |
| `nd_get_hub_model` | `repo_id`, `revision?` | `repo_id,revision,author?,description?,revision_date?,file_count?` |
| `nd_read_model_readme` | `repo_id`, `revision`, `cursor?`, `max_chars?` | `text,next_cursor,has_more,revision` |
| `nd_list_hub_model_contents` | `repo_id`, `revision`, `kind: files\|quantizations`, Page | files: Page<{path,size_bytes,hash_kind?,hash?}>; quantizations: Page<{repo_id,description?,used_storage?}> |
| `nd_download_model` | `repo_id`, `revision`, `files?`, `label?` | `operation_id,state` |
| `nd_scan_local_models` | `verify_hashes=false` | `operation_id,state` |
| `nd_check_model_updates` | `model_id?` | `operation_id,state`; завершение содержит `checked_count,updated_count` |
| `nd_list_model_updates` | `model_id?`, Page | Page<{model_id,state,local_revision?,remote_revision?,reason?}> |
| `nd_delete_local_model` | `model_id`, `group_id?` | `model_id,group_id?,deleted` |

`search` требует непустой query, `trending` использует group текущего UI.
Hub description не включает README. Чтение README имеет конфигурируемый
`MCP_DEFAULT_TEXT_CHARS=4000`, необязательный `MCP_MAX_TEXT_CHARS`; доступен
весь текст через продолжение, без потери символов на стыках Unicode.
Списки локальных variants и вспомогательных блоков также не вкладываются
целиком в get. В случаях, когда group нельзя выбрать без её дочерних файлов,
`nd_list_local_model_contents` принимает дополнительно `group_id?`.

При первом чтении Hub разрешает revision в конкретный commit ID. Следующие
страницы и скачивание используют этот ID, чтобы ветка не изменилась между
выбором файлов и скачиванием. Отсутствующий files означает полный набор по
действующим правилам скачивания сервиса; пустой массив — validation_error.
Выбранные файлы, shard-комплекты и вспомогательные компоненты проверяются
существующим сервисом; недостаточная полнота набора явно диагностируется.
Гейтированные/недоступные репозитории дают объяснимую upstream-ошибку;
токены доступа не возвращаются в ответах.

Проверка обновлений сохраняет различия unknown / update_available / diverged
и семантику сравнимых хешей текущего сервиса. Она ничего не скачивает и
не перезаписывает автоматически. Хеши и повторное сканирование — отдельные
явные действия. Несопоставимые Git blob и LFS хеши не объявляются равными.

### 6.6. Статистика и метрики

| Инструмент | Вход | Поля ответа |
|---|---|---|
| `nd_get_statistics` | `period=month`, `from?`, `to?`, `profile_id?`, `model_name?` | `period{from,to,timezone}`, `requests`, `input_tokens{known_total,unknown_requests}`, `output_tokens{known_total,unknown_requests}`, `cost{known_total_usd,unknown_requests,state}` |
| `nd_list_statistics_rows` | те же фильтры, `group_by: profile\|client\|day\|hour`, Page | Page<{key,profile_id?,name?,requests,input_tokens,output_tokens,cost}>, `period` |
| `nd_get_profile_summary` | `profile_id` | `period`, `performance{prefill_tps,decode_tps}`, `memory{disk_bytes,vram_current_bytes,vram_last_bytes,vram_avg_bytes,vram_peak_bytes}`, `concurrency{optimal,limit,observed_max,benchmark_state}`, `load{percent}`, `endpoint{port,conflict,conflict_with,state}` |
| `nd_get_instance_metrics` | `instance_id`, `mode: current\|history\|peaks`, `from?`, `to?`, Page для history | `instance_id`, `observed_at?`, `items[{name,value,unit,state,source,observed_at}]`, пагинация для history |

Period: `week|month|quarter|half_year|year|custom`; custom требует from/to,
from < to. Фактические границы периода всегда включаются в ответ; интервалы
полуоткрытые [from,to), календарная зона явно UTC. Profile summary сохраняет
текущее окно текущего UTC-дня, не выдаёт его за скользящие 24 часа.
Поля summary-измерений имеют форму Metric. Наблюдаемый максимум конкурентности
не выдаётся за измеренный benchmark-оптимум. Cached tokens — часть input,
не прибавляются повторно. Групповые суммы сохраняют known/unknown.
Неизвестные деньги/токены не заменяются нулём. Агрегации не включают полный
журнал запросов. Current metrics не инициируют генерацию в модели.

### 6.7. Логи

| Инструмент | Вход | Поля ответа |
|---|---|---|
| `nd_list_log_sources` | `kind?`, `profile_id?`, Page | Page<{id,kind,label,profile_name?,file_count,updated_at}> |
| `nd_read_log` | `source_id`, `direction: tail\|forward=tail`, Page | `source_id,lines[{number,text}],has_more,next_cursor,page_size,snapshot_at` |
| `nd_export_log` | `source_id`, `latest_only=false` | `download_url,filename,size_bytes,expires_at` |
| `nd_delete_log_source` | `source_id` | `source_id,deleted_files` |

Default строк задаётся `MCP_DEFAULT_LOG_LINES=200`; caller задаёт page_size.
`tail` начинает с последних строк, продолжение идёт к более старым блокам,
строки внутри блока идут по возрастанию номеров. `forward` идёт от начала.
Cursor привязан к снимку/версии источника, не перескакивает между ротациями.
Новые строки получают новой выборкой. Одна длинная строка не обрезается:
при превышении конфигурируемого размера текстового блока применяется
продолжение строки с `line_number,char_offset,text,line_complete` вместо lines;
это отдельный документированный output-variant со своим next_cursor.

Экспорт формирует файл-снимок текущего набора логов по существующим правилам
UI, не помещая содержимое файла в MCP-ответ. download_url — абсолютный
достижимый URL backend, не localhost сервера; base URL задаётся конфигом
`MCP_PUBLIC_BASE_URL` либо берётся из доверенной конфигурации приложения.
URL не строится из произвольного Host. Срок хранения артефакта задаётся
`MCP_EXPORT_TTL_SECONDS=3600`, просроченный URL даёт 410. Файл отдаётся
потоково с Content-Disposition. HTTP-клиент агента может скачать его целиком;
агент, имеющий только MCP, читает всё через nd_read_log. Экспорт доступен
без аутентификации на той же согласованной границе локального контура.
Не допускается объединение всех логов в память для выдачи/download.

### 6.8. Чат

| Инструмент | Вход | Поля ответа |
|---|---|---|
| `nd_list_chat_models` | Page | Page<{id,engine_id?,types,context_length,reasoning_capabilities}>, `types_note`, `type_legend` |
| `nd_chat` | `model`, `messages[{role: system\|user\|assistant,content: string}]`, `reasoning_effort=auto`, `max_output_tokens?` | `model,message{role: assistant,content},finish_reason,usage?,timings?` |

В списке — доступные текстовые модели и алиасы текущего proxy registry,
те же модельные ID, что UI/`/v1/models`. Выбор не включает скрытый запуск
остановленного профиля. Неизвестная модель/недоступный маршрут — явная ошибка.
Reasoning принимает auto/off и поддерживаемые конкретной моделью уровни;
MCP использует тот же capability mapping к reasoning_effort /
chat_template_kwargs, что UI. Неподдерживаемый режим не заменяется молча.

Один вызов — один реальный ответ модели. История передаётся вызывающим агентом,
conversation/chat_id на сервере не хранится. Содержимое ответа не урезается.
`max_output_tokens`, если передан, переводится в поддерживаемый backend
параметр; неподдерживаемый параметр даёт явную ошибку, а не игнорируется.
Если не передан — defaults движка, без добавочного лимита MCP. При length
возвращается соответствующий finish_reason. Превышение контекста — ошибка;
удалять старые messages без решения агента запрещено.

usage: `input_tokens,output_tokens,total_tokens,cached_input_tokens?,
reasoning_tokens?`, только полученные от движка значения. timings:
`total_ms,first_token_ms?,decode_tps?,decode_tps_source?`. Значения скорости
движка и оценки по времени потока различаются через source. MCP adapter
читает внутренний stream для измерений и отмены, результат tool call
возвращает целым сообщением; нестандартные токеновые MCP-уведомления не нужны.
Reasoning-текст в ответ по умолчанию не включается; `include_reasoning=false`
может быть явно включён, тогда `message.reasoning` содержит выданный движком
текст. Отмена MCP-запроса прекращает upstream-генерацию по возможности
текущего proxy service; завершившийся запрос не выдаётся за отменённый.
При обрыве потока запрещено незаметно повторять частично выполненный запрос.

Отдельного smoke_test нет. nd_chat пригоден для проверки реального инференса
и продолжения разговора; серии измерений и бенчмарки не добавляются.

### 6.9. Прокси и настройки

| Инструмент | Вход | Поля ответа |
|---|---|---|
| `nd_list_proxy_routes` | Page | Page<{name,kind,available,candidate_profile_ids,candidate_count,diagnostic_count}> |
| `nd_list_proxy_candidates` | `route_name`, Page | Page<{profile_id,profile_name,instance_id,engine_id,upstream_model,port,state,available,reason?}> |
| `nd_read_proxy_diagnostics` | `route_name`, Page | Page<{message}> |
| `nd_set_proxy_route_order` | `route_name`, `profile_ids[]` | `route_name,profile_ids` |
| `nd_save_proxy_alias` | `mode`, `name`, `profile_ids[]`, `previous_name?` для rename | `name,profile_ids,created` |
| `nd_delete_proxy_alias` | `name` | `name,deleted` |
| `nd_get_settings` | нет | `data_root,models_root,log_root,extra_venv_roots,host,port,openai_port` |
| `nd_update_settings` | `models_root` | `models_root,restart_required` |

Остальные settings read-only, как в текущем API. Изменение models_root
использует текущие проверки путей/сохранение настроек. Оно не перемещает
файлы автоматически и не перезапускает профили. Внешние auto-routes нельзя
удалить через alias-delete. Порядок кандидатов значим и не сортируется MCP.
Если список profile_ids/конфликтов внутри одной записи становится велик,
он также выносится в страничный detail; сервер не отрезает хвост списка.

## 7. Сценарии работы агента

### Подготовить новую модель

1. nd_get_status → nd_get_hardware → nd_list_instances: убедиться в состоянии
   сервисов, выбрать UUID GPU и увидеть уже запущенные процессы.
2. nd_search_hub_models → nd_get_hub_model → nd_list_hub_model_contents:
   выбрать репозиторий, фиксированную revision и комплект файлов.
3. nd_download_model → nd_get_operation до завершения. При failed — прочитать
   связанный источник логов; не начинать новое скачивание без выяснения статуса.
4. nd_list_engines / nd_list_installations. При необходимости создать definition,
   установить pinned-версию, выполнить maintenance-патч. Дождаться завершения,
   проверить фактическую версию/статус установки.
5. nd_list_local_models / nd_list_local_model_contents: получить точный путь
   загруженного артефакта. Не конструировать путь по имени Hub-репозитория.
6. Подготовить ProfileData с installation_id, model path, командой и GPU UUID.
   nd_validate_profile → nd_preview_profile → nd_save_profile(create).
7. Повторно nd_get_hardware / nd_list_instances перед запуском. Не останавливать
   чужие процессы автоматически ради освобождения памяти.
8. nd_start_profile → nd_get_instance до ready/error. Ошибка — nd_read_log.
9. nd_list_chat_models → nd_chat выбранной моделью. Проверить содержимое ответа,
   finish_reason, usage/timings. При необходимости — nd_get_instance_metrics.

### Разобрать ошибку запуска

Status → конкретный instance/profile → небольшой tail лога → предыдущие
страницы при необходимости. Полный экспорт нужен для передачи/сохранения,
а не для автоматического помещения мегабайт текста в контекст.

### Посмотреть производительность и нагрузку

Hardware даёт текущую нагрузку; instance metrics — конкретный процесс;
profile summary — агрегаты с их периодом/состоянием; statistics — запросы,
токены и стоимость за выбранный период. Эти данные не подменяют бенчмарк.

### Продолжить разговор

Первый nd_chat содержит user message. Следующий содержит предыдущие user/
assistant messages и новую реплику. Можно явно выбрать другую доступную модель.
Reasoning возвращается только если агент запросил его для своей задачи.

## 8. Конкурентность, повторные вызовы и ресурсы

- Активный профиль не запускается второй раз; ошибка conflict содержит
  существующий instance_id. Проверки порта/installation не обходятся MCP.
- Параллельные update/maintenance одной установки сериализуются либо дают
  conflict с operation_id. То же для несовместимых скачиваний в одну цель.
- MCP не выполняет автоматические повторы мутирующих действий после таймаута.
  Агент сначала проверяет operations/instances. Повтор tools/call с новым
  request ID сам по себе не является idempotency key.
- Длительная операция живёт независимо от обрыва HTTP-соединения, если уже
  выдан её operation_id. Явная отмена — nd_cancel_operation.
- Chat живёт до завершения/отмены своего запроса. Его не сохраняют как
  фоновую бесконечную operation.
- Снимки, экспортные артефакты и буферы имеют TTL/очистку; число активных
  снимков и память контролируются конфигурируемыми ресурсными квотами.
  Отказ по квоте явно сообщает retryable resource_exhausted, не выдаёт конец
  пагинации. Квоты не ограничивают число прочитанных элементов выборки.
- Все пути/ID проходят существующие проверки. Полные команды/environment
  не записываются в access logs MCP; логируются имя тулы, ID цели, длительность,
  результат и request ID. Текст чата не добавляется в отдельный MCP-журнал.

## 9. План реализации после утверждения

1. Основа: SDK/dependency pin, mounting /mcp, lifespan, schemas, errors,
   annotations, конфигурация page sizes/TTL/base URL, cursor service.
2. Чтение: system/status, instances, engines/definitions/installations,
   profiles и диагностики, local/Hub catalog с реальным продолжением.
3. Управление: registry операций, install/update/maintenance/version verification,
   download/scan/check-updates, profile validate/preview/save/start/stop,
   удаление с сохранением действующих правил приложения.
4. Наблюдаемость: компактные summary/statistics, страницы history и логов,
   экспорты файлов без загрузки их целиком в память.
5. Chat: доступные модели, capabilities, reasoning, usage/timings,
   cancellation и отсутствие автоматического retry частичного ответа.
6. UI-паритет: proxy aliases/order/candidates/diagnostics, settings и formatter.
7. Интеграционные проверки, документация подключения и примеры agent workflows.
   Документировать известные ограничения upstream и результаты измерения
   объёма ответов. Отчёт — `docs/results/TASK-007-mcp-management.md`.

Все этапы входят в эту задачу, а не являются необязательным будущим расширением.
Пользователь может уточнить объём при утверждении черновика. Необходимые
изменения сервисов выполняются в той же ветке; worktree не создаётся.
Удалённый сервер и deployment требуют отдельного поручения. В рамках текущей
подготовки спецификации команды установки и runtime-тесты не выполняются.

## 10. Приёмка и проверки реализации

1. Реальный MCP-клиент подключается по /mcp, получает tools/list с валидными
   схемами, вызывает чтение/запись; версия протокола согласуется SDK.
2. Коллекции из 0, 1, 20, 21 и нескольких сотен записей читаются полностью
   без пропусков/дубликатов на снимке. Page size меняется между страницами;
   default меняется конфигом, элементы за первой страницей доступны.
3. Invalid/expired/несовместимый с фильтром cursor даёт явную ошибку;
   max_page_size не обрезает ответ; upstream Hub continuation реально проходит
   границу старого лимита 50. Изменение локальной коллекции не меняет снимок.
4. Каждая list/get/mutation-тулa имеет contract test с output schema;
   списки не содержат полных команд, env, README, logs или raw API-обёрток.
   Сохранение/удаление не возвращает весь каталог. Измеряется размер JSON
   representative ответов на 1/20/100 элементов; бюджет определяется полями
   и размером страницы, не скрытым отрезанием содержимого.
5. Профиль создаётся в файле, читается, изменяется, валидируется и удаляется.
   UUID нескольких GPU сохраняются без замены индексами. Проверяются
   конфликт порта, неизвестная установка/GPU, повреждённый YAML, restart_required.
6. Install/download/maintenance дают operation_id; queued/running/success/
   failure/cancelled и отмена между завершением/опросом отображаются корректно.
   Операции видны из всех подсистем. Version mismatch не помечается успехом.
7. Логи больше одной страницы, ротация, длинные Unicode-строки и полная
   выгрузка читаются без потери данных. Экспорт скачивается с другого хоста
   локальной сети; после TTL — 410; нет произвольного доступа к путям.
8. Chat вызывает выбранную модель и продолжает историю. Проверяются aliases,
   недоступная модель, unsupported reasoning, отказ контекста, length,
   cancellation и оборванный stream без скрытого повторного запроса.
   Нет дублирования reasoning-текста по умолчанию и invented usage/speed.
9. Известные/неизвестные токены, стоимость, cached tokens, UTC-границы и
   состояние недоступных метрик сохраняют смысл исходного service layer.
10. Proxy order/aliases и editable models_root работают по правилам UI.
    /v1 и внутренний chat proxy сохраняют существующую адресацию и поведение.
11. Проверки выполняются в принятой Docker-среде; meaningful unit/contract/
    integration tests включают негативные сценарии и границы сервисов.
    Реальная GPU-установка/генерация проводится только в разрешённой среде;
    mock-тесты не выдаются за такую проверку. Выполненные/невыполненные
    проверки и факт deployment отдельно фиксируются в отчёте.

## 11. Документация подключения

В реализации добавить `docs/mcp.md`: URL /mcp, конфигурация MCP-клиента без
credentials, список настроек, краткий каталог тулов, пагинация, handling ошибок,
пример установки модели/профиля и многошагового чата. Конкретный формат client
config сверить с выбранным клиентом, а не придумывать универсальный JSON.
Документировать, что endpoint даёт возможности изменения системы в доверенном
контуре. До утверждения этот файл спецификации — источник предложенного
контракта; runtime-документация не должна объявлять MCP уже доступным.
