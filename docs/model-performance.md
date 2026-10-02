# Производительность выбранного запуска

Панель справа на Dashboard показывает показатели выбранного процесса. Состояние
процесса остаётся в заголовке. Сырые названия exporter и API latency в панель
не выводятся. Общие ресурсы машины остаются отдельным блоком.

## Источники и смысл

### Включение метрик при запуске

Проверено 2026-10-02 по документации и исходникам upstream. Это проверка
контракта движков, а не установленных на сервере версий.
Стандартные шаблоны LLM Studio уже включают доступные HTTP-метрики:

| Движок | Источник | Включение | Переменная окружения |
|---|---|---|---|
| llama.cpp | `/metrics` | `--metrics` в стандартном шаблоне | Поддерживается `LLAMA_ARG_ENDPOINT_METRICS=1` |
| vLLM | `/metrics` | Включены по умолчанию | Отдельная переменная включения не найдена; не требуется |
| SGLang | `/metrics` | `--enable-metrics` в стандартном шаблоне | Эквивалент флага в проверенных исходниках не найден |
| FreeToken | `/v1/stats` | Доступен по умолчанию | Отдельный переключатель включения не найден; не требуется |
| NInfer | Статистика throughput в логах | Включена по умолчанию: `--log-stats-interval-ms 5000`; `0` выключает её | Эквивалент не найден; поддерживаемый HTTP-endpoint метрик отсутствует |

Для llama.cpp можно вместо `--metrics` задать в поле окружения профиля
`LLAMA_ARG_ENDPOINT_METRICS=1`. В стандартном шаблоне сохранён явный флаг.
Переменные SGLang `SGLANG_ENABLE_METRICS_DEVICE_TIMER` и
`SGLANG_ENABLE_METRICS_DP_ATTENTION` относятся к дополнительным измерениям
и не заменяют `--enable-metrics`. `PROMETHEUS_MULTIPROC_DIR` также не является
переключателем включения exporter.

В форме профиля есть галочка «Включить метрики», по умолчанию включённая.
Её значение сохраняется в YAML как `enable_metrics` (старые профили без поля
считаются включёнными). При сохранении профиля или запуске редактируемой
конфигурации для llama.cpp добавляется `--metrics`, для SGLang —
`--enable-metrics`, если соответствующего аргумента нет. Остальные движки
не требуют изменения команды. Добавленный флаг сохраняется в `command` и
отображается в фактической команде в карточке профиля.
Снятая галочка оставляет команду в точности как введена, включая уже указанные
флаги: она отключает автоматическую модификацию, а не метрики движка.
Чтение старого YAML и запуск сохранённого профиля сами файл не переписывают.
Для составного shell-скрипта без флага автоматическое добавление отклоняется:
пользователь добавляет флаг к вызову движка вручную или снимает галочку.
Комментарии не считаются аргументами; простые команды с переносами через `\`
и завершающим комментарием поддерживаются. Для NInfer нельзя обещать показатели
Dashboard только на основании включённой статистики в логах: HTTP-адаптер
метрик для него отсутствует.

Источники проверки:

- [llama.cpp: аргументы и переменные окружения](https://github.com/ggml-org/llama.cpp/blob/master/common/arg.cpp).
- [vLLM: метрики](https://docs.vllm.ai/en/stable/design/metrics/) и [переменные окружения](https://github.com/vllm-project/vllm/blob/main/vllm/envs.py).
- [SGLang: параметры наблюдения](https://github.com/sgl-project/sglang/blob/main/python/sglang/srt/arg_groups/fields/observability.py) и [переменные окружения](https://github.com/sgl-project/sglang/blob/main/python/sglang/srt/environ.py).
- [FreeToken: сервер](https://github.com/FlashML-org/FreeToken/blob/main/python/freetoken/server/api_server.py) и [аргументы](https://github.com/FlashML-org/FreeToken/blob/main/python/freetoken/server/args.py).
- [NInfer: HTTP serving](https://github.com/Neroued/ninfer/blob/master/docs/serving.md) и [параметры запуска](https://github.com/Neroued/ninfer/blob/master/src/serve/serve_options.cpp).

### Показатели

- llama.cpp: `/slots` для текущей генерации, `/metrics` с параметром запуска
  `--metrics` для счётчиков завершённых запросов. Decode/Prefill — разность
  `next_token.n_decoded` / `n_prompt_tokens_processed` между опросами одного
  slot/task, делённая на время. Первый опрос и смена task не дают известную
  скорость. Если slots недоступны, остаётся средняя скорость завершённых
  запросов из exporter. Live output tokens показываются отдельно от
  завершённых токенов, без сложения двух несинхронных снимков.
  Requests и Queue — processing/deferred; токены — счётчики prompt/predicted.
  TTFT и Served не выводятся как известные, если exporter их не предоставляет.
  KV cache поддерживается в версиях, отдающих `kv_cache_usage_ratio`.
- vLLM: `/metrics`, running/waiting, cache usage, prompt/generation counters,
  request success и histogram TTFT.
- SGLang: `/metrics`, running/queue, token usage, prompt/generation counters,
  request counters и histogram TTFT. Exporter должен быть включён в движке.
- vLLM/SGLang throughput: разность счётчика токенов / время между свежими
  измерениями. Первое измерение и сброс счётчика дают неизвестное значение.
  TTFT — среднее по новым наблюдениям histogram: delta(sum)/delta(count).
  Отсутствие новых запросов не заменяется нулевым TTFT.
- FreeToken: известные поля `/v1/stats`; процент KV — used/total pages.
- NInfer: неизвестные показатели обозначаются прочерком; endpoint не угадывается.
- Uptime: с начала текущего процесса, а не накопленное время жизни профиля.
- VRAM: `nvidia-smi --query-compute-apps=pid,used_gpu_memory`, сумма по
  подтверждённому PID и его дочерним процессам. Если PID в namespace не совпадает
  с данными драйвера либо память недоступна, результат неизвестен.
- Power/Energy: без отдельного источника измерения на процесс остаются неизвестными.
  Мощность общей карты не приписывается выбранной модели.

Значения относятся к серверному процессу, включая запросы напрямую к движку.
При нескольких моделях/репликах в одном exporter значения счётчиков и запросов
суммируются, занятость KV берётся по наиболее занятому пулу. Это не отдельная
статистика каждой модели внутри multi-model server.

## Графики

Отдельные графики Prefill, Generation и активных Requests. Браузер хранит
не более 120 точек без ограничения числа минут. При открытии восстанавливает
последние сохранённые точки, затем добавляет live-наблюдения раз в 2 секунды.
История не перечитывается при каждом опросе. Разрывы во времени соединяются
между валидными точками одной серии; неизвестные значения не заменяются нулями.
Подсказка показывает значение конкретной точки. Max относится к наблюдениям
текущего запуска (профиль + PID) и восстанавливается после перезапуска backend.

## Хранение

Фоновый сбор независимо от браузера пишет снимки движков каждые 5 секунд.
История доступна за последние сутки. Очистка происходит при старте и ежедневно
в полночь по времени ОС сервера; физически записи могут оставаться почти 48 часов.
Журнал API-запросов с токенами, PID и доступными метриками конкретного запроса
хранится бессрочно. Неизвестные показатели остаются NULL. Запросы напрямую к
движку не создают отдельных записей в журнале API Студии.

`GET /api/v1/metrics` поддерживает `mode=current|bootstrap|history|peaks|usage`,
`profile_name`, `pid`, `scope=all`, `from`, `to`, `limit`. Журнал usage также
поддерживает `client_id` и `offset`; ответ содержит `next_offset`.

## Опциональный Prometheus

Укажите `PROMETHEUS_URL` — базовый URL query API. Если требуется Bearer
авторизация чтения, задайте `PROMETHEUS_TOKEN`. При пустом URL чтение идёт
из локального сборщика. При настроенном URL свежие данные Prometheus имеют
приоритет; при ошибке или устаревании используются снимки SQLite. Локальная
запись всегда продолжается. Журнал отдельных запросов читается из SQLite.

Prometheus должен отдельно опрашивать `/metrics` Студии на порту интерфейса.
Настройка URL не регистрирует источник в Prometheus автоматически. Пример
для `prometheus.yml` (адрес заменить адресом своей Студии):

```yaml
scrape_configs:
  - job_name: llm-studio
    scrape_interval: 5s
    scrape_timeout: 2s
    static_configs:
      - targets: ["studio-host:3000"]
```

Labels: `studio_id`, `profile_name`, `pid`, `engine_id`. Постоянный studio_id
хранится в DATA_ROOT/studio-id. Экспортируются только свежие известные значения,
их исходное время измерения и доступные веса для вычисления средних.
Query timestamp Prometheus не выдаётся за время наблюдения движка.

## Документация движков

- [llama.cpp server](https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md)
- [vLLM metrics](https://github.com/vllm-project/vllm/blob/main/docs/design/metrics.md)
- [SGLang metrics](https://github.com/sgl-project/sglang/blob/main/docs_new/docs/references/production_metrics.mdx)
- [NVIDIA SMI](https://docs.nvidia.com/deploy/nvidia-smi/index.html)
