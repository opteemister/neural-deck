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
- В totals входят успешные, failed и unknown запросы. Их статусы
  показываются отдельно.
  Прерванные (`interrupted`, `client_disconnected`) исключены из request-расчётов,
  включая токены, признаки неполных данных, стоимость и activity heatmap.
  Загруженность движка независимо учитывает фактически наблюдаемую работу.
- Benchmark traffic входит в totals, tokens и cost; benchmark interval — только
  overlay/маркер и не фильтрует обычную статистику.
- Load — time-weighted active concurrency / historical max concurrency профиля.
  Historical max — максимальное число параллельных запросов из метрик движка
  за всю историю. Prefill и Decode — взвешенные средние измерений движка за
  выбранные календарные дни, с объединением дневного архива и сырых измерений
  без двойного счёта. Журнал запросов не является источником этих скоростей.
  Load интегрирует число активных запросов между свежими измерениями движка;
  разрывы более 15 секунд не интерполируются. Старый архив без длительностей
  даёт unavailable, а не ложный ноль. Малые проценты (например 0,04%) сохраняются
  в отображении; пояснение находится у заголовка колонки.
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
   и `started_at` для unknown. Прерванные запросы исключены. Один logical request целиком попадает
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
- В requests входят успешные, terminal failed/HTTP-error и unknown rows.
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

External/unmanaged rows без profile identity показываются отдельно с
`unavailable/unknown`, а не объединяются с похожим профилем.

Таблица клиентов содержит тот же usage-набор: input, cached input, output,
requests, status counters и cost, а также список фактически обслуживших
профилей. Profile-only поля load и engine performance для клиента показываются
только при наличии корректной attribution; иначе имеют `unavailable`.
Вместо «Клиент #N» показывается API-токен клиента, для анонимного клиента — IP.

Итоги за период выводятся внутри заголовка/подвала соответствующей таблицы.
Известные суммы не включают unknown values, а недоступная стоимость не
отображается как ноль.

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
partial usage) получает bucket по `finished_at`; unknown row без
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
  зафиксированным unknown исходом, включённых
  выбранным `request_event_time` и фильтрами. Рекомендуемая граница периода
  `[from,to)` и единое правило применяются к таблице, totals и heatmap.
- Failed/HTTP-error rows входят в requests и получают
  отдельные status counters (`failed`, `unknown`). Tokens и cost
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
При неполных данных показывается сумма по запросам с известными input,
cached input, output и всеми тремя ценами, с `state: partial`, звёздочкой и
числом неоценённых запросов в подсказке. Неизвестный cached input не считается
нулём. Если не удалось оценить ни одного запроса, показывается прочерк;
пустой период имеет стоимость $0. Правило одинаково для сводки и обеих таблиц.

### 5.6. Busy/load (решено)

Numerator — интеграл `active_requests` из метрик движка по свежим наблюдениям,
с сохранением длительностей в дневном архиве. Журнал не определяет Load.
Denominator — UTC-период, умноженный на historical max concurrency из метрик движка за
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
