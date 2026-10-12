# План реализации универсальных движков

Статус: реализация в общей feature worktree; утверждённая спецификация —
`docs/specs/2026-10-12-universal-engine-platform.md`.

## Общие контракты

- Встроенные определения — YAML в `backend/app/engines/definitions/`, пользовательские —
  `<DATA_ROOT>/engines/<id>.yaml`. SQLite не хранит определения или профили.
- `EngineDefinition` содержит `version`, валидируемый строковый `id`, `name`,
  `types`, `install_script`, optional `update_script`, `executable`, optional
  `working_directory`, `environment`, `command`, optional `healthcheck`,
  `api: {protocol, base_url}` и optional `ui_url`.
- `types` — список capability из `text`, `tts`, `asr`, `video`, `image`,
  `embeddings`, `decision`. Протоколы пока ограничены готовыми адаптерами:
  `none`, `openai`, `comfyui`, `jev`.
- `healthcheck` имеет вид `{path: "/health", timeout_seconds: 3.0}`. Путь
  начинается с `/`; GET выполняется на path у origin эффективного `api.base_url`;
  статус 2xx означает успешную readiness. Поэтому healthcheck требует
  `api.base_url`; UI-only определения без readiness оставляют его пустым.
- `version_command` — список аргументов executable, запускаемый как argv без shell;
  при отсутствии команды версия остаётся неизвестной.
- Общие методы: `EngineService.get_definition(engine_id)` и
  `EngineService.resolve_profile(profile, installation=None)`. Последний
  возвращает `ResolvedEngineProfile`: `definition`, `installation`,
  `executable_path`, `working_directory`, сохранённую `command`, объединённый
  `environment`, effective `types`, `api_protocol`, `api_base_url`, `ui_url`,
  `healthcheck`, optional `model_path` и `model_name`.
- В `ProfileData` модель необязательна; отсутствующие `types`, `api`, `ui_url`
  наследуются из определения. Профиль может сузить `types` и переопределить
  протокол/URL. `api.protocol: none` отключает подключение.
- Канонические metadata поля `Instance` для proxy: `types`, `api_protocol`,
  `api_base_url`, `ui_url`. Readiness отражается существующим `state`: `ready`
  означает успешную настроенную/protocol проверку, `running` — процесс жив, но
  готовность API не настроена или не подтверждена. Отдельного `api_ready` нет;
  proxy исключает `api_protocol == "none"`.
- Установка сохраняет YAML snapshot использованного определения и результат
  обнаружения версии/commit рядом с installation manifest. CRUD определений не
  переписывает существующие профили и установки.
- Backend API определений: `GET/POST /api/v1/engines/definitions` и
  `GET/PUT/DELETE /api/v1/engines/definitions/{id}`. Существующие scan,
  installation, operation и profile endpoints сохраняют адресацию.
- Generic installation API: `POST /api/v1/engines/{id}/installations` принимает
  `{target_path, run_install, executable_path?, command?}`. `command` — optional
  переопределение install script для конкретной установки; если поле опущено,
  используется `install_script` определения. `run_install=false` подходит для
  регистрации уже существующего executable.
- Примеры пользовательских runtime YAML, не являющиеся встроенными определениями,
  размещаются в `docs/examples/engine-definitions/`.

## Владение работой

| Владелец | Область |
|---|---|
| Этот исполнитель | EngineDefinition schemas/YAML registry, CRUD API, generic installation/version snapshot, profile schema/storage и backup migration `image` → `comfyui` |
| Process worker | ProcessManager, discovery/registry процессов, Instance schema, catalogue и image integration; использует `EngineService.resolve_profile` |
| Proxy worker | Proxy/API routing, MCP, wiring в `main`/container; использует metadata `Instance` выше |
| Frontend worker | UI и клиентские API для динамических definitions/profiles |

Не меняются process, proxy, MCP, frontend и app composition файлы этим
исполнителем. Передача интерфейса между владельцами идёт по описанным выше
typed contracts.
