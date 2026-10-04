---
title: Карточки профилей: статистика, порт, возможности и тарифы
status: draft
status_ru: Черновик, не согласовано
date: 2026-10-04
---

# TASK-001 — Черновик спецификации карточек профилей

Статус: **Черновик, не согласовано**.

Документ фиксирует требования пользователя, исследованные ограничения и
предложения для обсуждения. Реализация до согласования этой спецификации не
разрешается. Исследовательская база находится в
[docs/research/TASK-001-profile-cards.md](../research/TASK-001-profile-cards.md).

## 1. Цель и границы

На странице «Профили» пользователь должен видеть рядом с каждым профилем
минимально необходимую информацию о том, что именно будет запущено, где будет
доступно, какие характеристики реально наблюдались и какие тарифы применяются.
Карточка относится к паре «сохранённый профиль + выбранная модель в его
конфигурации», а не к абстрактной модели или общему GPU сервера.

В задачу входят:

- добавление базовой статистики производительности на карточку;
- отображение эффективного порта и обнаружение совпадений;
- отображение достоверного статуса Image Generation, если источник существует;
- настройка и отображение отдельных input/cached-input/output тарифов токенов;
- контракт данных, который позднее позволит TASK-003 считать стоимость и
  использовать те же значения профиля;
- согласование границ с дневной статистикой TASK-003 и benchmark-результатами
  TASK-005.

В задачу не входят выполнение benchmark-ов, запуск/остановка моделей,
изменение внешнего OpenAI API `/v1`, автоматическая настройка engine flags,
классификация моделей по одному имени файла и полноценный экран статистики
TASK-003.

## 2. Требования пользователя

После отдельного согласования следующие пункты являются обязательными:

1. В карточке показывать среднюю скорость prefill для модели в этой
   конфигурации.
2. В карточке показывать среднюю скорость decode для модели в этой
   конфигурации.
3. Показывать, сколько памяти занимает модель с этой конфигурацией.
4. Показывать оптимальную и максимальную поддерживаемую concurrency — число
   параллельных запросов.
5. Показывать процент загруженности профиля за день. Определение рабочего
   времени должно совпадать со страницей статистики TASK-003.
6. Показывать порт профиля. При отсутствии переопределения использовать порт
   по умолчанию соответствующего engine; при переопределении показывать порт
   из конфигурации.
7. Если несколько профилей используют одинаковый порт, отмечать совпадение
   жёлтым треугольником.
8. При подтверждённой поддержке Image Generation показывать статус или
   компактную иконку.
9. В настройках каждого профиля задавать отдельные цены input, cached input и
   output в USD за 1 млн токенов.
10. Показывать три цены с явными подписями и единицей измерения.
11. Использовать фактические input/output/cached token counts и текущие цены
    профиля для расчёта стоимости в статистике.

Слово `prefield` из исходного запроса трактуется как `prefill`.

## 3. Согласованные с текущим проектом ограничения

- Профили остаются YAML-файлами в отдельном каталоге. SQLite не становится
  источником конфигурации профиля.
- Существующие старые YAML без новых полей должны продолжать читаться после
  согласования миграции схемы; отсутствие нового поля не равно нулевой цене,
  выключенной capability или нулевой метрике.
- Текущая страница использует компактные строки, shadcn/ui и существующие
  Button/Card/Input/Select/Tooltip-подобные компоненты. Редактирование остаётся
  на месте; иконные квадратные действия и удаление в раскрытой области нужно
  сохранить.
- Неизвестные значения показываются как `—`/«неизвестно» с понятной причиной.
  `0` допустим только для реально измеренного нуля.
- Общий OpenAI API `/v1`, `OPENAI_PORT` и URL в шапке не меняются.
- Внешние запуски с `profile_name = null` не приписываются сохранённому профилю
  только из-за похожего `model_name`.
- Никакие значения из демонстрационного HTML-макета не являются данными,
  defaults или контрактом метрик.

## 4. Предложения для согласования

Все подразделы этого раздела являются предложениями, пока пользователь явно не
утвердит их.

### 4.1. Состав карточки

Предлагается сохранить текущую основную строку:

`профиль — публичное имя модели — локальный путь — статус — автозапуск — engine`

и добавить компактную строку показателей в раскрытой части или сразу под
основной строкой:

| Блок | Содержание |
|---|---|
| Производительность | `Prefill`, `Decode`, единица `tok/s`, выбранный период и источник |
| Память/размер | размер выбранного локального варианта на диске; доступные текущие/средние/пиковые VRAM с отдельными подписями |
| Concurrency | `optimal`, `limit` из benchmark; отдельно `observed max`, если benchmark отсутствует |
| Загруженность | процент за согласованный календарный день и пояснение знаменателя |
| Endpoint | effective port, при необходимости host и значок совпадения |
| Возможности | `Image Generation: да/нет/неизвестно` только по явному источнику |
| Тариф | `input / cached_input / output`, валюта и единица тарифа |

Карточка не должна скрывать источник, период и состояние данных при наведении,
фокусе или через дополнительную подпись. Состояние нельзя кодировать только
цветом: для совпадения порта нужен треугольник с tooltip/aria-label и текстовое
сообщение.

### 4.2. Период статистики

Предложение: использовать тот же период, границы и IANA timezone, которые
TASK-003 определит для профильной статистики. Server timezone нельзя выбирать
молча. Пока TASK-003 не утвердил период и timezone, карточка показывает их
явно и не называет среднее «за всё время».

Предлагаемый порядок fallback:

1. дневной архив и request journal за выбранный период;
2. необработанные snapshots только в пределах доступного retention;
3. `—` при отсутствии валидных измерений.

Изменение model/engine или исполняющей конфигурации не следует сливать с прежней
конфигурацией без execution fingerprint. Текущий ключ истории `(profile_name,
pid)` недостаточен: PID должен сопровождаться `process_start_time_ns`, а
`instance_id` предпочтителен. Изменение display name, alias или цены не должно
инвалидировать ранее измеренную performance data; цена имеет отдельную tariff
revision.

### 4.3. Prefill и decode

Предложение: показывать среднюю скорость как агрегат фактических наблюдений за
период, а не как среднее всех snapshot values.

- Для request timings использовать `total prefill tokens / total prefill
  seconds` и `total generated tokens / total generation seconds`, если оба
  знаменателя достоверны.
- Для engine histograms и cumulative counters использовать их native
  denominator/weight; при reset counters начать новый baseline.
- Не смешивать per-request speed с aggregate throughput без подписи.
- Не считать unavailable или stale samples валидными; stale можно показывать
  отдельным состоянием, но не добавлять в среднее.
- Для разных engines суммировать только совместимые series: одинаковые unit,
  numerator/denominator, cache/queue semantics и явный source, с весом из того
  же знаменателя. Несовместимые semantics хранить отдельными рядами с
  engine/source либо показывать `unknown`; одного одинакового имени метрики
  недостаточно.

Карточка должна показывать `tok/s`, округление и период одинаково со страницей
TASK-003. Формула, выбор весов и отображение p50/p95 остаются вопросом
согласования TASK-003; TASK-001 не вводит новые процентили.

### 4.4. Память и размер модели

Предложение: не использовать одно неоднозначное поле «вес». Показывать
раздельно:

- `Размер на диске` — размер выбранной группы/варианта из каталога моделей;
- `VRAM процесса` — current/avg/peak `vram_bytes` только для подтверждённого
  profile/PID;
- `RAM процесса` — показывать только после появления достоверного process-RSS
  источника;
- если пользователь имел в виду только один из вариантов, это должно быть
  уточнено до реализации.

Для составной модели размер на диске предлагается считать по выбранной группе
и явно показать, включены ли auxiliary blocks. Общая GPU memory из
`GET /api/v1/system` не должна делиться между профилями пропорционально или
приписываться профилю без process-level evidence.

### 4.5. Concurrency

Предложение: разделить наблюдение и доказанный benchmark:

- `Observed max` — максимальный валидный `active_requests` из выбранного окна;
- `Optimal` — benchmark level, где `aggregate_output_tps` достиг плато по
  утверждённому TASK-005 порогу при заданных context/output, cache mode и
  повторах. Если появится отдельный server phase-only decode source, применим
  только его явно сохранённая formula; client/window-inclusive throughput не
  называется aggregate decode;
- `Limit` — benchmark level с доказанным началом очереди/ошибками либо иной
  согласованной границей;
- если benchmark отсутствует, `Optimal` и `Limit` показывать как `—`, а
  `Observed max` подписывать именно как наблюдение.

Результат benchmark считается применимым к карточке только при совпадении
  execution fingerprint: model path/revision или группа, engine и installation,
  resolved command/environment, endpoint и GPU selection. Context/output/cache/
  sampler — отдельная workload applicability. Display name, alias и tariff
  revision не являются ключом performance identity; результат другого
  исполняющего fingerprint или неполной applicability не копируется
  автоматически.

Сырые дневные `MetricConcurrencyDaily` и `active_requests` остаются полезными
  для анализа, но не заменяют TASK-005. `max-num-seqs`, очередь или единичный
  live snapshot сами по себе не являются benchmark proof.

### 4.6. Загруженность профиля

Предложение для единой трактовки TASK-001/TASK-003:

`load_percent = 100 × длительность объединения рабочих интервалов / знаменатель`

Рабочим интервалом считать подтверждённый managed процесс в `ready` или
`running`. `starting` до готовности не включать; `stopping` считать частью
интервала только если он уже был рабочим и окончательное правило TASK-003 это
разрешит. Для текущего дня знаменатель должен быть явно выбран: прошедшее время
дня или полный календарный день.

Значение не выводить из `uptime_s` без учёта restart gaps. Процент нельзя
получить задним числом из редких snapshots без правила пропусков. До появления
interval/heartbeat source показывать `—` и состояние «история загруженности не
собрана».

### 4.7. Порт и совпадение

Предложение: добавить вычисляемый `effective_port` без переписывания
сохранённой command.

1. Разрешить placeholders и получить фактический command тем же безопасным
   preview/resolver, который используется перед запуском.
2. Текущий общий resolver подтверждает `--port N` и `--port=N` в известных
   command templates. `-p N` он синтаксически принимает, но engine-specific
   parser-а, доказывающего смысл этой формы для каждого engine, сейчас нет;
   поэтому `-p` остаётся `candidate/unknown`, пока такое правило не будет
   зарегистрировано.
3. При отсутствии явного CLI override использовать versioned engine default из
   обязательного registry, привязанного к engine/version/installation:
   llama.cpp — 8080, vLLM — 8000, SGLang — 30000 только в проверенном Ascend
   guide, NInfer — 8080 в upstream serving docs, FreeToken — 1919 в CLI docs.
   Эти значения не являются универсальными для неизвестной версии/варианта;
   встроенный локальный `--port 8000` — явный template argument, не default.
4. Учитывать documented environment override до применения default. Например,
   llama.cpp использует `LLAMA_ARG_PORT`, причём CLI имеет приоритет над env.
   Для engines без подтверждённого env rule не угадывать переменную.
5. Если команда динамическая, составная, версия неизвестна или форма не
   подтверждена source, вернуть `port = null`, `state = unknown`, `reason` и не
   выполнять shell для догадки.

Source-backed registry (docs checked 2026-10-04; version/release must be pinned
before implementation):

| Engine | Documented launch form/default | Version limitation |
|---|---|---|
| llama.cpp | [`--port PORT`, 8080](https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md); `LLAMA_ARG_PORT`, CLI wins | `-p` and unpinned master variants unknown |
| vLLM | [`--port`, 8000](https://docs.vllm.ai/en/stable/cli/serve/) | No `VLLM_PORT` found in checked CLI reference; `-p` unknown |
| SGLang | [`--port`, 30000](https://github.com/sgl-project/sglang/blob/main/docs/docs/hardware-platforms/ascend-npus/getting-started/installation.mdx) | Ascend guide/main only; not universal across versions/platforms |
| NInfer | [`--port N`, 8080](https://github.com/Neroued/ninfer/blob/master/docs/serving.md) | Must bind to installed build/version; `-p` and env unknown |
| FreeToken | [`--port`, 1919](https://github.com/BAWES/freetoken/blob/main/docs/cli.md) | main without pinned release; `-p` and env unknown |

До появления такого versioned registry числовой default для соответствующего
профиля не показывать как подтверждённый.

Для пользовательского warning рекомендуется сравнивать все сохранённые профили
с известным одинаковым **effective_port**, независимо от `listen_address` и от
текущего состояния запуска. Это сохраняет требование «одинаковый port».
`listen_address` может быть дополнительным контекстом или отдельным будущим
решением для снижения false positives, но неизвестный адрес не должен молча
отключать port-only warning. Runtime conflict check активных процессов остаётся
отдельной защитой и не заменяется UI-треугольником.

Порт приложения Studio и внешний `OPENAI_PORT` не являются портом модели и не
должны попадать в этот warning.

### 4.8. Image Generation

Предложение: использовать трёхзначный статус с provenance:

- `supported` — capability подтверждена явным upstream/API metadata или
  ручным значением профиля;
- `unsupported` — источник явно говорит, что возможность отсутствует;
- `unknown` — источник не дал доказательства.

Иконка показывается только для `supported`; для `unknown` — нейтральный статус
с tooltip. `vision`, `mmproj`, projector, MTP и имя репозитория не считаются
подтверждением. Если ввод изображений и генерация изображений различаются,
они должны быть разными capability fields.

### 4.9. Тарифы токенов и стоимость

Предложение для нового блока YAML, точное имя и единица требуют утверждения:

```yaml
pricing:
  currency: USD
  unit: 1M_tokens
  input: "0.00"
  cached_input: "0.00"
  output: "0.00"
```

Правила предложения:

- `input`, `cached_input` и `output` обязательны только после явного включения
  pricing; пустое
  поле означает `unknown`, а явный `"0"` — бесплатный тариф;
- значения передаются и хранятся как decimal string, не binary float;
- unit и currency задаются явно; приложение не придумывает валюту или цену по
  engine/model;
- карточка показывает `input / output` с единицей (`за 1M токенов`, например)
  и не скрывает `unknown`;
- cached input входит в input tokens один раз и оплачивается по отдельной
  ставке;
- денежная сумма считается только по известным input/cached/output counts и
  текущим ценам профиля; неизвестный счётчик не заменяется нулём.

Предложение по историческим ценам: при закрытии proxy-запроса сохранять
`pricing_revision` и применённые input/output rates в journal snapshot либо
создать отдельный versioned tariff record с `effective_from`. Пересчёт прошлых
сумм по текущему YAML без указания версии не допускается. Это нужно согласовать
с TASK-003 до добавления итоговой стоимости.

Benchmark traffic должен иметь явный `benchmark_run_id` или иной фильтр до
решения, считается ли он частью обычного расхода. Один и тот же request не
должен дважды увеличивать input из-за cached subset.

## 5. Предлагаемый контракт данных

Это вариант для обсуждения; он не разрешает менять код до утверждения.

### 5.1. Профиль и YAML

Расширить профиль отдельными группами, не смешивая вычисленные метрики с
конфигурацией:

```yaml
pricing:                 # optional; exact schema pending approval
  currency: USD
  unit: 1M_tokens
  input: "..."
  output: "..."
capabilities:             # optional; only explicit values
  image_generation: unknown
```

`port` предпочтительно не дублировать в YAML, если команда остаётся source of
truth: effective port должен вычисляться из command и engine default. Если
пользователь должен редактировать порт отдельным полем, нужно утвердить
приоритет поля над command и обратную совместимость с ручным YAML.

### 5.2. Вычисленная сводка

Рекомендация: не перегружать `GET /api/v1/profiles` live-опросом и не делать
запрос summary на каждую карточку (N+1). Добавить один read-only batch endpoint
на страницу и период:

`GET /api/v1/profiles/summary?profile_names=a,b&from=<UTC>&to=<UTC>&timezone=<IANA>`

Backend группирует один запрос по profile name, semantic и source и возвращает
одинаковую структуру для всех запрошенных профилей. Это техническая
рекомендация; окончательные URL и формат остаются на утверждение человека.
Если profile list пуст, endpoint может вернуть summaries для всех профилей с
пагинацией, но отдельный вызов на карточку не нужен.

Ответ может содержать:

```json
{
  "profiles": [{
  "profile_name": "qwen-local",
  "period": {"from": "...", "to": "...", "timezone": "..."},
  "performance": {
    "prefill_tps": {"value": null, "unit": "tok/s", "state": "unavailable", "source": "sqlite"},
    "decode_tps": {"value": null, "unit": "tok/s", "state": "unavailable", "source": "sqlite"}
  },
  "memory": {
    "disk_bytes": null,
    "vram_current_bytes": null,
    "vram_avg_bytes": null,
    "vram_peak_bytes": null
  },
  "concurrency": {
    "optimal": null,
    "limit": null,
    "observed_max": null,
    "benchmark_state": "not_measured"
  },
  "load": {"percent": null, "state": "unavailable"},
  "endpoint": {"port": null, "state": "unknown", "conflict": false},
  "capabilities": {"image_generation": "unknown"},
  "pricing": {"currency": null, "unit": null, "input": null, "output": null}
  }]
}
```

Каждое числовое поле должно иметь state/source/freshness или общую сводку
состояния. API должен различать `0`, `null` и неподдерживаемое поле. Если
согласованный вариант использует другой endpoint, смысл этих различий нужно
сохранить.

### 5.3. Совместимость и identity

Сводка и benchmark-result должны разделять два понятия:

- **run identity:** `instance_id + pid + process_start_time_ns` для конкретного
  запуска. PID без времени старта недостаточен из-за reuse.
- **execution fingerprint:** model path/revision или выбранная группа, engine и
  installation/build, resolved command, endpoint/port, environment и GPU
  selection. Профильная revision может храниться для provenance, но применимость
  определяется этим fingerprint.
- **workload applicability:** context/output/cache/sampler и benchmark artifact
  из TASK-005; это свойства измерения, а не текущие поля профиля.

Изменение display name, alias или цены не меняет execution fingerprint и не
инвалидирует ранее измеренные performance data. Цена должна ссылаться на
отдельную tariff revision, чтобы пересчёт стоимости был исторически
воспроизводим. Изменение исполняющей конфигурации создаёт новый fingerprint и
не смешивается со старым без явной политики совместимости.

Это предложение к обсуждению; оно не разрешает менять код до утверждения.

### 5.4. Технические defaults для реализации

Это предложения по безопасным техническим значениям, а не продуктовые
решения:

- run identity: `instance_id + pid + process_start_time_ns`; profile revision,
  display name/alias и tariff revision — provenance, не execution key;
- `null`/unknown не являются нулём. В агрегат попадают только совместимые
  валидные numerator/denominator; ответ хранит coverage и valid count, а
  решение о показе partial aggregate остаётся UI/product choice;
- engine-native timing используется для своей semantics, request journal —
  request-level fallback; mixed semantics остаются отдельными/`unknown`;
- source-backed port/default registry из раздела 4.7 версионируется вместе с
  engine/installation; без version evidence effective port остаётся unknown.

## 6. Предлагаемые критерии приёмки — уточняются после решений

Ниже приведён рабочий список для обсуждения. Номера и формулировки не являются
утверждёнными требованиями: каждый пункт применяется к будущей реализации только
после согласования зависимого решения в TASK-001, TASK-003 или TASK-005.

### Данные и расчёты

1. Старый YAML без pricing/capabilities читается и отображается без ошибки.
2. Explicit input/output prices сохраняются раздельно, показываются с явными
   unit/currency и не теряют десятичную точность.
3. Средние prefill/decode совпадают с утверждённой формулой TASK-003 на
   синтетическом наборе с разными весами, counter reset и отсутствующим полем.
4. Cached input не добавляется к input второй раз; `NULL` не превращается в 0.
5. После изменения тарифа историческая стоимость либо сохраняет прежний
   tariff revision, либо явно пересчитывается по утверждённой политике.
6. Размер диска, VRAM и RAM имеют разные подписи; отсутствие per-process RAM не
   маскируется общей RAM.
7. Uptime процесса не используется как daily load без interval semantics.
8. Daily load на карточке и TASK-003 использует одну функцию/формулу, одну
   timezone и один знаменатель.
9. `optimal`/`limit` появляются только из валидного benchmark с совпадающей
   identity; без него отображается `—`/`not_measured`.
10. `Observed max` явно помечен как наблюдаемое значение и не называется
    поддерживаемым пределом.

### Порт и capability

11. В рамках утверждённого resolver явный порт из поддержанных форм command
    отображается без изменения YAML.
12. В рамках утверждённого приоритета источников при отсутствии override
    применяется подтверждённый versioned engine default (для известных
    источников: llama.cpp 8080, vLLM 8000, SGLang 30000 в оговорённом Ascend
    guide, NInfer 8080, FreeToken 1919); неизвестная версия/dynamic command
    возвращает `unknown`, а не выдуманный номер.
13. Известное совпадение одинакового `effective_port` среди сохранённых
    профилей видно через доступный текст и треугольник; неизвестный
    `listen_address` не подавляет это port-only warning. Дополнительное влияние
    адреса возможно только после отдельного решения.
14. UI warning не заменяет backend runtime conflict protection.
15. Если будет утверждён трёхзначный capability contract, Image Generation
    имеет `supported/unsupported/unknown` и provenance; неподтверждённая
    эвристика по названию файла не отображается как подтверждение.

### Batch и identity

16. Страница получает summary одним batch-запросом на период, а не отдельным
    live-вызовом на каждую карточку; backend сохраняет различия `0`, `null`,
    stale и unavailable.
17. Перемены display name, alias или tariff revision не делают старый
    execution fingerprint неприменимым; для процесса используется start-aware
    identity, а workload context/output/cache хранится как applicability.

### UI и границы

18. Карточка сохраняет текущие компактные строки, keyboard/focus access,
    icon-only square actions, inline editing и Delete в раскрытой области.
19. Нагрузка карточек не создаёт отдельный визуальный renderer, если
    существующий компонент можно расширить.
20. Период и freshness доступны пользователю; stale/unavailable различаются.
21. Изменение карточек не меняет URL шапки, `OPENAI_PORT` или внешний `/v1`.
22. Тесты проверяют минимум: старый YAML, тарифы, null/zero, конфликт портов,
    отсутствие benchmark, capability unknown и совместимость текущих действий.

## 7. Этапы и границы реализации

Порядок ниже предлагается только после утверждения требований и решений,
которые помечены открытыми.

### Этап A — решения и контракты

- Утвердить период, timezone, daily load и формулы prefill/decode вместе с
  TASK-003.
- Утвердить benchmark identity, optimal/limit и жизненный цикл результата с
  TASK-005.
- Утвердить pricing schema, currency/unit, cache pricing и historical tariff.
- Утвердить источник capability и приоритет explicit port/command/default.
- Зафиксировать API response и обратную совместимость YAML.

Граница выхода: есть согласованная спецификация; коду не добавляются поля из
неутверждённых предложений.

### Этап B — конфигурация и read-only данные

- Добавить versioned schema pricing/capability только через профильный YAML
  repository/API.
- Реализовать read-only resolver summary с состояниями и источниками.
- Добавить нормализованный effective port/conflict result.
- Подключить существующие history/archive/journal без изменения внешнего `/v1`.

Граница: не запускать benchmark, не менять engine command автоматически и не
переписывать старые профили без явного сохранения.

### Этап C — карточка

- Расширить текущий `ProfileCard` и существующие UI-компоненты.
- Добавить loading/empty/stale/unknown/error состояния.
- Сохранить текущие edit/start-stop/delete/log interactions и компактную
  адаптивную сетку.
- Добавить доступные подписи/tooltip с периодом, источником и причиной unknown.

### Этап D — верификация

- Выполнить targeted backend/frontend tests в Docker согласно правилам проекта.
- Проверить старые и новые YAML, синтетические агрегаты и boundary cases.
- Проверить только локальный результат; deployed/remote verification является
  отдельной явно разрешённой операцией.

## 8. Открытые вопросы к пользователю

1. Под «памятью» нужны VRAM процесса, RAM процесса, размер весов на диске или
   несколько раздельных значений? Нужен current, average, peak или все три?
2. Какой период у performance summary карточки: выбранный на странице
   статистики, фиксированные последние 7 календарных дней или другой?
3. «Оптимальная» concurrency определяется plateau `aggregate_output_tps` (либо
   отдельно согласованным server phase-only decode); какой процент и какой
   baseline принять?
4. «Максимальная» concurrency — первая очередь, первая ошибка, заданный
   предел теста или другой критерий? Как поступать при недетерминированности?
5. Оставить ли `listen_address` только дополнительной подписью к обязательному
   port-only warning или позже вводить отдельное правило снижения false
   positives? Сравнение известных портов среди сохранённых профилей уже
   рекомендуется независимо от address.
6. Должен ли отдельный порт редактироваться в карточке/форме или source of
   truth остаётся command? Что делать с shell-динамикой?
7. Какой источник подтверждает Image Generation: ручное поле, model metadata,
   manifest или явный capability endpoint? Нужно ли отдельно отражать image
   input/vision?
8. Какая валюта и единица тарифа обязательны: за токен, 1K или 1M? Нужны ли
   разные rates для cached input?
9. Что считать стоимостью запроса при неизвестном input/output/cache: unknown
   whole amount, partial amount или исключать строку?
10. Цена исторически фиксируется на момент запроса, на начало дня, или история
    пересчитывается по текущей цене? Как версионировать ручное изменение цены?
11. Включать ли benchmark traffic в обычную статистику стоимости и загруженности
    или показывать только через фильтр benchmark?
12. Для daily load считать `starting` и `stopping` рабочими? Знаменатель
    текущего дня — прошедшие часы или полные 24 часа? Как учитывать пропуск
    наблюдения/рестарт backend?
