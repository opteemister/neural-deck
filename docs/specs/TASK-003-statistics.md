---
title: TASK-003 — страница статистики
status: draft_not_agreed
date: 2026-10-04
---

# Черновик, не согласовано

Этот документ переводит требования `docs/tasks/TASK-003-statistics.md` в
проверяемый контракт для обсуждения. Он не разрешает реализацию. Выбор
часового пояса, тарифа, правил оплаты cache, определения busy/загруженности,
исторических цен и включения benchmark traffic остаётся открытым.

Исходные факты и ограничения приведены в
`docs/research/TASK-003-statistics.md`. Существующая approved-спецификация
метрик (`docs/specs/2026-10-02-profile-metrics-history.md`) определяет
снимки/архив движка и журнал usage как инфраструктуру. Она не выбирает UX и
бизнес-правила для этой страницы.

## Review 2026-10-04 — решено review

Ниже зафиксированы рекомендуемые shared contracts для TASK-001/003/004/005.
Это review-решения для согласования документов, не разрешение реализации и не
выбор конкретной timezone/currency/cache policy.

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
   Day/hour используют absolute interval keys + выбранный IANA offset, включая
   отдельные повторные часы DST. Run/concurrency intervals пересекаются с
   buckets; observation gaps не интерполируются.
4. Рекомендуется immutable `profile_id` + revision; PID/start — runtime identity,
   legacy rows получают явный fallback. Edit тарифа не меняет execution
   performance identity; currencies агрегируются раздельно без FX.
5. Benchmark interval — overlay. Per-request benchmark статистика требует
   explicit events с `benchmark_run_id` и dedup; interval/counter spike не
   объявляет весь overlapping traffic тестовым.

Оставшиеся вопросы: конкретная timezone и UI трактовка недели, включение
failed/interrupted rows, cache billing и price unit, отображаемая load/busy
метрика, benchmark inclusion в обычные totals и acceptance threshold TASK-005.

## 1. Назначение

Страница «Статистика» в разделе «Наблюдение» должна показывать расход токенов,
наблюдаемую производительность, concurrency, активность по дням и часам,
ориентировочную стоимость при наличии согласованных цен и загрузку профилей.
Статистика строится по выбранному периоду и фильтрам модели/профиля.

Первый экран открывается с периодом «неделя» и общей областью по всем
наблюдаемым моделям и профилям. Точная трактовка «неделя» — последние семь
календарных дней или текущая календарная неделя — открытый вопрос раздела 11.

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

- Период по умолчанию — «неделя».
- Пользователь может задать период, а границы и часовой пояс отображаются рядом
  с результатом.
- Доступны общий вид, фильтр конкретной модели и фильтр конкретного профиля.
- В каждом ответе фильтр применяется одинаково к обзору, дневной таблице,
  итогам, activity heatmap, concurrency и стоимости.
- Если фильтр выбирает alias, в деталях показываются запрошенное имя и
  фактические served profiles, когда такая attribution доступна.

### 3.2. Обзор, дневная таблица и итоги

Для каждого дня показываются:

- количество логических запросов;
- известные input и output tokens и отдельные количества запросов с неизвестным
  input/output usage;
- известные cached input tokens и количество запросов с неизвестным cache;
- наблюдаемая concurrency с подписью метода агрегации;
- средние показатели производительности профиля, как минимум prefill и decode
  при наличии совместимого источника, с единицами и числом/весом наблюдений;
- примерная стоимость дня, только когда для каждого включённого расхода
  существует согласованная ставка и currency;
- процент загрузки профиля за день вместе с числителем, знаменателем и basis.

Под таблицей показываются totals за выбранный период. Известные суммы не
включают неизвестные значения. Unknown counters и недоступная стоимость
остаются видимыми как «неизвестно/недоступно», а не как ноль.

В общем обзоре разрешается tooltip с разбивкой дня по моделям и/или served
profiles. Tooltip не меняет totals и не скрывает неизвестные значения.

### 3.3. Activity heatmap

Нужен график с квадратами для комбинаций day × hour: интенсивность отражает
известный расход токенов, а tooltip сообщает календарную дату, час, tokens,
запросы и разбивку по моделям/profiles, если она есть.

Требуемая форма и детализация остаются на согласование: одна сетка day × hour
или переключение дневного/часового режима. Нулевой расход, неизвестный расход и
отсутствие событий должны иметь различимые текстовые состояния; интенсивность
не может превращать unknown в ноль.

### 3.4. Загрузка профиля

На странице статистики и на карточке профиля TASK-001 показывается процент
загрузки за день. В подписи обязательно указывать определение basis:

- «процесс профиля был запущен»; или
- «профиль обрабатывал хотя бы один запрос».

Одно из определений и знаменатель неполного текущего дня ещё не выбраны.
Показатель с неизвестным интервалом должен иметь состояние unavailable/unknown,
а не выглядеть как 0%.

## 4. Контракт данных агрегации (предложение)

Ниже — логический контракт, который нужен UI независимо от конкретного
endpoint. Имена полей являются предложением и могут измениться после
согласования API.

```json
{
  "period": {
    "from": "RFC3339 UTC",
    "to": "RFC3339 UTC",
    "timezone": "IANA timezone supplied by the UI/request",
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
    "cost": {"value": null, "currency": null, "state": "unavailable"}
  },
  "days": [],
  "activity": [],
  "missing": []
}
```

Каждый дневной элемент должен содержать `day`, `source`, identity измерений и
те же token/unknown поля, а также:

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
    "currency": null,
    "state": "unavailable",
    "unknown_requests": 0
  }
}
```

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

Для day/hour response дополнительно нужны absolute `bucket_from`/`bucket_to` и
local UTC offset. Повторный час DST получает отдельный key, а 23/25-часовой
день не приводится к массиву с молчаливыми индексами `0..23`. Run/concurrency
intervals пересекаются с bucket и period; разрывы наблюдений не интерполируются.

## 5. Правила расчёта, которые предлагаются для согласования

### 5.1. Tokens и запросы

- `requests.total` — число логических завершённых request rows, включённых
  выбранным `request_event_time` и фильтрами. Рекомендуемая граница периода
  `[from,to)` и единое правило применяются к таблице, totals и heatmap.
- Failed/HTTP-error rows входят в requests, если они terminal; interrupted rows
  с fallback `started_at` показываются отдельно как `unknown/interrupted`, пока
  не будет согласовано включение. Attempts не считаются дополнительными
  логическими запросами.
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
Кандидат для страницы — time-weighted average, максимум и распределение
наблюдавшихся уровней concurrency, с числом секунд и samples. Переходы,
невалидные значения и большие пробелы не интерполируются. При пересечении
day/hour bucket observation contributes только пересечением своего интервала;
coverage/observed_seconds остаются явными.

Вопрос, считать ли concurrency только за время обработки запросов, весь период
работы профиля или весь календарный день, открыт. Если валидных observations
нет, поля concurrency возвращают `NULL`.

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

### 5.4. Стоимость (предложение, не решение)

Стоимость может быть вычислена только когда TASK-001 добавит цену input/output,
валюту и единицу тарифа в общий контракт профиля/настроек. Для исторической
статистики нужно выбрать один вариант:

1. записывать эффективные ставки в каждую request row при завершении;
2. хранить версию/интервал ставки и связывать с request row;
3. пересчитывать прошлое по текущей ставке.

Ни один вариант не выбран. До выбора `cost.state = unavailable` либо
`partial`, с объяснением причины. Нельзя считать нулевую цену, отсутствие цены
и неизвестный cache одинаковыми состояниями.

Currency является частью каждой стоимости. Профили с разными currencies не
суммируются и не конвертируются без явного FX source: overview возвращает
группы по currency и `multi_currency`/`unavailable`. Иллюстративный пример,
не выбор billing policy: `input=100`, `cached=20`, `output=50`, rates
`input=2/M`, `cached=0.5/M`, `output=8/M` дают
`(80*2 + 20*0.5 + 50*8) / 1_000_000 = 0.00057`. Cached tokens уже входят в
input subset; если cached rate не утверждён, result остаётся `partial` или
`unavailable`, а не считается бесплатным молча.

### 5.5. Cache billing (открытый вопрос)

Исходные cached tokens отображаются как subset input. Не выбрано, оплачиваются
ли они по полной input ставке, по отдельной скидочной ставке или исключаются из
оплачиваемого input. UI и API должны хранить известный token count независимо
от этого решения, а cost должен указывать `billing_rule`/`state`, когда правило
будет утверждено.

### 5.6. Busy/load (открытый вопрос)

Возможные numerator:

- время, когда процесс профиля был запущен/доступен;
- время, когда `active_requests > 0` по snapshots;
- объединение фактических request intervals из журнала.

Возможные denominator: полный календарный день, прошедшая часть текущего дня,
или наблюдаемое время профиля. Нельзя выбрать одно значение молча. Snapshot
каждые 5 секунд не доказывает состояние между пропущенными наблюдениями; для
точного process basis нужен журнал run intervals с PID и process start time.

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

До реализации TASK-005 нужно решить:

- получает ли тест `benchmark_run_id`, `started_at`, `finished_at`, сценарий,
  профиль/PID и уровни concurrency;
- включается ли benchmark traffic в общие расход токенов и стоимость;
- если включается, как tooltip отличает его от обычного трафика;
- если исключается, как запросы/метрики фильтруются без удаления из журнала;
- как показывать пересечение benchmark interval с другими профилями и
  внешними клиентами.

Статистика не должна выводить benchmark из всплеска токенов или concurrency без
явной interval-записи. Review рекомендует разделять overlay и traffic filter:
interval (`started_at`, `finished_at`, scenario) только подсвечивает фон графика.
Proxy benchmark rows получают `benchmark_run_id`; runner, который обращается к
engine напрямую, должен писать по одному logical request event с profile/run
identity, status, usage и dedup key `(benchmark_run_id, client_request_id,
attempt)`. Только explicit events участвуют в benchmark-filtered request/cost
totals. Instance counters за interval помечаются potentially mixed, потому что
обычный трафик мог идти одновременно. Исходные usage rows и metric samples
сохраняются.

## 8. Предложение API

Предлагается отдельный management endpoint, чтобы не перегружать текущие
`/api/v1/token-stats` и `/api/v1/metrics` с их фиксированными семантиками:

```text
GET /api/v1/statistics
  ?from=<RFC3339>
  &to=<RFC3339>
  &period=week|custom
  &timezone=<IANA timezone>
  &boundary=[from,to)
  &profile_name=<optional>
  &model_name=<optional>
  &served_profile_name=<optional>
  &benchmark_run_id=<optional>
  &include_benchmarks=true|false
```

Ответ — стандартный success envelope проекта с `period`, `filters`,
`overview`, `days`, `activity`, optional `served_profiles`, `benchmark_intervals`
`currencies` и `missing`. `period` возвращает normalized `[from,to)`, IANA
timezone и event-time policy. Точные поля, paging и лимит периода следует определить после
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
2. **Prices:** profile price/rate revision и эффективная ставка на момент
   request, если будет выбран исторический тариф.
3. **Run intervals:** start/stop или heartbeat intervals с profile/PID/start
   identity, bucket intersection и coverage, если нужен process uptime basis.
4. **Benchmark intervals/events:** immutable interval/scenario/profile/PID
   metadata плюс explicit logical request events для bypass-proxy runner.
5. **Optional daily usage rollup:** день, timezone key, model, served profile,
   tokens, unknown counters, requests, cost state и mergeable values. Это
   оптимизация после измерения объёма журнала, а не обязательное решение этого
   черновика.

Все timestamps хранения остаются UTC по общему API-контракту. Это не выбирает
конкретный часовой пояс группировки day/hour и не меняет существующий timezone
cleanup архива метрик. Profile rename/delete оставляет старые rows и dangling
route/run references диагностируемыми, а не переиспользует PID как identity.

## 10. UI и дизайн

После утверждения страница должна быть добавлена в группу «Наблюдение» и
получить отдельный route `/statistics`. Реализация использует существующие
shadcn/ui `Card`, `Table`, `Select`, `Tabs`, `Tooltip`, `Progress`, empty/error
states и тему из `docs/specs/2026-09-29-design-system.md`.

Текущий `MetricLineChart` переиспользуется для временных performance series;
существующий `TokenHistoryChart` является основой stacked token bars, но его
24-hour-only контракт не достаточен для выбранного периода. Для day × hour
heatmap понадобится отдельный визуальный renderer только после подтверждения,
что существующие компоненты нельзя расширить без дублирования поведения.

Состояния недоступности показываются текстом/иконкой и tooltip с источником и
причиной. Цвет не является единственным сигналом. На узком экране фильтры,
итоги, таблица и tooltip остаются доступными; числовые столбцы используют
tabular/monospace без уменьшения основного текста.

## 11. Открытые вопросы для согласования

1. «Неделя» — последние семь календарных дней или текущая календарная неделя?
2. Какой timezone выбрать для day/hour и как показывать переходы DST? Review
   рекомендует IANA timezone и absolute offset-aware buckets, но конкретный
   продуктовый default не выбран. Это
   не утверждает ни UTC референса vllm-studio, ни timezone ОС архива метрик.
3. Как считать concurrency: time-weighted average, максимум, квантили,
   распределение; учитывать весь день, uptime или только request processing?
4. Что считать «работает/busy»: process alive, active request или request
   interval; какой denominator у неполного текущего дня?
5. Какие цены задаются в TASK-001: валюта, за токен/тысячу/миллион,
   precision и правила отсутствующей/нулевой ставки?
6. Применяется ли историческая цена на момент запроса, interval revision или
   текущая цена при пересчёте?
7. Как оплачиваются cached input tokens: полная ставка, отдельная скидка или
   исключение; как выглядит `cost.state` при неизвестном cache?
8. Включать ли failed/interrupted/retry/fallback attempts в requests и cost?
9. При alias/failover считать attribution успешному served profile, разделять
   attempts или показывать обе величины?
10. Считать ли benchmark requests общей статистикой и стоимостью; нужны ли
    фильтры «с benchmark/без benchmark»?
11. Нужен ли в tooltip heatmap уровень profile, model, engine и benchmark?
12. Подтвердить operational threshold, после которого optional daily rollup
    понадобится; review рекомендует query-time агрегации для прототипа и не
    делать rollup источником истины до измерения объёма.
13. Какие performance series показывать в первом UI кроме рекомендуемых
    prefill/decode, latency и native throughput; review уже требует раздельные
    source/semantic/weights и не смешивает их в одну цифру?

## 12. Предлагаемые критерии приёмки после утверждения

### Backend/data

- Период и фильтры применяются одинаково к overview, days, activity и totals;
  границы тестируются на точном переходе часа/дня.
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
  историческая ставка покрыта тестом изменения цены.
- Busy/load показывает basis, numerator и denominator; текущий неполный день и
  отсутствие run interval покрыты тестами.
- Benchmark intervals не угадываются из spike и корректно фильтруются/маркируются
  согласно TASK-005.

### Frontend

- По умолчанию виден согласованный период недели, фильтры модели/профиля и
  общий обзор; ошибка/пустой ответ/unknown различимы.
- Дневная таблица показывает input/output/cache, requests, concurrency,
  performance, cost и load без скрытого нулевого значения.
- Heatmap day × hour даёт доступные tooltip по дате, часу, model/profile и
  unknown/source.
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
