---
title: TASK-003 — страница статистики
status: agreed
date: 2026-10-04
---

# Согласованная спецификация

Решения ниже являются контрактом для реализации страницы статистики. Все даты
в базе, API и UI интерпретируются как UTC. Текущий тариф профиля применяется
при чтении статистики.

## Решения 2026-10-04

- По умолчанию показываются последние 30 календарных дней: от 00:00 UTC
  первого дня до текущего момента UTC. Период в API — `[from,to)`.
- Основная activity — дневная heatmap за последний год. Ячейки — чистые прямоугольники
  без текста внутри; даты, токены и статусы доступны через tooltip/accessible
  label. Легенда находится только за пределами ячеек.
- Heatmap — 53 колонки недель и 7 строк дней (Пн–Вс), квадратные ячейки,
  gap 3 px, месяцы сверху и подписи дней слева. Неделя начинается с понедельника,
  первая — за 52 недели до текущей. Пустые дни нейтральные; будущие — пустые.
  Текущая неделя всегда показана целиком до воскресенья, даже в понедельник.
  Все 53 недели помещаются в ширину блока без горизонтальной прокрутки.
  На узком экране gap 1 px и подписи каждого второго месяца; сами дни не скрываются.
- Фильтров модели и профиля нет. Перед таблицами один общий Select периода:
  неделя (7 календарных дней), 30 дней по умолчанию, 3 и 6 календарных месяцев
  назад от текущей даты (00:00 первого дня). Видимых подписей UTC нет;
  расчёты и хранение остаются UTC. В таблицах Input, Cached и Output — отдельные колонки,
  load сопровождается компактной полосой. Пустые таблицы заменяются сообщением.
- Сверху сводка за 30 дней в две строки по 6 метрик: requests, input, cached,
  output, ошибки, USD; сессии, активные дни, streak, success rate, latency P95,
  средняя latency. P95 — nearest rank завершённых запросов; success rate —
  success / все запросы; streak — подряд активные UTC-дни, заканчиваясь сегодня
  или вчера, внутри периода сводки. Сессии пока не записываются: показывать
  unavailable, не заменять числом клиентов/запросов. После activity идут таблицы
  по профилям и ниже по клиентам. Верхние графики не нужны.
- Cached input является частью input. На профиле задаются три цены в USD за 1
  млн токенов: input, cached input и output. Стоимость считается как
  `(input-cached)*input_price + cached*cached_price + output*output_price`.
- В totals входят успешные, failed, interrupted и unknown запросы. Их статусы
  показываются отдельно.
- Benchmark traffic входит в totals, tokens и cost; benchmark interval — только
  overlay/маркер и не фильтрует обычную статистику.
- Load — time-weighted active concurrency / historical max concurrency профиля.
  Historical max — максимальное число параллельных запросов за всю историю.
  Процесс в памяти без активного запроса даёт 0%.
- Выпадающие списки должны повторять текущий Select формы создания профиля;
  эталон — `frontend/src/components/ui/select.tsx`.

Исходные факты и ограничения приведены в
`docs/research/TASK-003-statistics.md`. Существующая approved-спецификация
метрик (`docs/specs/2026-10-02-profile-metrics-history.md`) определяет
снимки/архив движка и журнал usage как инфраструктуру. Пользовательские правила
этой страницы зафиксированы решениями выше.

## Review 2026-10-04 — решено review

Ниже сохранены review-рекомендации по shared contracts для TASK-001/003/004/005.
Они дополняют решения выше и описывают совместимость с остальными метриками.

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

Бывшие вопросы review закрыты решениями выше: timezone и хранение — UTC,
default period — 30 календарных дней, cache billing — отдельная третья ставка,
load — active request intervals относительно исторического max concurrency,
benchmark traffic входит в обычные totals.

## 1. Назначение

Страница «Статистика» в разделе «Наблюдение» должна показывать расход токенов,
наблюдаемую производительность, concurrency, дневную активность,
ориентировочную стоимость при наличии цен и загрузку профилей.
Таблицы строятся по выбранному периоду без UI-фильтров модели/профиля;
сводка — за 30 дней, heatmap — за последний год.

Первый экран открывается с периодом «месяц»: последние 30 календарных дней по
UTC, от 00:00 первого дня до текущего момента.

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

- Период по умолчанию — «месяц», последние 30 календарных дней по UTC.
- Перед таблицами пользователь выбирает неделю, 30 дней, 3 или 6 месяцев.
  Этот период общий для обеих таблиц. Сводка и годовая сетка не меняются.
- Фильтры моделей и профилей в UI отсутствуют; API сохраняет их для совместимости.

### 3.2. Итоги и таблицы профилей/клиентов

В таблице профилей и ниже в таблице клиентов показываются:

- количество логических запросов;
- известные input и output tokens и отдельные количества запросов с неизвестным
  input/output usage;
- известные cached input tokens и количество запросов с неизвестным cache;
- наблюдаемая concurrency с подписью метода агрегации;
- средние показатели производительности профиля, как минимум prefill и decode
  при наличии совместимого источника, с единицами и числом/весом наблюдений;
- примерная стоимость дня, только когда для каждого включённого расхода
  существует согласованная ставка и currency;
- процент загрузки профиля за выбранный период вместе с числителем, знаменателем и basis.

В верхней сводке показываются totals за 30 дней. Известные суммы не
включают неизвестные значения. Unknown counters и недоступная стоимость
остаются видимыми как «неизвестно/недоступно», а не как ноль.

В общем обзоре разрешается tooltip с разбивкой дня по моделям и/или served
profiles. Tooltip не меняет totals и не скрывает неизвестные значения.

### 3.3. Activity heatmap по дням

Нужна дневная heatmap за последний год (53 недели × 7 дней): интенсивность отражает известный расход
токенов, а tooltip сообщает календарную дату, tokens, запросы и ошибки.
Прямоугольники чистые, без текста и чисел внутри; легенда находится только
по краям/под сеткой.

Нулевой расход, неизвестный расход и отсутствие событий различимы; интенсивность
не превращает unknown в ноль. Benchmark interval отображается отдельным
контуром только при наличии явного interval event.

### 3.4. Загрузка профиля

На странице статистики показывается процент загрузки за период. В подписи
обязательно указывать определение basis:

- «процесс профиля был запущен»; или
- «профиль обрабатывал хотя бы один запрос».

Выбрано определение «профиль обрабатывал активные запросы». Знаменатель —
период в UTC и historical max concurrency за всю историю профиля. Если
исторический max равен нулю, показатель равен 0% и явно показывает max 0.

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
    "method": "time_weighted_active_concurrency"
  },
  "performance": {
    "prefill_tps": {"value": null, "weight": null, "source": null},
    "decode_tps": {"value": null, "weight": null, "source": null}
  },
  "load": {
    "percent": null,
    "numerator_seconds": null,
    "denominator_seconds": null,
    "basis": "historical_max_concurrency",
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

Для этой страницы concurrency строится по фактическим интервалам запросов из
журнала: активным считается запрос от `started_at` до `finished_at` либо до
момента чтения для незавершённой строки. В каждый момент времени уровень —
число пересекающихся запросов; нагрузка использует time-weighted сумму этого
уровня. Простой период работы профиля и загрузка процесса в памяти не входят.
Исторический max concurrency считается по всей истории профиля и служит
знаменателем load. Невалидные интервалы отбрасываются, пробелы не
интерполируются.

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

### 5.4. Стоимость (решено)

Профиль содержит три текущие ставки в USD за миллион токенов: input, cached
input и output. Исторические строки пересчитываются по текущим ставкам профиля
при чтении статистики. Нельзя считать нулевую цену, отсутствие цены и
неизвестный cache одинаковыми состояниями.

Currency для этой версии фиксирована как USD. Иллюстративный пример:
`input=100`, `cached=20`, `output=50`, rates
`input=2/M`, `cached=0.5/M`, `output=8/M` дают
`(80*2 + 20*0.5 + 50*8) / 1_000_000 = 0.00057`. Cached tokens уже входят в
input subset; если cached rate не утверждён, result остаётся `partial` или
`unavailable`, а не считается бесплатным молча.

### 5.5. Cache billing (решено)

Исходные cached tokens отображаются как subset input, вычитаются из обычного
input и оплачиваются по отдельной cached-input ставке. UI и API хранят все три
token counters и состояние стоимости, если ставка или usage неизвестны.

### 5.6. Busy/load (решено)

Numerator — time-weighted сумма фактических request intervals из журнала.
Denominator — UTC-период, умноженный на historical max concurrency профиля за
всю доступную историю. Профиль в памяти без active request не считается
загруженным.

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

Все timestamps хранения, группировки, очистка архива и UI остаются в UTC по
общему API-контракту. Profile rename/delete оставляет старые rows и dangling
route/run references диагностируемыми, а не переиспользует PID как identity.

## 10. UI и дизайн

Страница добавлена в группу «Наблюдение» и получила отдельный route
`/statistics`. Реализация использует существующие
shadcn/ui `Card`, `Table`, `Select`, `Tabs`, `Tooltip`, `Progress`, empty/error
states и тему из `docs/specs/2026-09-29-design-system.md`.

Верхние временные performance/token-графики не используются. Дневная heatmap
реализуется как отдельный renderer чистых ячеек; обычные `Card`, `Table` и
`Select` переиспользуются из приложения.

Состояния недоступности показываются текстом/иконкой и tooltip с источником и
причиной. Цвет не является единственным сигналом. На узком экране фильтры,
итоги, таблица и tooltip остаются доступными; числовые столбцы используют
tabular/monospace без уменьшения основного текста.

## 11. Закрытые вопросы согласования

1. Период — последние 30 календарных дней по UTC.
2. Bucket — день, ячейки heatmap чистые, legend только снаружи.
3. Load — time-weighted active concurrency относительно historical max.
4. Цены — текущие цены профиля, три ставки в USD за 1 млн токенов.
5. Cached input — subset input с отдельной ставкой.
6. Failed/interrupted/unknown запросы входят в requests и видны отдельными
   статусами; benchmark traffic входит в totals.
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

- По умолчанию видны сводка за 30 дней, годовая сетка и таблицы за 30 дней;
  фильтров модели/профиля нет. Ошибка/пустой ответ/unknown различимы.
- Дневная таблица показывает input/output/cache, requests, concurrency,
  performance, cost и load без скрытого нулевого значения.
- Дневная heatmap даёт доступные tooltip по дате, model/profile и
  unknown/source.
- Страница и карточка профиля используют одно согласованное определение load.
- Existing dashboard charts и profile UI не меняют поведение; новый renderer
  не дублирует общие линии, tooltip и темы.
- Проверены desktop/узкий layout, keyboard focus, доступные labels/tooltips и
  `git diff --check`.

## 13. Реализованные этапы

1. Зафиксированы UTC, месяц по умолчанию, дневная heatmap, статусы,
   benchmark traffic, load и три ставки профиля.
2. Добавлена backend-агрегация `/api/v1/statistics` по request journal с
   фильтрами модели/профиля, token metrics, cost, performance и load.
3. Добавлена страница `/statistics` с чистой heatmap и таблицами профилей и
   клиентов на существующих UI primitives; Select приведён к стилю формы
   создания профиля.
4. Проверены backend-тесты и Docker-сборка frontend. Deployment и
   runtime/server checks остаются отдельным явно согласованным этапом.
