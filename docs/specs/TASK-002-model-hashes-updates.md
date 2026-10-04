# TASK-002 — Хэши файлов моделей и обновления Hugging Face

**Статус: Черновик, не согласовано.**

Документ подготовлен по постановке пользователя и исследованию
[TASK-002](../research/TASK-002-model-hashes-updates.md). Код, миграции,
endpoint и UI в рамках этого черновика не изменялись.

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
5. Объявлять update только когда сравнимые hash различаются и есть
   подтверждение, что remote файл новее.
6. Показывать заметный информационный цветной значок или уведомление, дату
   локальной версии и дату доступной remote версии. Если надёжная версия
   доступна, разрешается показать сокращённый revision дополнительно.
7. Автоматическое скачивание, замена или удаление файлов не входят в запрос.

## Предлагаемое решение

### 1. Расширить состояние manifest

Сохранить существующий `<model-dir>/.llm-studio-hf.yaml` как источник связи
модели с Hub. Для отдельного файла сохранить соседний sidecar, как описано в
контрактах Version Zero. Версию формата следует поднять до 2 либо ввести
явно согласованный additive migration; это открытый вопрос до реализации.

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
  в `downloaded_revision`. Она нужна для сравнения с
  `remote_snapshot.file_date`; `downloaded_revision_date` остаётся датой commit
  всего repo и не заменяет per-file дату. Ни одна из них не вычисляется из
  локального `mtime` или часов завершения download.
- Локально хранить raw SHA-256 для требования повторного использования hash и,
  когда нужен remote Git-файл, Git blob SHA-1. Оба digest можно получить за
  один проход по файлу. Для remote hash без известного алгоритма хранить
  metadata, но не объявлять файл проверенным.
- Рекомендуемый первоначальный fingerprint — относительный path, `mtime_ns` и
  размер: он прямо выполняет требование reuse и одинаково трактуется на
  поддерживаемых файловых системах. Robust-вариант может дополнить его
  `st_dev`, `st_ino` и `st_ctime_ns` для обнаружения замены с сохранёнными
  mtime/размером, но может вызвать rehash при неизменном mtime и требует
  отдельного решения. Поля расширенного варианта остаются optional до этого
  решения.
- `hash_calculated_at` — техническая дата вычисления. Она не является датой
  версии Hub и не должна называться «дата модели».
- `downloaded_revision_date`, `downloaded_file_date` и
  `remote_snapshot.file_date` сохраняются только после получения от Hub. При
  отсутствии даты хранится `null`, а не придуманная
  дата загрузки или текущий момент.

Сохранение sidecar должно оставаться атомарным. Ошибка записи не должна
удалять последнюю рабочую metadata; повреждённый или неизвестный формат
manifest переводит соответствующий update status в `unknown`.

#### Baseline date и миграция

Для каждого baseline path нужны две разные даты:

- `downloaded_revision_date` — дата commit всего immutable repo revision для
  отображения и диагностики;
- `downloaded_file_date` — дата commit, затронувшего именно этот path в
  `downloaded_revision`, единственная дата baseline для правила «remote файл
  новее».

При миграции старого manifest следует:

1. сохранить известный `revision` как `downloaded_revision`, а известный remote
   hash — как `downloaded_remote_hash` только при подтверждённом алгоритме;
2. запросить metadata paths на **этой же immutable revision** через cache или
   `get_paths_info(..., expand=True)` и заполнить `downloaded_file_date`, если
   Hub её вернул; repo date записать отдельно в `downloaded_revision_date`;
3. при недоступности metadata оставить `downloaded_file_date: null` и
   `unknown`, не подставлять repo date, локальный `mtime` или время download;
4. не придумывать `tracking_ref` для старого manifest. Без явно сохранённого
   ref автоматическая проверка текущей ветки выключена (`not_tracked`);
5. для ручной модели без подтверждённой downloaded revision не создавать
   baseline D из локального файла. Локальный L можно вычислить отдельно, но
   update по одному отличию от remote не разрешён без baseline date.

Миграция не должна считать текущую remote проверку успешной: после backfill
`remote_check.last_successful_at` остаётся `null`, пока текущая tracked ref не
будет реально разрешена и snapshot не сохранён.

### 2. Правило повторного использования локального hash

Для каждого файла при сканировании:

1. Без чтения содержимого получить `stat` и построить fingerprint из
   относительного пути, `mtime_ns` и размера согласно выбранной политике
   fingerprint. Ошибка `stat`, отсутствие файла или отказ в чтении атрибутов
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

Рекомендуемая начальная политика — `mtime_ns + size`: изменение любого из них
приводит к пересчёту, а повторный scan с теми же значениями использует cache.
Robust-политика (`mtime_ns + size + st_dev + st_ino + st_ctime_ns`) лучше
обнаруживает замену файла с сохранёнными mtime и размером, но может вызвать
rehash при неизменном mtime; она не утверждается этим черновиком. Если
файловая система не даёт надёжные признаки, следует использовать выбранные
доступные поля и иметь явную команду принудительного пересчёта. Нельзя обещать
обнаружение изменения, если внешний оператор смог подменить все сохранённые
признаки.

Путь к файлу проверяется относительно model root. Рекомендуемая политика —
принимать только regular files, открывать их с запретом следования symlink
(`O_NOFOLLOW`, если доступно), а symlink или выход за root отмечать как
`unavailable`/`unknown`. Для replacement path сравниваются path `stat` до и
после открытия с `fstat` descriptor; если path уже указывает на другой inode,
результат чтения отбрасывается. Старая cache запись может остаться в manifest
для диагностики, но текущая попытка с race не получает `verified`.

Обычный `GET /api/v1/models/local` и startup scan могут читать только metadata
и fingerprint. Явный «Сканировать» должен проверять hash, но для неизменившихся
файлов также использовать cache. Полный force rehash — отдельное действие или
режим для диагностики, если он понадобится.

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
4. Если revision отличается, получить metadata только для tracked paths.
   Для даты файла использовать `list_repo_tree(..., expand=True)` или
   `get_paths_info(..., expand=True)` с current revision. Для hash использовать
   LFS SHA-256 или Git blob OID с сохранением `remote_snapshot.hash_kind`.
5. Для каждого tracked path вычислить три значения: baseline `D` из
   `downloaded_remote_hash`, фактический локальный `L` и текущий remote `R` из
   `remote_snapshot.hash`. Сравнение update делать по `R` против `L`, а дату
   новизны брать из `remote_snapshot.file_date` против
   `downloaded_file_date`. `downloaded_revision_date` (дата repo commit) и
   локальный `mtime` для этого сравнения не подходят.
6. Сопоставить записи строго по relative path и применить scope/filter:
   `selection` сравнивает только сохранённые paths; добавленные в repo файлы
   за пределами selection игнорируются. `snapshot` учитывает исходный набор
   tracked paths, а появление/удаление компонента помечает структурное или
   частичное изменение без выдуманного hash.
7. Сначала проверить наличие сравнимого `R`: отсутствующий remote hash или
   несовместимый алгоритм дают `unknown`/`changed_without_date` и не считаются
   отличием. Затем при `L == R` сразу выставить `remote_update: up_to_date`;
   дата не нужна, даже если `D` отличается или дата remote неизвестна. Только
   при `L != R` требуются сравнимые `remote_snapshot.file_date` и
   `downloaded_file_date`, причём `remote_snapshot.file_date` должна быть
   новее; при отсутствии/равенстве/старшинстве даты используется нейтральный
   status (`changed_without_date` или `remote_older`).
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
дорогие expanded данные без необходимости. Результаты можно кэшировать на
интервал, но ручная «Проверить обновления» должна уметь обойти свежий cache.
Remote check не обязан перечитывать все байты: stat tracked paths + reuse
стабильных local hashes достаточны для обычного цикла. Полный обход и force
rehash остаются отдельным действием. При rate limit нужен backoff и
дедупликация параллельных запросов.

#### Таблица решения D/L/R

`D` — immutable downloaded baseline, `L` — фактический локальный digest после
стабильной проверки, `R` — digest текущей успешно разрешённой remote revision.
Эти значения нельзя подменять друг другом:

| Случай | baseline status | remote status | `remote_update` | Интерпретация |
| --- | --- | --- | --- | --- |
| `D = L = R` | `matches_downloaded` | `matches_current_remote` | `up_to_date` | Локальный файл цел и remote update нет |
| `D = L != R`, `R` новее `downloaded_file_date` | `matches_downloaded` | `differs_from_current_remote` | `update_available` | Доступен новый remote файл |
| `D != L = R` | `diverged_from_downloaded` | `matches_current_remote` | `up_to_date` | Локально уже лежит текущий remote digest; update не показывать |
| `D = R != L` | `diverged_from_downloaded` | `differs_from_current_remote` | `up_to_date` | Remote прежний, локальный файл повреждён или заменён |
| Все различны, `R` новее baseline | `diverged_from_downloaded` | `differs_from_current_remote` | `update_available` | Доступен update и одновременно есть local divergence |
| Все различны, `R` старше baseline | `diverged_from_downloaded` | `differs_from_current_remote` | `remote_older` | Remote не доказывает update |
| `L = R`, дата неизвестна | по `D/L`, если они проверены | `matches_current_remote` | `up_to_date` | Равенство hash достаточно, дата не нужна |
| `L != R`, `R`/hash неизвестен | по `D/L`, если они проверены | `unknown` | `unknown` | Update не объявлять |
| `L != R`, дата неизвестна | по `D/L`, если они проверены | `differs_from_current_remote` | `changed_without_date` | Update не объявлять |
| Remote попытка недоступна | прежний status | прежний status, `stale: true` | прежний status | `availability: unavailable`, snapshot/status не затирать |

В частности, `L = R` всегда запрещает `update_available`, даже если
`D != L` и remote revision новее baseline: это ручное локальное обновление до
текущего remote состояния, а не доступное обновление. Если у path нет `D`,
`downloaded_file_date` или сравнимого hash, отсутствие baseline не заменяется
локальным mtime; при `L != R` остаётся нейтральный `unknown`/
`changed_without_date`.

Короткие aggregate-примеры: для `selection` из двух weight paths изменение
README и добавление третьего кванта вне selection игнорируются; если один из
двух tracked weights имеет `L != R` и более новую `remote_snapshot.file_date`,
модель получает `update_available` для этого path. Для `snapshot` добавленный
shard без baseline `D` получает path-level `structural_change`/
`changed_without_date`, а удалённый tracked shard — path-level `remote_missing`;
ни один из этих случаев без
сравнимых hash не получает `update_available`. Изменение tracked config или
tokenizer показывается отдельной ролью и не меняет aggregate `weights` badge,
если `tracked_filter: weights`.

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

Ответ содержит локальные модели и update state, включая `attempted_at`,
`successful_at`, текущую revision, даты и changed paths. `attempted_at` меняется
на каждой попытке; `successful_at` меняется только после полного успешного
получения и атомарного сохранения remote snapshot. При ошибке ответ может
содержать `availability: unavailable` и `stale: true`, но прежние успешные
hash/date/status сохраняются. Название, envelope и возможность фильтровать
`model_id` требуют согласования. `POST /api/v1/models/local/scan` должен
  сохранить текущую семантику локального сканирования с reuse cache; проверка
  сети может вызываться отдельно планировщиком и ручной кнопкой.

Состояния должны быть разделены и ортогональны `hf_match.status`:

- `remote_check.availability`:

```text
success | unavailable
```

- `remote_update.status` (результат последнего успешного snapshot):

```text
up_to_date | update_available | remote_older | changed_without_date |
unknown | not_tracked
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
- `Изменения на HF, дата не подтверждена` — нейтральное предупреждение;
- `Проверка HF недоступна` — stale/unknown с временем последней успешной
  проверки;
- отсутствие badge для `remote_update: up_to_date` и `not_tracked`, если это не
  нужно для объяснения состояния и remote check успешен, а оба local integrity
  статуса показывают совпадение.

В раскрытой карточке или details показывать:

- `Локальная версия HF: {short downloaded_revision} · {downloaded_revision_date}`;
- `Доступная версия HF: {short current_revision} · {remote_snapshot.file_date}`;
- `Проверка: попытка {attempted_at} · успех {successful_at}` с пометкой
  `данные могут быть устаревшими`, если последняя попытка неуспешна;
- для диагностики `Локальный файл изменён: {local_mtime}` и `Hash рассчитан:
  {hash_calculated_at}`;
- список конкретных changed/missing/added paths и их component/group.

Дата должна иметь подпись происхождения. Если Hub дал только дату последнего
commit репозитория, показывать её как дату репозитория, не как дату конкретного
файла. Если `L != R` и per-file дата отсутствует, показывать «дата неизвестна»
и не объявлять обновление по одному отличию hash; при `L == R` равенство hash
по-прежнему даёт `up_to_date` без даты.

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
4. Разделить `attempted_at` и `successful_at`; remote snapshot, baseline и
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
   immutable `downloaded_revision`; при недоступности Hub оставлять `null` и
   neutral status. Не использовать local mtime, дату запуска или repo date как
   подмену per-file baseline.

## Оставшиеся вопросы, требующие решения продукта

1. Выбрать начальную политику fingerprint: рекомендуемые переносимые
   `mtime_ns + size` или robust `+ device/inode/ctime`, который лучше ловит
   replacement, но может вызвать rehash при неизменном mtime.
2. Подтвердить, что отсутствие per-file `last_commit.date` всегда оставляет
   `changed_without_date`/`unknown`, даже если repo-level date новее. Это самый
   честный вариант для требования «remote файл новее».
3. Согласовать aggregate badge для `snapshot`, когда добавлен/удалён shard или
   изменён ancillary/config path без сравнимого D/L/R; предлагаем показывать
   `структурное изменение` отдельно и не называть его `update_available`.
4. Уточнить цвет, место badge и необходимость видимого `remote_hash`; поля
   дат и stale-признак остаются обязательными для диагностики.
5. Подтвердить отдельное действие `force rehash`; оно полезно для случаев, где
   filesystem сохранила все fingerprint признаки при изменении содержимого.

## Критерии приёмки для будущей реализации

- Первый scan вычисляет и атомарно сохраняет hash и fingerprint для каждого
  проверяемого локального файла только после стабильной проверки path/FD до и
  после чтения; повторный scan с тем же выбранным fingerprint не читает байты
  этого файла.
- Изменение `mtime_ns` или размера приводит к пересчёту. Если будет выбран
  robust fingerprint, замена с сохранёнными mtime/размером и новым
  inode/device/ctime также приводит к пересчёту; при простой политике такая
  гарантия не заявляется.
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
  даёт local divergence без remote update. `D = L != R` с более новой
  per-file date даёт `update_available`; все различия и remote older/unknown
  дают соответственно update с local divergence либо neutral status.
- Недоступность Hub не выдаётся за отсутствие обновлений и не меняет локальную
  полноту; `attempted_at` обновляется, `successful_at` и последний успешный
  snapshot/status сохраняются, а ответ содержит `availability: unavailable` и
  `stale: true`.
- Миграция не подставляет local mtime или repo-level date в
  `downloaded_file_date`; старый manifest без ref остаётся `not_tracked`.
- Для многокомпонентной модели видны изменившиеся paths и частичный статус;
  selection не сравнивается с незагруженными файлами repo, а added/removed
  snapshot components и README/ancillary paths не получают `update_available`
  без сравнимых D/L/R.
- Existing HF credential/token paths продолжают использоваться; secrets не
  попадают в sidecar, remote snapshot или scheduler logs.
- UI показывает локальную и remote даты с ясными подписями и заметный badge,
  а автоматическая загрузка или замена файлов отсутствует.
- Тесты покрывают reuse cache, replacement с сохранённой датой, Git/LFS hash,
  равные hash, remote newer/unknown date, недоступный Hub и частичный набор.

## Границы

В TASK-002 не входят скачивание обновлений, атомарная замена или откат модели,
поддержка новых типов Hub-репозиториев и новая политика авторизации
private/gated repos. Существующие HF credential/token paths должны сохраниться
и использоваться scheduler/check-updates; secrets нельзя писать в sidecar или
логи. Также не входят переписывание существующего OpenAI API, изменение
профилей запуска, удалённое развёртывание и серверная диагностика. Эти решения
могут быть отдельными задачами после утверждения настоящей спецификации.

До ответа на открытые вопросы документ остаётся черновиком и реализация не
начинается.
