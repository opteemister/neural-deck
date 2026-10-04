---
title: TASK-005 — Исследование бенчмарков производительности
status: draft_not_agreed
status_ru: Черновик, не согласовано
date: 2026-10-04
---

# TASK-005 — Исследование бенчмарков

Статус: **Черновик, не согласовано**.

Исследование выполнено в локальном checkout
`/Users/opteemist/Documents/MyProjects/llm-studio` по исходникам и документам.
Модели, нагрузка, endpoint, Docker, SSH, серверы и удалённый runtime не
запускались. Рабочее дерево содержит изменения других задач; они считаются
локальными и не являются доказательством deployed-состояния.

## Выводы

1. В локальном приложении страница Benchmarks пока является placeholder; API,
   хранилища и идентификатора benchmark run нет.
2. Выбранный уже запущенный профиль можно однозначно привязать к текущему
   `Instance`: доступны profile name, instance id, PID, process start time,
   model, engine и port. Это должно быть снимком identity в начале теста, а не
   повторным поиском профиля после завершения.
3. Обычный сборщик метрик уже нормализует prefill/decode, active/queue
   requests, TTFT, cache, VRAM и другие поля. Снимки сохраняются с nullable
   значениями и source/metadata; benchmark marker к ним пока не привязан.
4. Журнал запросов знает profile/instance/PID и timings только для прокси.
   Он не знает benchmark run, поэтому всплеск токенов или concurrency нельзя
   задним числом объявлять результатом теста.
5. Reference Club 3090 полезен как набор измеряемых сигналов, но не как
   утверждённая методика LLM Studio: он ориентирован на vLLM/OpenAI SSE,
   читает engine/container logs и `nvidia-smi`, а часть порогов зависит от
   конкретного железа и режима запуска.
6. Во время performance-запуска runner должен видеть сырые времена, токены,
   ошибки и параметры каждого case/repetition, чтобы отличить очередь, плохую
   генерацию, короткий EOS и плато. В постоянное хранилище при этом достаточно
   записывать компактный итог запуска; отдельные строки measurements не нужны.
7. Intelligence не является частью текущего контура TASK-005: качество
   результата нельзя смешивать с performance throughput. Эта часть вынесена в
   отдельную TASK-006, где позднее нужно спроектировать runner, sandbox и judge.

## Локальные источники и факты

### Профиль, endpoint и identity запуска

- `backend/app/schemas/profiles.py:53-103` описывает YAML `ProfileData`. В нём
  есть имя профиля, public `model_name`, engine, модель, command и окружение,
  но нет benchmark id или revision.
- `backend/app/process/manager.py:189-239,320-347` перед запуском подставляет
  placeholders, извлекает port и создаёт `Instance` с `profile_name`, PID,
  `process_start_time_ns`, model, engine и состоянием `starting`. Runtime
  conflict check относится к активным процессам и не является benchmark
  storage.
- `backend/app/process/runs.py:15-53` и миграция
  `backend/alembic/versions/0006_model_runs.py:12-19` хранят только текущий
  snapshot managed process в `model_runs` по PID/start time. Это не журнал
  benchmark runs и не история этапов.
- `backend/app/process/manager.py:131-187,440-598` восстанавливает текущие
  процессы и удаляет snapshot после завершения/исчезновения identity. Поэтому
  benchmark должен иметь собственную неизменяемую запись.
- `frontend/src/pages/benchmarks.tsx:5-6` показывает текстовую заготовку:
  запуск тестов, измерение скорости и сравнение качества пока отсутствуют.
  `frontend/src/pages/benchmarks.test.tsx:7-12` закрепляет именно этот
  placeholder.
- `frontend/src/lib/api.ts:228-263` содержит profiles/instances/metrics API,
  но benchmark endpoint или result type отсутствует.

Для benchmark нельзя использовать `profile_name + pid` как полную identity:
PID может быть переиспользован, поэтому нужен `instance_id` и
`process_start_time_ns`. Удобно разделить immutable run identity и
execution-config fingerprint. В fingerprint входят resolved command, model
path/revision, engine/installation/build, endpoint и GPU/environment; alias,
display name и цена относятся к представлению/тарифу и не должны инвалидировать
результат исполнения. Context/output/cache описывают применимость case, а не
текущий профиль. Это также позволяет TASK-001 обновлять имя или тариф без
потери исторической performance identity.

### Метрики и запросы

- `backend/app/metrics/performance.py:11-70` задаёт явные engine mappings:
  llama.cpp, vLLM и SGLang имеют разные имена exporter metrics. Неподтверждённые
  поля не угадываются. Нормализованные единицы prefill/decode — `tok/s`, TTFT
  — `ms`, queue/cache/VRAM имеют отдельные semantics.
- `backend/app/metrics/performance.py:133-159,272-371` выводит counter rates
  по fresh distinct samples; reset или невалидный denominator оставляет
  значение неизвестным. Для vLLM prefill может считаться по паре histogram
  токенов/времени, причём computed KV tokens исключают cached/transferred
  prefix и prefill time не включает initial queue/decode.
- `backend/app/metrics/service.py:110-191,362-409` собирает метрики активных
  instances через периодический collector, сохраняет snapshots и добавляет
  подтверждённый VRAM процесса/детей. Это фоновые наблюдения, а не точные
  границы benchmark request.
- `backend/app/schemas/instances.py:22-64` различает `Metric.state` (`ok`,
  `unavailable`, `stale`) и nullable value. Неизвестную метрику нельзя
  превращать в ноль.
- `backend/app/metrics/entities.py:12-82` имеет `metric_samples`,
  `metric_launch_extrema`, `metric_daily` и `metric_concurrency_daily`, но
  ни одна сущность не содержит `benchmark_run_id` или stage.
- `backend/app/metrics/storage.py:81-140,178-238` хранит profile/PID/model/
  engine, timestamps, values, metadata и distributions. Aggregates суммируют
  concurrent rates и используют weights для средних; это нельзя безоговорочно
  трактовать как per-request benchmark result.
- `backend/app/metrics/archive.py:96-164,229-265` архивирует daily statistics
  и time-weighted concurrency curve по observed `active_requests`; пробелы,
  reset и устаревшие samples отбрасываются. Архив не знает о benchmark interval.
- `backend/app/journal.py:25-64` хранит одну строку на proxy request:
  request/profile/instance/PID, input/output/cached tokens, timings, status и
  metadata. `backend/app/proxy/service.py:216-336,762-852` привязывает запись
  к фактически выбранному instance и извлекает usage/cache при наличии, но
  benchmark attribution отсутствует.
- `backend/app/api/metrics.py:21-103` публикует current/history/aggregates,
  token stats и daily/concurrency routes. Новый benchmark API этим контрактом
  не покрывается.

### Граница с TASK-006

По локальному `rg` и просмотренным backend/frontend routes нет runner-а агента,
изолированного task directory, модели разрешений файлов, artifact manifest или
judge pipeline. Это не блокирует performance-часть TASK-005: требования к
интеллекту, агенту, sandbox и judge перенесены в отдельную TASK-006.

## Разбор reference Club 3090

Проверены первичные GitHub-файлы в ветке `master`, commit
`01da5ebc4b28648aab76e83cdab89a3ad6c565a9`, дата проверки 2026-10-04:

- [`scripts/bench.sh`](https://github.com/noonghunna/club-3090/blob/01da5ebc4b28648aab76e83cdab89a3ad6c565a9/scripts/bench.sh)
  — client-side streaming bench и prefill probe;
- [`scripts/concurrency-probe.sh`](https://github.com/noonghunna/club-3090/blob/01da5ebc4b28648aab76e83cdab89a3ad6c565a9/scripts/concurrency-probe.sh)
  — single-N, live matrix и reboot-per-N sweep;
- [`scripts/lib/concurrency_probe.py`](https://github.com/noonghunna/club-3090/blob/01da5ebc4b28648aab76e83cdab89a3ad6c565a9/scripts/lib/concurrency_probe.py)
  — prompt generator, formulas, engine stats, VRAM checks и matrix planner.

### `bench.sh`: performance reference

В заголовке и коде `bench.sh:3-10,372-415,943-1084,1178-1285` зафиксированы:

- три warm-up и по умолчанию пять measured runs для narrative/code prompt;
- streaming `/v1/chat/completions` или raw completion, `include_usage`,
  фиксированные sampler-параметры и optional forced output length;
- `wall_tps = completion_tokens / wall_seconds`;
- `decode_tps = completion_tokens / (wall_seconds - TTFT)` при положительном
  измеряемом decode window;
- `TTFT = first content timestamp - request send timestamp`; это клиентское
  время до первого вывода и при очереди включает ожидание, поэтому не является
  чистым engine prefill;
- `prefill/PP tok/s = prompt_tokens / TTFT` в отдельном long-prompt probe;
- mean, standard deviation, CV, min/max и mean TTFT по валидным runs.

Для prefill reference строит свежий salted haystack на каждой заявке, получает
отношение tokens/word через `/tokenize` при доступности, использует глубины по
умолчанию 10k и 90k токенов и отдельное число repetitions (в текущем файле
3 для shallow и 1 для deep). Глубина сверх served context пропускается. В
измеренном prefill leg optional `DEPTH_DECODE_TOKENS` принудительно добавляет
короткий decode-at-depth; это важно, потому что обычный короткий output не
показывает поведение глубокого KV context.

Скрипт отбрасывает decode rate при `decode_window < 5% wall` по умолчанию и
помечает результат как `n/a`/canvas-granularity; деление на epsilon запрещено.
Короткий EOS ниже 25% от max tokens помечается как неиспользуемый run. Это
защищает throughput от ложного среднего, но пороги являются параметрами
reference, а не согласованными правилами LLM Studio.

Ограничения reference:

- `TTFT` смешивает queue и prefill, если запрос реально ждал admission;
- log-scraped PP (`Avg prompt throughput`) — отдельное windowed engine value,
  а не автоматически замена client timing;
- force/min tokens и `ignore_eos` измеряют sustained throughput, а не качество;
- chat/completion endpoint, prompt template, sampler, cache state и hardware
  должны совпадать между сравниваемыми runs;
- прогрев engine не прогревает hardware: источник предупреждает о thermal
  steady state и последовательном A/B bias. Локальный проект эти значения не
  собирал и не подтверждал.

### `concurrency-probe.sh` и `concurrency_probe.py`

В `concurrency-probe.sh:96-121,204-224,300-350` и
`concurrency_probe.py:128-210,517-885` обнаружены следующие измерения:

- slot count пытается определиться из vLLM `max-num-seqs`, llama.cpp `-np`,
  SGLang `max-running-requests`, `/get_server_info` или llama.cpp `/props`;
  если источник не найден, reference завершает probe ошибкой вместо молчаливого
  `N=2`.
- matrix planner отбрасывает context выше served max length, N выше served
  slots и cell, где `N * (context_tokens + generation_tokens) > KV_TOKENS`.
  При неизвестном KV pool clipping не выполняется и это явно отмечается.
- каждый round запускает N потоков, собирает streaming usage и timestamps.
  Reference использует per-stream `completion_tokens /
  (last_content - first_content)`, но это клиентская оценка: первый и последний
  chunk могут содержать несколько токенов, а streaming chunks не являются
  token events. В numerator следует брать `usage.completion_tokens`; fallback
  на число chunks нужно хранить как `estimated_output_chunks`, а не как точные
  tokens. Per-round aggregate `sum(completion_tokens) / round_wall` включает
  prefill и queue, поэтому это aggregate output throughput, не чистый
  aggregate decode;
  report per-stream — median валидных stream TPS; TTFT — median, tail — p95
  TTFT и p05 stream TPS.
- cache mode `cold` меняет prefix salt для каждого stream/round; `shared`
  повторяет общий prefix и оставляет salt в хвосте. `cached_tokens` берутся из
  usage, а approximate uncached prefill — `(prompt_tokens - cached_median) /
  median_TTFT`; это лишь reference estimate.
- engine logs дают last и peak `Running`/`Waiting`; peak важнее последнего
  snapshot, потому что последний может попасть после завершения requests.
- VRAM сравнивается как cold → warm pool fill → final post-warm growth. Clean
  допускает симметричную полосу `-VRAM_GROWTH_MB <= leak <= +VRAM_GROWTH_MB`,
  а не только рост вверх. Неизвестный `nvidia-smi` не доказывает отсутствие
  leak.
- retention — отношение поздней медианы per-stream TPS к ранней медиане после
  прогрева; в `VALIDATE` default `RETENTION_MIN=0.98`. Clean round требует
  `done=N`, без silent/error; floor TPS и retention добавляются отдельно.

Reference намеренно отделяет requested N, фактически admitted peak и waiting.
При N выше серверных slots дополнительные streams queue; такая кривая измеряет
ожидание, а не параллельную обработку. Важное ограничение: `admission_ok` в
`concurrency_probe.py:483-497` выводится, но не входит прямо в `PASS`; shell
sweep использует peak для early-stop. Это требует отдельного решения в TASK-005,
а не бездумного переноса.

`admitted_peak < requested_n` сам по себе не доказывает queue/limit: короткий
request может закончиться до polling, старты могут быть staggered, а sampling
может пропустить пик. Аналогично единичный `waiting > 0` может быть краткой
фазой admission или следствием фонового трафика. Для честной классификации
нужны source/scope (engine log или exporter), poll cadence, число samples,
coverage доля окна, request-start spread и признак background contention.
Conservative recommendation: считать saturation sustained только при известном
scope/source, достаточном покрытии окна и повторяющемся waiting/queue в
последовательных наблюдениях; иначе состояние `transient` или `unknown`. Sweep
показывает лишь bounded lower bound: если N=8 чистый и N=12 не проверен,
`highest_tested_clean=8`, а не `tested_max=8`; actual limit не достигнут.

Числовая проверка формул для будущих synthetic tests: при
`output_tokens=120`, `request_sent=0.00`, `first_content=0.40`,
`last_content=2.90`, `end=3.00` получаются client TTFT `0.40 s`, decode window
`2.50 s`, client-output estimate `120/2.50 = 48 tok/s` и wall output
throughput `120/3.00 = 40 tok/s`. Если первый chunk содержал 4 токена, окно
всё равно ограничено first/last timestamps и не доказывает per-token timing.
Для двух streams с outputs 100 и 80 и общей wall `5 s` aggregate output
throughput равен `(100+80)/5 = 36 tok/s`; это включает prefill/queue. Если их
decode windows 2.0 и 2.5 s, per-stream estimates равны 50 и 32 tok/s, median
41 tok/s — другая семантика, которую нельзя смешивать с 36.

Режимы reference:

- single-N — один уровень при уже работающем endpoint;
- matrix/live sweep — N×context без reboot, с optional early-stop и wall budget;
- `SWEEP` — reboot сервера для каждого N и поиск largest clean N at/above
  `TPS_FLOOR`;
- `SWEEP_DRY=1` — только план, без запуска; restore trap и смена compose
  config показывают, что reboot sweep опасен для контрактной задачи
  «выбранный уже запущенный профиль».

### Attribution прямого benchmark traffic

Прямой запрос к engine в обход proxy не попадает в текущий `request_journal`;
interval overlay на графике не доказывает attribution и не должен подменять
его. По уточнённому решению отдельные benchmark request events не сохраняются:
сырые времена, токены и ошибки нужны runner-у только во время запуска для
расчёта компактного итога. Профильные metric samples и существующий request
journal, если они собираются обычным путём, остаются обычными данными
приложения; `benchmark_runs` хранит только запуск, параметры и summary.
Один public header не является доверенным маркером benchmark attribution и не
должен менять totals/cost. Рекомендуемая policy по включению benchmark traffic
в обычные totals/cost остаётся продуктовым решением TASK-003/TASK-001.

### Intelligence вынесен в TASK-006

В локальном checkout нет agent runner, sandbox или judge. После уточнения scope
это не является пробелом TASK-005: logic/code tasks, custom prompts/files,
agent execution, artifacts и judge перенесены в отдельную TASK-006 с собственной
sub-specification sandbox/permissions.

## Уточнение требований пользователя 2026-10-04

Пользователь подтвердил, что за основу performance нужно взять Club 3090, но
разделить результаты по семантике нагрузки, а не выдавать один общий TPS:

- **decode-only concurrency:** синхронно запускаются N запросов с пустым или
  минимальным prompt и контролируемым output. Этот режим показывает нагрузку
  одновременной генерации без длинного prefill; prefill/TTFT всё равно
  сохраняется отдельно, если runtime его сообщает.
- **mixed concurrency:** несколько генераций уже выполняются, а после завершения
  отдельных потоков сразу запускаются новые запросы с большим контекстом. Новый
  prefill должен быть виден отдельно и не смешиваться с decode-only результатом.
- Базовые глубины context: примерно 10k, 50k, 130k и 200k токенов. Превышение
  served context — skipped cell с причиной.
- Каждый сценарий выполняется несколькими повторениями, предварительное рабочее
  значение — пять measured repetitions, если ограничения конкретного runtime не
  потребуют иного. Club 3090 остаётся источником формул и структуры, а не
  безусловным переносом hardware-specific порогов.
- Отдельно нужны cache reuse, capacity и concurrent capacity tests: повторный
  запрос с тем же prefix, последовательное заполнение 5/10/20 уникальных
  prefixes и повторная проверка hit/miss. Основные доказательства — latency,
  cached tokens и engine/runtime cache metrics; отсутствие cache telemetry даёт
  `unknown`, не `miss`.
- Performance cases должны быть независимыми scripts в выделенной папке. UI
  сканирует только scripts с descriptor-контрактом, получает metadata, запускает
  выбранный script и принимает единый JSONL output, поэтому новый script не
  требует отдельной ветки frontend-кода.
- Каждый запуск должен попадать в единственную общую `benchmark_runs` history
  table. В ней хранятся только выбранный профиль, script/version, параметры,
  интервалы, статус и компактный нормализованный итог. Отдельной таблицы
  измерений, `payload_json` и копии JSONL/script-а не требуется: обычные
  профильные metric samples уже собираются существующим collector-ом, а вывод
  script-а разбирается runner-ом во время запуска и после этого не сохраняется.

Предварительный набор workload-профилей: prose, coding и reasoning/structured
task. Точные prompt-ы и третья методика должны быть зафиксированы в descriptor,
когда будет утверждён набор scripts.

## Сопоставление с задачами 001/003/004

- **TASK-001:** карточка может показывать benchmark `optimal`/`limit` только
  при exact execution-fingerprint совпадении model/engine/build, resolved
  command/endpoint, GPU environment, валидной saturation evidence и
  workload applicability; display name/alias/tariff revision не являются
  identity key. Live observed max и `max-num-seqs` не заменяют benchmark.
- **TASK-003:** collector продолжает обычные snapshots во время benchmark;
  статистика получает явный interval/run id и отдельно решает, включать ли
  benchmark requests в token/cost totals. Spike без marker не является тестом.
- **TASK-004:** выбранный profile должен быть served profile фактического
  endpoint. Чтобы grouped model не выбрал случайный fallback, первой версии
  нужен pinned direct target или согласованный managed bypass маршрутизации.
  Benchmark по alias/failover требует отдельного сценария и attribution;
  сейчас безопаснее ограничить performance тест прямым profile.
- **TASK-006:** intelligence benchmark, agent execution, task folders,
  sandbox и judge исследуются и специфицируются отдельно.

## Не доказано исследованием

- Ни одна модель, GPU, endpoint или concurrency не была измерена в этом
  checkout.
- Нет подтверждённых порогов plateau, queue limit, retries, context depths,
  repeats, wall budget или stop policy для LLM Studio.
- Нет решения о включении benchmark traffic в общую статистику/стоимость.
- TASK-006 ещё не содержит принятого judge, разрешений agent sandbox, формата
  task folders и критериев качества code/logic; это не часть текущего scope.
- Нет доказательства, что все локальные engine exporters дают одинаковую
  prefill/decode semantics; mapping зависит от engine и metadata.
