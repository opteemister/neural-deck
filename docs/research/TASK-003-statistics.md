# TASK-003 — исследование источников данных для страницы статистики

Дата: 2026-10-04
Статус: исследование для обсуждения, реализация и решения по UX не согласованы
Область проверки: локальный checkout `/Users/opteemist/Documents/MyProjects/llm-studio`.

## Review 2026-10-04 — решено review

Это согласованные внутри review рекомендации для TASK-001/003/004/005, а не
утверждение пользовательских тарифов или UI. Зафиксированы следующие общие
правила, чтобы документы не расходились:

- verified exporter, привязанный к конкретному `Instance` с PID/start и
  `profile_name`, можно приписывать этому профилю как **наблюдаемую activity**;
  это не превращает cumulative counter или gauge в per-request событие. Для
  unscoped/multi-pool exporter и external без profile identity остаётся только
  engine/instance или unscoped источник.
- `prefill`, `decode`, proxy latency, queue time и TTFT — разные semantics.
  Pure request speed считается только по совместимым токенам и фазовым
  длительностям; latency не является prefill/decode. Смешанный source остаётся
  раздельным с `source`/`semantic` и весом.
- Рекомендуемый `request_event_time`: `finished_at` для terminal rows, включая
  failed/partial usage, иначе `started_at` для interrupted/unknown. Одна row
  попадает ровно в один bucket, период имеет границу `[from,to)`. `usage_received_at`
  сохраняется как provenance, но не меняет bucket задним числом.
- Day/hour labels для TASK-003 строятся из абсолютных UTC instants и всегда
  показываются в UTC; DST в пользовательском интерфейсе не возникает. Run и
  concurrency intervals пересекаются с bucket, а observation gaps не
  интерполируются.
- Stable `profile_id`/revision рекомендуется для новых данных; PID/start остаётся
  runtime identity, а старые rows получают явный legacy fallback. Редактирование
  тарифа не меняет performance identity и execution metrics.
- Benchmark interval — только overlay. Прямые benchmark requests появляются в
  общей статистике лишь через явные logical request events с `benchmark_run_id`
  и dedup key; interval или counter spike сами по себе не доказывают тестовый
  трафик.

После решения пользователя остаются product-вопросы: точный набор
concurrency-полей и UI labels. Период по умолчанию определён как последние 30
календарных дней в UTC; для него основная activity-визуализация дневная.
`profile_max_concurrency` определён
как исторический максимум active requests по профилю за весь доступный период.
Зафиксированы UTC, USD по текущей ставке профиля, включение failed/interrupted
и benchmark traffic в общие totals, а load определяется по time-weighted active
concurrency относительно максимальной ёмкости профиля.

## Как проводилась проверка

Прочитаны `AGENTS.md`, `DEVELOPMENT_RULES.md`,
`docs/development/frontend.md`,
`docs/specs/2026-09-29-design-system.md`, задача
`docs/tasks/TASK-003-statistics.md`, референс
`docs/research/TASK-003-statistics-reference.md`, а также связанные
`docs/tasks/TASK-001-profile-cards.md`,
`docs/tasks/TASK-004-proxy-routing-aliases.md` и
`docs/tasks/TASK-005-benchmarks.md`.

Проверены локальные исходники, схемы, миграции и тесты backend/frontend:

- `backend/app/journal.py`, `backend/app/token_stats.py` и
  `backend/app/proxy/service.py` — журнал событий прокси, usage и выделение
  показателей из ответов движка;
- `backend/app/metrics/{entities,storage,service,archive,performance,providers}.py`
  и `backend/app/api/metrics.py` — сбор, нормализация, хранение и API метрик;
- `backend/app/process/{manager,runs}.py` — жизненный цикл экземпляров и
  текущее хранилище запусков;
- `backend/app/schemas/profiles.py` — фактическая схема YAML-профиля;
- `frontend/src/{types/api.ts,lib/api.ts}` и компоненты
  `model-performance.tsx`, `token-history-chart.tsx`,
  `metric-line-chart.tsx`, `concurrency-chart.tsx`, `app-shell.tsx`, а также
  `pages/benchmarks.tsx` — существующие API-вызовы и UI;
- миграции `backend/alembic/versions/0003_request_journal_token_stats.py`,
  `0005_metrics_history.py`, `0006_model_runs.py`, `0007_daily_metrics.py` и
  тесты `backend/tests/test_metrics_history.py`,
  `backend/tests/test_metrics_archive.py`.

Приложение не запускалось, Docker не поднимался, данные SQLite не читались,
к движкам, Prometheus, серверам и runtime API подключений не было. Выводы ниже
относятся к исходному коду и локальным тестам. Незакоммиченные изменения в
checkout существовали до проверки; они не интерпретировались как результат
данной работы и не изменялись.

## Карта фактических источников

### События запросов через прокси

`RequestJournalRecord` в `backend/app/journal.py` — одна строка на логический
запрос прокси без содержимого запроса и ответа. Сохраняются `request_id`, время,
route, публичное `model_name`, выбранные `profile_name`, `instance_id`,
`engine_id`, `pid`, код ответа, длительность, streaming, клиент и токены.
Дополнительные поля уже предусмотрены для `cached_input_tokens`,
`usage_received_at`, `usage_source`, `metric_meta`, TTFT, ожидания очереди,
prefill/decode и cache/speculative показателей.

`ProxyService.forward()` сначала создаёт незавершённую строку с внутренним
`journal_id`, затем после выбора экземпляра дополняет ту же строку профилем,
PID и движком. Для обычного ответа `_response_metrics()` читает usage и
timings. Для streaming `_forward_stream()` накапливает usage до завершения и
записывает одну итоговую строку; промежуточные SSE chunks отдельными запросами
не считаются. При перезапуске незавершённые строки помечаются
`interrupted` в `RequestJournalService.interrupt_pending()`.

Из OpenAI usage извлекаются `prompt_tokens`/`input_tokens`,
`completion_tokens`/`output_tokens`, а также
`prompt_tokens_details.cached_tokens` или
`input_tokens_details.cached_tokens`. Для llama.cpp и NInfer дополнительно
читаются `timings.cache_n`, `prompt_ms`, `predicted_ms`, `prompt_per_second`,
`predicted_per_second`, `prompt_n`, `predicted_n`, `draft_n` и
`draft_n_accepted`. Прокси измеряет TTFT до первого содержимого или reasoning.
Известные значения сохраняются, отсутствующие остаются `NULL`.

`RequestJournalService.stats()` умеет группировать запросы по профилю,
считать известные суммы input/output и количество неизвестных запросов.
`usage()` возвращает журнал с пагинацией и фильтрами профиля, PID, клиента и
времени. `token_history()` строит 24 часовых корзины по `usage_received_at`,
затем `finished_at`; текущая реализация держит границы по UTC и возвращает
известные суммы вместе с `*_unknown`.

Это уже пригодно как исходная основа для дневных токенов и запросов через
OpenAI API, но не является полной статистикой всех обращений к моделям:

- прямой клиент движка минует прокси и не даёт per-request строку;
- instance-scoped exporter с подтверждёнными `profile_name`, PID и process
  start можно атрибутировать этому профилю как activity/snapshot. Это не
  создаёт per-request rows и не доказывает, какой клиент породил counter;
  unscoped labels, multi-pool и external без profile identity остаются
  engine/instance-scoped или unscoped;
- накопительные counters из `/metrics` нельзя разложить на отдельные запросы;
- текущий `stats()` не группирует по модели, не считает cache и стоимость и
  фильтрует период по `created_at`, тогда как usage предпочитает момент
  получения финального usage;
- запись профиля в журнале относится к выбранному upstream в текущем
  реестре; будущие алиасы и failover TASK-004 потребуют явного контракта
  «какой профиль фактически обслужил запрос».

### Периодические снимки движков

`MetricsService._background()` раз в 5 секунд обнаруживает активные managed и
подтверждённые external экземпляры, собирает `MetricList` и пишет его в
`MetricsStorage`. Сбор не зависит от наличия браузера. Один процесс изолирован
ошибкой провайдера; невалидные, `NaN` и `Inf` значения не становятся нулями.

`MetricSample` в `backend/app/metrics/entities.py` хранит
`profile_name`, PID, engine/model, `observed_at` и `recorded_at`, JSON
метаданных/распределений и поля:

- `decode_tps`, `prefill_tps`, `ttft_mean_ms`;
- `active_requests`, `queue_requests`, `queue_time_mean_ms`;
- `kv_cache_percent`, `cached_prompt_tokens_total`,
  `prefix_cache_hit_percent`, `prefix_cache_hit_mean_percent`;
- `prompt_tokens_total`, `completion_tokens_total`,
  `completed_requests`, `inflight_completion_tokens`;
- `vram_bytes`, power/energy, uptime и другие совместимые поля.

Состояние и источник каждого поля сохраняются в `metric_meta`. `NULL` означает
отсутствие достоверного измерения. Cumulative prompt/completion/cache counters
являются наблюдением состояния движка и не должны складываться между всеми
снимками.

Провайдеры в `backend/app/metrics/providers.py` читают:

| Движок | Фактический источник | Ограничение для статистики |
|---|---|---|
| `vllm` | `/metrics`, Prometheus text | Доступны gauges, counters и histogram; смысл зависит от labels и окна приращения |
| `sglang` | `/metrics`, Prometheus text | `token_usage` может объединять разные пулы; cache/speculative требуют подтверждённой семантики |
| `llama.cpp` | `/metrics` и `/slots` | `/slots` даёт текущий active и производные скорости, общего per-request журнала нет |
| `freetoken` | `/v1/stats`, JSON | Скорость и p95 относятся к окну/кольцу движка, а не обязательно ко всей жизни процесса |
| `ninfer` | подтверждённого общего `/metrics` нет | Timings находятся в ответе конкретного запроса и видны только через прокси |

`PerformanceNormalizer` сопоставляет только явные mappings. Counter rates,
cache hit, queue mean и speculative acceptance получают приращения между
достоверными наблюдениями; сброс counters даёт `NULL` на первом интервале.
Histogram сохраняет buckets/sum/count и не делает из них точные события.

Для attribution используется такая граница: gauge/counter относится к профилю,
если observation содержит verified instance identity и exporter scope ровно
этого instance. Тогда можно показать `profile activity`, длительность
наблюдения и counter-derived throughput. Нельзя назвать это количеством
логических запросов или сложить с `request_journal` без отдельной source
разбивки. Если один exporter обслуживает несколько процессов/пулов и labels не
разделяют их, profile attribution остаётся unknown.

### Дневной архив метрик

`MetricDaily` хранит бессрочные агрегаты снимков по `day`, профилю, model и
engine; в JSON есть avg/max/веса/число измерений и mergeable распределения.
`MetricConcurrencyDaily` хранит кривую по дню, профилю, model, engine и
наблюдавшемуся числу активных запросов. В текущем `archive.fold()` точка
попадает в кривую только при положительных скорости и concurrency, сохранении
одинакового concurrency между наблюдениями и разрыве не длиннее 15 секунд.
Весом служит фактический elapsed time. Переходы и длинные разрывы пропускаются.

Архивирование запускается перед очисткой `metric_samples` старше 24 часов;
чекпоинт и очистка выполняются одной транзакцией. Сейчас день в архиве
вычисляется через локальный timezone ОС (`astimezone()`), а timestamps API
остаются UTC. Это ограничение текущей реализации: TASK-003 должен группировать
данные по UTC и не использовать локальное поле `day` архива без проверки или
пересчёта.

`MetricsService.read(mode="aggregates")` сейчас даёт окно последних 24 часов,
max и взвешенное среднее по снимкам. `mode="token-history"` использует журнал,
`mode="usage"` отдаёт сырые request rows, а `mode="history"`/`bootstrap` —
только историю снимков за 24 часа с возможным Prometheus fallback.

### Жизненный цикл и runs

`model_runs` (`backend/app/process/runs.py`) — durable snapshot только текущих
управляемых процессов, ключ `(pid, process_start_time_ns)`. Строка сохраняется
при старте/восстановлении и удаляется после завершения/остановки. Это не
история запусков и не журнал start/stop.

У экземпляра есть `profile_name`, PID, `process_start_time_ns`, состояние и
`observed_at`; у external процесса `profile_name` может быть `NULL`. У
`metric_samples` те же ограничения: после очистки остаётся дневной архив,
но нет отдельного immutable profile revision или run id. Точный процент
«профиль работает за день» нельзя доказать одной текущей строкой
`model_runs`; при редких/пропущенных наблюдениях также нельзя без допущения
заполнить интервалы между snapshot.

### Профили и цены

`backend/app/schemas/profiles.py` и YAML-файлы содержат имя, публичное имя
модели, engine/installation, путь модели, команду, environment, autostart и
`enable_metrics`. Цен input/output, валюты, единицы тарифа, revisions цен и
идентификатора профиля в текущем контракте нет. Поэтому требование TASK-003 о
стоимости зависит от TASK-001 и не может быть достоверно реализовано только
текущим checkout.

### Existing UI и референс

Сейчас в `frontend/src/components/model-performance.tsx` на Dashboard уже
показываются текущие Prefill/Decode/TTFT/Requests/cache/queue и агрегаты
последних 24 часов. `token-history-chart.tsx` рисует stacked Input/Output/
Cached по 24 часовым корзинам; Cached визуально считается подмножеством Input.
`metric-line-chart.tsx` — общий SVG renderer для линии, точек, сетки,
масштабов и tooltip. `concurrency-chart.tsx` использует дневной API
`/metrics/concurrency` и строит Total/Per request.

Навигация в `frontend/src/components/app-shell.tsx` содержит Status и
зарезервированный Benchmarks; отдельного маршрута Statistics нет.
`frontend/src/pages/benchmarks.tsx` пока показывает только placeholder. Поэтому
новая страница должна переиспользовать `Table`, `Card`, `Select`, существующий
line chart и общий визуальный образец, но сама структура страницы статистики
ещё не существует.

Соседний `vllm-studio` рассмотрен в
`docs/research/TASK-003-statistics-reference.md`: его heatmap — календарь
дней за 53 недели, а почасовая активность вынесена в отдельные столбики. В
референсе нет требуемых TASK-003 цены, дневного cache, concurrency и загрузки;
UTC там не должен автоматически становиться часовым поясом LLM Studio.

## Что реально можно получить сейчас

| Показатель страницы | Источник сейчас | Состояние данных | Что понадобится для полного контракта |
|---|---|---|---|
| Input/output tokens | `request_journal.input_tokens`, `output_tokens`; instance-scoped counters | Per-request values известны через прокси; verified instance counters дают только attributed activity/throughput, не request events | Для direct per-request нужен явный event source; counters нельзя разворачивать в события |
| Cached input tokens | `request_journal.cached_input_tokens`; snapshot counters `cached_prompt_tokens_total` | Per-request cache доступен только при usage/timings; snapshot cache — отдельная cumulative наблюдаемая величина | Текущий profile billing rule; cache не добавлять к input второй раз |
| Количество запросов | count строк журнала | Точно только для запросов через прокси; включая ошибки/unknown нужно определить | Отдельные status counters для ошибок/interrupted; benchmark traffic входит в totals |
| Модель | `RequestJournalRecord.model_name` и snapshot `model_name` | В журнале это запрошенное публичное имя; для маршрута alias может отличаться от upstream имени | Контракт external model vs served upstream model и фактический served profile |
| Профиль/PID/engine | journal `profile_name`, `pid`, `engine_id`; snapshot instance | Журнал фиксирует served target; verified instance counters могут иметь profile attribution; external/direct engine может быть без профиля | Stable `profile_id`/revision плюс run identity; PID-only unsafe при reuse |
| Prefill/decode | request timings для llama.cpp/NInfer; snapshots и daily archive для поддержанных exporters | Pure phase speed только при явных phase tokens/time; native engine throughput — отдельная semantic; latency не подменяет speed | Общие source/semantic/weight labels и formula per TASK-001/005 |
| TTFT/queue/latency | proxy TTFT/latency; engine histogram/counter snapshots; queue fields nullable | Proxy request latency, engine queue, TTFT и phase time не взаимозаменяемы; direct clients не видны per-request | Показывать раздельно; `NULL` при отсутствии источника |
| Concurrency | `active_requests` snapshot; `MetricConcurrencyDaily` | Наблюдаемая concurrency с шагом опроса, не полное распределение запросов | Решить среднее/max/распределение, time-weighted окно и пробелы |
| Работа профиля | live process state, `process_start_time_ns`, snapshot timestamps | История start/stop и интервалы не хранятся | Run intervals или явное правило по наблюдаемым snapshots |
| Стоимость | нет цены в `ProfileData`/YAML | Расчёт невозможен без TASK-001 | Тариф input/output, валюта/единица, версия ставки и cache billing |
| Heatmap day×hour | raw journal event times; snapshot samples | Request row имеет один completion/start event time; intervals пересекаются с buckets; direct engine и unknown остаются отдельно | UTC, `[from,to)` и tooltip-разбивка |
| Benchmark interval/events | нет хранилища benchmark/run stages; страница placeholder | Interval overlay не доказывает, что весь трафик тестовый; direct runner bypass proxy не создаёт journal row | `benchmark_run_id`, explicit logical events и dedup для provenance; traffic входит в totals |

## Ограничения, которые должны попасть в спецификацию

1. `NULL`/unknown не подменять нулём. Пустой bucket без запросов можно
   показывать как нулевую активность, но запросы с неизвестным usage должны
   иметь отдельный счётчик и не давать ложную сумму.
2. Cached input — подмножество input. Для stacked bars использовать
   `uncached_input = input - cached`, только когда оба значения совместимы и
   неотрицательны; cache не прибавлять к input ещё раз.
3. Counters движка (`prompt_tokens_total`, `completion_tokens_total` и cache)
   не являются событиями. Их приращения годятся для throughput/снимочной
   статистики только при валидном baseline и reset handling; при verified
   instance-scoped exporter их можно приписать profile activity, но не считать
   per-request и не смешивать с request journal.
4. Показатели разных профилей, PID, моделей и engines нельзя соединять в одну
   линию или приписывать external process сохранённому профилю по похожему
   имени модели.
5. Alias/failover TASK-004: `model_name` клиента и фактически обслуживший
   профиль — разные измерения. Ошибочный первый upstream и повторная попытка
   могут относиться к одной логической заявке; критерий attribution должен быть
   определён до реализации. Не считать каждый fallback автоматическим новым
   пользовательским запросом.
6. Benchmark TASK-005: обычные snapshots продолжают собираться во время
   теста, но без benchmark interval нельзя объяснить всплеск на графике. Нельзя
   задним числом объявлять spike benchmark-активностью. Bypass-proxy runner
   обязан отправлять explicit logical request events в общий event contract,
   иначе его per-request traffic не входит в обычный request count.
7. Изменение/переименование/удаление YAML профиля не должно менять смысл уже
   сохранённого usage. Рекомендуется immutable `profile_id` + revision; до
   миграции допустим явный legacy key `profile_name + process_start_time_ns`,
   но PID без process start небезопасен.

## Review-контракт времени, performance и стоимости

Рекомендуемый request contract для новой агрегации:

- terminal logical row получает `request_event_time = finished_at`, включая
  успешный, HTTP-error и partial/failed ответ. Если `finished_at` отсутствует
  (`interrupted`, process crash, неизвестный исход), используется `started_at`,
  а row помечается `event_completeness=unknown`;
- `usage_received_at` сохраняется для provenance и диагностики позднего usage,
  но не переносит row между buckets. Запрос, пересекающий midnight или границу
  периода, не дробится: его tokens/requests целиком относятся к bucket его
  event time. In-flight rows не входят в terminal total, но могут быть отдельным
  `inflight_unknown` счётчиком;
- period — абсолютные instants с границей `[from,to)`. Day/hour отображаются
  в UTC. Bucket key включает абсолютные `bucket_from/bucket_to`; DST не создаёт
  дополнительных пользовательских часов;
- run intervals и concurrency observations режутся пересечением с period и
  bucket. Интервал с observation gap не заполняется автоматически; его вклад
  ограничивается подтверждёнными участками и получает coverage/unknown state.

Для performance рекомендуются отдельные series:

- pure request prefill = `prefill_tokens / prefill_seconds`, pure decode =
  `output_tokens / generation_seconds`, только когда engine timing явно
  обозначает соответствующую фазу и знаменатель положителен;
- request latency = `finished_at - started_at`, proxy TTFT, engine queue и
  phase durations не заменяют друг друга. При snapshots используется native
  engine throughput с его denominator/weight; average-of-averages запрещено;
- сравнение и агрегация разрешены только внутри одинаковых
  `(profile_id/revision, model, engine, source, semantic)`. Different source
  выводится отдельными строками или `mixed`, но не одной цифрой без подписи.

Для cost используется USD и текущая ставка профиля на момент чтения
статистики; историческая ставка не восстанавливается. Cached — subset input.
Профиль хранит три ставки: `input_rate`, `cached_input_rate` и `output_rate`.
Применяется формула `(input-cached)×input_rate + cached×cached_input_rate +
output×output_rate`. Cached input не прибавляется к input второй раз. При
неизвестных tokens или rates cost остаётся `partial`/`unavailable`, а не
превращается в ноль.

Benchmark review contract: interval overlay (`started_at`, `finished_at`,
scenario) не является фильтром трафика. Proxy benchmark rows получают
`benchmark_run_id`; direct runner, который обходит proxy, обязан писать один
logical event на request с profile/run identity, status, token usage (если есть)
и dedup key `(benchmark_run_id, client_request_id, attempt)`. Такие events нужны
для provenance и отображения нагрузки; benchmark traffic входит в обычные
request/cost totals. Instance counters за interval показываются как potentially
mixed activity.

## Проверенные, но не подтверждённые runtime факты

- Реальные версии движков, фактический набор exporter labels и доступность
  `/metrics`, `/slots` и `/v1/stats` не проверялись в установленной среде.
- Фактические объёмы `request_journal`, распределение неизвестных usage,
  direct-engine трафик и реальные цены отсутствуют без запуска/данных.
- Зелёные локальные тесты не выполнены в рамках исследования; наличие тестов
  подтверждает только ожидаемые контракты, а не состояние production.
- Deployment и server state не затрагивались.

## Вывод для черновика спецификации

Для первой версии страницы есть два разных источника: бессрочные события
proxy usage для input/output/cache/requests и дневной архив периодических
снимков для performance/concurrency. Их следует объединять только по явному
профилю/модели/engine и периоду, с сохранением source и unknown counts.

Требования о cache billing уточнены: cached input — подмножество input, а
отдельная cache-hit ставка применяется только если она явно задана для модели
или профиля; иначе используется обычная input ставка. Стоимость считается в
USD по текущим ставкам профиля, а benchmark traffic входит в обычные totals и
cost с визуальной отметкой интервала.

Официальные тарифные документы подтверждают, что отдельная cache billing у
провайдеров встречается: DeepSeek публикует cache-hit и cache-miss ставки для
`deepseek-flash` и `deepseek-v4-pro`, а Alibaba Cloud Model Studio описывает
отдельную цену cache-hit для Qwen и model-specific исключения. Эти цены нельзя
зашивать в LLM Studio: в проекте они должны приходить из текущих настроек
профиля и периодически проверяться пользователем.

Источники проверки:

- [DeepSeek Models & Pricing](https://api-docs.deepseek.com/quick_start/pricing/)
  — отдельные cache-hit/cache-miss цены и зависимость от peak/off-peak.
- [DeepSeek Context Caching](https://api-docs.deepseek.com/guides/kv_cache/)
  — поля `prompt_cache_hit_tokens` и `prompt_cache_miss_tokens` в usage.
- [Alibaba Cloud Qwen Context Cache](https://www.alibabacloud.com/help/en/model-studio/context-cache)
  — cache-hit billing и model-specific правила.
- [Alibaba Cloud Model Pricing](https://docs.modelstudio.console.alibabacloud.com/en/model-studio/model-pricing)
  — отдельные input/cache-hit/output ставки для поддерживаемых моделей.

Отдельно нужно согласовать, как TASK-004 фиксирует served profile при
alias/failover и как TASK-005 маркирует интервал теста. При отсутствии истории
active requests процент загрузки должен иметь coverage/unknown state, а не
выглядеть как достоверный 0%.
