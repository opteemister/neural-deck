---
title: TASK-005 — Бенчмарки производительности и интеллекта
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

Раздел Benchmarks должен запускать воспроизводимые тесты относительно выбранного
уже запущенного профиля и сохранять результат так, чтобы:

- performance показывал влияние входного контекста, длины output и concurrency;
- queue, plateau, observed admission и устойчивость были видны раздельно;
- обычные метрики приложения продолжали собираться, а interval теста был виден
  на графиках как помеченная фоновая заливка;
- карточка TASK-001 и статистика TASK-003 могли использовать только результат
  с совпадающей identity и понятным источником;
- intelligence отдельно хранил задачи, prompt/files, артефакты и ручную или
  будущую judge-оценку, не смешивая качество с TPS.

В задачу входят две группы: **performance** и **intelligence**. Полный scope
intelligence (logic/code bug tasks, custom prompts/files, agent execution в
ограниченной папке и отдельный judge) сохраняется; он реализуется отдельным
этапом и собственной sub-specification sandbox/judge, а не вычёркивается из
версии. Автоматическая перенастройка engine, запуск остановленного профиля,
reboot/sweep compose-конфигурации и изменение внешнего `/v1` остаются за
границами performance runner. Политика оплаты benchmark traffic и конкретные
разрешения sandbox требуют продуктового решения.

## 2. Пользовательские требования

### 2.1. Performance

Нужны сценарии:

1. Один запрос с большим входным контекстом — prefill/TTFT и изменение скорости
   при разных глубинах context.
2. Один запрос с большой генерацией — decode throughput при фиксированной или
   явно заданной длине output.
3. Матрица context × concurrency — prefill, decode, queue и суммарный throughput
   при росте числа параллельных клиентов.
4. Поиск кандидата на optimal concurrency (плато aggregate output без
   устойчивой очереди) и предела/limit (очередь, недопуск, ошибка; доступные
   VRAM/retention diagnostics могут добавить failure evidence). Порог плато,
   максимумы, шаги и правило остановки требуют согласования.
5. Сохранение каждого проверенного уровня, а не только итоговой пары
   `optimal/limit`.

Тест запускается для выбранного **уже работающего** профиля; профиль определяет
endpoint и model name. Во время теста обычные metrics snapshots продолжаются.
Доступные дополнительные поля (TTFT, queue, cache, VRAM, engine histograms,
power и т. п.) сохраняются только с source и state.

### 2.2. Intelligence

Нужны сценарии для logic и programming, включая заранее подготовленный баг,
который модель должна исправить. Результат может быть получен разными путями;
нужно различать корректное исправление и обходной результат. Пользователь должен
иметь возможность создавать custom prompt и папку файлов задания, запускать
агента в этой папке и просматривать артефакты.

Judge, критерии, процесс проверки, повторяемость и безопасность sandbox —
отдельная тема для согласования. До решения judge нельзя выдавать числовой
quality score как установленный факт.

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

Предлагается ввести отдельное хранилище benchmark, не переиспользуя
`model_runs` или `metric_samples` как журнал тестов.

### 4.1. Сущности (предложение)

`benchmark_runs`:

- `id`, `created_at`, `started_at`, `finished_at`, `status`;
- `group` (`performance`/`intelligence`), scenario name/version;
- immutable selected-profile identity из раздела 3;
- `config` без секретов, `source_version`, `environment_fingerprint`;
- `error`, cancellation reason и summary reference.

`benchmark_cases`:

- `run_id`, case id/type, context target, output target, concurrency N,
  cache mode, warmup/repetition counts, sampler;
- stage (`warmup`, `measured`, `cooldown`), started/finished times, status;
- raw observations: request send/first token/last token/end, input/output/cache
  token counts, queue/engine metrics, errors, source/state;
- derived summary с формулами/weights и validity flags.

`benchmark_intervals` может быть отдельной таблицей или частью run, но должен
содержать `run_id`, profile/PID/start identity, start/end и label для overlays.
Обычные `MetricSample` и request journal не удаляются и не заменяются копиями.

Рекомендуется отдельная нормализованная `benchmark_request_events` для каждого
logical request: `run_id`, `case_id`, repetition, stable request id,
selected instance/PID/start, sent/first/last/end timestamps, input/output/
cached token counts, status/error, source (`proxy`/`direct`) и tariff revision.
Это не замена `request_journal`: direct engine request иначе не будет иметь
journal row. JSON metadata допустима для engine-specific extras, но основные
поля должны быть колонками для дедупликации и статистики.

Техническая рекомендация по API: `POST /api/v1/benchmark-runs` создаёт run,
`GET /api/v1/benchmark-runs` перечисляет результаты,
`GET /api/v1/benchmark-runs/{id}` возвращает cases/events/status, а
`POST /api/v1/benchmark-runs/{id}/cancel` запрашивает отмену. Названия —
предложение для конкретизации, а не утверждённый внешний контракт.

### 4.2. Background metrics и графики

Предложение: collector продолжает работать с обычным интервалом. Samples,
попавшие между `started_at` и `finished_at`, получают ссылку на run только если
это сделано явно; иначе график получает отдельный immutable interval overlay.
Tooltip показывает scenario/run id и пересечение с другими активностями.

Запрещено восстанавливать interval по spike токенов, `active_requests` или
времени запуска процесса. Если benchmark storage недоступно, обычная метрика
остаётся обычной и не получает guessed marker.

### 4.3. Traffic и стоимость

Рекомендация для технического контракта: benchmark event является источником
logical request для direct endpoint. Если managed bypass проходит через proxy,
он передаёт тот же `benchmark_run_id` и stable request id; TASK-003 объединяет
proxy journal и benchmark events по id и оставляет одну запись. Время interval
используется только для графического overlay и не является attribution.

`benchmark_run_id` не должен доверяться произвольному внешнему клиенту через
один public header: пометка benchmark включается только из trusted backend-runner
context с проверенными run/attempt identity. Внешний или неподтверждённый header
хранится как opaque metadata и не переводит запрос в benchmark mode, не
исключает его из totals и не меняет cost attribution.

Рекомендуемый безопасный default — исключать benchmark events из ordinary
totals/cost, сохраняя явный фильтр `include_benchmarks=true`. Включение в
обычные totals, отдельная стоимость и правила tariff revision — продуктовый
выбор TASK-003/TASK-001; ни один вариант не должен появляться молча. Cache
остаётся subset input; неизвестный usage не считается нулём.

## 5. Performance protocol (предложения)

### 5.1. Общие условия

Каждый case обязан сохранять raw input/output token counts, sampler, endpoint,
cache mode, prompt/context target, output target, wall time, TTFT/first-content,
decode window, errors и readiness. Сравнение двух runs разрешается только при
совпадении этих параметров и environment fingerprint, либо с явной пометкой
`non_comparable`.

Порядок одного case:

1. проверить, что selected instance существует и отвечает на readiness/model
   check;
2. выполнить согласованное число warmups и отдельно пометить их;
3. выполнить repetitions с фиксированным request body;
4. сохранить raw observations и дополнительные snapshots;
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

Reference Club 3090 использует salted deep prompt, `/tokenize` и по умолчанию
10k/90k depth; это предложение к адаптации, а не принятые размеры.

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

Предложение: case определяет `(context_tokens, output_tokens, N, cache_mode)`.
Каждый round запускает N клиентов одновременно и хранит:

- `requested_n`, `admitted_peak`, `waiting_peak` и last observed running/waiting;
- per-stream output, TTFT, decode window, decode TPS и errors;
- `aggregate_output_tps = sum(valid output_tokens) / round_wall_seconds`;
  numerator берётся только из valid usage token counts; эта величина включает
  prefill и queue и потому не называется aggregate decode;
- `per_stream_decode_tps` как median валидных streams и p05/p50/p95;
- aggregate/per-stream prefill estimates с указанием cache/TTFT semantics;
- VRAM current/peak и post-warm delta, если scoped source доступен.

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

## 6. Intelligence protocol (предложения)

Предлагается отдельный task package:

- immutable task id/revision, prompt template и declared expected behavior;
- fixture directory только внутри разрешённого workspace;
- код/данные до запуска, diff после запуска, stdout/stderr и artifact manifest;
- selected profile/endpoint identity и полный request transcript без секретов;
- resource/time/file limits, cancellation и статус sandbox;
- human review или future judge result с отдельным provenance.

В performance prompt может быть force-output и синтетический haystack; в
intelligence prompt нельзя считать такой output quality. Нельзя считать тест
успешным по HTTP 200, наличию файла или строковому совпадению без утверждённого
criterion. Полный agent execution scope сохраняется в версии, но его
реализация должна пройти отдельный этап и sub-specification: sandbox
permissions, task folder boundary, доступные tools, cancellation, artifact
manifest и judge/rubric. До утверждения judge score имеет состояние `unjudged`,
а не нулевое качество; это не исключает сам запуск и сохранение артефактов в
разрешённой sandbox.

## 7. Связи с другими задачами

- **TASK-001:** карточка показывает benchmark optimal/limit только для
  identity-matched valid result; без результата — `not measured`. Наблюдаемый
  live max и engine configured slots показываются отдельно.
- **TASK-003:** benchmark interval, traffic filter, token/cache/cost policy и
  timezone должны иметь один контракт. Direct requests идут в
  `benchmark_request_events`; proxy path передаёт тот же logical request id,
  а TASK-003 deduplicate-ит источники по id. Interval overlay используется
  только для графика и не является attribution. Рекомендуемый default —
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

## 8. Предлагаемые критерии приёмки — уточняются после решений

Пункты ниже являются рабочим чек-листом для будущей реализации, а не
утверждёнными требованиями.

1. Запуск невозможен, если выбранный profile/instance не ready/running или
   endpoint identity потеряна; результат содержит понятный status/reason.
2. Run сохраняет start-aware profile/instance/PID identity, execution
   fingerprint, model/engine, request config, context/output/N/cache mode,
   sampler, source version и timestamps; display name, alias и tariff revision
   не инвалидируют уже завершённую execution identity.
3. Warmup не попадает в measured summary; raw repetitions и ошибки доступны.
4. Prefill distinguishes engine timing from client TTFT; queue-inclusive value
   не выдаётся за pure prefill.
5. Decode не делит на нулевое/вырожденное окно; canvas/unknown semantics видны
   как unavailable или wall-derived с подписью.
6. Summary хранит `aggregate_output_tps` (wall-inclusive) и per-stream
   decode-window estimates, admitted/waiting observations, TTFT tails,
   valid/error counts и cache source; aggregate decode появляется только при
   отдельном decode-only timing source.
7. Matrix сохраняет skipped cells и причину (context/slot/KV/budget/early-stop).
8. `optimal`/`limit` появляются только после согласованных threshold и valid
   identity; `admitted_peak < N` или единичный `waiting > 0` дают `unknown` без
   source/scope/coverage evidence; непроверенный следующий N даёт
   `limit=not_reached`, а observed max не называется profile limit.
9. Каждый direct/proxy benchmark request имеет logical event и dedup key;
   обычные snapshots и proxy journal продолжают собираться, а interval/run id
   визуально отмечается на графике без угадывания по spike.
10. Benchmark traffic имеет явно выбранную TASK-003 policy и не меняет cost
    без согласованного price/cache contract.
11. Intelligence task сохраняет revision, prompt/files, artifacts, sandbox
    status и provenance judge; отсутствие judge не маскируется score.
12. UI/API отображают unavailable, stale, error, cancelled и non-comparable
    состояния текстом; тестовые данные не меняют внешний `/v1` контракт.

## 9. Этапы и границы

### Этап A — решения

- Утвердить direct profile identity, readiness и alias/fallback boundary.
- Утвердить scenario sizes, output policy, warmups/repetitions, cache modes,
  plateau/queue/limit thresholds и stop variants.
- Утвердить formulas/semantics для client TTFT, engine prefill, decode window,
  aggregate/per-stream throughput и error/short-EOS handling.
- Совместно с TASK-003 выбрать interval/traffic/cost policy; с TASK-001 выбрать
  правила публикации optimal/limit на карточке.
- Отдельно обсудить task folders, agent sandbox, judge и artifact retention.

### Этап B — performance storage/API

После утверждения добавить benchmark run/case/interval contract, сохранять raw
observations и возвращать status/source/validity. Не заменять existing
`metric_samples`, `model_runs` или request journal.

### Этап C — runner и UI

Добавить bounded single/matrix runner для уже работающего profile, cancellation,
background interval marker и UI result table/curves. Reboot/config sweep и
alias routing остаются отдельными границами performance runner.

### Этап D — intelligence execution

Сохранить полный пользовательский scope: подготовить отдельную
sub-specification task package/sandbox/judge, затем реализовать logic/code bug
tasks, custom prompt/files, agent execution, artifacts и отдельный human/future
judge provenance. До этой sub-specification agent не получает неописанные
filesystem/network/process permissions; это порядок проектирования, а не
исключение intelligence из версии.

### Этап E — проверка

Проверить synthetic fixtures для formulas, queue/admission distinction,
cache/null, identity change, skipped cells, short EOS, stale/unavailable,
partial/cancelled runs и graph overlays. Реальный benchmark на модели требует
отдельного явного разрешения и не является частью проверки документа.

## 10. Открытые вопросы

Технические defaults уже предложены выше: batch storage с
`benchmark_request_events`, direct/proxy dedup по logical id, рекомендуемые
`/api/v1/benchmark-runs*` endpoints и default exclusion benchmark из ordinary
totals с явным filter. Остаются решения пользователя:

1. Какие context depths, output lengths, N-list, warmups, repetitions и wall
   budget являются минимальным профилем? Нужны ли 10k/90k как reference anchors?
2. Какой sampler/prompt template обязателен для сравнимых runs? Допустимы ли
   greedy и sampled режимы как разные series?
3. Считать ли prefill через pure engine histogram, client TTFT или оба; какую
   plateau tolerance, retention/TPS floor и coverage threshold утвердить?
4. Какой stop variant нужен в первой версии: полная matrix, early-stop при
   monotonic assumption или wall-budget? Как трактовать non-monotonic curve?
5. Включать ли benchmark requests в TASK-003 totals и TASK-001 pricing; как
   применять historical tariff revision и cache policy?
6. Разрешены ли тесты через TASK-004 alias/fallback; как фиксировать attempts,
   served profile и partial response?
7. Какой retention/export нужен для raw results и intelligence artifacts?
8. Какие filesystem/network/process capabilities получает intelligence agent,
   как изолировать task folder и кто утверждает judge/rubric?
