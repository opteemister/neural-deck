---
title: TASK-003 — страница статистики
status: draft_not_agreed
date: 2026-10-04
---

# Черновик, не согласовано

Этот документ переводит требования `docs/tasks/TASK-003-statistics.md` в
проверяемый контракт для обсуждения. Он не разрешает реализацию. Пользователь
согласовал UTC, USD, применение текущих ставок профиля, включение ошибок,
benchmark traffic и месячный период по умолчанию как последние 30 календарных
дней.
Остаются отдельные детали UI.

Исходные факты и ограничения приведены в
`docs/research/TASK-003-statistics.md`. Существующая approved-спецификация
метрик (`docs/specs/2026-10-02-profile-metrics-history.md`) определяет
снимки/архив движка и журнал usage как инфраструктуру. Она не выбирает UX и
бизнес-правила для этой страницы.

## Review 2026-10-04 — решено review

Ниже зафиксированы рекомендуемые shared contracts для TASK-001/003/004/005.
Это review-решения для согласования документов, не разрешение реализации.
Конкретные продуктовые решения пользователя приведены ниже.

1. Instance-scoped counters/gauges с verified exporter identity (`profile_id`,
   PID/start) можно показывать как profile activity/throughput. Они не являются
   logical request events, не заменяют request journal и не приписываются
   профилю, если exporter scope не разделяет instance.
2. Performance series не смешивают pure prefill/decode, request latency, TTFT,
   queue и native engine throughput. Pure phase speed требует совместимых phase
   tokens/time; snapshots сохраняют native source/semantic/weight.
3. `request_event_time = finished_at` для terminal rows (включая failed/partial)
   и `started_at` для interrupted/unknown. Один logical request целиком попадает
   в один bucket, период `[from,to)`, `usage_received_at` остаётся provenance.
   TASK-003 использует UTC для day/hour и absolute bucket keys; на месячном
   экране основной bucket — календарный день. DST в UI не возникает.
   Run/concurrency intervals пересекаются с buckets; observation gaps не
   интерполируются.
4. Рекомендуется immutable `profile_id` + revision; PID/start — runtime identity,
   legacy rows получают явный fallback. Edit тарифа не меняет execution
   performance identity; currencies агрегируются раздельно без FX.
5. Benchmark interval — overlay. Per-request benchmark статистика требует
   explicit events с `benchmark_run_id` и dedup; interval/counter spike не
   объявляет весь overlapping traffic тестовым.

Оставшиеся вопросы: детали load coverage, UI-агрегации и acceptance threshold
TASK-005.

## Решения пользователя 2026-10-04

- Вся временная семантика страницы, API, базы и UI — UTC. Дневные buckets
  группируются и подписываются в UTC; пользовательская timezone не выбирается.
  Почасовые buckets остаются технической детализацией источников и не являются
  основным графиком месячного экрана.
- Валюта статистики — USD. Для расчёта берётся текущая ставка, указанная в
  профиле на момент чтения статистики; исторические ставки не восстанавливаются.
- В requests входят успешные, terminal failed/HTTP-error и interrupted rows.
  Ошибочные состояния показываются отдельными счётчиками/цветами; неизвестные
  tokens и стоимость не превращаются в ноль.
- Benchmark traffic входит в общие tokens, requests и cost. Benchmark не
  является отдельным фильтром: на графике отмечается интервал запуска,
  сценарий и нагрузка.
- Load — не uptime процесса. Профиль считается загруженным пропорционально
  активной concurrency: `load_percent = 100 * time_weighted_active_requests /
  profile_max_concurrency`, где `profile_max_concurrency` — исторический
  максимум active requests по этому профилю за весь доступный период. Поэтому
  0 запросов дают 0%, один запрос при максимуме 10 — 10%, десять — 100%.
  Простой процесс в памяти без запросов не считается загрузкой.
  Если в будущем будет зафиксирован новый исторический максимум, проценты
  прошлых периодов пересчитаются относительно нового знаменателя.
- В настройках модели задаются три ставки: `input_rate`, `output_rate` и
  `cached_input_rate`. Cached input остаётся частью общего input token count,
  но вычитается из обычного input перед расчётом и считается по третьей ставке.
  Отдельная cache ставка не является дополнительными токенами.

## 1. Назначение

Страница «Статистика» в разделе «Наблюдение» должна показывать расход токенов,
наблюдаемую производительность, concurrency и активность по календарным дням,
ориентировочную стоимость при наличии согласованных цен и загрузку профилей.
Статистика строится по выбранному периоду и фильтрам модели/профиля.

Первый экран открывается с периодом «месяц», который означает последние 30
календарных дней с границами `00:00 UTC`, и общей дневной heatmap по всем наблюдаемым
моделям и профилям. Ниже располагаются сводки по профилям и клиентам.

Источники данных показываются отдельно в контракте ответа. Наблюдаемые через
прокси события нельзя выдавать за полный расход всех прямых клиентов движка.

## 2. Термины и границы attribution

**Логический запрос** — одна строка usage-журнала, созданная при входе запроса
в прокси и дополненная при завершении. Streaming chunks не являются отдельными
запросами.

**Запрошенное имя модели** — имя из клиентского payload, включая пользовательский
alias TASK-004. **Upstream model** — имя, которое прокси передал конкретному
движку.

**Served profile** — профиль, который фактически обслужил запрос после выбора
маршрутом. До реализации TASK-004 текущего `profile_name` в журнале отражает
выбранную запись реестра. После появления alias/failover это поле/измерение
должно быть явно зафиксировано до завершения запроса и не вычисляться заново
по текущему процессу.

Одна логическая заявка с fallback может иметь несколько upstream attempts.
Рекомендуется считать request один раз, а attempts хранить отдельно; tokens
приписывать фактически обслужившему profile только при подтверждённом usage.
Неизвестный исход после отправки не даёт права повторно считать запрос или
суммировать токены. Latency логического запроса и latency отдельных attempts
показываются раздельно.

Обращения напрямую к API движка, минуя прокси, не получают per-request
attribution. Если periodic counter/gauge имеет verified exporter identity,
scoped ровно к instance с `profile_id` и PID/start, его разрешается показывать
как activity/throughput этого профиля с `source=instance_counter`; это не
создаёт запросы, не раскрывает клиентов и не добавляет rows в proxy request
count. Unscoped/multi-pool counters и external instance без profile identity
остаются engine/instance/unscoped. Из разности counters нельзя выдумывать
logical requests.

## 3. Пользовательские требования к странице

### 3.1. Период и фильтры

- Период по умолчанию — последние 30 календарных дней, включая текущий
  неполный день; границы периода выравниваются по `00:00 UTC`.
- Пользователь может задать период, а границы и часовой пояс отображаются рядом
  с результатом.
- Доступны общий вид, фильтр конкретной модели и фильтр конкретного профиля.
- В каждом ответе фильтр применяется одинаково к overview, activity heatmap,
  profile summary, client summary, concurrency и стоимости.
- Если фильтр выбирает alias, в деталях показываются запрошенное имя и
  фактические served profiles, когда такая attribution доступна.

### 3.2. Сводки по профилям и клиентам

Страница показывает две сводки за выбранные 30 календарных дней по умолчанию;
при смене периода таблицы агрегируются по выбранному диапазону.

Таблица профилей содержит строки профилей и колонки:

- input tokens, cached input tokens и output tokens;
- total requests и отдельные status counters для failed/interrupted/unknown;
- average и peak concurrency за период, а также исторический максимум,
  использованный как знаменатель load;
- load percentage с basis, numerator, denominator и coverage;
- доступные performance series (prefill/decode и другие source/semantic series
  раздельно);
- стоимость в USD по трём текущим ставкам профиля.

External/unmanaged rows без profile identity показываются отдельно с
`unavailable/unknown`, а не объединяются с похожим профилем.

Таблица клиентов содержит тот же usage-набор: input, cached input, output,
requests, status counters и cost, а также список фактически обслуживших
профилей. Profile-only поля load и engine performance для клиента показываются
только при наличии корректной attribution; иначе имеют `unavailable`.

Итоги за период выводятся внутри заголовка/подвала соответствующей таблицы.
Известные суммы не включают unknown values, а недоступная стоимость не
отображается как ноль.

### 3.3. Activity heatmap

Нужен один основной график с квадратами по календарным дням UTC. Для периода
«месяц» используется календарная сетка: одна ячейка соответствует одному дню,
а строки группируют последовательные недели. Интенсивность отражает известный
расход токенов за день. Tooltip сообщает дату, input/cached/output tokens,
requests, разбивку по профилям и клиентам, а также benchmark interval, если он
пересекает ячейку. Почасовая детализация не является основным графиком
месячного периода и может появиться позже как drill-down выбранного дня.

Нулевой расход, неизвестный расход и отсутствие событий имеют различимые
текстовые состояния; интенсивность не может превращать unknown в ноль.

### 3.4. Загрузка профиля

На странице статистики в сводке профилей показывается процент загрузки за
выбранный период; на карточке профиля TASK-001 этот показатель также может
показываться за день. Load не означает uptime: профиль в памяти без активных
запросов имеет 0%. Значение рассчитывается как time-weighted active
concurrency относительно максимальной ёмкости профиля. В подписи указываются
basis, `profile_max_concurrency`, числитель, знаменатель и coverage.

При неизвестной максимальной ёмкости или недостаточном покрытии наблюдения
показатель имеет состояние unavailable/unknown, а не выглядит как 0%.

## 4. Контракт данных агрегации (предложение)

Ниже — логический контракт, который нужен UI независимо от конкретного
endpoint. Имена полей являются предложением и могут измениться после
согласования API.

```json
{
  "period": {
    "from": "RFC3339 UTC",
    "to": "RFC3339 UTC",
    "timezone": "UTC",
    "boundary": "[from,to)",
    "request_event_time": "finished_at_else_started_at"
  },
  "filters": {
    "model_name": null,
    "profile_name": null,
    "served_profile_name": null
  },
  "overview": {
    "requests": {"total": 0, "source": "proxy", "unknown": 0},
    "input_tokens": {"known_total": null, "unknown_requests": 0},
    "output_tokens": {"known_total": null, "unknown_requests": 0},
    "cached_input_tokens": {"known_total": null, "unknown_requests": 0},
    "cost": {"value": null, "currency": "USD", "state": "unavailable"}
  },
  "profiles": [],
  "clients": [],
  "activity": [],
  "missing": []
}
```

Каждый элемент `profiles` должен содержать profile identity, те же
token/unknown поля, status counters и:

```json
{
  "concurrency": {
    "average": null,
    "maximum": null,
    "distribution": null,
    "observed_seconds": 0,
    "sample_count": 0,
    "method": "не выбран"
  },
  "performance": {
    "prefill_tps": {"value": null, "weight": null, "source": null},
    "decode_tps": {"value": null, "weight": null, "source": null}
  },
  "load": {
    "percent": null,
    "numerator_seconds": null,
    "denominator_seconds": null,
    "basis": "не выбран",
    "state": "unavailable"
  },
  "cost": {
    "value": null,
    "currency": "USD",
    "state": "unavailable",
    "unknown_requests": 0
  }
}
```

Каждый элемент `clients` содержит client identity, input/cached/output tokens,
requests, status counters, cost и список served profiles. Profile-only поля
`load` и engine performance имеют `unavailable`, если корректной attribution
нет. Каждый элемент `activity` содержит UTC day bucket и token/request метрики
для месячной heatmap, включая optional profile/client breakdown. Если позднее
появится почасовая детализация, она возвращается отдельными hour buckets, не
меняя дневную агрегацию.

`unknown`, `unavailable`, `stale` и отсутствие строк должны быть различимы в
ответе или в `missing`. Timestamps и внутренние числовые значения передаются
в RFC 3339/JSON согласно общему контракту `docs/specs/2026-09-29-version-zero-contracts.md`.

Рекомендуемый temporal contract: terminal row (success, HTTP error, failed или
partial usage) получает bucket по `finished_at`; interrupted/unknown row без
`finished_at` — по `started_at` и помечается unknown. `usage_received_at` только
фиксирует момент получения usage. Одна logical row не дробится при crossing
midnight/period; если `event_time == to`, она исключена из `[from,to)`. In-flight
rows не входят в terminal totals, пока не появится отдельная явно подписанная
inflight metric.

Для activity response дополнительно нужны absolute `bucket_from`/`bucket_to` и
UTC offset `+00:00`. DST не создаёт дополнительных UI-бuckets. Run/concurrency
intervals пересекаются с bucket и period; разрывы наблюдений не интерполируются.

## 5. Правила расчёта, которые предлагаются для согласования

### 5.1. Tokens и запросы

- `requests.total` — число логических request rows с terminal или явно
  зафиксированным interrupted/unknown исходом, включённых
  выбранным `request_event_time` и фильтрами. Рекомендуемая граница периода
  `[from,to)` и единое правило применяются к таблице, totals и heatmap.
- Failed/HTTP-error rows и interrupted rows входят в requests и получают
  отдельные status counters (`failed`, `interrupted`, `unknown`). Tokens и cost
  для них учитываются только при подтверждённом usage. Attempts не считаются
  дополнительными логическими запросами.
- `input_tokens.known_total` и `output_tokens.known_total` — суммы только
  известных per-request значений. `unknown_requests` — число строк с `NULL`.
- `cached_input_tokens.known_total` — сумма cache subset только для совместимой
  семантики входных токенов. При `cached > input`, отрицательном значении или
  несовместимом источнике cache становится unknown для этой строки.
- Cache не прибавляется к input второй раз. Если известны `input` и `cached`,
  UI может показать `uncached = input - cached`; вычисление не разрешено при
  неизвестном знаменателе.
- Журнал API является бессрочным источником событий. `MetricSample` counters
  служат отдельным источником наблюдения движка и не суммируются как requests.
  Если counter/gauge scoped к verified instance, он может попасть в profile
  activity с source label, но не в logical request count.

### 5.2. Concurrency

Текущие источники дают `active_requests` gauges и архив
`MetricConcurrencyDaily`, где скорость взвешена по наблюдаемому elapsed time.
Для load используется time-weighted average `active_requests` за выбранный
bucket, разделённый на исторический `profile_max_concurrency` — максимум
`active_requests` по профилю за весь доступный период. Нулевые интервалы дают
0%; один запрос при максимуме 10 — 10%, десять — 100%. Uptime и простой
процесса в памяти в load не входят. Новый исторический максимум меняет
знаменатель и может пересчитать load прошлых buckets.

Переходы, невалидные значения и большие пробелы не интерполируются. При
пересечении day/hour bucket observation contributes только пересечением своего
интервала; coverage/observed_seconds остаются явными. Если максимальная
ёмкость не подтверждена или валидных observations нет, load возвращает `NULL`.
Точный набор отображаемых полей concurrency (average, maximum,
distribution/quantiles) остаётся UI-вопросом.

### 5.3. Performance

Prefill/decode агрегируются только по совместимой семантике и источнику. Для
per-request timings предпочтительны суммы совместимых токенов/времени; для
periodic snapshots — существующие weights/denominators и daily archive. Среднее
средних без веса запрещено. Серверный TTFT, proxy TTFT, queue histogram и
request latency не смешиваются без `source`/`semantic` в metadata. Pure
prefill/decode request speed считается как `phase_tokens / phase_seconds` только
при явных phase timings; latency (`finished-started`), TTFT, queue и native
throughput не заменяют друг друга.

Если profile/model/engine имеют разные series, UI либо показывает отдельные
строки, либо ставит `unavailable`; он не соединяет их в одну кривую. Сравнение
разрешено внутри одинаковых profile/model/engine/source/semantic; edit тарифа
не invalidates execution performance.

### 5.4. Стоимость

Стоимость рассчитывается в USD по текущим ставкам профиля. Исторические
запросы пересчитываются по текущей конфигурации профиля; изменение ставки
должно быть явно видно как следствие этой политики. Внутренний расчёт
нормализует единицу ставки перед умножением на tokens.

Если известны `input`, `cached` и `output`, формула по трём ставкам профиля
имеет вид: `(input - cached) * input_rate + cached * cached_input_rate +
output * output_rate`. Cached input не прибавляется к input второй раз.
Неизвестный input/output/rate даёт `partial` или `unavailable`, но не нулевую
стоимость.

### 5.5. Cache billing

Исходные cached tokens отображаются как subset input и не добавляются к input
второй раз. Для каждой модели профиль хранит отдельную `cached_input_rate`.
UI и API сохраняют token count, три ставки и `billing_rule`/`state`.

### 5.6. Busy/load

Numerator — time-weighted сумма `active_requests` по подтверждённым
интервалам наблюдения. Denominator — та же длительность, умноженная на
максимальную concurrency профиля. Это даёт среднюю долю ёмкости за bucket:
0 активных запросов — 0%, один запрос при ёмкости 10 — 10%, десять — 100%.
Uptime, наличие процесса в памяти и простои без запросов в load не входят.

Пропуски наблюдений не заполняются автоматически. При неизвестной максимальной
ёмкости или недостаточном coverage возвращаются `unknown/unavailable` и
coverage, а не ложный 0%.

## 6. Профиль, routing и историческая идентичность

Текущий `ProfileData` — файл YAML с именем, публичным `model_name`, engine и
командой; цен и immutable ID в нём нет. Журнал хранит имя профиля и PID, а
`model_runs` хранит только текущие managed process snapshots.

Для TASK-003 предлагается:

- фиксировать `served_profile_name`, `served_instance_id`, PID и process start
  time в момент выбора upstream;
- хранить отдельно запрошенное имя/alias, upstream model и итоговый статус;
- при переименовании/редактировании профиля не переписывать старые rows;
- до появления stable profile revision явно показывать оговорку, что имя/PID
  являются историческим ключом.

Рекомендуется отдельный immutable `profile_id` и revision. При переименовании
или правке YAML profile id сохраняется, новая revision применяется только к
новым наблюдениям; edit тарифа создаёт pricing revision и не invalidates
execution performance. Для legacy rows до миграции допускается ключ
`profile_name + process_start_time_ns` с пометкой legacy; PID без process start
небезопасен. Stats не объединяет разные конфигурации по одному имени молча.

## 7. Benchmark intervals (связь с TASK-005)

TASK-005 требует запускать тест относительно выбранного уже запущенного
профиля, продолжать обычный background collector и выделять интервал теста на
графиках. Текущий Benchmarks UI — placeholder; benchmark storage и ID нет.

До реализации TASK-005 тест получает `benchmark_run_id`, `started_at`,
`finished_at`, сценарий, профиль/PID и уровни concurrency. Его traffic входит
в общие расход токенов, requests и стоимость; отдельного фильтра benchmark не
требуется. Tooltip показывает, что выбранный интервал включал benchmark, его
сценарий и нагрузку. Пересечение с другими профилями и внешними клиентами
показывается как потенциально смешанная активность.

Статистика не должна выводить benchmark из всплеска токенов или concurrency без
явной interval-записи. Review рекомендует разделять overlay и traffic filter:
interval (`started_at`, `finished_at`, scenario) только подсвечивает фон графика
и сообщает нагрузку; traffic остаётся в общих totals и cost.
Proxy benchmark rows получают `benchmark_run_id`; runner, который обращается к
engine напрямую, должен писать по одному logical request event с profile/run
identity, status, usage и dedup key `(benchmark_run_id, client_request_id,
attempt)`. Explicit events нужны для provenance и отображения нагрузки; они
входят в общие request/cost totals. Instance counters за interval помечаются
potentially mixed, потому что обычный трафик мог идти одновременно. Исходные
usage rows и metric samples сохраняются.

## 8. Предложение API

Предлагается отдельный management endpoint, чтобы не перегружать текущие
`/api/v1/token-stats` и `/api/v1/metrics` с их фиксированными семантиками:

```text
GET /api/v1/statistics
  ?from=<RFC3339>
  &to=<RFC3339>
  &period=month|custom
  &timezone=UTC
  &boundary=[from,to)
  &profile_name=<optional>
  &model_name=<optional>
  &served_profile_name=<optional>
```

Ответ — стандартный success envelope проекта с `period`, `filters`,
`overview`, `profiles`, `clients`, `activity`, `benchmark_intervals`, `currencies`
и `missing`. Benchmark intervals всегда являются overlay и не исключают traffic
из totals. `period` возвращает normalized `[from,to)`, timezone `UTC` и event-time
policy. Точные поля, paging и лимит периода следует определить после
оценки объёма бессрочного журнала.

До появления нового endpoint текущие источники остаются полезными:

- `GET /api/v1/metrics?mode=usage` — сырые usage rows и pagination;
- `GET /api/v1/metrics?mode=token-history` — текущие 24 UTC-часа;
- `GET /api/v1/metrics/daily` — дневной snapshot archive;
- `GET /api/v1/metrics/concurrency` — архивная concurrency curve.

Новый endpoint не должен менять внешний OpenAI `/v1` contract или endpoint
внутреннего чата.

## 9. Хранение (предложение после согласования)

Существующие таблицы достаточны для прототипа чтения небольших периодов, но
полный бессрочный экран потребует определить следующие additions:

1. **Request attribution:** stable `profile_id`/revision, served profile,
   upstream model, process start identity и benchmark marker; существующий
   journal сохраняется, а attempts и direct benchmark events лучше хранить в
   нормализованных child rows с logical-request id и dedup key, чем в opaque JSON.
2. **Prices:** текущие profile price/rate settings, нормализованные к единице
   расчёта; историческую effective rate сохранять не требуется.
3. **Run intervals:** start/stop или heartbeat intervals с profile/PID/start
   identity, bucket intersection и coverage, если нужен process uptime basis.
4. **Benchmark intervals/events:** immutable interval/scenario/profile/PID
   metadata плюс explicit logical request events для bypass-proxy runner.
5. **Optional daily usage rollup:** день, timezone key, model, served profile,
   tokens, unknown counters, requests, cost state и mergeable values. Это
   оптимизация после измерения объёма журнала, а не обязательное решение этого
   черновика.

Все timestamps хранения и дневная группировка для месячного TASK-003 остаются UTC по
общему API-контракту. Это не меняет существующий timezone cleanup архива
метрик, который должен быть отдельно приведён к UTC или пересчитан при чтении.
Profile rename/delete оставляет старые rows и dangling
route/run references диагностируемыми, а не переиспользует PID как identity.

## 10. UI и дизайн

После утверждения страница должна быть добавлена в группу «Наблюдение» и
получить отдельный route `/statistics`. Реализация использует существующие
shadcn/ui `Table`, `Select`, `Tooltip`, empty/error states и тему из
`docs/specs/2026-09-29-design-system.md`. Таблицы профилей и клиентов строятся
одним переиспользуемым renderer с разным набором колонок.

Основной визуальный блок — дневная календарная heatmap в медной палитре
дизайн-системы. Для периода «месяц» одна ячейка означает один календарный день
UTC; почасовая детализация не выводится отдельным верхним графиком.
Новые графики расхода токенов и верхние KPI-блоки не добавляются. Существующий
`TokenHistoryChart` и `MetricLineChart` не используются для отдельного верхнего
графика; их можно переиспользовать только если конкретная performance series
появится в согласованной сводке профиля.

Состояния недоступности показываются текстом/иконкой и tooltip с источником и
причиной. Цвет не является единственным сигналом. На узком экране фильтры,
итоги, таблица и tooltip остаются доступными; числовые столбцы используют
tabular/monospace без уменьшения основного текста.

## 11. Открытые вопросы для согласования

1. Какие поля concurrency показывать кроме load: time-weighted average,
   maximum, distribution/quantiles?
2. Какие performance series показывать в первом UI кроме рекомендуемых
   prefill/decode, latency и native throughput; review требует раздельные
   source/semantic/weights и не смешивает их в одну цифру?
3. Подтвердить operational threshold, после которого optional daily rollup
   понадобится; review рекомендует query-time агрегации для прототипа и не
   делать rollup источником истины до измерения объёма.

## 12. Предлагаемые критерии приёмки после утверждения

### Backend/data

- Период и фильтры применяются одинаково к overview, profiles, clients,
  activity и totals;
  границы тестируются на точном переходе дня UTC.
- Один proxy request, включая streaming, считается одной логической строкой;
  retries/fallback имеют проверенную attribution policy.
- Известные input/output/cache суммируются, unknown counters сохраняются;
  cached input не удваивает input.
- Накопительные counters не превращаются в отдельные requests; direct-engine
  activity явно отделена от proxy usage.
- Prefill/decode/concurrency используют явные source, semantic и weights;
  stale/NULL не превращаются в ноль и не интерполируются через пробелы.
- Run identity, rename/edit профиля, PID reuse и external process проверены.
- Стоимость возвращается только при наличии согласованной цены/billing rule;
  изменение текущей цены проверяет пересчёт уже сохранённых rows по выбранной
  current-rate policy.
- Busy/load показывает basis, numerator и denominator; текущий неполный день и
  отсутствие run interval покрыты тестами.
- Benchmark intervals не угадываются из spike и корректно фильтруются/маркируются
  согласно TASK-005.

### Frontend

- По умолчанию видны последние 30 календарных дней UTC, фильтры модели/профиля
  и общий heatmap; ошибка/пустой ответ/unknown различимы.
- Сводка по профилям показывает input/output/cache, requests, concurrency,
  performance, cost и load без скрытого нулевого значения.
- Отдельная сводка по клиентам показывает те же usage-поля и attribution
  профилей.
- Дневная heatmap даёт доступные tooltip по дате, model/profile/client,
  benchmark и unknown/source.
- Страница и карточка профиля используют одно согласованное определение load.
- Existing dashboard charts и profile UI не меняют поведение; новый renderer
  не дублирует общие линии, tooltip и темы.
- Проверены desktop/узкий layout, keyboard focus, доступные labels/tooltips и
  `git diff --check`.

## 13. Этапы после согласования

1. Зафиксировать ответы на открытые вопросы TASK-001, TASK-004 и TASK-005 и
   перевести этот документ из draft в согласованный статус.
2. Уточнить storage/миграции для served attribution, prices, run intervals и
   benchmark intervals; не удалять существующий journal и metric archive.
3. Реализовать backend aggregation/API с contract tests unknown/cache,
   boundaries, weights, profile identity и source semantics.
4. Реализовать страницу `/statistics`, фильтры, таблицу, totals и heatmap на
   существующих UI primitives; добавить load на карточку профиля TASK-001.
5. Протестировать в Docker backend/frontend и обновить docs. Deployment и
   runtime/server checks выполняются отдельным явно согласованным этапом.
