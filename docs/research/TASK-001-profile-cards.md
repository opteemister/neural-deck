# TASK-001 — Исследование карточек профилей

Статус: **Черновик, не согласовано**
Дата исследования: 2026-10-04
Область: локальный checkout `/Users/opteemist/Documents/MyProjects/llm-studio`.

Исследование выполнено по исходным файлам, тестам и документации в checkout.
Приложение не запускалось, серверы, SSH, удалённые API и production runtime не
использовались. Незакоммиченные изменения считаются локальным состоянием и не
являются свидетельством deployed-функциональности.

## Выводы для обсуждения

1. Карточка уже имеет компактную строку профиля, состояние процесса, engine,
   автозапуск, разворачивание фактической команды, редактирование на месте и
   удаление только в раскрытой части. Новые показатели следует встроить в эту
   модель, сохранив иконные квадратные действия.
2. Источник конфигурации профиля — YAML-файл. В `ProfileData` пока нет полей
   порта, тарифов или возможностей модели. Добавление этих полей потребует
   изменения YAML/API/frontend и правил совместимости старых файлов.
3. Порт в текущем runtime извлекается общим parser-ом только для
   `--port N`, `--port=N` и `-p N`. Это синтаксическое распознавание, а не
   engine-specific доказательство: `-p` не подтверждён launch docs проверенных
   engines, а environment precedence текущий resolver не вычисляет. Для
   отсутствующего override нужен versioned engine/default registry; число из
   локального шаблона нельзя считать универсальным default. Команда с
   переменной, shell-скриптом или без распознаваемого аргумента может дать
   `null`, даже если движок имеет свой default.
4. Prefill/decode доступны как нормализованные метрики, но их источники и
   семантика зависят от движка. Среднее «профиля» нужно считать по выбранному
   периоду и весам, а не усреднять снимки без проверки знаменателей.
5. `vram_bytes` — память, занятая подтверждённым процессом и его текущими
   детьми по `nvidia-smi`; это не размер модели на диске и не общая VRAM. Размер
   локального артефакта модели — отдельное поле каталога.
6. Запросный журнал уже хранит input/output/cached tokens и timings, но цены и
   зафиксированная цена конкретного запроса отсутствуют. Нельзя считать
   стоимость, пока не согласованы валюта, единица тарифа, кэш и исторические
   изменения цен.
7. Признак Image Generation в локальном контракте не найден. Названия
   `vision`, `mmproj`, MTP и вспомогательных блоков не доказывают генерацию
   изображений.
8. Измеренная concurrency и её дневная кривая уже имеют локальные структуры
   `active_requests`/`metric_concurrency_daily`, но «оптимальная» и
   «максимальная» concurrency из бенчмарков ещё не определены. Карточка не
   должна выдавать наблюдавшийся максимум за доказанный предел.

## Требования пользователя и граница исследования

В [описании TASK-001](../tasks/TASK-001-profile-cards.md) пользователь запросил
на карточке: среднюю скорость prefill, среднюю скорость decode, память модели с
конфигурацией, оптимальную и максимальную concurrency, процент загруженности
профиля за день, порт, предупреждение о совпадении портов, признак Image
Generation, отдельные input/output цены токенов и основу для будущего расчёта
стоимости. Слово `prefield` в описании трактуется как `prefill`.

Связанные границы:

- период, знаменатель и единое определение времени работы должны быть согласованы
  с [TASK-003](../tasks/TASK-003-statistics.md);
- измерение уровней concurrency, оптимума и предела относится к
  [TASK-005](../tasks/TASK-005-benchmarks.md);
- уже согласованные правила сбора истории и кэша описаны в
  [спецификации метрик профилей](../specs/2026-10-02-profile-metrics-history.md)
  и в [дневном архиве](../specs/2026-10-03-daily-metrics.md), но они не
  согласовывают новые поля TASK-001 автоматически.

## Найденные источники

### Конфигурация и экран профилей

- [AGENTS.md](../../AGENTS.md) фиксирует файловое хранение профилей, Docker для
  локальной разработки, границу `/v1` и требование не менять внешний OpenAI API
  при изменении внутреннего чата.
- [DEVELOPMENT_RULES.md](../../DEVELOPMENT_RULES.md) требует сначала утвердить
  спецификацию, писать проектные документы по-русски и читать инструкции области.
- [docs/development/frontend.md](../development/frontend.md) требует найти и
  переиспользовать существующие UI-компоненты и не создавать параллельный
  renderer.
- [дизайн-система](../specs/2026-09-29-design-system.md) задаёт shadcn/ui,
  компактный тёмный интерфейс, строки/таблицы, медный акцент, текст и иконку
  как независимые сигналы состояния. В макете карточки не должны превращать
  каждый показатель в отдельную рамку.
- `backend/app/paths.py:15-33,44-84` вычисляет `profiles_dir` как
  `DATA_ROOT/profiles`, а SQLite хранит отдельно в `DATA_ROOT/data`.
- `backend/app/profiles/repository.py:1-6,68-146` читает YAML свежим на каждой
  операции, валидирует его и возвращает отдельные `issues`; запись выполняется
  атомарно.
- `backend/app/schemas/profiles.py:32-103` задаёт `ProfileData`: `version`,
  `name`, `model_name`, engine/installation, путь модели, optional draft,
  `command`, environment, `autostart`, `enable_metrics`. `extra="forbid"`
  означает, что новые поля сначала нужно внести в схему.
- `backend/app/api/profiles.py:29-145` предоставляет list/get/create/put/delete
  и start. Отдельного endpoint вычисленных характеристик карточки нет.
- `frontend/src/pages/profiles.tsx:25-97` загружает profiles, engines,
  local models и system; system опрашивается каждые 5 секунд. Текущий form
  отправляет только поля существующего `ProfileData`.
- `frontend/src/pages/profiles.tsx:129-169` показывает текущий
  `ProfileCard`: имя/публичное имя, относительный путь модели, статус,
  автозапуск, engine, раскрытие команды, edit/start-stop и Delete в раскрытой
  части. Это основная точка расширения.
- `frontend/src/lib/api.ts:228-263` содержит API профилей, instances и метрик;
  карточка сейчас не запрашивает агрегаты по имени профиля. Отдельного
  batch-summary endpoint также нет, поэтому запрос summary на каждую карточку
  создал бы N+1 и повторил одну и ту же агрегацию.
- `frontend/src/types/api.ts:214-260` повторяет текущие TypeScript-контракты:
  `ProfileData` и `Metric` не имеют тарифа, порта, capability или готового
  uptime/concurrency summary.

### Порт, запуск и совпадения

- `backend/app/engines/templates.py:3-11` задаёт шаблоны для `llama.cpp`,
  vLLM, SGLang, NInfer и FreeToken. Каждый текущий шаблон явно содержит
  `--host 127.0.0.1 --port 8000`.
- `backend/app/process/manager.py:222-239,327-347` подставляет command,
  извлекает порт и отклоняет запуск при совпадении с уже активным managed или
  external instance. Это runtime-защита, а не расчёт карточки для всех
  сохранённых профилей.
- `backend/app/process/manager.py:752-759` распознаёт только `--port N`,
  `--port=N` и `-p N`; динамический shell-порт не разрешается статически.
- `backend/app/process/discovery.py:93-107,178-202` для внешнего процесса
  сначала читает порт из command line, затем может найти listening socket;
  неизвестный порт остаётся `null`.
- `backend/app/schemas/instances.py:22-36` содержит `Instance.port`, но
  `ProfileData` не содержит effective port. Поэтому карточка не может получить
  порт только из профиля без отдельного правила разрешения.

Матрица launch forms/defaults (первичные docs проверены 2026-10-04; версии не
зафиксированы, поэтому default обязателен только после привязки профиля к
конкретной версии/installation):

| Engine | Source-backed launch form/default | Env/precedence | Ограничение для resolver-а |
|---|---|---|---|
| llama.cpp | [`--port PORT`, default 8080](https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md) | [`LLAMA_ARG_PORT`](https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md) поддержан; CLI имеет приоритет над env | `-p` не подтверждён docs; учитывать env, иначе `unknown` |
| vLLM | [`--port`, default 8000](https://docs.vllm.ai/en/stable/cli/serve/) | В проверенной CLI reference `VLLM_PORT` не найден | `--port N` подтверждён; `-p` и env override неизвестны |
| SGLang | [`--port`, default 30000](https://github.com/sgl-project/sglang/blob/main/docs/docs/hardware-platforms/ascend-npus/getting-started/installation.mdx) | В проверенном guide env override не установлен | Это Ascend guide/main, не универсальный default всех SGLang versions/platforms |
| NInfer | [`--port N`, default 8080](https://github.com/Neroued/ninfer/blob/master/docs/serving.md) | Env override в serving/CLI docs не установлен | Привязать к версии/installation; `-p` не подтверждён |
| FreeToken | [`--port`, default 1919](https://github.com/BAWES/freetoken/blob/main/docs/cli.md) | Env override в проверенной CLI reference не установлен | main без pinned release; `-p` не подтверждён |

Все текущие локальные templates всё ещё явно содержат `--host 127.0.0.1
--port 8000`; это только command argument и не переопределяет source-backed
default policy для ручного/другого versioned command. Для произвольного
command нет безопасного shell evaluation. Поэтому resolver обязан учитывать
explicit CLI, documented environment override и versioned engine default в
этом порядке, а при неизвестной версии/форме возвращать `unknown`.

### Runtime, метрики и история

- `backend/app/metrics/performance.py:11-71` нормализует engine-specific поля в
  `prefill_tps`, `decode_tps`, `active_requests`, `queue_requests`,
  `vram_bytes`, `uptime_s`, counters и cache ratios. Единицы для скоростей —
  `tok/s`, для VRAM — bytes; неизвестные exporter-поля не угадываются.
- `backend/app/metrics/performance.py:133-187,272-323` считает rates через
  разность свежих cumulative counters; для vLLM prefill использует согласованные
  histogram series, а при reset/неполных знаменателях значение не создаётся.
- `backend/app/metrics/performance.py:386-405` вычисляет `uptime_s` от
  подтверждённого `process_start_time_ns`; это uptime процесса, не доля времени
  работы за календарный день.
- `backend/app/metrics/performance.py:407-459` получает `vram_bytes` только
  если PID и время запуска подтверждены, и суммирует PID с текущими детьми через
  `nvidia-smi`.
- `backend/app/metrics/service.py:61-99,144-178,362-409` опрашивает активные
  instances в фоне примерно раз в 5 секунд, кэширует live-результат на 1
  секунду, записывает `MetricSample` и допускает local/Prometheus/SQLite source.
- `backend/app/metrics/storage.py:81-140,178-238` хранит snapshot с
  `profile_name`, `pid`, `engine_id`, `model_name`, observed/recorded time,
  nullable numeric fields и metadata; агрегаты различают суммы rate и
  взвешенные averages.
- `backend/app/metrics/entities.py:12-47` показывает текущую схему
  `metric_samples`; `MetricDaily` и `MetricConcurrencyDaily` находятся в
  `:58-82`.
- `backend/app/metrics/archive.py:96-164,229-265` группирует дневные данные по
  локальной календарной дате, профилю, модели и engine; concurrency-кривая
  строится по наблюдаемому `active_requests` и суммарной скорости, с пропуском
  переходных/невалидных снимков.
- [docs/specs/2026-10-03-daily-metrics.md](../specs/2026-10-03-daily-metrics.md)
  говорит, что дневной архив и исходные histogram должны храниться бессрочно,
  а snapshots движка очищаются после архивации. Это локальный документ и
  текущий dirty checkout, не результат проверки production.
- `backend/app/schemas/instances.py:45-64` различает `Metric.state`:
  `ok`, `unavailable`, `stale`; `null` не должен превращаться в ноль.

### Токены, кэш и стоимость

- `backend/app/journal.py:25-64` хранит в `request_journal` профиль, PID,
  input/output tokens, `cached_input_tokens`, timestamps, request timings,
  prefill/decode и cache ratio. Полей цены, валюты, ставки или стоимости нет.
- `backend/app/proxy/service.py:216-336` сначала привязывает запрос к
  зарегистрированному instance/profile, а затем записывает journal; запросы без
  выбранного upstream остаются без profile.
- `backend/app/proxy/service.py:762-852` извлекает cached input tokens из
  upstream usage и `llama.cpp`/NInfer timings. Cached tokens не считаются
  дополнительными input tokens; при неизвестном usage поле остаётся `null`.
- `backend/app/token_stats.py:149-198,274-329` сохраняет неизвестные input,
  output и cached значения отдельно от известных totals. Старый `/token-stats`
  агрегирует input/output по profile, но не вычисляет стоимость.
- [спецификация истории](../specs/2026-10-02-profile-metrics-history.md#23-семантика-кеша)
  уже требует считать cached tokens подмножеством input и оставлять неизвестное
  значение неизвестным. Это должно быть общей основой TASK-001 и TASK-003.

### Размер модели, память и capability

- `backend/app/schemas/models.py:50-68` имеет у локального артефакта
  `size_bytes`, формат, группы/варианты и вспомогательные блоки. Это размер
  файлового набора, не runtime VRAM/RAM.
- `backend/app/models/catalogue.py:197-239` рассчитывает размер каталога или
  файла и описывает группы. Профиль хранит лишь `model.path`; выбранная группа
  и вычисленный размер в `ProfileData` не сохраняются.
- `backend/app/system.py:39-110` получает общую RAM и общие GPU через
  `psutil`/`nvidia-smi`; `SystemData` не содержит распределение общей памяти по
  профилям.
- `backend/app/proxy/service.py:149-206,719-750` умеет извлекать метаданные
  модели и reasoning capabilities, но не признак image generation.
- В текущих схемах и UI нет поля `image_generation`, capability catalogue или
  документированного детектора. Наличие `vision`, `mmproj`, `projector` или
  auxiliary block описывает компонент модели, но не доказывает способность
  генерировать изображения.

## Матрица доступности показателей

| Показатель карточки | Что есть локально | Надёжность и ограничение |
|---|---|---|
| Средний prefill | `prefill_tps` в `MetricSample`, histogram/engine timings | Требует периода и весов; vLLM, llama.cpp/NInfer и остальные engines имеют разные источники; без валидного знаменателя — `null`. |
| Средний decode | `decode_tps` в snapshots и archive | Можно агрегировать только совместимые наблюдения; snapshot average нельзя выдавать как запросный average. |
| Размер на диске | `LocalModel.size_bytes`, `groups[].size_bytes` | Это файл/группа на диске; для profile path нужно разрешить выбранную группу. |
| VRAM процесса | `vram_bytes` через `nvidia-smi` для подтверждённого PID | Доступность зависит от GPU/runtime; не равна размеру весов и не включает неидентифицированные процессы. |
| RAM процесса | Отдельного process-RSS поля нет | Общая RAM есть, per-profile RAM нет; добавлять оценку без источника нельзя. |
| Пиковая/средняя память | `vram_bytes` snapshots, но UI summary не определён | Нужно выбрать peak/average/current и период; отсутствие данных не равно 0. |
| Текущая concurrency | `active_requests` и `queue_requests` | Наблюдаемый exporter metric, зависит от engine и момента снимка. |
| Наблюдаемый максимум | `MetricLaunchExtrema`/daily curve | Это максимум с момента наблюдения данного `(profile, PID)`, а не доказанный предел профиля. |
| Оптимальная/максимальная concurrency | Только будущие результаты TASK-005 | Нельзя выводить из одного live maximum или из `max-num-seqs`; нужны методика, критерий и воспроизводимый benchmark. |
| Загруженность за день | Есть snapshots с active process и daily archive | Нет отдельного interval/heartbeat accumulator; определение «process работает» и знаменатель неполного дня должны прийти из TASK-003. |
| Порт | `Instance.port`, parser command, шаблоны с 8000 | Нет effective-port поля и engine-default registry; динамический/неявный порт может быть неизвестен. |
| Image Generation | Поля нет | Нужен явный источник/ручная capability; нельзя выводить из имени компонента. |
| Input/output price | Поля и storage нет | Нужен контракт профиля с currency/unit/rate и правило исторической фиксации. |
| Стоимость | Input/output/cache в journal | Формула возможна только после решения по тарифу, cache subset, NULL и benchmark traffic. |

## Ограничения и зависимости

### Цены и кэш (общие с TASK-003)

Текущая безопасная семантика, которую следует вынести на обсуждение как
предложение:

- тариф профиля задаётся отдельно для input и output;
- тариф имеет явную валюту и единицу (`за 1`, `за 1K` или `за 1M` токенов);
- cached input — подмножество input и не прибавляется второй раз;
- неизвестный input/output/cached не превращается в ноль и не участвует в
  денежной сумме без явного правила;
- при изменении тарифа будущая стоимость должна быть воспроизводимой: либо цена
  фиксируется в каждой journal-записи, либо профиль хранит версию тарифа с
  effective-from и расчёт использует историческую версию;
- стоимость benchmark-запросов должна быть отдельно решена TASK-003/TASK-005:
  включать в обычную статистику, помечать и фильтровать либо хранить только в
  benchmark report.

### Смешанные engines, период и identity

Нормализованные имена `prefill_tps`/`decode_tps` не доказывают одинаковую
семантику у разных engines: один источник может быть counter rate, другой —
request timing, а третий может включать queue или cached work. Предложенный
контракт для карточки: агрегировать только ряды с одинаковыми unit, numerator,
denominator, cache/queue semantics и явным `source`; вес брать из того же
знаменателя. Несовместимые semantics оставлять отдельными рядами с engine/source
либо показывать `unknown`, не складывать их молча. Период, границы календаря и
IANA timezone должны приходить из общего контракта TASK-003; server timezone не
выбирается молча.

Для исторической применимости полезно разделить неизменяемую identity запуска и
fingerprint исполнения. `instance_id + pid + process_start_time_ns` — ключ
конкретного процесса; PID без времени старта небезопасен из-за reuse. В
execution fingerprint входят resolved command, model path/revision, engine и
installation/build, endpoint и GPU/environment. Изменение display name, alias
или тарифа не должно инвалидировать ранее измеренный runtime; тариф хранится
отдельной revision. Context/output/cache являются применимостью benchmark-case,
а не полями текущей карточки профиля.

Данные для расчёта уже различают `input_tokens`, `output_tokens` и
`cached_input_tokens`, но текущая модель не хранит цену на момент запроса. Цена
только в текущем YAML профиля не позволяет безоговорочно пересчитать прошлый
период после редактирования.

### Загруженность (общая с TASK-003)

Есть два различных смысла:

1. **Процессный uptime** — время от `process_start_time_ns` до наблюдаемого
   момента. Он уже доступен как `uptime_s`, но при перезапуске начинается заново.
2. **Доля календарного дня** — длина объединения интервалов, в которых managed
   профиль считался работающим, делённая на длину выбранного знаменателя. Для
   этого нужны наблюдаемые transitions/heartbeat, правило для `starting`/`ready`,
   остановки и неполного текущего дня.

Предложение для согласования: считать «работает» как подтверждённый managed
процесс в состояниях `ready` или `running`; `starting` показывать отдельным
состоянием и не включать в рабочую долю, пока модель не готова. Это не принятое
решение: TASK-003 должен утвердить его и источник знаменателя. Внешний процесс с
`profile_name = null` не следует автоматически приписывать сохранённому профилю.

### Concurrency и TASK-005

`active_requests` — число наблюдаемых одновременно выполняющихся запросов, а
`queue_requests` — ожидающие запросы у поддержанных exporter-ов. В daily archive
кривая группируется по наблюдаемому числу активных запросов и хранит скорость,
per-request скорость, samples и seconds. Это полезно для контекста, но не
определяет оптимум.

Предложение для согласования: карточка показывает две разные пары:

- `Наблюдалось`: max active requests за выбранное окно истории;
- `Benchmark`: `optimal` и `limit`, только если завершённый TASK-005 benchmark
  имеет валидные criteria по `aggregate_output_tps` (или отдельной server
  phase-only decode formula), saturation evidence, context, output length,
  cache mode, engine/build, model revision, GPU environment и повторяемость.

До согласования TASK-005 значения `optimal`/`limit` должны быть `—` с
подсказкой «не измерено», а не оцениваться по конфигурационному флагу или
случайному максимуму.

Техническая рекомендация для сводки: использовать `instance_id + pid +
process_start_time_ns` для run identity; profile revision/display name/alias и
tariff revision хранить как provenance, но не делать ключом исполнения. `null`
и unknown не превращать в 0: суммировать только совместимые валидные
numerator/denominator и возвращать coverage/valid count. Engine-native timing
имеет приоритет для своей semantics, request journal — request-level fallback;
источники с разной semantics не объединять молча.

### Порт и совпадения

В текущем коде есть только общий `_extract_port`: он понимает `--port N`,
`--port=N` и `-p N`, но не проверяет engine-specific semantics. Поэтому
`--port` из текущих шаблонов можно считать распознанным override, а `-p` —
только кандидатом, пока для конкретного engine не зарегистрировано правило.
Default берётся только из подтверждённого versioned реестра; source-backed
значения и ограничения приведены в таблице выше. Число 8000 в локальном
шаблоне не следует выдавать за универсальный upstream default. Dynamic/shell
command остаётся `unknown`; shell не выполняется.

Рекомендация для пользовательского warning: сравнивать все сохранённые профили
по известному одинаковому **port**, независимо от `listen_address`; это прямо
сохраняет требование пользователя. `listen_address` можно позднее показывать
как дополнительный контекст или уменьшать false positives только отдельным
согласованным решением, но неизвестный адрес не должен молча отключать
port-only warning. Runtime conflict check активных процессов остаётся отдельной
защитой.

Для карточек предпочтителен один batch summary-запрос на страницу и период,
сгруппированный backend-ом по profile name/semantic/source; live polling каждой
карточки не рекомендуется.

### Image Generation

Нужен один из явных источников: capability, возвращаемая upstream model
metadata; ручная настройка профиля; либо детерминированный manifest/catalogue
признак. Автоматическое правило по имени файла (`vision`, `mmproj`, `image`)
может быть только подсказкой и не должно показываться как подтверждённая
генерация изображений.

## Что не доказано этим исследованием

- Нет live-измерений prefill/decode, VRAM, uptime или concurrency для какого-либо
  профиля.
- Нет результата benchmark TASK-005, поэтому нет согласованных optimal/limit.
- Проверены source-backed defaults для указанных version/branch docs, но не
  подтверждена применимость этих значений к любой installed version, fork или
  launch mode; registry должен хранить такую область применимости.
- Не проверена способность конкретных моделей к image generation.
- Не проверено production состояние, миграции или deployed API.
