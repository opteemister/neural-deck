# TASK-004 — черновик спецификации маршрутизации прокси и алиасов

Статус: **Черновик, не согласовано**
Дата: 2026-10-04

Документ описывает требования для обсуждения. Решения, явно зафиксированные в
разделе «Обсуждение с пользователем», являются рабочими defaults; остальные
order, timeout, timezone, цена, cache billing и busy/concurrency остаются
неутверждёнными.

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
attempt ограничен остатком budget. Availability-oriented retry с
duplicate-work risk в эту версию не входит.

Оставшийся архитектурный стык: контракт атрибуции alias с TASK-003/005.

## Обсуждение с пользователем 2026-10-04

Подтверждено: auto-route и alias не возвращаются в `/v1/models`, если нет ни
одного доступного кандидата. В management UI они остаются видимыми с причинами
недоступности. Технические детали ниже выбраны как defaults и не требуют
отдельного продуктового решения:

- TTL model metadata — 10 минут по существующему контракту; процесс и его
  доступность перепроверяются при каждом обновлении списка;
- alias name — ASCII model id `[A-Za-z0-9][A-Za-z0-9._-]{0,127}`;
- коллизии с profile model name, auto-route или alias блокируют сохранение и
  показываются inline при добавлении/редактировании, с backend revalidation;
- alias включает только managed profiles; unmanaged external models остаются
  в каталоге, но не участвуют в сохранённой маршрутизации;
- неизвестное имя получает `404 model_not_found`, известный недоступный route —
  `503 route_unavailable`;
- failover идёт по порядку при техническом отказе до upstream headers. Ошибки
  клиентского запроса, полученные headers и начавшийся stream не переключаются;
  stitched streams запрещены, deadline общий для logical request;
- строгая capability-матрица не вводится: managed profiles разных моделей
  разрешены, известные различия показываются предупреждением, а окончательная
  совместимость определяется upstream;
- route config хранится в отдельном YAML под `data_root/config` с версией схемы,
  atomic write и поддержкой ручной правки; последняя сохранённая запись
  полностью заменяет предыдущую (last-write-wins).

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

**R-6.** Alias и auto route скрываются из списка, если ни один кандидат не
доступен. Состояние процесса проверяется при каждом обновлении списка; TTL 10
минут относится к свежей непустой model metadata engine и не отменяет проверку
текущего запуска. Management response продолжает показывать скрытые routes и
диагностику.

**R-7.** Доступность и список не обещают успешность будущего POST: process может
остановиться между probe и запросом. Это состояние должно быть видимо в
management response, но внешний model list остаётся компактным.

### 3.3 Порядок и сохранение

**R-8.** Пользователь может переставлять кандидаты auto route и alias drag-and-
drop; backend принимает полный упорядоченный список, проверяет уникальность и
атомарно сохраняет результат. При одновременных сохранениях применяется простое
last-write-wins; optimistic conflict/version check не вводится.

Ссылки используют stable `profile_id`/revision. Rename сохраняет membership;
delete оставляет dangling reference с `profile_missing` diagnostic до явной
очистки и не переиспользует PID.

**R-9.** Для новой auto group рекомендуется детерминированный порядок по
stable `profile_id` (legacy fallback — имя), затем явный сохранённый order;
capacity/latency нельзя использовать неявно. Новый профиль добавляется в
конец, migration не создаёт duplicate members и не теряет existing order.

**R-10.** Alias name валидируется как non-empty bounded public model id и
проверяется на коллизии с auto route, profile public name и другим alias.
Используется ASCII-грамматика `[A-Za-z0-9][A-Za-z0-9._-]{0,127}`; пробелы,
слеши, Unicode и управляющие символы запрещены. Коллизия показывается inline
при добавлении/изменении и повторно проверяется backend. Один профиль не может
входить в alias дважды. Alias references ограничены managed profiles;
unmanaged external models сохраняются в listing, но не подключаются к alias.

### 3.4 Forward/failover

**R-11.** Для non-streaming запроса сервис проходит candidates строго в
сохранённом порядке, исключая недоступные runtime states. При успехе выбирается
первый ответивший candidate; запрос не ставится в очередь.

**R-12.** Переход к следующему кандидату возможен при техническом отказе до
upstream headers: например, при отказе соединения или невозможности получить
ответ до границы принятия upstream. Timeout/write timeout после возможной
отправки тела, remote protocol error после отправки, неизвестный разрыв и любые
ответы после headers не повторяются и получают `routing_outcome=unknown`.
Лимит attempts — не более одного прохода по списку. Один logical request
получает общий timeout budget, attempt ограничен его остатком. Отдельный
availability-oriented retry с риском duplicate work в эту версию не входит.

**R-13.** Ошибки входного запроса, auth, validation и capability не переключают
candidate: другой профиль не исправляет тело или ключ клиента. Технический
отказ до upstream headers передаёт попытку следующему кандидату по порядку;
после headers retry запрещён. `retryable=true` внутренней ошибки не означает
автоматический retry за пределами этой политики.

**R-14.** Для streaming после upstream headers (даже если client ещё не получил
byte), первого byte или первого SSE content
переключение запрещено. Уже отправленные chunks нельзя заменить; клиент получает
частичный поток и стабильную ошибку закрытия, а journal — `partial` или
`unknown` и отсутствие финального usage, если его не было. До отправки и до
headers действует R-12. Cancellation закрывает upstream connection/task и не
делает failover. OpenAI-compatible SSE не расширяется внутренними chunks только
для сообщения о failover; stitched streams запрещены.

**R-15.** Неизвестное имя получает `404 model_not_found`. Известный auto-route
или alias, скрытый из list из-за отсутствия доступных кандидатов, получает
`503 route_unavailable`. Ответ остаётся стабильным OpenAI-style JSON; внешний
model list при этом не содержит недоступный route.

### 3.5 Журнал и статистика

**R-16.** Один client request остаётся одним logical request. Журнал обязан
различать `requested_model` (public route), `route_kind`, фактически
обслуживший `profile_name`, `instance_id`, PID/start, engine и `upstream_model`.
Также сохраняются stable profile reference/revision и `routing_outcome`, чтобы
переименование не ломало историческую атрибуцию.

**R-17.** Если были attempts, сохраняются их ordinal, candidate identity,
phase и outcome (`success`, `http_error`, `unavailable`, `unknown`, `partial`).
Они являются диагностикой маршрутизации, а не отдельными статистическими
запросами. Для базовой версии достаточно итогового `routing_outcome` и
фактически вызванного profile/instance; отдельная агрегируемая таблица попыток
не обязательна. Не складывать токены нескольких попыток как один успех. При
отсутствии точного usage оставить значения `NULL` с признаком unknown.

**R-18.** TASK-003 агрегирует токены и latency по фактическому served profile;
то есть по профилю, который proxy выбрал, подставил в upstream payload и
вызвал. `requested_model`/alias сохраняется только как дополнительное поле
журнала и фильтра диагностики, но не становится отдельной моделью для
агрегации. Для benchmark TASK-005 сохраняется тот же served profile; benchmark
traffic помечается отдельно и не смешивается с обычной статистикой. Цены,
часовой пояс, cache billing и busy/concurrency не определяются этой
спецификацией.

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
    "available": true
  }]
}
```

Возможные операции: create/update/delete alias, reorder candidates, получить
auto groups и их diagnostics. Update принимает полный order; alias name —
идентификатор ресурса и не меняется reorder-операцией. Точный формат envelope
следует общим management contracts; внешний `/v1` не раскрывает
`candidate_profile_names`, PID или причины внутренних отказов.

Хранение: предложение — отдельный YAML под `data_root/config` с `schema_version`
и atomic write по образцу `ProfileRepository`; SQLite остаётся журналом/историей,
если это подтвердит решение по конфигурации. Нельзя тихо добавлять маршрут в
profile YAML или считать volatile registry источником сохранённого порядка.
Ручная правка и UI используют last-write-wins.

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
2. Зафиксировать route config schema, management API и atomic persistence с
   политикой last-write-wins; отдельно согласовать миграцию текущего
   duplicate-name guard и volatile registry.
3. Утвердить retry matrix и journal attempt/logical-request contract с TASK-003;
   решить benchmark attribution с TASK-005.
4. Реализовать backend registry/route resolver/listing/forwarding и meaningful
   tests: grouping, order, process races, unknown send, HTTP errors, streaming
   partial, actual profile and no duplicate token aggregation.
5. Реализовать management UI с accessible reorder и alias editor, затем
   проверить existing chat/model picker, `/v1` compatibility и design-system.
6. Провести локальную проверку в Docker после утверждения; deployment/runtime
   проверка относится к отдельному согласованному этапу.

## 8. Архитектурный стык перед утверждением

- С TASK-003/005 зафиксировать контракт атрибуции: proxy выбирает профиль,
  подставляет его upstream model и вызывает его; токены, latency и фактическая
  нагрузка относятся к этому profile/instance. Alias сохраняется только как
  requested route для журнала и диагностики, а `unknown` и `partial` не
  становятся успешным traffic для агрегатов и benchmark.
