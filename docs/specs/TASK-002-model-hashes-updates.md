# TASK-002 — Хэши файлов моделей и обновления Hugging Face

**Статус: Реализовано и слито в `codex/version-zero`; deploy не выполнялся.**

Документ подготовлен по постановке пользователя и исследованию
[TASK-002](../research/TASK-002-model-hashes-updates.md). Код, endpoint и UI
реализованы и слиты в основную ветку; текущий документ остаётся согласованным
контрактом.

## Цель

Уменьшить повторное чтение больших локальных файлов и дать оператору честный
сигнал о новой версии файлов на Hugging Face. Проверка должна различать
локальное состояние, исходную Hub revision, текущую remote revision и
неподтверждённые случаи.

## Требования пользователя

1. Для каждого локального файла сохранять вычисленный hash и дату изменения
   файла, для которой этот hash вычислен.
2. Если признак файла не изменился, повторно использовать сохранённый hash,
   включая нажатие «Сканировать».
3. При изменении файла пересчитывать его hash и обновлять сохранённый
   fingerprint.
4. После скачивания периодически сравнивать локальные файлы с соответствующими
   файлами на Hugging Face.
5. Объявлять update, когда remote hash отличается от локального и от
   подтверждённого downloaded baseline. Если отличается только локальный файл,
   показывать local divergence без уведомления об update. Дату версии
   показывать отдельно как справочную информацию; отсутствие даты не блокирует
   уведомление.
6. Показывать заметный информационный цветной значок или уведомление, дату
   локальной версии и дату доступной remote версии. Если надёжная версия
   доступна, разрешается показать сокращённый revision дополнительно.
7. Автоматическое скачивание, замена или удаление файлов не входят в запрос.

## Предлагаемое решение

### 1. Расширить состояние manifest

Сохранить существующий `<model-dir>/.llm-studio-hf.yaml` как источник связи
модели с Hub. Для отдельного файла сохранить соседний sidecar, как описано в
контрактах Version Zero. Версию формата поднять до `schema_version: 2` через
additive migration.

Каждый entry `files[]` должен содержать remote metadata и локальный cache
примерно следующего вида:

```yaml
schema_version: 2
repo_id: Qwen/Qwen3-8B
tracking_ref: main
downloaded_revision: abc1234567890abcdef1234567890abcdef123456
downloaded_revision_date: 2026-10-01T12:30:00Z # дата commit всего repo
scope: selection
tracked_filter: weights
remote_check:
  last_attempted_at: 2026-10-04T08:00:00Z
  last_successful_at: 2026-10-04T08:00:01Z
  current_revision: abc1234567890abcdef1234567890abcdef123456
  last_error: null
files:
  - path: Q4_K_M/model-00001-of-00002.gguf
    role: weights
    size: 123456789
    downloaded_remote_hash: 0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef
    downloaded_remote_hash_kind: lfs_sha256
    downloaded_file_date: 2026-10-01T12:29:58Z
    remote_snapshot:
      revision: abc1234567890abcdef1234567890abcdef123456
      hash: 0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef
      hash_kind: lfs_sha256
      file_date: 2026-10-01T12:29:58Z
    local:
      size_bytes: 123456789
      fingerprint_policy: mtime_size
      mtime_ns: 1790857800000000000
      sha256: 0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef
      git_blob_sha1: null
      hash_calculated_at: 2026-10-04T08:00:00Z
```

Имена полей примерные и требуют утверждения. Существенные свойства:

- `downloaded_revision` — immutable commit, из которого был скачан набор;
  `tracking_ref` — branch/tag, которую разрешено проверять на новые commits.
  Для намеренно pinned commit `tracking_ref` может быть `null`, что отключает
  автоматический update check.
- `downloaded_remote_hash` — зафиксированный при скачивании baseline `D`.
  `remote_snapshot.hash` — hash текущей успешно проверенной remote revision
  `R`. Их нельзя хранить под одним неоднозначным именем: сравнение обновления
  должно использовать `R` против фактического локального `L`, а не только
  `R` против baseline `D`.
- `downloaded_remote_hash_kind` и `remote_snapshot.hash_kind` должны различать
  минимум `lfs_sha256` и `git_blob_sha1`. Hash не следует называть просто
  `sha256`, если источник отдаёт Git blob OID.
- `downloaded_file_date` — дата commit, затронувшего именно этот baseline path
  в `downloaded_revision`. Она нужна для пояснения пользователю, когда была
  изменена версия файла; это не условие определения обновления.
  `downloaded_revision_date` остаётся датой commit всего repo и также
  используется только для отображения с ясной подписью. Обе даты получаются
  из expanded metadata Hugging Face и не вычисляются из локального `mtime` или
  часов завершения download.
- Локально хранить raw SHA-256 для требования повторного использования hash и,
  когда нужен remote Git-файл, Git blob SHA-1. Оба digest можно получить за
  один проход по файлу. Для remote hash без известного алгоритма хранить
  metadata, но не объявлять файл проверенным.
- Fingerprint — относительный path, `mtime_ns` и размер файла. Это простой
  переносимый вариант для повторного использования hash. Замена файла с
  сохранёнными одновременно path, `mtime_ns` и размером может остаться
  незамеченной; дополнительная защита inode/ctime и отдельный force rehash в
  эту задачу не входят.
- `hash_calculated_at` — техническая дата вычисления. Она не является датой
  версии Hub и не должна называться «дата модели».
- `downloaded_revision_date`, `downloaded_file_date` и
  `remote_snapshot.file_date` сохраняются только после получения из expanded
  metadata Hugging Face. Дата загрузки, локальный `mtime` и текущий момент не
  используются как дата версии.

Сохранение sidecar должно оставаться атомарным. Ошибка записи не должна
удалять последнюю рабочую metadata; повреждённый или неизвестный формат
manifest переводит соответствующий update status в `unknown`.

#### Baseline date и миграция

Для каждого baseline path нужны две разные даты:

- `downloaded_revision_date` — дата commit всего immutable repo revision для
  отображения и диагностики;
- `downloaded_file_date` — дата commit, затронувшего именно этот path в
  `downloaded_revision`, дата baseline-файла для отображения возраста версии.

При миграции старого manifest следует:

1. сохранить известный `revision` как `downloaded_revision`, а известный remote
   hash — как `downloaded_remote_hash` только при подтверждённом алгоритме;
2. запросить metadata paths на **этой же immutable revision** через
   `get_paths_info(..., expand=True)` и заполнить `downloaded_file_date`;
   repo date записать отдельно в `downloaded_revision_date`;
3. при ошибке expanded metadata не завершать backfill как успешный и не
   подставлять локальный `mtime` или время download. Последняя корректная
   metadata сохраняется, а remote check получает состояние `unavailable`;
4. не придумывать `tracking_ref` для старого manifest. Без явно сохранённого
   ref автоматическая проверка текущей ветки выключена (`not_tracked`);
5. для ручной модели без подтверждённой downloaded revision не создавать
   baseline D из локального файла. Локальный L можно вычислить отдельно, но
   результат без baseline показывать как неизвестное состояние.

Миграция не должна считать текущую remote проверку успешной: после backfill
`remote_check.last_successful_at` остаётся `null`, пока текущая tracked ref не
будет реально разрешена и snapshot не сохранён.

### 2. Правило повторного использования локального hash

Для каждого файла при сканировании:

1. Без чтения содержимого получить `stat` и построить fingerprint из
   относительного пути, `mtime_ns` и размера (`mtime_ns + size`). Ошибка
   `stat`, отсутствие файла или отказ в чтении атрибутов
   дают для этой попытки `local_integrity.baseline_status: unavailable` и
   `local_integrity.remote_status: unavailable`; старый cache entry сохраняется
   и не перезаписывается.
2. Найти cache entry с тем же алгоритмом и тем же fingerprint. При совпадении
   использовать сохранённый digest; чтение файла запрещено.
3. Если entry нет, признак отличается или изменился алгоритм, зафиксировать
   fingerprint до чтения, открыть файл через descriptor и потоково прочитать
   именно его, затем повторить `fstat` и проверку path. Если чтение завершилось
   ошибкой, сохранить старую запись без изменений и вернуть `unavailable`.
   Если pre-read и post-read fingerprint или identity descriptor различаются,
   digest не считается достоверным и в cache не записывается; допускается
   ограниченный повтор, после которого результат остаётся `unavailable` или
   `unknown`.
4. Только при успешном чтении и совпадении pre-read/post-read fingerprint
   атомарно сохранить новый digest, fingerprint и `hash_calculated_at`. Такая
   проверка защищает от объявления файла проверенным, если он изменился во
   время чтения.

Изменение `mtime_ns` или размера приводит к пересчёту, а повторное
сканирование с теми же значениями использует cache. Замена файла с сохранёнными
`mtime_ns` и размером может быть не обнаружена; это принятое ограничение
простого варианта. Отдельная команда принудительного пересчёта в задачу не
входит.

Путь к файлу проверяется относительно model root. Рекомендуемая политика —
принимать только regular files, открывать их с запретом следования symlink
(`O_NOFOLLOW`, если доступно), а symlink или выход за root отмечать как
`unavailable`/`unknown`. Для replacement path сравниваются path `stat` до и
после открытия с `fstat` descriptor; если path уже указывает на другой inode,
результат чтения отбрасывается. Старая cache запись может остаться в manifest
для диагностики, но текущая попытка с race не получает `verified`.

Обычный `GET /api/v1/models/local` и startup scan могут читать только metadata
и fingerprint. Явный «Сканировать» должен проверять hash, но для неизменившихся
файлов также использовать cache. Отдельные ручные действия проверки remote
обновлений или полного пересчёта hash не добавляются.

### 2.1. Конкурентная запись sidecar

Атомарный rename защищает от частично записанного YAML, но не от lost update:
background remote check, локальный scan и download могут прочитать один старый
manifest и последний writer затрёт изменения первого. Рекомендуется:

- использовать один advisory per-model lock (например, соседний lock-файл) для
  read-modify-write всех владельцев sidecar, включая download merge, local hash
  cache и remote snapshot;
- под lock перечитывать актуальный manifest, менять только свой раздел и
  сохранять через временный файл + atomic rename; lock timeout не должен
  записывать stale данные;
- дополнительно хранить `generation` или digest sidecar и проверять его перед
  rename. Если lock недоступен или generation изменился, перечитать и повторить
  merge, а не затирать чужой раздел.

Логические владельцы разделов должны быть раздельны: baseline D изменяется
только завершённым download/migration, local cache L — scan, remote snapshot R
и времена попыток — remote check. Ошибка одного владельца не очищает данные
другого. После race чтения файла старая cache запись можно сохранить для
диагностики, но текущая попытка получает `unknown`/`unavailable` и не может
использовать её как `verified`.

### 3. Проверка remote обновлений

Проверка выполняется только для локальных entries с подтверждёнными `repo_id`,
`downloaded_revision` и manifest path. `tracking_ref` должен быть явно записан;
для старого manifest без ref нельзя молча выбрать `main`, поэтому его remote
check имеет `not_tracked`/`unknown`.

1. Сгруппировать локальные entries по `repo_id` и `tracking_ref`, чтобы не
   запрашивать один репозиторий отдельно для каждого шарда или кванта.
2. Получить текущую resolved revision и дату repo commit. В качестве первого
   шага достаточно `model_info(repo_id, revision=tracking_ref)` или
   эквивалентного API запроса к `/api/models/{repo_id}`.
3. Если текущая revision равна `downloaded_revision`, можно переиспользовать
   сохранённые remote metadata и не запрашивать expanded file dates. Это
   означает только `remote_update: up_to_date` относительно этой revision:
   для каждого tracked path всё равно определить фактический локальный `L`.
   Лёгкая проверка делает `stat` и при стабильном fingerprint переиспользует
   сохранённый local digest; содержимое не читается на каждом remote check.
   Изменившийся fingerprint требует локального hash check, а race/ошибка дают
   обоим local integrity status значение `unknown`/`unavailable`, не
   `matches_current_remote` или `matches_downloaded`.
4. При изменении revision получить expanded metadata только для tracked paths.
   Дата каждого файла берётся из `last_commit.date` через
   `list_repo_tree(..., expand=True)` или `get_paths_info(..., expand=True)` с
   current revision. Для hash использовать LFS SHA-256 или Git blob OID с
   сохранением `remote_snapshot.hash_kind`.
5. Для каждого tracked path вычислить три значения: baseline `D` из
   `downloaded_remote_hash`, фактический локальный `L` и текущий remote `R` из
   `remote_snapshot.hash`. Сравнение update делать по `R` против `L`.
   `remote_snapshot.file_date` и `downloaded_file_date` показывать как возраст
   версии; они не участвуют в решении о наличии update.
   `downloaded_revision_date` и локальный `mtime` также не являются hash или
   заменой даты файла.
6. Сопоставить записи строго по relative path и применить scope/filter:
   `selection` сравнивает только сохранённые paths; добавленные в repo файлы
   за пределами selection игнорируются. `snapshot` учитывает исходный набор
   tracked paths, а появление/удаление компонента помечает структурное или
   частичное изменение без выдуманного hash.
7. Сначала проверить наличие сравнимого `R`: отсутствующий remote hash или
   несовместимый алгоритм дают `unknown` и не позволяют объявить update. При
   `L == R` выставить `remote_update: up_to_date`. При `D == R != L` также
   оставить `up_to_date`, но показать local divergence. Во всех случаях
   `R != D` и `R != L` выставить `remote_update: update_available` независимо
   от наличия даты; в подробностях показать дату commit файла. При отсутствии
   `D` оставить `unknown`, поскольку нельзя подтвердить, что изменение
   относится к remote версии после скачивания.
8. Изменение только README, служебного файла или ancillary component не
   объявляется обновлением весов, если path не входит в согласованный
   `weights` filter. Если такой path входит в manifest, его изменение всё равно
   показывается отдельно и не смешивается с aggregate weights status.
9. При timeout, 429, 5xx, 401/403, отсутствии repo или недоступном token
   provider сохранить последний успешный remote snapshot и его status. В
   текущей попытке вернуть `remote_check: unavailable`, обновив только
   `last_attempted_at` и ошибку. `last_successful_at`, baseline, local cache и
   последняя успешная оценка не затираются. Такая ошибка не превращает
   локальный `downloaded` в `incomplete` и не доказывает отсутствие update.

Обычная проверка должна сначала сравнивать revision, чтобы не запрашивать
дорогие expanded данные без необходимости. Remote check не обязан перечитывать
все байты: stat tracked paths + reuse стабильных local hashes достаточны для
обычного цикла. При rate limit нужен backoff и дедупликация параллельных
запросов.

#### Таблица решения D/L/R

`D` — immutable downloaded baseline, `L` — фактический локальный digest после
стабильной проверки, `R` — digest текущей успешно разрешённой remote revision.
Эти значения нельзя подменять друг другом:

| Случай | baseline status | remote status | `remote_update` | Интерпретация |
| --- | --- | --- | --- | --- |
| `D = L = R` | `matches_downloaded` | `matches_current_remote` | `up_to_date` | Локальный файл цел и remote update нет |
| `D = L != R` | `matches_downloaded` | `differs_from_current_remote` | `update_available` | Remote hash изменился; дата показывается отдельно и может быть неизвестной |
| `D != L = R` | `diverged_from_downloaded` | `matches_current_remote` | `up_to_date` | Локально уже лежит текущий remote digest; update не показывать |
| `D = R != L` | `diverged_from_downloaded` | `differs_from_current_remote` | `up_to_date` | Remote прежний, локальный файл повреждён или заменён |
| Все различны | `diverged_from_downloaded` | `differs_from_current_remote` | `update_available` | Remote hash изменился и одновременно есть local divergence |
| `L != R`, `R`/hash неизвестен | по `D/L`, если они проверены | `unknown` | `unknown` | Update не объявлять |
| `L != R`, `D` отсутствует | `unknown` | `differs_from_current_remote` | `unknown` | Нет подтверждённого downloaded baseline |
| Remote попытка недоступна | прежний status | прежний status, `stale: true` | прежний status | `availability: unavailable`, snapshot/status не затирать |

В частности, `L = R` всегда запрещает `update_available`, даже если
`D != L`: локальный файл уже соответствует текущему remote состоянию. Если у
path нет `D`, отсутствие baseline не заменяется локальным mtime; при `L != R`
остаётся нейтральный `unknown`. Отсутствие даты само по себе не меняет
решение по update; дата показывается отдельно как дата commit файла.

Короткие aggregate-примеры: для `selection` из двух tracked paths изменение
README и добавление третьего кванта вне selection игнорируются; если один из
двух tracked paths имеет подтверждённое отличие remote hash от локального,
модель получает `update_available` и список изменившихся paths. Для `snapshot`
добавленный или удалённый tracked shard показывается как структурное/частичное
изменение; изменение tracked config или tokenizer также перечисляется отдельно.

### 4. Периодичность и API

Рекомендуемый начальный интервал автоматической проверки — один раз в 6 часов
для моделей с tracked manifest: это снижает rate-limit нагрузку, сохраняя
обновления достаточно свежими для локального сервиса. Проверка запускается
только пока работает backend, не скачивает файлы и не блокирует выдачу
локального каталога. При открытой странице моделей frontend может получить уже
сохранённое состояние.

Для одной remote попытки рекомендуется timeout 8 секунд, до двух повторов для
429/5xx с уважением к `Retry-After`, без повторов для 401/403/404. Параллельные
проверки одного `repo_id`/`tracking_ref` дедуплицируются. Это параметры
реализации, а не обещание доступности Hub.

Предлагается добавить отдельный endpoint, чтобы не менять смысл существующего
полного scan:

```text
POST /api/v1/models/local/check-updates
```

Ответ содержит локальные модели и update state, включая
`last_attempted_at`, `last_successful_at`, текущую revision, даты и changed
paths. `last_attempted_at` меняется на каждой попытке;
`last_successful_at` меняется только после полного успешного
получения и атомарного сохранения remote snapshot. При ошибке ответ может
содержать `availability: unavailable` и `stale: true`, но прежние успешные
hash/date/status сохраняются. Endpoint используется планировщиком; отдельная
кнопка ручной проверки в UI не добавляется. `POST
/api/v1/models/local/scan` сохраняет текущую семантику локального
сканирования с reuse cache.

Состояния должны быть разделены и ортогональны `hf_match.status`:

- `remote_check.availability`:

```text
success | unavailable
```

- `remote_update.status` (результат последнего успешного snapshot):

```text
up_to_date | update_available | unknown | not_tracked
```

- `local_integrity.baseline_status` (сравнение фактического `L` с baseline `D`):

```text
matches_downloaded | diverged_from_downloaded | unknown | unavailable
```

- `local_integrity.remote_status` (сравнение фактического `L` с текущим `R`):

```text
matches_current_remote | differs_from_current_remote | unknown | unavailable
```

`remote_update: up_to_date` не означает, что `L` совпадает с `D` или что
проверка локального файла успешна. Например, повреждённый или заменённый
локальный файл при той же remote revision даёт
`baseline_status: diverged_from_downloaded` и
`remote_status: differs_from_current_remote`; remote update и локальное
расхождение должны быть видны отдельно. При ошибке remote check
`availability: unavailable` и `stale: true` не затирают последний успешный
`remote_update.status`.

`hf_match.status` (`downloaded`, `incomplete`, `unknown`) продолжает описывать
полноту локального набора. Remote update не превращает изменившийся, но
полностью присутствующий локальный файл в `incomplete`.

### 5. UI

На карточке локальной модели показать компактный цветной информационный badge:

- `Доступно обновление` — заметный цвет и количество изменившихся файлов;
- `Проверка HF недоступна` — stale/unknown с временем последней успешной
  проверки;
- отсутствие badge для `remote_update: up_to_date` и `not_tracked`, если это не
  нужно для объяснения состояния и remote check успешен, а оба local integrity
  статуса показывают совпадение.

В раскрытой карточке или details показывать:

- `Локальная версия HF: {short downloaded_revision} · {downloaded_revision_date}`;
- `Доступная версия HF: {short current_revision} · {remote_snapshot.file_date}`;
- `Проверка: попытка {last_attempted_at} · успех {last_successful_at}` с пометкой
  `данные могут быть устаревшими`, если последняя попытка неуспешна;
- для диагностики `Локальный файл изменён: {local_mtime}` и `Локальная
  проверка файла: {hash_calculated_at}`;
- список конкретных changed/missing/added paths и их component/group.

Дата должна иметь подпись происхождения. Для файла показывается
`last_commit.date`; дата `ModelInfo.last_modified` показывается отдельно как
дата последнего commit репозитория и не подписывается как дата файла. При
`L == R` равенство hash даёт `up_to_date`.

Для `scope: selection` проверять только сохранённые выбранные paths; добавление
другого кванта в тот же repo не должно внезапно пометить выбранную локальную
группу неполной. Для `scope: snapshot` изменение одного shard или
вспомогательного блока должно показываться как частичное обновление с перечнем
файлов. Изменения только README/служебного файла не должны автоматически
показываться как новая версия весов без отдельного решения о правилах групп.

## Рекомендуемые технические решения

1. Использовать `schema_version: 2` в существующем per-model sidecar без
   SQLite-копии. Sidecar уже является рабочим источником manifest-фактов, а
   additive schema сохраняет связь baseline, local cache и remote snapshot в
   одном атомарном документе.
2. Добавить `POST /api/v1/models/local/check-updates`; не менять семантику
   `POST /api/v1/models/local/scan`. Remote endpoint возвращает отдельные
   `remote_check`, `remote_update`, `local_integrity` и `changed_paths`, чтобы
   failure сети не выглядел как локальная неполнота.
3. Для scheduler принять интервал 6 часов, timeout 8 секунд на попытку, два
   повтора для 429/5xx с `Retry-After`, без повторов для 401/403/404 и не более
   одного одновременного запроса на `repo_id`/`tracking_ref`. Это ограничивает
   нагрузку Hub и предотвращает дублирование одинаковой работы.
4. Разделить `last_attempted_at` и `last_successful_at`; remote snapshot, baseline и
   последний успешный status заменять только после полностью успешной проверки.
   Ошибку показывать через `availability: unavailable` и `stale`, сохраняя
   последнюю успешную оценку.
5. Использовать lock + atomic rename для всех writers sidecar; generation/CAS
   оставить защитой при невозможности lock. Merge должен владеть только своим
   разделом (D, L-cache или R-snapshot), иначе atomic rename не защищает от
   lost update.
6. Scheduler должен переиспользовать существующий механизм HF credentials и
   не сохранять token, cookie или Authorization header в sidecar, log или
   remote snapshot. Добавление новой auth-политики для private/gated repo не
   входит в задачу, но существующие token paths нельзя удалять или обходить.
7. Для старых manifest делать backfill `downloaded_file_date` только по
   immutable `downloaded_revision` через expanded file metadata. При ошибке
   backfill не считается успешным, а local mtime и дата запуска не
   используются как дата версии. Repo date показывать только с подписью
   «дата последнего commit репозитория».

## Согласованные продуктовые решения

1. Для повторного использования hash используется простой fingerprint
   `mtime_ns + size`. Более сложная проверка replacement и force rehash не
   добавляются.
2. Дата Hugging Face берётся из `last_commit.date` expanded metadata каждого
   tracked файла и отображает возраст remote версии. Отличающийся сопоставимый
   hash является достаточным основанием для уведомления; дата не участвует в
   этом решении.
3. Для `selection` проверяются только сохранённые paths. Для `snapshot`
   изменившиеся, добавленные и удалённые tracked paths показываются как
   частичное/структурное изменение с перечнем файлов.
4. На карточке модели показывается цветной badge, в деталях — даты, revision,
   stale-состояние и список paths. Значение hash пользователю не показывается.
5. Отдельные ручные действия «Проверить обновления» и «Пересчитать hash» не
   добавляются. Пользовательское «Сканировать» остаётся обычным сканированием
   с reuse hash по неизменившемуся fingerprint.

## Критерии приёмки для будущей реализации

- Первый scan вычисляет и атомарно сохраняет hash и fingerprint для каждого
  проверяемого локального файла только после стабильной проверки path/FD до и
  после чтения; повторный scan с тем же выбранным fingerprint не читает байты
  этого файла.
- Изменение `mtime_ns` или размера приводит к пересчёту. При сохранении обоих
  признаков замена файла может быть не обнаружена — это принятое ограничение
  простого fingerprint.
- Ошибка `stat`/чтения, исчезновение файла или изменение fingerprint во время
  чтения не перезаписывают старый cache entry и дают `unavailable`/`unknown`;
  digest после race не используется.
- Для LFS сравнивается raw SHA-256 содержимого, для обычного Git-файла — Git
  blob SHA-1; неизвестный или несовместимый ETag не даёт ложного `downloaded`
  или `update_available`.
- После перезапуска backend сохранённые записи читаются из sidecar. Ошибка или
  частичная запись не уничтожает последнюю корректную metadata и не скрывает
  локальную модель.
- Concurrent download, local scan и remote check используют общий per-model
  lock и merge/CAS; atomic rename не допускает partial write, а generation или
  повторное чтение не допускает lost update между владельцами разделов.
- Remote check разрешает текущую tracked revision, сравнивает одинаковые
  relative paths и делает не больше одного запроса на repo/ref за цикл до
  необходимости expanded file metadata.
- При совпавшей remote revision local check переиспользует стабильные hashes и
  не перечитывает байты. `L = R` не даёт update даже при `D != L`; `D = R != L`
  даёт local divergence без remote update. `D = L != R` даёт
  `update_available` независимо от наличия даты; все даты показываются только
  как справочная информация.
- Недоступность Hub не выдаётся за отсутствие обновлений и не меняет локальную
  полноту; `last_attempted_at` обновляется, `last_successful_at` и последний успешный
  snapshot/status сохраняются, а ответ содержит `availability: unavailable` и
  `stale: true`.
- Миграция не подставляет local mtime или дату запуска в `downloaded_file_date`;
  repo-level date хранится и показывается с явной подписью происхождения.
- Для многокомпонентной модели видны изменившиеся paths и частичный статус;
  selection не сравнивается с незагруженными файлами repo, а added/removed
  snapshot components перечисляются как структурное изменение.
- Existing HF credential/token paths продолжают использоваться; secrets не
  попадают в sidecar, remote snapshot или scheduler logs.
- UI показывает локальную и remote даты с ясными подписями и заметный badge,
  а автоматическая загрузка или замена файлов отсутствует.
- Тесты покрывают reuse cache, replacement с сохранённой датой, Git/LFS hash,
  равные hash, remote difference и получение expanded file date, недоступный
  Hub и частичный набор.

## Границы

В TASK-002 не входят скачивание обновлений, атомарная замена или откат модели,
поддержка новых типов Hub-репозиториев и новая политика авторизации
private/gated repos. Существующие HF credential/token paths должны сохраниться
и использоваться scheduler/check-updates; secrets нельзя писать в sidecar или
логи. Также не входят переписывание существующего OpenAI API, изменение
профилей запуска, удалённое развёртывание и серверная диагностика. Эти решения
могут быть отдельными задачами после утверждения настоящей спецификации.

Реализация может начинаться отдельным этапом после проверки согласованной
спецификации; настоящим документом скачивание или автоматическая замена файлов
не разрешаются.
