# TASK-004 — черновик спецификации маршрутизации прокси и алиасов

Статус: **Черновик, не согласовано**
Дата: 2026-10-04

Документ описывает требования для обсуждения. Ни один default order, timeout,
retry, статус доступности, формат хранения, timezone, цена, cache billing или
определение busy/concurrency не является утверждённым решением.

## Review 2026-10-04 — решено review

Это рабочие рекомендации для повторного чтения, а не согласование продукта.
Рекомендуется группировать managed profiles по `ProfileData.model_name`,
проверяя engine ids и требуя явного mapping для multi-model engine. Стабильный
`profile_id`/revision используется в route references; rename сохраняет
membership, delete оставляет dangling reference с диагностикой. PID/start —
только runtime identity, PID сам по себе не является ключом. Начальный порядок
детерминирован по stable id (legacy fallback — имя), затем сохраняется явно,
новый профиль добавляется в конец.

Сохраняются unmanaged external model entries (`profile_name=NULL`); collision
не решается тихим удалением существующего `/v1/models` id. `available` требует
verified identity, порт и свежую непустую metadata; `stale`/starting/stopping/
exited/error остаются diagnostics и пропускаются routing. Existing external
scope: `/v1/models`, `/v1/chat/completions`, `/v1/embeddings`; internal scope:
`/api/proxy/models`, `/api/proxy/chat/completions`. `/v1/completions` и
`/v1/images` этим черновиком не добавляются.

Public route name рекомендуется возвращать в JSON и SSE `model`, если поле есть;
`[DONE]` не переписывается. Журнал хранит requested public name, upstream id и
фактический served profile/instance. Консервативный failover допустим только
при доказанном отказе до принятия тела; после возможной отправки — `unknown` и
без spurious fallback. После upstream headers переключение запрещено даже при
нулевых client bytes; после client content нет stitched stream. Cancellation
закрывает upstream и не failover. Один logical request получает общий deadline,
attempt ограничен остатком budget. Availability-вариант остаётся opt-in с
duplicate-work risk и без exact-once гарантии.

Оставшиеся product choices: alias grammar/collisions и error 404/503, правило
скрытия auto route при нуле running candidates, exact availability retry
allowlist, capability compatibility, alias на external profile, ручное
редактирование route config и формат direct stream error.

## 1. Цель и границы

Нужно дать одному OpenAI-compatible public model name упорядоченный набор
профилей и выбирать первый доступный профиль. Auto route создаётся для
совпадающих публичных `ProfileData.model_name`; alias получает произвольное
внешнее имя и явной настройкой объединяет профили, в том числе разных моделей.
Клиент сохраняет имя alias при изменении состава и порядка.

Входит: наблюдение кандидатов, `/v1/models`, ordered failover, управление
порядком, alias CRUD, drag-and-drop UI, error/streaming policy и фактическая
атрибуция запроса. Не входит: очередь запросов, балансировка по нагрузке,
запуск/остановка профилей, изменение port conflict из TASK-001, benchmark
runner из TASK-005, цены и billing из TASK-001/003.

Внешний контракт остаётся `OPENAI_PORT` + `/v1`, с существующим
`VITE_OPENAI_API_BASE_URL`; внутренний чат продолжает использовать
`/api/proxy` через тот же service. `/api/v1/models` не заменяется: это каталог
локальных моделей.

Точный scope существующих proxy routes: внешние `/v1/models`,
`/v1/chat/completions`, `/v1/embeddings`; internal `/api/proxy/models` и
`/api/proxy/chat/completions`. `/v1/completions` и `/v1/images` сейчас не
существуют и не входят в эту задачу.

## 2. Термины и инварианты

- **Профиль** — сохранённая конфигурация со стабильным `profile_id`/revision и
  display name; PID или порт профилем не являются.
- **Экземпляр** — конкретный managed/external process с identity PID + start;
  только verified instance с подтверждённым портом может быть upstream.
- **Route** — логическое публичное имя и ordered candidate references.
- **Auto route** — route, вычисляемый по одинаковому `model_name` профилей.
- **Alias** — сохранённое пользовательское имя с явным ordered profile list.
- **Upstream model** — `id` engine `/v1/models`, подставляемый в upstream body;
  он может отличаться от public route name.

Managed auto route рекомендуется строить по `ProfileData.model_name`; engine
model ids служат candidate mapping. External candidate без profile reference
может сохраняться в model catalog/diagnostics, но не получает alias reference
молча.

Инварианты:

1. public route name не меняется при reorder и изменении состава alias;
2. один профиль может входить в несколько alias;
3. порт и совпадение портов — отдельная диагностика, не ключ группировки;
4. route не считается доступным только потому, что процесс существует;
5. фактически выбранные profile/instance/upstream фиксируются до завершения
   логического запроса и используются TASK-003 для статистики;
6. `NULL`/unknown не превращаются в нулевые токены или успешные attempts.

## 3. Требования к backend

### 3.1 Построение групп и кандидатов

**R-1.** Для managed profiles с одинаковым `ProfileData.model_name`
создаётся один auto route, показывающий все связанные profiles. Engine ids
используются как mapping каждого candidate; multi-model mapping должен быть
явным, первый id нельзя выбирать молча. Миграция убирает только текущий
duplicate-name guard, сохраняя проверку конфликтов портов.

**R-2.** Candidate order хранится отдельно от volatile registry и применяется
после каждого refresh. Candidate выбирается только если он соответствует
согласованному `available` (минимум verified identity, port и успешная
readiness/model metadata). Незагруженный профиль пропускается без очереди.
States `stale`, `starting`, `stopping`, `exited`, `error` видимы в diagnostics,
но не становятся available. Refresh использует bounded cache/probe и не
запускает дорогой runtime loop model-list или POST health checks.

**R-3.** Для каждого кандидата сохраняются diagnostic state, profile name,
instance identity, engine id, upstream model id и причина unavailable. Runtime
обновление PID/start не должно переписывать внешний alias name.

**R-4.** Если engine публикует несколько upstream ids, route должен иметь
явное mapping/compatibility решение. Нельзя молча выбрать первый id, если это
меняет смысл запроса.

### 3.2 Список моделей

**R-5.** `GET /v1/models` возвращает auto routes и aliases как стандартные
model objects с публичным `id`. `object=list`, `data[]`, `object=model`,
`created`, `owned_by` остаются совместимыми с OpenAI; локальные metadata
поля optional и не должны быть обязательными для клиента.
Unmanaged external entries (`profile_name=NULL`) сохраняются; collision с
configured route не удаляет существующий catalog id молча и получает
diagnostic state.
Для existing chat/embeddings forwarding public route name возвращается в
обычном JSON и SSE `model`, если поле присутствует; `[DONE]` остаётся без
изменений. Request journal сохраняет requested public name отдельно от
upstream model.

**R-6.** Alias скрывается из списка, если ни один его кандидат не доступен.
Применять ли такое же правило к auto route — оставшийся product choice;
после выбора общего правила кэш списка не должен публиковать остановленный
route после согласованного TTL/refresh policy.

**R-7.** Доступность и список не обещают успешность будущего POST: process может
остановиться между probe и запросом. Это состояние должно быть видимо в
management response, но внешний model list остаётся компактным.

### 3.3 Порядок и сохранение

**R-8.** Пользователь может переставлять кандидаты auto route и alias drag-and-
drop; backend принимает полный упорядоченный список, проверяет уникальность и
атомарно сохраняет результат. Одновременная правка не должна терять более
новую версию без ошибки conflict/version.

Ссылки используют stable `profile_id`/revision. Rename сохраняет membership;
delete оставляет dangling reference с `profile_missing` diagnostic до явной
очистки и не переиспользует PID.

**R-9.** Для новой auto group рекомендуется детерминированный порядок по
stable `profile_id` (legacy fallback — имя), затем явный сохранённый order;
capacity/latency нельзя использовать неявно. Новый профиль добавляется в
конец, migration не создаёт duplicate members и не теряет existing order.

**R-10.** Alias name валидируется как non-empty bounded public model id и
проверяется на коллизии с auto route, profile public name и другим alias.
Разрешение пробелов, слешей, Unicode и совпадающих имен должно быть утверждено
до API-контракта.
Alias references рекомендуется ограничить managed profiles; unmanaged external
models сохраняются в listing, но не подключаются к alias без отдельного
явного правила.

### 3.4 Forward/failover

**R-11.** Для non-streaming запроса сервис проходит candidates строго в
сохранённом порядке, исключая недоступные runtime states. При успехе выбирается
первый ответивший candidate; запрос не ставится в очередь.

**R-12.** Предлагаемая безопасная граница retry: переход возможен при
доказанном отказе до принятия POST upstream. Connect refusal можно рассмотреть;
timeout, write timeout, remote protocol error после отправки, неизвестный
разрыв и любые ответы после headers по умолчанию не повторяются и получают
`routing_outcome=unknown`. Лимит attempts — не более одного прохода по списку.
Это conservative recommendation и фактически соответствует текущей policy:
сервис завершается ошибкой, чтобы не обещать отсутствие duplicate work.
Availability-вариант (не более одного следующего кандидата только без headers
и client body) допустим лишь как opt-in; outcome остаётся `unknown`, exact-once
не гарантируется. Один logical request получает общий timeout budget, attempt
ограничен его остатком. Allowlist и idempotency support остаются открытыми.

**R-13.** HTTP 4xx validation/auth/model/capability не переключает candidate.
HTTP 408/429/5xx и network failures переключаются только после отдельного
решения о side effect/idempotency; `retryable=true` внутренней ошибки не
означает автоматический retry.

**R-14.** Для streaming после upstream headers (даже если client ещё не получил
byte), первого byte или первого SSE content
переключение запрещено. Уже отправленные chunks нельзя заменить; клиент получает
частичный поток и стабильную ошибку закрытия, а journal — `partial` или
`unknown` и отсутствие финального usage, если его не было. До отправки и до
headers действует R-12. Cancellation закрывает upstream connection/task и не
делает failover. OpenAI-compatible SSE не расширяется внутренними chunks только
для сообщения о failover; stitched streams запрещены.

**R-15.** При отсутствии кандидатов запрос к route, скрытому из list, получает
стабильный OpenAI error. Выбор между `404 model_not_found` и `503
route_unavailable`, а также текст и `retryable` — открытый вопрос.

### 3.5 Журнал и статистика

**R-16.** Один client request остаётся одним logical request. Журнал обязан
различать `requested_model` (public route), `route_kind`, фактически
обслуживший `profile_name`, `instance_id`, PID/start, engine и `upstream_model`.
Также сохраняются stable profile reference/revision и `routing_outcome`, чтобы
переименование не ломало историческую атрибуцию.

**R-17.** Если были attempts, сохраняются их ordinal, candidate identity,
phase и outcome (`success`, `http_error`, `unavailable`, `unknown`, `partial`).
Не складывать токены нескольких неизвестных/повторных попыток как один успех.
При отсутствии точного usage оставить значения `NULL` и счётчик unknown.
Рекомендуемый storage — нормализованные `request_attempts` child rows с
logical-request id и dedup key; opaque JSON не должен быть единственным
источником для TASK-003.

**R-18.** TASK-003 агрегирует токены и latency по фактическому served profile;
alias name используется как requested route filter отдельно. Для benchmark
TASK-005 запоминаются served profile и route/interval либо применяется заранее
согласованное правило исключения из обычного traffic. Цены, часовой пояс,
cache billing и busy/concurrency не определяются этой спецификацией.

## 4. Предлагаемый контракт управления (для обсуждения)

Названия endpoint и поля ниже — черновое предложение, не утверждённый API.

`GET /api/v1/proxy/routes` возвращает:

```json
{
  "items": [{
    "name": "smart",
    "kind": "alias",
    "candidate_profile_ids": ["profile-fast", "profile-safe"],
    "candidates": [{
      "profile_id": "profile-fast",
      "profile_name": "fast",
      "instance_id": "managed/fast/1-2",
      "upstream_model": "engine-id",
      "state": "ready",
      "available": true,
      "reason": null
    }],
    "available": true,
    "version": 3
  }]
}
```

Возможные операции: create/update/delete alias, reorder candidates, получить
auto groups и их diagnostics. Update принимает `version` и полный order; alias
name — идентификатор ресурса и не меняется reorder-операцией. Точный формат
envelope следует общим management contracts; внешний `/v1` не раскрывает
`candidate_profile_names`, PID или причины внутренних отказов.

Хранение: предложение — отдельный versioned YAML под `data_root/config` с
atomic write по образцу `ProfileRepository`; SQLite остаётся журналом/историей,
если это подтвердит решение по конфигурации. Нельзя тихо добавлять маршрут в
profile YAML или считать volatile registry источником сохранённого порядка.
Конкретная схема должна иметь `schema_version`, stable references и optimistic
version; ручная правка не должна silently overwrite более новую UI-версию.

## 5. Предлагаемые UI-требования

**U-1.** Добавить отдельную management page с auto groups и aliases. Для каждой
группы видны public name, route kind, availability и ordered profile rows;
unavailable candidates остаются видимыми с текстовой причиной.

**U-2.** Reorder выполняется drag-and-drop и доступной клавиатурной альтернативой
(move up/down); после сохранения UI показывает conflict/error и не притворяется,
что порядок применён.

**U-3.** Alias editor позволяет задать имя, добавить существующие profiles
разных model names, удалить и переставить строки. Создание не требует запуска
профиля, но `/v1/models` скрывает alias по R-6.

**U-4.** Используются существующие shadcn Table, Button, Dialog, Alert,
Combobox и Tooltip с темой из design-system; названия, состояние и причина
доступности доступны без цвета. Точная страница/навигационный label открыты.

## 6. Критерии приёмки черновой реализации

1. Два запущенных профиля с одним согласованным public name видны как одна
   auto group; listing содержит один id, а management показывает оба порядка.
2. Запрос проходит profiles в сохранённом порядке, пропускает stopped/unready,
   а обычный и SSE-ответ содержит public route name, при этом upstream получает
   свой model id; `[DONE]` не меняется.
3. Reorder сохраняется после refresh/restart и не путает профили с одинаковым
   port; alias может включать profiles разных public names.
4. Alias имеет стабильный id; при отсутствии available candidate он отсутствует
   в list и direct-call получает согласованный error.
   Existing unmanaged external model ids не исчезают из `/v1/models` из-за
   configured route collision.
5. Запросы не повторяются после uncertain send, headers или SSE content;
   pre-send policy, cap, общий deadline и reason попадают в attempt diagnostics;
   availability retry, если включён, явно показывает duplicate-work risk.
6. Streaming partial response не соединяется с ответом другого профиля; usage
   остаётся unknown, если финальный chunk не пришёл.
7. Journal/usage показывает requested alias и actual served profile/instance/
   upstream, а TASK-003 не считает retry дважды. Benchmark association из
   TASK-005 проверяется отдельно.
8. Внешний `OPENAI_PORT`, `/v1` и frontend override сохраняют прежние маршруты;
   internal chat продолжает `/api/proxy`.
9. Внешний model list соответствует required OpenAI fields и не требует от
   клиента локальных diagnostics полей.

## 7. Этапы после согласования

1. Подтвердить review recommendations: grouping по `ProfileData.model_name`,
   available по verified identity/port/metadata, stable profile references и
   deterministic initial order; принять product choices по alias validation,
   collisions, direct-call error и capability policy.
2. Зафиксировать route config schema, management API, optimistic versioning и
   atomic persistence; отдельно согласовать миграцию текущего duplicate-name
   guard и volatile registry.
3. Утвердить retry matrix и journal attempt/logical-request contract с TASK-003;
   решить benchmark attribution с TASK-005.
4. Реализовать backend registry/route resolver/listing/forwarding и meaningful
   tests: grouping, order, process races, unknown send, HTTP errors, streaming
   partial, actual profile and no duplicate token aggregation.
5. Реализовать management UI с accessible reorder и alias editor, затем
   проверить existing chat/model picker, `/v1` compatibility и design-system.
6. Провести локальную проверку в Docker после утверждения; deployment/runtime
   проверка относится к отдельному согласованному этапу.

## 8. Вопросы перед утверждением

- Распространяется ли правило скрытия при нуле running candidates также на auto
  route, и какой TTL/refresh выбрать?
- Какая alias name grammar/precedence и какой direct-call error (`404` или
  `503`) нужен для скрытого/недоступного route?
- Разрешён ли availability retry после 408/429/5xx, timeout/write и remote
  disconnect; какой idempotency contract и duplicate-work disclosure нужны?
- Какие capability mismatches (tools, reasoning, context length) блокируют alias,
  а какие только показываются; разрешать ли alias на unmanaged external model?
- По результатам проверки рекомендуется versioned YAML маршрутов в
  `data_root/config`, атомарная запись и контроль версии. На обсуждение остаются
  доступность управления через API и правила ручного редактирования.
- С TASK-003/005 подтвердить фильтр alias, unknown/partial tokens и benchmark
  interval/event attribution; technical storage recommendation уже дана выше.
