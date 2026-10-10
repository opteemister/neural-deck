# TASK-002 — Исследование хэшей файлов моделей и обновлений Hugging Face

Дата просмотра: 2026-10-04. Прочитаны `AGENTS.md`,
`DEVELOPMENT_RULES.md`, постановка задачи и локальная реализация каталога
моделей. Приложение не запускалось, зависимости не устанавливались, к
пользовательским серверам, SSH и runtime API доступа не было. Изменения в
грязном checkout считались локальными и неиспользованными как доказательство
развёрнутого состояния.

## Вывод

В текущем коде уже есть sidecar manifest Hugging Face рядом с локальной моделью
и одноразовая проверка доступных remote hash при явном сканировании. Сохранённого
локального хэша с fingerprint файла нет: при `verify_hashes=True` содержимое
снова читается для каждого сопоставимого файла. Периодического сравнения с
текущей веткой или уведомления об обновлении также нет. Даты версии Hub и даты
изменения отдельных remote-файлов наружу не выдаются.

## Что уже есть в локальном проекте

### Manifest, сканирование и проверка

- `backend/app/models/catalogue.py:55-116` содержит `ModelCatalogue.scan()`.
  При обычном чтении используется `verify_hashes=False`; startup и `GET
  /api/v1/models/local` поэтому не перечитывают содержимое файлов. Явный `POST
  /api/v1/models/local/scan` в `backend/app/api/management.py:442-450` передаёт
  `verify_hashes=True`.
- Для каталога модели sidecar находится в
  `<model-dir>/.neural-deck-hf.yaml`; для отдельного файла используется соседний
  `<model-file>.neural-deck-hf.yaml` (`_read_manifest`, строки 785-793).
  `_merge_download_manifest()` (строки 489-550) атомарно сохраняет `repo_id`,
  commit `revision`, `scope` (`snapshot` или `selection`) и массив `files` с
  путём, размером и remote metadata. Запись выполняется до и после загрузки.
- `_reconcile_local_manifest()` (строки 795-927) при ручном добавлении папки
  может запросить публичные `/api/models/{repo_id}` и
  `/api/models/{repo_id}/tree/{revision}?recursive=true`. Сопоставление
  начинается с относительного пути и размера; при недоступности сети папка
  остаётся локальной с неопределённым источником.
- `_refresh_manifest_hashes()` (строки 931-991) обновляет remote metadata для
  **той же** сохранённой revision. Сначала читается локальный tree cache
  `.cache/huggingface/trees/{revision}.json`, затем выполняется запрос к tree
  API. Функция не разрешает текущую `main`, не получает новую revision и не
  вычисляет локальный hash.
- `_manifest_status()` и `_hash_matches()` (строки 1231-1318) при явной
  проверке читают файл заново. 64 символа трактуются как raw SHA-256, 40 — как
  Git blob SHA-1 только при `hash_kind: git_blob`, 32 — как legacy MD5; прочие
  ETag считаются непроверяемыми. `_git_blob_sha1()` (строки 1321-1328) считает
  SHA-1 от `blob {size}\0` плюс содержимое, а не SHA-1 байтов файла.
- `backend/app/schemas/models.py:15-24,109-137` не содержит локального
  fingerprint, времени вычисления, remote dates или статуса обновления. `HfFile`
  содержит `path`, `size`, `etag`, `sha256` и `hash_kind`; `HfRepoData` содержит
  только `repo_id`, resolved `revision`, описание и файлы.
- `backend/app/models/entities.py:37-50` и миграция
  `backend/alembic/versions/0002_catalogue_operations.py:34-51` определяют
  `model_artifacts`, но текущий `ModelCatalogue` в эту таблицу не пишет. Рабочим
  источником manifest-фактов остаётся sidecar.

### Загрузка и кэш Hugging Face

- `backend/app/models/catalogue.py:398-482` передаёт в дочерний процесс
  `repo_id`, разрешённую revision и выбранные пути. Перед загрузкой создаётся
  или объединяется sidecar; после успешной загрузки каталог перечитывается.
- `backend/app/models/hf_download.py:15-75` вызывает официальный
  `snapshot_download()` с `dry_run=True`, а затем скачивание в `local_dir`.
  Вычисления локального hash и сохранения даты завершения файла там нет.
- Официальный `snapshot_download(local_dir=...)` создаёт
  `<local_dir>/.cache/huggingface/`, а tree cache для commit хранит размеры и
  идентификаторы файлов. Это пригодно как offline-источник metadata именно для
  уже известной immutable revision, но не является проверкой актуальности ветки.
  См. [документацию загрузки файлов](https://huggingface.co/docs/huggingface_hub/en/package_reference/file_download#huggingface_hub.snapshot_download)
  и [описание локального cache](https://huggingface.co/docs/hub/en/local-cache).

### API и интерфейс

- Backend публикует `GET /api/v1/models/local`, `POST
  /api/v1/models/local/scan`, `GET /api/v1/models/hf/repos/{namespace}/{name}`,
  `GET .../files` и `POST /api/v1/models/downloads`. Отдельного endpoint для
  проверки updates нет.
- `frontend/src/pages/models.tsx:70-81` получает локальные модели без
  `refetchInterval`; периодически опрашивается только список активных загрузок
  раз в 1,5 секунды. Кнопка «Сканировать» вызывает именно дорогой explicit
  scan.
- `frontend/src/pages/models.tsx:189-200` показывает только `downloaded`,
  `incomplete` или `unknown`, а `FileRow` (строка 394) выводит «sha256», «etag»
  или «без hash». В данных карточки сейчас нет дат, локального hash и update
  badge.
- Глобальный `staleTime` React Query равен пяти секундам
  (`frontend/src/App.tsx:14`), но это не периодический remote check. Само
  устаревание cache не вызывает запрос без refetch или повторного монтирования.

## Официальные возможности Hugging Face

### Revision и даты

- `HfApi.model_info(repo_id, revision=...)` возвращает `sha` репозитория для
  запрошенной revision. `ModelInfo.last_modified` документирован как дата
  последнего commit **репозитория**, а не дата конкретного файла. Поля зависят
  от выбранных `expand`/`files_metadata`; это описано в
  [ModelInfo и model_info](https://huggingface.co/docs/huggingface_hub/en/package_reference/hf_api#huggingface_hub.HfApi.model_info).
- `HfApi.list_repo_tree(repo_id, revision=..., recursive=True)` возвращает
  `RepoFile` с относительным `path`, размером, `blob_id` и LFS metadata. При
  `expand=True` в `RepoFile.last_commit` появляется `oid`, название и дата
  commit, затронувшего конкретный путь. Расширенный запрос дороже и
  ограничивается 50 элементами на страницу; библиотека сама пагинирует.
  Источник: [list_repo_tree и RepoFile](https://huggingface.co/docs/huggingface_hub/en/package_reference/hf_api#huggingface_hub.HfApi.list_repo_tree).
- Для уже известного списка файлов можно использовать
  `get_paths_info(..., expand=True, revision=...)`, чтобы не запрашивать
  расширенные сведения для всего большого репозитория. Это тот же источник
  `last_commit`, с тем же ограничением расширенной выдачи. Источник:
  [get_paths_info](https://huggingface.co/docs/huggingface_hub/en/package_reference/hf_api#huggingface_hub.HfApi.get_paths_info).
- Дата `created_at` означает дату создания репозитория, а не дату версии файла.
  HTTP `Last-Modified` и дата загрузки на локальный диск не заменяют commit date
  Hub. В API `HfFileMetadata` документированы `commit_hash`, `etag`, `location`
  и `size`, но не дата изменения файла. Источник:
  [metadata одного файла](https://huggingface.co/docs/huggingface_hub/en/package_reference/file_download#huggingface_hub.HfFileMetadata).

### Hash, OID, LFS и ETag

- Для обычного Git-файла `RepoFile.blob_id` — Git object ID. Он равен Git blob
  SHA-1 с заголовком `blob {size}\0`, поэтому не сравнивается с raw SHA-1 или
  raw SHA-256 файла.
- Для Git LFS `RepoFile.lfs.sha256` — SHA-256 содержимого большого файла; `size`
  внутри LFS metadata относится к содержимому, а не к маленькому pointer-файлу.
  Это значение сопоставимо с локальным потоковым SHA-256.
- Официальный file-download guide описывает ETag как идентификатор версии:
  git-sha1 для файла в Git или sha256 для Git LFS. Это объясняет, почему
  40-символьный идентификатор нельзя безоговорочно назвать SHA-256. HTTP ETag
  также может быть оформлен кавычками; провайдерский или непрозрачный ETag без
  известной семантики следует хранить как metadata и не объявлять проверенным.
  Источник: [ETag и правила кэширования Hub](https://huggingface.co/docs/huggingface_hub/en/package_reference/file_download#huggingface_hub.hf_hub_url).
- В современных ответах `RepoFile` может также присутствовать `xet_hash`.
  Документация не определяет его как raw hash итогового файла, поэтому он не
  должен автоматически использоваться вместо `lfs.sha256` или Git blob hash.
- Hub cache подтверждает это разделение: в `blobs/` Git-файлы названы SHA-1,
  LFS-файлы — SHA-256; `refs/` связывает имя branch/tag с commit SHA, а
  `snapshots/{commit}` — immutable представление файлов. Источник:
  [структура локального cache](https://huggingface.co/docs/hub/en/local-cache#inside-a-repository-folder).
- API Hub имеет rate limits. Частые проверки должны сначала получать текущую
  revision и только при её изменении запрашивать tree или file metadata. См.
  [общие Hub API endpoints и ограничения](https://huggingface.co/docs/hub/api).

## Достоверное сравнение и даты

Рекомендуемое разделение фактов при будущей реализации:

| Факт | Что хранить/показывать | Чего он не означает |
| --- | --- | --- |
| `local_mtime_ns` | Время изменения локального файла; для группы — максимум по файлам | Не доказывает, когда файл был скачан с Hub или когда автор изменил upstream |
| `local_hash_calculated_at` | Момент завершения вычисления локального hash | Не является версией модели |
| `downloaded_revision` | Commit SHA, записанный в manifest после загрузки | Не является датой и сам по себе не говорит, какая ветка отслеживается |
| `downloaded_revision_date` | Дата revision, полученная для этого commit, если Hub её вернул | Не следует заменять датой создания repo или временем загрузки |
| `remote_file_last_commit` | `RepoFile.last_commit.date` текущей проверяемой revision | Доступна только при расширенном file/tree запросе; это дата commit пути |
| `remote_repo_last_modified` | Последний commit всего репозитория | Не доказывает, что изменился конкретный файл |

Для интерфейса безопасно показывать дату сохранённой Hub revision
(`downloaded_revision_date`) как дату версии набора, а для update-решения
использовать per-file `downloaded_file_date`. Отдельно показывать «локальный
файл изменён» (`local_mtime`). Для вручную скопированного файла без
подтверждённого manifest единственной достоверной датой остаётся локальный
`mtime`, который нужно так и подписать; его нельзя использовать как baseline
remote date. Момент вычисления hash можно показывать в подробностях или
журнале, но не выдавать за дату версии.

Совпадение текущей remote revision с `downloaded_revision` доказывает только
отсутствие подтверждённого remote update относительно сохранённого commit. Оно
не доказывает, что локальный файл остался целым: при той же revision нужен
отдельный локальный hash check. Поэтому remote status (например,
`up_to_date`), baseline status (`matches_downloaded`/`diverged_from_downloaded`)
и current-remote status (`matches_current_remote`/`differs_from_current_remote`)
следует хранить и показывать раздельно. Remote metadata для той же revision
можно переиспользовать, но это не отменяет сравнение локального digest.

### Модель D/L/R

Для каждого tracked path нужно различать три digest:

- `D` — immutable hash файла в downloaded baseline;
- `L` — фактический hash локального файла после стабильной проверки;
- `R` — hash того же path в последней успешно разрешённой remote revision.

Update сравнивает `R` с `L`, а не только `R` с `D`. Дата новизны при этом
сравнивается с `downloaded_file_date` — датой commit именно baseline path, а не
с local `mtime` или датой операции download.

| Сочетание | Вывод |
| --- | --- |
| `D = L = R` | local baseline и remote совпадают; `up_to_date` |
| `D = L != R`, remote file date новее baseline | `update_available` |
| `D != L = R` | локальный файл уже соответствует текущему remote; update не нужен, но baseline diverged |
| `D = R != L` | remote прежний, локальный файл diverged/corrupt; remote update отсутствует |
| все различны | update только если `R != L` и remote file date новее baseline; local divergence показывается отдельно |
| `L = R`, date unknown | hash уже доказывает отсутствие update; `up_to_date`, date не нужна |
| `L != R`, remote hash unknown | `unknown`; update не объявляется |
| `L != R`, date unknown | `changed_without_date`; update не объявляется |
| remote older | `remote_older`; update не объявляется |

Если `L = R`, наличие более новой даты относительно `D` не должно показывать
update: пользователь мог вручную привести локальный файл к текущему remote.
Отсутствующий `D`, baseline date или сравнимый hash нельзя заменять
локальным временем.

### Попытки remote check и повторное использование local cache

`attempted_at` — время каждой попытки, `successful_at` — время только
получения и атомарного сохранения полного remote snapshot. Ошибка обновляет
только attempt/error/availability; последний успешный snapshot, status и
`successful_at` остаются stale и не затираются. Ответ может одновременно
показывать прежний `remote_update` и `availability: unavailable`.

При одинаковой revision можно пропустить expanded remote metadata, но нельзя
превращать это в полный обход содержимого диска. Рекомендуемая проверка делает
stat только tracked paths; при стабильном fingerprint переиспользует `L` без
чтения байтов, а при изменении fingerprint запускает отдельный hash check.
Таким образом, повторный hash неизменившихся файлов не требуется, но текущая
проверка не объявляет их verified после race/stat error.

### Baseline file date и старые manifest

`downloaded_revision_date` — дата commit всего repo и подходит для пояснения
revision. Для условия «remote файл новее» нужна отдельная
`downloaded_file_date`, полученная для каждого baseline path через expanded
metadata immutable downloaded revision. При миграции старого manifest дату
следует backfill-ить из Hub/cache этой revision; при недоступности оставить
`null` и neutral status. Для manual model или старого manifest без mapping/ref
нельзя придумывать `tracking_ref`, дату из mtime или дату запуска; automatic
check остаётся `not_tracked`/`unknown`.

## Ограничения и риски

- Сохранённая revision immutable, но `main`/tag подвижны. Для update check
  нужно хранить не только resolved SHA, но и отслеживаемый ref либо явно
  определить, что все загрузки из текущего UI отслеживают default branch.
  Старый manifest, где есть только SHA, не даёт безопасно вывести намерение
  пользователя.
- Сравнение должно быть по относительному пути manifest. Совпадение имени
  модели или только общего repo недостаточно: в одном репозитории могут быть
  несколько квантов и вспомогательных блоков.
- Для больших наборов расширенная дата каждого файла дорога. Можно сначала
  сравнить current `sha` repo с сохранённой revision, затем запрашивать только
  пути manifest через `get_paths_info`. При совпавших remote hash дата для
  update не нужна.
- Отсутствие remote hash, даты, файла или доступа к gated/private repo не
  является доказательством обновления. Такой результат должен быть
  `unknown`/`unavailable` и сохранять время последней попытки.
- Remote commit может менять README, config или один shard независимо от
  остальных. Нельзя объявлять весь многокомпонентный набор целиком скачанным
  или устаревшим без списка изменившихся путей.
- `mtime` можно изменить вручную. На целевой Linux-среде базовый fingerprint
  для требования пользователя — `st_mtime_ns` и размер; для защиты от замены с
  сохранёнными `mtime` и размером можно предложить `st_dev`, `st_ino` и, как
  дополнительный защитный признак, `st_ctime_ns`. Расширенный набор может
  вызвать rehash при неизменном `mtime`, если меняются inode/device/ctime, и
  требует согласования. Если все признаки подменены, без чтения содержимого
  доказать изменение невозможно; нужен принудительный пересчёт.
- При ошибке `stat` или чтения файл нельзя считать проверенным: результатом
  становится `unavailable`/`unknown`, старый cache entry сохраняется без
  перезаписи. Hash следует вычислять между pre-read и post-read `stat`; при
  изменении fingerprint во время чтения digest отбрасывается и не записывается
  в cache (после ограниченного повтора сохраняется неопределённый статус).
- Atomic rename sidecar не решает lost update между background remote check,
  local scan и download. Нужен общий per-model lock либо generation/CAS с
  read-merge-write; владельцы baseline D, local cache L и remote snapshot R не
  должны затирать разделы друг друга.
- Для replacement path одной проверки path `stat` недостаточно: hash следует
  считать из открытого descriptor, сравнивать pre/post `fstat` и identity path.
  Symlink или выход за model root безопаснее считать `unknown`/`unavailable`,
  если отдельная политика не разрешает их.
- Для `selection` новые remote paths вне сохранённого списка не относятся к
  модели; для `snapshot` добавленный/удалённый shard может дать структурное или
  частичное изменение. README/config/ancillary изменения нельзя называть
  обновлением весов без filter/role и сравнимых D/L/R.
- Проверка private/gated не должна удалять или обходить существующие HF
  credential paths. Scheduler должен использовать тот же provider, не хранить
  token/cookie/Authorization в sidecar или логах; отсутствие credential даёт
  unavailable/unknown, а не отсутствие обновления.

## Итог исследования

Для TASK-002 достаточно расширить существующий manifest и API состояния: рядом
с remote metadata хранить локальный hash, fingerprint и время его расчёта, а
для источника — resolved revision, отслеживаемый ref и даты только с явно
указанным происхождением. Для корректного решения нужны три digest `D/L/R`:
baseline downloaded, actual local и current remote. Проверка обновлений должна
сравнивать `R` с `L`, требовать подтверждённую более новую per-file date против
`downloaded_file_date`, разделять baseline divergence и remote comparison, а
также использовать отдельный `remote_older` для remote, который не новее
baseline, и сохранять last successful snapshot отдельно от failed attempt.
Формат,
lock/CAS, endpoint и рекомендуемые timeout/backoff описаны в отдельном
черновике спецификации [TASK-002](../specs/TASK-002-model-hashes-updates.md).
