# TASK-004 — исследование маршрутизации прокси и алиасов

Дата: 2026-10-04
Статус: исследование для обсуждения; решения и реализация не согласованы

## Review 2026-10-04 — решено review

Ниже зафиксированы рекомендации для повторного чтения, а не утверждённые
продуктовые решения. Рекомендуемый source auto-group — сохранённый
`ProfileData.model_name`; engine `GET /v1/models` используется для проверки и
mapping upstream id каждого кандидата. Текущий duplicate-name guard следует
убрать только после отдельного согласования, сохранив проверку конфликтов
портов. Если engine публикует несколько ids, mapping должен быть явным: первый
id нельзя выбирать молча.

Границы review — существующие внешние `/v1/models`, `/v1/chat/completions`,
`/v1/embeddings` и внутренние `/api/proxy/models`, `/api/proxy/chat/completions`.
`/v1/completions` и `/v1/images` в текущем router отсутствуют и этим черновиком
не добавляются. Auto route и alias публикуются только при хотя бы одном
кандидате с verified identity, подтверждённым портом и свежей непустой model
metadata; `stale` остаётся диагностическим состоянием, но не маршрутом. При
любом refresh внешний unmanaged model не удаляется молча: `profile_name=NULL`
сохраняется как external candidate или получает явную collision diagnostic.

Рекомендуется стабильный `profile_id`/revision для сохранённых ссылок; rename
меняет display name, а delete оставляет dangling reference с диагностикой до
явной очистки. PID и process start — только runtime identity, PID сам по себе
не является ключом. Начальный порядок детерминирован по stable id (для legacy —
по имени), далее сохраняется явно; новый профиль добавляется в конец.

Для ответа public route имя должно переписываться в JSON и SSE `model`, если
поле присутствует; `[DONE]` не меняется. Журнал сохраняет requested public
name, фактический served profile/instance и upstream id. Консервативный retry
рекомендуется только при доказанном отказе до принятия тела; после возможной
отправки запрос завершается `unknown`, чтобы не обещать отсутствие дубля. После
upstream headers failover запрещён даже если клиент ещё не получил byte; после
client content нельзя сшивать потоки. Cancellation закрывает upstream и не
переключает профиль. На logical request действует общий deadline, каждая
попытка ограничена остатком бюджета. Availability-oriented retry можно
предложить отдельно как opt-in с явным duplicate-work risk.

До архитектурного обсуждения оставались product choices: grammar/collisions и
error 404/503, правило
скрытия auto route при нулевых кандидатах, точный allowlist availability retry,
capability compatibility, разрешение alias на external profile, ручное
редактирование route config и отображение direct stream error.

## Решения пользователя и технические дефолты 2026-10-04

Пользователь подтвердил, что auto-route и alias скрываются из `/v1/models`,
если ни один кандидат не доступен. Внутренний экран маршрутизации продолжает
показывать такой route и причины недоступности.

Остальные вопросы, не требующие отдельного продуктового выбора, закрываются
следующими дефолтами:

- TTL проверки model metadata сохраняется существующий — 10 минут; состояние
  процесса проверяется при каждом обновлении списка;
- alias id использует ASCII-грамматику `[A-Za-z0-9][A-Za-z0-9._-]{0,127}`;
  точные коллизии с profile model name, auto-route и alias блокируют сохранение
  и диагностируются inline в UI и повторно на backend;
- alias может ссылаться только на managed profiles; unmanaged external models
  не теряются из каталога, но в сохранённые маршруты не добавляются;
- неизвестный route отвечает `404 model_not_found`, известный route без
  доступных кандидатов — `503 route_unavailable`;
- failover выполняется по порядку при техническом отказе до upstream headers;
  ошибки входного запроса и уже начавшийся ответ не переключаются, streaming
  не сшивается;
- строгая capability-матрица не вводится: mixed managed profiles допустимы,
  известные расхождения отображаются предупреждением, а upstream остаётся
  источником фактического ответа;
- route config хранится отдельно в YAML под `data_root/config` с версией схемы
  и atomic write; ручная правка поддерживается, а последняя сохранённая запись
  заменяет предыдущую (last-write-wins).

Таким образом, из списка ниже остаётся один архитектурный стык: правила
передачи alias-attribution к фактически вызванному профилю в TASK-003 и TASK-005.

## Область и метод

Проверен локальный checkout `/Users/opteemist/Documents/MyProjects/llm-studio`.
Прочитаны `AGENTS.md`, `DEVELOPMENT_RULES.md`,
`docs/development/frontend.md`, `docs/specs/2026-09-29-design-system.md`,
`docs/tasks/TASK-004-proxy-routing-aliases.md`, связанные TASK-001/003/005,
`docs/openai-endpoint.md` и `docs/specs/2026-09-29-version-zero-contracts.md`.
Проверены текущие backend/frontend исходники, схемы и proxy-тесты. Приложение,
Docker, SQLite, движки, серверы и runtime API не запускались; выводы относятся
к исходному коду в локальном checkout. Незакоммиченные изменения, которые были
до этой работы, не интерпретировались как результат исследования и не менялись.

## Что существует сейчас

### Профили, экземпляры и обнаружение

- `backend/app/schemas/profiles.py` (`ProfileData`) хранит `name`, публичное
  `model_name`, engine/installation, путь модели, команду, environment,
  `autostart` и `enable_metrics`. Настроек групп, маршрутов или алиасов нет.
- `backend/app/schemas/instances.py` (`Instance`) даёт `id`, `source`,
  `profile_name`, `engine_id`, PID и `process_start_time_ns`,
  `identity_verified`, `port`, `model_name`, `state` и `observed_at`.
  У внешнего процесса `profile_name` может быть `NULL`.
- `backend/app/process/discovery.py` подтверждает внешний процесс по командной
  строке, находит порт и при включённом probe читает `GET
  http://127.0.0.1:{port}/v1/models`. Только непустые строковые `data[].id`
  становятся кандидатами; отсутствие identity запрещает маршрутизацию.
- `backend/app/process/manager.py:189-220` сейчас запрещает одновременно
  запустить два managed-профиля с одинаковым `model_name` (`model_name_conflict`).
  Это расходится с требованием TASK-004 и должно быть отдельным изменением
  после утверждения спецификации; совпадение порта проверяется отдельно
  (`manager.py:229-238`) и относится к TASK-001.
- `backend/app/process/runs.py`/`model_runs` — снимок только текущих managed
  запусков, ключ PID + время старта; это не история маршрутизации и не журнал
  start/stop.

### Реестр моделей и фактическое ограничение

`backend/app/process/registry.py` содержит volatile `ModelRegistry` с одним
словарём `public id -> RegisteredModel`. Для managed экземпляра public id
берётся из `instance.model_name`, а upstream id — из ответа engine. При
совпадении публичного имени у другого экземпляра текущий код искусственно
делает имя `instance_id/upstream`; он не создаёт группу и не хранит порядок.
External экземпляры публикуют upstream ids отдельно. Реестр очищается при
завершении процесса и заново наполняется наблюдением.

Рекомендуемая миграция строит auto route по `ProfileData.model_name`, а не
переименовывает конфликтующие записи в `instance_id/upstream`. При этом
unmanaged external ids сохраняются в model catalog с `profile_name=NULL`; их
нельзя тихо потерять из-за появления configured route. Стабильный route config
должен быть отделён от volatile registry.

Из этого следуют ограничения текущих данных:

1. профильное имя можно использовать как ключ кандидата только для managed;
   для external нет надёжной связи с YAML-профилем;
2. один managed-профиль может вернуть несколько engine ids, но текущая запись
   сохраняет одну public name и подставляет выбранный upstream id;
3. не хранится признак «почему кандидат выбран», номер попытки, время ответа,
   неизвестный исход запроса или версия конфигурации маршрута;
4. `instance_id` с PID/start защищает от подмены процесса, но не заменяет
   устойчивую запись настроенного маршрута.

### Список моделей

`backend/app/proxy/service.py:77-137` при каждом `GET /v1/models` вызывает
`ProcessManager.refresh(probe_models=False)`, отбрасывает exited/error/stopping
и не подтверждённые external экземпляры, затем пробует engine `/v1/models`.
Непустой ответ на один экземпляр кэшируется на 10 минут с identity
`pid/start/port/public model/engine`; пустой или ошибочный ответ кэш не создаёт.
Публичная запись берётся из реестра и проверяется по фактическому upstream id.
`backend/app/schemas/proxy.py` выдаёт стандартные `id`, `object`, `created`,
`owned_by` и локальные optional `engine_id`, `context_length`,
`reasoning_capabilities`. Схема запрещает лишние поля на входе модели, но это
не обещает, что произвольный внешний клиент обработает нестандартные поля.

При обновлении списка внешний unmanaged id (`profile_name=NULL`) должен
сохраняться, даже если рядом появилась configured route с таким public name;
коллизия показывается диагностикой и не решается тихим удалением. Для routing
кандидат считается `available` только при verified identity, подтверждённом
порте и свежей непустой model metadata. `stale`, `starting`, `stopping`,
`exited` и `error` остаются видимыми в management state, но пропускаются при
выборе и не публикуются как доступный route.

Сейчас internal chat получает тот же список через `/api/proxy/models`;
frontend `src/lib/api.ts:104-109,285-287` использует этот backend path.
Внешний `/v1` слушает отдельный `OPENAI_PORT`; URL и `VITE_OPENAI_API_BASE_URL`
из `docs/openai-endpoint.md` менять нельзя. `/api/v1/models` — каталог
локальных артефактов, а не модельный список OpenAI-прокси.

Официальный OpenAI model-list contract использует `GET /v1/models`, объект
`{"object":"list","data":[...]}`, а модель — `id`, `object`, `created`,
`owned_by`: [OpenAI Models API Reference](https://platform.openai.com/docs/api-reference/models/object?lang=curl).
Следовательно, логическая группа или алиас может иметь стабильный публичный
`id`, но нельзя требовать от клиента локальных полей для маршрутизации.
При refresh нужен ограниченный cache/probe текущей реализации, а не цикл
дорогих runtime health checks или POST-проб на каждый listing. `ready` и
`available` следует различать: stale/starting/stopping/exited/error видимы в
management diagnostics, но не должны автоматически становиться кандидатами.

### Forwarding и streaming

`backend/app/proxy/router.py` сохраняет `/v1/models`,
`/v1/chat/completions`, `/v1/embeddings` и отдельные internal routes. Ошибки
`AppError` преобразуются в OpenAI-style `error` с `message`, `type`, `param`,
`code`. `ProxyService._forward()` (`service.py:265-433`) выбирает ровно одну
`RegisteredModel`, вызывает `ProcessManager.get()`, подставляет
`registered.upstream_model` в payload и передаёт запрос на localhost engine.
Именно этот набор routes входит в review; новые `/v1/completions` или
`/v1/images` здесь не проектируются. После forwarding public route name следует
возвращать в обычном JSON и SSE `model`, сохраняя `[DONE]`; requested name и
upstream id должны оставаться раздельными в журнале.

Подтверждение поведения есть в
`backend/tests/test_proxy_metrics_version_zero.py:54-101` (подстановка
upstream model и passthrough SSE) и
`backend/tests/test_proxy_models_cache.py:35-188` (identity/TTL, пустой probe,
смена process и повторный probe starting instance).

Текущая обработка:

- network/timeout/remote protocol failure до получения обычного ответа даёт
  `502 upstream_unavailable`, помеченный `retryable`, но сам сервис retry не
  выполняет;
- HTTP-ошибка upstream прозрачно возвращается клиенту как `upstream_error`;
- streaming (`service.py:435-580`) отдаёт SSE chunks по мере чтения. После
  получения заголовков или любого chunk текущий код не переключает профиль;
  ошибка потока записывается после его завершения/обрыва;
- журнал создаётся сначала без target, а затем обновляется фактическим
  `profile_name`, `instance_id`, `engine_id`, PID и токенами. У него одна строка
  на логический запрос (`backend/app/journal.py:25-64`), попыток и их исходов
  нет.

Официальная документация описывает Chat Completions `stream=true` как SSE и
предупреждает, что при прерванном потоке финальный usage chunk может не прийти:
[Chat Completions API Reference](https://platform.openai.com/docs/api-reference/chat/object?lang=ruby)
и [официальные типы openai-python](https://github.com/openai/openai-python/blob/main/src/openai/types/chat/completion_create_params.py).
Для error response и кодов ответов использована опубликованная OpenAI
[OpenAPI specification](https://github.com/openai/openai-openapi/blob/main/openapi.yaml):
ошибки являются JSON-ответами endpoint, но конкретное решение о локальных
`404`/`503` для скрытого alias остаётся вопросом TASK-004.
Это делает retry после начала потока несовместимым с прозрачным продолжением:
клиент уже получил часть ответа, а повторный POST может создать второй ответ.

### Метрики и связь с TASK-003/005

`RequestJournalRecord` и `RequestJournalService` (`backend/app/journal.py`,
`backend/app/token_stats.py`) агрегируют токены по выбранному профилю и PID;
`NULL` означает неизвестное значение. В текущем журнале `model_name` — имя,
запрошенное клиентом. После алиаса его нельзя трактовать как upstream model.
`docs/research/TASK-003-statistics.md` уже фиксирует необходимость сохранять
фактически обслуживший запрос профиль. Для failover потребуется также отличать
логический запрос от попыток, иначе одна заявка может дать два набора токенов.

`TASK-005` требует привязать benchmark к фактически проверяемому профилю.
Запрос через alias должен либо явно сохранять selected profile и benchmark
interval, либо быть исключён из benchmark-атрибуции по согласованному правилу.
Цены, cache billing, timezone и busy/concurrency semantics TASK-004 не
утверждает; их значения должны прийти из согласованных TASK-001/003/005.

### UI и хранение конфигурации

`frontend/src/components/app-shell.tsx` пока не содержит страницы proxy
routing. `frontend/src/pages/chat.tsx` и `components/chat-options.tsx`
показывают простой model picker. Профили редактируются через YAML-пути
`backend/app/profiles/repository.py`; `_atomic_write()` сохраняет профиль
через временный файл, `fsync` и `os.replace`. Существующей схемы хранения
маршрутов нет.

Для будущего экрана применимы требования
`docs/development/frontend.md` и `docs/specs/2026-09-29-design-system.md`:
переиспользовать shadcn Table/Button/Dialog/Alert/Combobox, плотные строки,
видимый фокус и доступное drag-and-drop взаимодействие. Самодельный renderer
или новая параллельная таблица без необходимости не следует вводить.

## Безопасная политика переключения (предложение)

Ни один вариант ниже не утверждён. Причина осторожности: для POST
`chat/completions` и `embeddings` timeout/разрыв соединения после отправки тела
не доказывает, что engine не принял и не обработал запрос. Повторная попытка
может удвоить генерацию и токены. `X-Request-Id`/`request_id` коррелируют
события, но сами по себе не дают engine idempotency.

| Событие попытки | Предлагаемое действие | Обоснование |
|---|---|---|
| Кандидат не запущен, нет verified identity/порта или readiness metadata | пропустить без POST | запрос ещё не мог быть обработан |
| До отправки запроса подтверждён отказ соединения | можно перейти к следующему | bytes не приняты данным socket; определить границу в клиенте и тестах |
| timeout на connect/write, remote protocol error до доказанного ответа | по умолчанию не retry; пометить `outcome=unknown` | engine мог принять тело; безопасный retry требует idempotency |
| HTTP 408/429/5xx от engine до body | считать ответом upstream; retry только по явному allowlist и согласованной идемпотентности | HTTP status не доказывает отсутствие side effect |
| HTTP 4xx validation/auth/model/capability | не переключать | другой профиль не исправляет запрос, ключ или schema |
| streaming: заголовки получены, первый byte/chunk или SSE content выдан | не переключать; передать обрыв/ошибку и зафиксировать partial | иначе клиент смешает два ответа |
| streaming: connect отказал до headers и до отправки тела | условно можно применить ту же pre-send политику | остаётся вопрос, видел ли engine request |

Без явной поддержки idempotency со стороны engine безопаснее ограничить
автоматический failover только доказанным pre-send отказом. Retry cap должен
быть на один логический запрос и не скрывать `unknown` outcome. Для alias
смешанные capabilities (text/image/tools/reasoning) требуют общего
пересечения или явного отказа до выбора кандидата; это решение открыто.

Review уточняет границу: получение headers upstream — уже commit upstream,
даже если клиенту ещё не отправлен chunk. После любого client content поток
остаётся partial и не заменяется ответом другого профиля. Cancellation должна
закрыть upstream task/connection без failover. Общий timeout — budget logical
request, а не независимый timeout на каждый candidate; attempt timeout не
превышает оставшийся budget. Availability-вариант может повторить только
если нет headers и client body, но outcome остаётся `unknown` и exact-once не
гарантируется.

## Предлагаемая модель данных (черновая)

Это варианты для спецификации, не существующие поля.

- **Route**: `kind=auto|alias`, публичный `name`, упорядоченный список
  стабильных `profile_id` references, `enabled` и версия конфигурации. Auto
  route создаётся из одинакового `ProfileData.model_name`; alias хранит
  заданный клиентом name. Профиль может быть членом нескольких alias. External
  candidate может отображаться в diagnostics/model catalog, но alias reference
  рекомендуется ограничить сохранёнными profiles.
- **Candidate status**: profile reference, instance id/PID/start, port,
  upstream model id, `state`, readiness probe timestamp и причина недоступности.
  В `/v1/models` попадает route только при наличии хотя бы одного кандидата,
  отвечающего согласованному `available`.
- **Attempt**: технический результат выбора кандидата, а не отдельный
  статистический запрос. Для базовой версии достаточно сохранить итоговый
  `routing_outcome` и фактически вызванный profile/instance; подробности
  неудачных pre-header попыток могут оставаться диагностикой без отдельной
  агрегируемой таблицы.
- **Logical request**: requested public model, selected actual profile/instance
  и upstream id, итоговый status/error, `attempt_count`, `routing_outcome`.
  Статистика и токены относятся только к фактически вызванному профилю;
  requested alias сохраняется как дополнительное поле фильтра/диагностики.
  Неизвестные/частичные значения остаются `NULL` с причиной.

## Открытые вопросы исследования

После review технические рекомендации и решения пользователя считаются рабочей
основой черновика. На согласование остаётся один архитектурный стык:

1. контракт TASK-003/TASK-005: requested alias сохраняется для фильтра и
   диагностики, но токены, latency и фактическая нагрузка относятся к
   profile/instance, который был вызван прокси.
