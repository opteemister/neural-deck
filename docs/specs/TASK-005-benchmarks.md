---
title: TASK-005 — Бенчмарки производительности
status: draft_not_agreed
status_ru: Черновик, не согласовано
date: 2026-10-04
---

# TASK-005 — Черновик спецификации бенчмарков

Статус: **Черновик, не согласовано**.

Документ переводит пользовательские требования и локальное исследование в
рабочий контракт для обсуждения. Все разделы с формулами, порогами, схемами и
критериями ниже являются **предложениями**, пока пользователь их не утвердил.
Реализация, запуск моделей и нагрузка до согласования не выполняются.
Фактические пути и ограничения собраны в
[исследовании TASK-005](../research/TASK-005-benchmarks.md).

## 1. Цель и границы

Раздел Benchmarks должен запускать воспроизводимые performance-тесты относительно
выбранного уже запущенного профиля и сохранять результат так, чтобы:

- performance показывал влияние входного контекста, длины output и concurrency;
- queue, plateau, observed admission и устойчивость были видны раздельно;
- обычные метрики приложения продолжали собираться, а interval теста был виден
  на графиках как помеченная фоновая заливка;
- карточка TASK-001 и статистика TASK-003 могли использовать только результат
  с совпадающей identity и понятным источником;
- результаты разных типов нагрузки не смешивались: decode-only, mixed
  concurrency, prefill и cache tests имели собственные series и summary.

В TASK-005 входит только performance. Intelligence, задачи на качество,
agent execution, sandbox и judge перенесены в отдельную [TASK-006](../tasks/TASK-006-intelligence-benchmarks.md).
Автоматическая перенастройка engine, запуск остановленного профиля,
reboot/sweep compose-конфигурации и изменение внешнего `/v1` остаются за
границами performance runner.

## 2. Пользовательские требования

### 2.1. Набор performance-сценариев

Нужны сценарии:

1. Prefill/TTFT для context depths около 10 000, 50 000, 130 000 и 200 000
   токенов. Cell выше served context профиля сохраняется как `skipped` с
   причиной.
2. Decode при контролируемой длине output.
3. **Decode-only concurrency:** синхронная волна из N запросов с пустым или
   минимальным prompt. Режим показывает одновременную генерацию без длинного
   prefill.
4. **Mixed concurrency:** несколько потоков продолжают decode, а завершившие
   потоки сразу запускают новые запросы с большим context. Prefill новых запросов
   должен измеряться отдельно от decode работающих потоков.
5. Cache reuse, cache capacity и concurrent cache capacity.
6. Сохранение каждой проверенной точки N, context, workload и cache case, а не
   только итоговой пары `optimal/limit`.

Тест запускается для выбранного **уже работающего** профиля; профиль определяет
endpoint и model name. Во время теста обычные metrics snapshots продолжаются.
Доступные дополнительные поля (TTFT, queue, cache, VRAM, engine histograms,
power и т. п.) сохраняются только с source и state. Предварительные workload-
профили: prose, coding и reasoning/structured task. Точные prompt-ы входят в
descriptor script-а, а не зашиваются в UI.

### 2.2. Cache-сценарии

- **Reuse:** первый запрос содержит большой prefix и output; второй повторно
  отправляет тот же prefix и небольшую новую инструкцию. Сравниваются latency,
  cached tokens и runtime cache metrics. Ответ не является самостоятельным
  критерием cache hit.
- **Capacity:** последовательно прогреть 5, 10 и 20 уникальных prefixes, затем
  повторно запросить их и определить границу, после которой cache hit исчезает
  или latency возвращается к полному prefill.
- **Concurrent capacity:** повторить capacity-проверку при одновременных
  cache-backed запросах и сохранить per-cache hit/miss и latency.
- При отсутствии достоверной cache telemetry результат имеет состояние
  `unknown`, а не `cache_miss`.

### 2.3. Независимые scripts

Performance cases реализуются отдельными поддерживаемыми scripts в
`scripts/benchmarks/performance/`. Новый script не должен требовать изменения
UI под конкретное имя. Каждый зарегистрированный script предоставляет:

- descriptor с `script_id`, версией, названием, описанием, параметрами и
  поддерживаемыми workload/case;
- режим запуска относительно переданного selected profile и endpoint;
- единый JSONL output с событиями запуска, raw observations, summary,
  `status`/`reason` и неизвестными значениями;
- человекочитаемый log как дополнительный, но не основной интерфейс.

UI и backend обнаруживают только scripts, соответствующие descriptor-контракту;
произвольный файл из папки не запускается. Общий runner сохраняет script id и
версию вместе с benchmark run. Добавление descriptor-а расширяет список тестов,
таблицу и универсальные графики без отдельной frontend-ветки.

## 3. Термины и identity

**Benchmark run** — одна неизменяемая попытка теста с interval, выбранным
профилем, сценариями и результатами. **Case** — один тип измерения с одной
конфигурацией context/output/concurrency/cache. **Repetition** — повтор case.

### 3.1. Предложение: снимок выбранного запуска

При старте run сохранить:

- `profile_name`, profile file revision/updated time или другой stable revision;
- `instance_id`, `pid`, `process_start_time_ns`, `port`, `model_name`,
  `engine_id`, installation/build identity;
- resolved endpoint origin/path и command fingerprint без секретов;
- GPU selection/UUID, visible device scope и релевантные engine settings;
- request endpoint (`/v1/chat/completions` либо иной явно выбранный путь),
  sampler, prompt template mode, cache mode, context/output targets.

Разделить два уровня:

- **immutable run identity:** `instance_id + pid + process_start_time_ns` и
  snapshot выбранного процесса. `profile_name + pid` без start time не является
  ключом: PID может быть переиспользован.
- **execution-config fingerprint:** model path/revision или группа, engine и
  installation/build, resolved command, endpoint/port, GPU selection и
  environment. Изменение этих полей до следующего measured case даёт
  `identity_changed` и делает результат `non_comparable`/неприменимым для
  `optimal`/`limit`.

Display name, alias и tariff/price revision не входят в fingerprint исполнения и
не инвалидируют уже завершённый run. Context/output/cache/sampler относятся к
`case applicability` и сохраняются для сравнения, но не являются текущими
полями profile identity. Переименование YAML после run не переписывает его
provenance.

### 3.2. Profile и routing

Предложение: первая версия принимает только прямой selected profile, который
уже имеет ready/running `Instance`. Запрос через TASK-004 alias или fallback
допускается отдельным решением с фиксацией requested model, served profile и
каждой upstream attempt. Runner должен передавать явный pinned target direct
profile либо использовать отдельный managed bypass grouped-model routing, если
такой механизм будет согласован. Benchmark не должен повторно находить другой
профиль по одному public model name.

## 4. Предлагаемый lifecycle и storage

Периодический collector уже сохраняет профильные `metric_samples`. Benchmark не
должен дублировать их отдельными measurement rows: нужен только журнал запусков
с компактным итогом, чтобы связать интервал работы script-а с выбранным
профилем и строить статистику/графики.

### 4.1. Сущности (предложение)

`benchmark_runs`:

- `id`, `created_at`, `started_at`, `finished_at`, `status`;
- `group` (`performance`), `script_id`, script version, scenario name/version;
- immutable selected-profile identity из раздела 3;
- benchmark parameters: mode, workload, context, output target, concurrency,
  cache mode и repetitions;
- compact normalized result: status, duration, TTFT/prefill/decode/throughput,
  cache result, queue/admission result and error reason;
- `source_version`, `environment_fingerprint`, cancellation reason.

Это единая таблица истории запусков для всех performance scripts. В ней не
создаются отдельные схемы для prefill, concurrency или cache tests. Минимально
она должна сохранять `profile_name`, `instance_id`, PID/start identity, endpoint,
requested/public model name, verified served model name, engine, benchmark mode,
script/version, start/finish timestamps, status и параметры запуска.

Результат хранится в той же строке компактными нормализованными nullable-полями:
например, `duration`, measured repetitions, aggregate throughput, TTFT/prefill,
decode, queue/admission, cache status/cached tokens, valid/error counts и
`reason`. Новые script-ы используют те же общие поля; поле, которое не имеет
смысла для конкретного типа теста, остаётся `NULL`/`unknown`. Raw request/case
строки и payload JSON в базу не записываются.

JSONL output script-а используется только во время запуска для разбора результата
и отображения прогресса. Raw output, payload JSON и копия script-а в таблицу не
сохраняются. В таблицу попадают только компактные нормализованные поля результата
и ссылка на `script_id`/версию, который был запущен.

Периодические метрики профиля продолжают собираться существующим collector-ом.
Benchmark row не дублирует эти samples: он хранит границы запуска, параметры и
компактную итоговую подпись для графиков. Если script получает дополнительные
request-level значения, runner агрегирует их в nullable summary; недоступное
значение хранится как `NULL`/`unknown`, а не как ноль. Промежуточные cases и
repetitions после завершения запуска не сохраняются.

Техническая рекомендация по API: `POST /api/v1/benchmark-runs` создаёт run,
`GET /api/v1/benchmark-runs` перечисляет результаты,
`GET /api/v1/benchmark-runs/{id}` возвращает run/status/summary, а
`POST /api/v1/benchmark-runs/{id}/cancel` запрашивает отмену. Названия —
предложение для конкретизации, а не утверждённый внешний контракт.

### 4.2. Background metrics и графики

Предложение: collector продолжает работать с обычным интервалом. Samples,
попавшие между `started_at` и `finished_at`, не получают `benchmark_run_id` и не
копируются в benchmark storage. График получает отдельный immutable interval
overlay по времени запуска; tooltip показывает scenario/run id и пересечение с
другими активностями.

Запрещено восстанавливать interval по spike токенов, `active_requests` или
времени запуска процесса. Если benchmark storage недоступно, обычная метрика
остаётся обычной и не получает guessed marker.

### 4.3. Traffic и стоимость

Benchmark runner может выполнять запросы напрямую к выбранному endpoint или
через согласованный backend path, но отдельные benchmark request events в
первой версии не сохраняются. Request journal/profile metrics, если они и так
собираются существующим путём, остаются обычными данными приложения; benchmark
run лишь задаёт временной интервал и compact summary. Нельзя пытаться
восстанавливать принадлежность запроса к benchmark по одному public header или
по spike на графике.

Рекомендуемый безопасный default — явно решить, входят ли benchmark requests в
ordinary totals/cost; эта политика TASK-003/TASK-001 остаётся продуктовым
решением. Она не требует второй таблицы benchmark measurements. Cache остаётся
subset input; неизвестный usage не считается нулём.

## 5. Performance protocol (предложения)

### 5.1. Общие условия

Во время запуска каждый case должен собрать raw input/output token counts,
sampler, endpoint, cache mode, prompt/context target, output target, wall time,
TTFT/first-content, decode window, errors и readiness. Эти observations нужны
только runner-у для расчёта summary и не сохраняются отдельными строками.
Сравнение двух runs разрешается только при
совпадении этих параметров и environment fingerprint, либо с явной пометкой
`non_comparable`.

Порядок одного case:

1. проверить, что selected instance существует и отвечает на readiness/model
   check;
2. выполнить согласованное число warmups и отдельно пометить их; рабочий
   default для measured repetitions — пять, если descriptor не задаёт иное;
3. выполнить repetitions с фиксированным request body;
4. обработать raw observations и дополнительные snapshots в памяти;
5. вычислить summary только по валидным repetitions; ошибки и short EOS видны
   отдельно;
6. закрыть interval, проверить, что profile/PID/start identity не изменились.

Warmup engine и warmup hardware — разные понятия. Число прогревов, inter-run
delay, thermal policy и A/B interleaving остаются открытыми.

### 5.2. Prefill и TTFT

Предложение для каждого context depth:

- строить prompt с измеренным tokenizer token count, а не только word heuristic;
- для cache-sensitive tests явно выбрать `cold` (уникальный prefix) или
  `shared` (общий prefix с per-stream suffix);
- сохранять `client_ttft = first_content_at - request_sent_at`;
- если engine даёт валидные отдельные `prefill_tokens` и `prefill_seconds`,
  считать `engine_prefill_tps = prefill_tokens / prefill_seconds`;
- fallback `input_tokens / client_ttft` называть `ttft_derived_prefill` и не
  показывать как чистый prefill, потому что client TTFT включает queue;
- сохранять cache tokens отдельно и считать uncached только при валидном
  `0 <= cached <= input`.

Reference Club 3090 использует salted deep prompt и `/tokenize`; в LLM Studio
рабочий набор context depth — около 10k, 50k, 130k и 200k. Descriptor может
пропустить depth, превышающий served context, с причиной `context_limit`.

### 5.3. Decode и output

Предложение:

- для streaming autoregressive response считать
  `decode_window = last_content_at - first_content_at`. Numerator —
  `usage.completion_tokens`, если upstream его сообщил; число SSE chunks не
  является числом токенов и хранится только как
  `estimated_output_chunks`/unknown fallback;
- `client_decode_tps_estimate = output_tokens / decode_window` — оценка окна
  вывода, а не точный per-token engine timing: первый chunk может содержать
  несколько токенов, последний тоже, а межчанковые timestamps не раскрывают их
  внутренние интервалы. При одном chunk или неизвестном numerator значение
  unavailable;
- если доступны только wall и TTFT, использовать `wall - client_ttft` лишь как
  fallback с source/semantic `client_window`; это окно также включает возможные
  transport/queue effects;
- при `decode_window <= 0` или слишком малом окне возвращать unavailable, а не
  делить на epsilon;
- для sustained throughput разрешить `force_output_tokens`, но помечать
  `quality_not_measured`: `ignore_eos/min_tokens` не является quality test;
- для block/canvas/non-token emission не называть wall TPS decode rate.

Для summary хранить `mean`, `std`, `CV`, min/max, sample count и valid count.
Среднее decode по разным requests не должно смешивать measured window и
wall-derived value.

Мини-примеры для будущих synthetic tests:

- `output_tokens=120`, `send=0.00`, `first=0.40`, `last=2.90`, `end=3.00`:
  client TTFT `0.40 s`, decode window `2.50 s`, output-window estimate
  `120/2.50 = 48 tok/s`, aggregate wall output `120/3.00 = 40 tok/s`.
  Если первый chunk содержал 4 токена, 48 tok/s остаётся оценкой границ
  окна, а не доказанным token event rate.
- Четыре однотокенных content events в `t=1,2,3,4 s` дают три интервала
  между токенами за `3 s`, то есть `1 tok/s` по inter-token convention.
  Формула `output_tokens/(last-first)=4/3 tok/s` включает первый токен до
  интервала и завышает этот результат. Поэтому numerator/window convention
  должна быть записана в raw result, а chunks нельзя считать tokens.

### 5.4. Concurrency matrix

Case определяет `(mode, workload, context_tokens, output_tokens, N, cache_mode)`.
Поддерживаются два самостоятельных режима.

**Decode-only:** N клиентов синхронно начинают генерацию с пустым или
минимальным фиксированным prompt и контролируемым output. Цель — измерить
одновременный decode без длинного нового prefill. Prefill/TTFT сохраняется
отдельно, но не используется для названия результата `pure decode`, если
engine не дал отдельный decode-only timing source.

**Mixed:** несколько клиентов выполняют генерацию, а после завершения каждого
клиента тот же worker сразу отправляет следующий запрос с выбранным большим
context. Так в одном round одновременно присутствуют decode уже работающих
запросов и prefill новых запросов. Сохраняются границы каждой волны и момент
замены worker-а.

Для каждого режима round запускает N клиентов и хранит:

- `requested_n`, `admitted_peak`, `waiting_peak` и last observed running/waiting;
- per-stream output, TTFT, decode window, decode TPS и errors;
- `aggregate_output_tps = sum(valid output_tokens) / round_wall_seconds`;
  numerator берётся только из valid usage token counts; эта величина включает
  prefill и queue и потому не называется aggregate decode;
- `per_stream_decode_tps` как median валидных streams и p05/p50/p95;
- aggregate/per-stream prefill estimates с указанием cache/TTFT semantics;
- VRAM current/peak и post-warm delta, если scoped source доступен.

В UI decode-only и mixed показываются как разные серии даже при одинаковых N и
context. Их aggregate throughput нельзя объединять в одну кривую.

`requested_n`, `admitted_peak` и `waiting_peak` показываются раздельно. Если
`waiting_peak > 0` или `admitted_peak < requested_n`, это только observation,
а не доказательство queue/limit: короткие requests, staggered starts и
sampling miss могут дать такой результат. `aggregate_decode_tps` допускается
только при отдельном server-side decode-only timing contract; рекомендуемая
формула тогда `sum(valid decoded_tokens) / sum(valid decode_seconds)` для одной
и той же scoped round. Иначе в report используется имя
`aggregate_output_tps`.

### 5.5. Optimal, queue и limit

Ниже **предлагаемая классификация**, не утверждённые пороги:

- **clean:** все обязательные requests завершились без silent/error, профиль не
  потерял identity и readiness не нарушен. VRAM post-warm delta и retention —
  optional diagnostics: если scoped source доступен, они проверяются по
  согласованной полосе; если недоступны, основной throughput получает
  `diagnostics_limited`, а отсутствие данных не превращается в failure.
- **transient/unknown:** waiting был замечен кратко, `admitted_peak < N`,
  coverage недостаточен либо source/scope не подтверждены. Это не queue и не
  limit;
- **sustained queue/under-admission:** source/scope известны, polling покрывает
  согласованную долю окна, а waiting/недопуск повторяется в нескольких
  последовательных наблюдениях. Background traffic и request-start spread
  сохраняются рядом с evidence; при contention результат помечается
  `contended`;
- **plateau/optimal candidate:** clean N без sustained queue, где
  `aggregate_output_tps` максимален или находится в согласованном tolerance от
  максимума, а tail TTFT проходит порог и saturation evidence имеет состояние
  `clear`. Доступные retention/VRAM diagnostics должны пройти свои пороги; при
  unavailable источнике throughput сохраняется с `diagnostics_limited`, но
  approved `optimal`/`limit` не выдаётся без достаточного queue/admission
  evidence.
- **limit candidate:** последующий проверенный N/context не clean из-за
  устойчивой очереди, ошибки, OOM/VRAM failure или retention/TPS failure.

Рекомендованный технический default для evidence — не менее трёх valid polls
на окно и повторяемый сигнал в последовательных samples с известными
cadence/source/scope; точные coverage/threshold остаются product decision.
Одного `waiting_peak > 0` или `admitted_peak < N` недостаточно: короткий
request, staggered start или sampling miss дают только `unknown`. Для N=4,
где короткие requests дали `admitted_peak=2`, а queue sample отсутствует,
статус `saturation=unknown`, не `limit`.

`optimal` и `limit` — кандидаты конкретного matrix/case и не являются
универсальным пределом профиля. Sweep предоставляет bounded evidence: при N=8
clean и непроверенном N=12 результат — `highest_tested_clean=8`,
`limit=not_reached`, а не `tested_max=8` или фактический максимум.

### 5.6. Sweep и stop variants

Варианты для обсуждения:

- **bounded matrix:** заранее заданные context/N; самый воспроизводимый режим;
- **early-stop per context:** после первого fail пропускать большие N только
  если согласована monotonic assumption; иначе продолжать все cells;
- **wall-budget stop:** прекращать новые cells при лимите времени, сохраняя
  `skipped=budget`;
- **live sweep:** менять только запросы на выбранном already-running profile,
  без reboot; N выше фактических slots помечается queue;
- **reboot/config sweep:** изменять engine max-num-seqs и перезапускать профиль
  для каждого N. Этот режим не входит в первую версию, потому что противоречит
  условию selected already-running profile и может затронуть обычный трафик;
- **cancel/identity stop:** немедленно завершать новые requests при отмене,
  readiness loss, identity change или repeated transport errors, сохраняя
  partial status.

Остановка по одной медленной repetition без ошибки не является доказанным
limit; она должна быть видна как outlier или retry по утверждённой политике.

## 6. Связи с другими задачами

- **TASK-001:** карточка показывает benchmark optimal/limit только для
  identity-matched valid result; без результата — `not measured`. Наблюдаемый
  live max и engine configured slots показываются отдельно.
- **TASK-003:** benchmark interval, traffic filter, token/cache/cost policy и
  timezone должны иметь один контракт. Отдельные benchmark request events в
  первой версии не создаются; interval overlay используется только для графика
  и не является attribution. Рекомендуемый default —
  исключать benchmark из ordinary totals/cost с явным `include_benchmarks`;
  окончательное включение остаётся product decision. Ordinary metrics
  продолжаются во время run; spike без marker не классифицируется как benchmark.
  Для mixed engines суммируются только совместимые semantics с весами их
  знаменателей; несовместимые series остаются раздельными или `unknown`, а
  period/boundary используют явную IANA timezone без server-timezone fallback.
- **TASK-004:** прямой selected profile является первой границей. Alias/fallback
  требует served-profile attribution и отдельного тестового сценария.
- **TASK-001/003 цены:** benchmark traffic нельзя включать или исключать из
  cost silently; исторический tariff revision должен применяться общей
  политикой.
- **TASK-006:** intelligence benchmark, agent execution, task folders, sandbox
  и judge проектируются отдельно и не входят в performance storage/runner.

## 7. Предлагаемые критерии приёмки — уточняются после решений

Пункты ниже являются рабочим чек-листом для будущей реализации, а не
утверждёнными требованиями.

1. Запуск невозможен, если выбранный profile/instance не ready/running или
   endpoint identity потеряна; результат содержит понятный status/reason.
2. Run сохраняет start-aware profile/instance/PID identity, execution
   fingerprint, model/engine, request config, context/output/N/cache mode,
   sampler, source version и timestamps; display name, alias и tariff revision
   не инвалидируют уже завершённую execution identity.
3. Warmup не попадает в measured summary; runner учитывает raw repetitions и
   ошибки во время запуска, а в `benchmark_runs` сохраняет только их counts и
   итоговый status.
4. Prefill distinguishes engine timing from client TTFT; queue-inclusive value
   не выдаётся за pure prefill.
5. Decode не делит на нулевое/вырожденное окно; canvas/unknown semantics видны
   как unavailable или wall-derived с подписью.
6. Summary хранит `aggregate_output_tps` (wall-inclusive) и per-stream
   decode-window estimates, admitted/waiting observations, TTFT tails,
   valid/error counts и cache source; aggregate decode появляется только при
   отдельном decode-only timing source.
7. Runner сообщает skipped cells и причину (context/slot/KV/budget/early-stop)
   во время выполнения, а итоговая строка сохраняет aggregate counts/status,
   без отдельной записи на каждую cell.
8. `optimal`/`limit` появляются только после согласованных threshold и valid
   identity; `admitted_peak < N` или единичный `waiting > 0` дают `unknown` без
   source/scope/coverage evidence; непроверенный следующий N даёт
   `limit=not_reached`, а observed max не называется profile limit.
9. Каждый запуск имеет одну строку в общей `benchmark_runs` table с выбранным
   profile/instance, параметрами, start/finish, status и компактным summary;
   обычные snapshots и proxy journal продолжают собираться, а interval/run id
   визуально отмечается на графике без угадывания по spike. Отдельные строки
   measurements и benchmark request events не создаются.
10. Benchmark traffic имеет явно выбранную TASK-003 policy и не меняет cost
    без согласованного price/cache contract.
11. UI/API отображают unavailable, stale, error, cancelled, skipped и
    non-comparable состояния текстом; неизвестное cache состояние не становится
    `cache_miss`.
12. Тестовые данные не меняют внешний `/v1` контракт.

## 8. Этапы и границы

### Этап A — решения

- Утвердить direct profile identity, readiness и alias/fallback boundary.
- Утвердить descriptor-ы, context depths 10k/50k/130k/200k, workload profiles,
  output policy, warmups/repetitions, cache modes и N-list.
- Утвердить formulas/semantics для client TTFT, engine prefill, decode window,
  decode-only/mixed throughput, cache hit/miss и error/short-EOS handling.
- Утвердить JSONL schema, script/version storage, plateau/queue/limit thresholds
  и stop variants.
- Совместно с TASK-003 выбрать interval/traffic/cost policy; с TASK-001 выбрать
  правила публикации optimal/limit на карточке.

### Этап B — performance storage/API

После утверждения добавить benchmark run/case/interval contract, сохранять raw
observations и возвращать status/source/validity. Не заменять existing
`metric_samples`, `model_runs` или request journal.

### Этап C — scripts, runner и UI

Добавить каталог независимых performance scripts, descriptor discovery, общий
runner для уже работающего profile, JSONL parser, cancellation, background
interval marker и UI result table/curves. Reboot/config sweep и alias routing
остаются отдельными границами performance runner.

### Этап D — проверка

Проверить synthetic fixtures для formulas, queue/admission distinction,
cache/null, identity change, skipped cells, short EOS, stale/unavailable,
partial/cancelled runs и graph overlays. Реальный benchmark на модели требует
отдельного явного разрешения и не является частью проверки документа.

## 9. Открытые вопросы

Технические defaults уже предложены выше: context depths 10k/50k/130k/200k,
пять measured repetitions, отдельные decode-only и mixed modes, cache tests для
5/10/20 prefixes, descriptor discovery и JSONL output. Остаются только вопросы
конкретизации:

1. Какой точный список N и output length использовать в первом descriptor-е?
2. Какой prompt и третий workload-профиль закрепить рядом с prose и coding?
3. Какой процент plateau, retention floor, coverage и stop policy считать
   достаточными для `optimal`/`limit`?
4. Какую политику TASK-003 выбрать для включения benchmark traffic в totals/cost?
5. Нужен ли alias/fallback-сценарий сразу или только прямой selected profile?
