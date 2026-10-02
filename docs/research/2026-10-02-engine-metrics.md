# Ответы движков и каталог метрик

Исследование: 02.10.2026. Сверены текущие официальные исходники и документация, а также наш код. Это **каталог возможностей**, не снятый ответ установленных у нас версий. Примеры чисел ниже условные. На сервер не подключался.

## Как читать документ

- Разделы 1–3: формат и смысл показателей.
- Раздел 4: все найденные имена семейств в рассмотренных экспортёрах, включая условные.
- [CSV](engine-metrics-catalogue.csv): тот же каталог для фильтрации по движку и типу.
- Раздел 5: что сейчас получает и теряет наша реализация.

Нельзя гарантировать один полный перечень для любой сборки: дополнительные плагины, коннекторы, системные экспортёры и форки добавляют свои поля. Реальный полный состав конкретной установки определяет её сырой ответ. В частности, часть семейств появляется только после запросов или включения функции. Для SGLang ниже перечислены все статические объявления основного `metrics_collector.py`; другие модули могут добавлять метрики.

## 1. Форматы

| Движок | Адрес | Формат | Включение |
|---|---|---|---|
| llama.cpp | `/metrics` | Текст Prometheus | `--metrics` |
| vLLM | `/metrics` | Текст Prometheus | API server публикует метрики |
| SGLang | `/metrics` | Текст Prometheus | `--enable-metrics` |
| FreeToken | `/v1/stats` | JSON | Отдельный endpoint статистики |
| NInfer | `/metrics` не найден в рассмотренной документации | Timings в ответах API | Особые поля запросов для прогресса/промежуточных timings |

Источники: [llama.cpp server](https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md), [vLLM](https://docs.vllm.ai/en/latest/usage/metrics/), [SGLang](https://docs.sglang.io/docs/references/production_metrics), [FreeToken stats](https://github.com/FlashML-org/FreeToken/blob/main/python/freetoken/server/stats.py), [NInfer serving](https://github.com/Neroued/ninfer/blob/master/docs/serving.md).

### Prometheus: три вида данных

**Gauge** — текущее значение: активные запросы, очередь, занятость кеша.
**Counter** — накопительный счётчик процесса: токены, запросы, ошибки. Скорость за интервал получается из разности двух показаний, с обработкой перезапуска.
**Histogram** — распределение наблюдений: задержек или размеров запросов. Обычно одно семейство разворачивается в `_bucket`, `_sum`, `_count`. Среднее за интервал: `Δsum / Δcount`. p95 из buckets — приближение, точность зависит от границ корзин. Нельзя складывать buckets с разными `le` как одинаковые распределения. [Типы Prometheus](https://prometheus.io/docs/concepts/metric_types/).

Python-экспортёры могут также отдавать `_created` и служебные `process_*`, `python_*`. В каталоге указаны **семейства**, а не каждая строка каждого bucket/label. Counter в Python обычно получает суффикс `_total`; уже существующий `_total` не удваивается. Суффикс имени сам по себе не определяет тип: `vllm:iteration_tokens_total` — histogram.

### llama.cpp — пример фрагмента

```text
# TYPE llamacpp:requests_processing gauge
llamacpp:requests_processing 1
llamacpp:requests_deferred 0
llamacpp:prompt_tokens_total 42000
llamacpp:prompt_tokens_cached_total 18000
llamacpp:tokens_predicted_total 12000
llamacpp:prompt_tokens_seconds 1800
llamacpp:predicted_tokens_seconds 32
llamacpp:spec_decode_num_draft_tokens_total 3000
llamacpp:spec_decode_num_accepted_tokens_total 1200
llamacpp:spec_decode_num_accepted_tokens_per_pos_total{position="0"} 700
```

### vLLM — пример фрагмента

```text
# TYPE vllm:time_to_first_token_seconds histogram
vllm:num_requests_running{model_name="qwen",engine="0"} 2
vllm:num_requests_waiting{model_name="qwen",engine="0"} 1
vllm:kv_cache_usage_perc{model_name="qwen",engine="0"} 0.42
vllm:generation_tokens_total{model_name="qwen",engine="0"} 12000
vllm:time_to_first_token_seconds_bucket{model_name="qwen",engine="0",le="0.5"} 70
vllm:time_to_first_token_seconds_bucket{model_name="qwen",engine="0",le="1.0"} 90
vllm:time_to_first_token_seconds_bucket{model_name="qwen",engine="0",le="+Inf"} 100
vllm:time_to_first_token_seconds_sum{model_name="qwen",engine="0"} 64
vllm:time_to_first_token_seconds_count{model_name="qwen",engine="0"} 100
```

Здесь `0.42` означает 42%, а `64 / 100` — средние 640 мс за весь накопленный период. За последние минуты нужны разности счётчиков. `model_name`, `engine`, `finished_reason`, `source`, `position` и другие labels разделяют серии: их нельзя терять.

### SGLang — пример фрагмента

```text
# TYPE sglang:gen_throughput gauge
sglang:gen_throughput{model_name="qwen"} 32
sglang:num_running_reqs{model_name="qwen"} 2
sglang:num_queue_reqs{model_name="qwen"} 1
sglang:token_usage{model_name="qwen"} 0.42
sglang:cache_hit_rate{model_name="qwen"} 0.60
sglang:spec_accept_rate{model_name="qwen"} 0.40
sglang:time_to_first_token_seconds_sum{model_name="qwen",is_streaming="true"} 64
sglang:time_to_first_token_seconds_count{model_name="qwen",is_streaming="true"} 100
```

Labels здесь сокращены для чтения; реальная серия может содержать ранги параллелизма и другие измерения.

### FreeToken — полный пример структуры `/v1/stats`

```json
{
  "instance_id": "example-instance",
  "model": {"id": "qwen", "ctx": 32768, "attn": "hybrid_linear", "moe": false, "input_modalities": ["text", "image"]},
  "uptime_s": 1200,
  "kv": {"used_pages": 400, "total_pages": 1000, "page_size": 16},
  "mamba": {"used_slots": 2, "total_slots": 8},
  "swa": {"used_pages": 100, "total_pages": 500, "page_size": 16},
  "vram_bytes": 28000000000,
  "gpus": [{"index": 0, "name": "Example GPU", "uuid": "GPU-example", "total_bytes": 32000000000}],
  "throughput": {"decode_tps": 32.0, "prefill_tps": 1800.0},
  "requests": {"active": 1, "completed": 100, "p95_ms": 12000, "ttft_mean_ms": 640, "prompt_tokens_total": 42000, "completion_tokens_total": 12000}
}
```

Неиспользуемые `kv`, `mamba`, `swa` могут быть `null`; `gpus` — пустым до готовности. `attn` различает обычный attention и гибридные варианты. Это все ключи рассмотренного `build_stats()`, но модель может поддерживать другой набор модальностей. [Исходник структуры](https://github.com/FlashML-org/FreeToken/blob/main/python/freetoken/server/stats.py).

### NInfer — пример `timings` в ответе генерации

```json
{
  "timings": {
    "cache_n": 100, "prompt_n": 1000, "prompt_ms": 500,
    "prompt_per_token_ms": 0.5, "prompt_per_second": 2000,
    "predicted_n": 101, "predicted_ms": 2500,
    "predicted_per_token_ms": 25, "predicted_per_second": 40,
    "draft_n": 300, "draft_n_accepted": 100
  }
}
```

Поля draft условные. `timings_per_token: true` даёт промежуточные cumulative timings; `return_progress: true` добавляет `prompt_progress` с `total`, `cache`, `processed`, `time_ms`. Это данные конкретного запроса, не глобальная статистика всех клиентов. Первый токен учитывается иначе, чем последующие: для generation используется число после первого. [NInfer serving](https://github.com/Neroued/ninfer/blob/master/docs/serving.md).

## 2. Что можно показывать

| Категория | Что узнаем | Что требует отдельного учёта |
|---|---|---|
| Скорость | Prefill, generation, токены за интервал | Работа движка и скорость отдельного пользователя различаются |
| Задержки | TTFT, очередь, prefill/decode, полный ответ, межтокенные паузы | p50/p95/p99 требуют распределений; не все движки их экспортируют |
| Запросы | Активные, очередь, завершённые, отменённые | Причины завершения и определения счётчиков различаются |
| Кеш | Занятость, попадания, новые/повторно использованные токены | KV, SWA, Mamba, CPU/диск — разные пулы |
| Speculative | Draft/accepted, acceptance rate/length | Не путать принятые draft-токены с общим output |
| Исполнение | Preemption, CUDA graphs, scheduler, MoE, LoRA | Обычно специфично для движка и режима |
| Ресурсы | Некоторые размеры памяти и оценки FLOPs | Температура/мощность GPU нужны из отдельного аппаратного источника |

## 3. Универсальность и точность

Общими можно сделать HTTP-опрос, разбор Prometheus, хранение истории, обработку сбросов и свежести. **Соответствие полей и смысл расчётов нужны отдельно для каждого движка.** JSON FreeToken и timings NInfer требуют самостоятельных адаптеров.

Для сравнения нужно явно различать:

- **Throughput:** выходные токены всех запросов / прошедшее время, включая простой.
- **Скорость исполнения:** токены / время работы модели; простой исключён.
- **Скорость запроса:** токены и задержка одного пользователя; batching меняет соотношение с общей пропускной способностью.

`1 / средний TPOT` не равно среднему TPS. Общий TTFT следует считать как сумму времён / число наблюдений, а не среднее средних разных запусков. Серверный TTFT не включает весь путь до удалённого браузера. Короткий всплеск gauge может пройти между опросами; накопительный counter его сохраняет, пока процесс не перезапустился. Отсутствие данных должно оставаться отсутствием, не превращаться в ноль.

### Различия по движкам

**llama.cpp:** экспортирует сравнительно небольшой набор. В рассмотренном `to_metrics()` нет TTFT histogram и текущей доли KV cache. Скоростные buckets сбрасываются при сборе `/metrics`; несколько сборщиков могут влиять на окно усреднения. Для скорости в ходе длинного запроса наш код дополнительно читает `/slots`. [Экспортёр](https://github.com/ggml-org/llama.cpp/blob/master/tools/server/server-task.cpp), [обработка опроса](https://github.com/ggml-org/llama.cpp/blob/master/tools/server/server-context.cpp).

**vLLM:** доступны распределения задержек и размеров, кеш, очереди и причины, отдельные стадии выполнения. Режимы KV connectors, speculative, diffusion и MFU добавляют условные семейства. Границы histogram можно настроить. [Каталог](https://docs.vllm.ai/en/latest/usage/metrics/), [spec/diffusion](https://github.com/vllm-project/vllm/blob/main/vllm/v1/spec_decode/metrics.py).

**SGLang:** `token_usage` в текущем исходнике — максимум занятости full KV/SWA/Mamba, поэтому подпись «KV cache» может вводить в заблуждение. Есть отдельные пулы. [Коллектор](https://github.com/sgl-project/sglang/blob/main/python/sglang/srt/observability/metrics_collector.py). TTFT отличается для streaming и non-streaming; TPOT имеет условия включения наблюдений. [Семантика](https://docs.sglang.io/docs/references/production_metrics).

**FreeToken:** throughput использует короткое окно около 5 секунд. `p95_ms` и TTFT считаются по ограниченному кольцу последних HTTP-записей (до 512), не по всей жизни модели; TTFT берёт только записи с измеренным значением, при отсутствии возвращает 0. [Скорость](https://github.com/FlashML-org/FreeToken/blob/main/python/freetoken/server/stats.py), [кольцо запросов](https://github.com/FlashML-org/FreeToken/blob/main/python/freetoken/server/request_ring.py).

**NInfer:** документированные timings полезны для запроса, но опрос готового общего `/metrics` не подтверждён. Для полной статистики внешних клиентов нужен поддерживаемый источник в самом движке; наблюдение только нашего API этих клиентов не охватывает.

## 4. Каталог семейств

Имена ниже — объявления семейств. Для Python Counter смотри wire `_total`; Histogram разворачивается в buckets/sum/count. GaugeHistogram и Summary не следует обрабатывать как обычные gauges. Перечень условных дополнительных экспортёров ограничен рассмотренными источниками.

### llama.cpp: 16 найденных семейств

**Counter**

```text
llamacpp:n_decode_total
llamacpp:n_tokens_max
llamacpp:prompt_seconds_total
llamacpp:prompt_tokens_cached_total
llamacpp:prompt_tokens_total
llamacpp:spec_decode_num_accepted_tokens_per_pos_total
llamacpp:spec_decode_num_accepted_tokens_total
llamacpp:spec_decode_num_draft_tokens_total
llamacpp:spec_decode_num_drafts_total
llamacpp:tokens_predicted_seconds_total
llamacpp:tokens_predicted_total
```

**Gauge**

```text
llamacpp:n_busy_slots_per_decode
llamacpp:predicted_tokens_seconds
llamacpp:prompt_tokens_seconds
llamacpp:requests_deferred
llamacpp:requests_processing
```

Источник: [полный экспортёр](https://github.com/ggml-org/llama.cpp/blob/master/tools/server/server-task.cpp). `n_tokens_max` — максимум длины последовательности, хотя объявлен Counter. Per-position speculative серия условная.

### vllm: 63 найденных семейств

**Counter**

```text
vllm:corrupted_requests
vllm:diffusion_num_canvas_positions
vllm:diffusion_num_committed_tokens
vllm:diffusion_num_denoising_steps
vllm:estimated_flops_per_gpu_total
vllm:estimated_read_bytes_per_gpu_total
vllm:estimated_write_bytes_per_gpu_total
vllm:external_prefix_cache_hits
vllm:external_prefix_cache_queries
vllm:generation_tokens
vllm:mm_cache_hits
vllm:mm_cache_queries
vllm:nixl_num_failed_notifications
vllm:nixl_num_failed_transfers
vllm:nixl_num_kv_expired_reqs
vllm:nixl_num_notifications_after_expiry
vllm:num_preemptions
vllm:prefix_cache_hits
vllm:prefix_cache_queries
vllm:prompt_tokens
vllm:prompt_tokens_by_source
vllm:prompt_tokens_cached
vllm:request_success
vllm:simple_kv_offload_load_blocks_total
vllm:simple_kv_offload_save_outcomes_total
vllm:spec_decode_num_accepted_tokens
vllm:spec_decode_num_accepted_tokens_per_pos
vllm:spec_decode_num_draft_tokens
vllm:spec_decode_num_drafts
```

**Gauge**

```text
vllm:cache_config_info
vllm:engine_sleep_state
vllm:kv_cache_usage_perc
vllm:lora_requests_info
vllm:num_requests_kv_fetch_by_stage
vllm:num_requests_running
vllm:num_requests_waiting
vllm:num_requests_waiting_by_reason
vllm:simple_kv_offload_info
vllm:simple_kv_offload_pending_store_blocks
vllm:simple_kv_offload_used_blocks
```

**Histogram**

```text
vllm:e2e_request_latency_seconds
vllm:inter_token_latency_seconds
vllm:iteration_tokens_total
vllm:kv_block_idle_before_evict_seconds
vllm:kv_block_lifetime_seconds
vllm:kv_block_reuse_gap_seconds
vllm:nixl_bytes_transferred
vllm:nixl_num_descriptors
vllm:nixl_post_time_seconds
vllm:nixl_xfer_time_seconds
vllm:request_decode_time_seconds
vllm:request_generation_tokens
vllm:request_inference_time_seconds
vllm:request_max_num_generation_tokens
vllm:request_num_preemptions
vllm:request_params_max_tokens
vllm:request_params_n
vllm:request_prefill_kv_computed_tokens
vllm:request_prefill_time_seconds
vllm:request_prompt_tokens
vllm:request_queue_time_seconds
vllm:request_time_per_output_token_seconds
vllm:time_to_first_token_seconds
```

Источники: [основной экспортёр](https://github.com/vllm-project/vllm/blob/main/vllm/v1/metrics/loggers.py), [документированные коннекторы/MFU](https://docs.vllm.ai/en/latest/usage/metrics/), [spec/diffusion](https://github.com/vllm-project/vllm/blob/main/vllm/v1/spec_decode/metrics.py).

### sglang: 152 найденных семейств

**Counter**

```text
sglang:backuped_tokens_total
sglang:cached_tokens_total
sglang:cuda_graph_passes_total
sglang:dp_cooperation_forward_execution_seconds_total
sglang:dp_cooperation_realtime_tokens_total
sglang:encoder_cache_evictions_total
sglang:encoder_cache_hit_files_total
sglang:encoder_cache_hit_tokens_total
sglang:encoder_cache_total_files_total
sglang:encoder_cache_total_tokens_total
sglang:encoder_requests_received_total
sglang:encoder_requests_total
sglang:estimated_flops_per_gpu_total
sglang:estimated_read_bytes_per_gpu_total
sglang:estimated_write_bytes_per_gpu_total
sglang:evicted_tokens_total
sglang:forward_execution_seconds_total
sglang:generation_tokens_total
sglang:hicache_backup_bytes_total
sglang:hicache_backup_dropped_tokens_total
sglang:hicache_backup_tokens_total
sglang:hicache_dropped_tokens_total
sglang:hicache_prefetch_aux_alloc_failed_tokens_total
sglang:load_back_bytes_total
sglang:load_back_tokens_total
sglang:num_aborted_requests_total
sglang:num_bootstrap_failed_reqs_total
sglang:num_decode_host_receive_reqs_total
sglang:num_grammar_aborted_total
sglang:num_grammar_cache_hit_total
sglang:num_grammar_timeout_total
sglang:num_grammar_total
sglang:num_prefill_retries_total
sglang:num_requests_total
sglang:num_retracted_input_tokens_total
sglang:num_retracted_output_tokens_total
sglang:num_retracted_requests_total
sglang:num_so_requests_total
sglang:num_transfer_failed_reqs_total
sglang:prefetched_tokens_total
sglang:prefill_delayer_outcomes_total
sglang:prefill_effective_tokens_total
sglang:prompt_tokens_total
sglang:realtime_tokens_total
sglang:scheduler_idle_seconds_total
sglang:scheduler_process_cpu_seconds_total
sglang:scheduler_stage_seconds_total
sglang:spec_verify_calls_total
sglang:storage_prefetch_deferred_tokens_total
sglang:storage_prefetch_hit_tokens_total
sglang:storage_prefetch_unfulfilled_tokens_total
```

**Gauge**

```text
sglang:cache_hit_rate
sglang:context_len
sglang:decode_sum_seq_lens
sglang:encoder_cache_entries
sglang:encoder_cache_size_mb
sglang:full_token_usage
sglang:fwd_occupancy
sglang:gen_throughput
sglang:graph_memory_usage_gb
sglang:hicache_host_total_tokens
sglang:hicache_host_used_tokens
sglang:is_cuda_graph
sglang:kv_available_tokens
sglang:kv_cache_memory_usage_gb
sglang:kv_evictable_tokens
sglang:kv_used_tokens
sglang:lora_pool_slots_total
sglang:lora_pool_slots_used
sglang:lora_pool_utilization
sglang:mamba_available_tokens
sglang:mamba_evictable_tokens
sglang:mamba_usage
sglang:mamba_used_tokens
sglang:max_running_requests_under_SLO
sglang:max_total_num_tokens
sglang:max_total_num_tokens_swa
sglang:new_token_ratio
sglang:num_decode_host_receive_queue_reqs
sglang:num_decode_prealloc_queue_reqs
sglang:num_decode_transfer_queue_reqs
sglang:num_grammar_queue_reqs
sglang:num_pages
sglang:num_paused_reqs
sglang:num_prefill_bootstrap_queue_reqs
sglang:num_prefill_inflight_queue_reqs
sglang:num_queue_reqs
sglang:num_retracted_reqs
sglang:num_running_reqs
sglang:num_streaming_sessions
sglang:num_unique_running_routing_keys
sglang:num_used_tokens
sglang:page_size
sglang:pending_prealloc_token_usage
sglang:spec_accept_length
sglang:spec_accept_rate
sglang:spec_block_accept_length
sglang:spec_cap_length
sglang:spec_num_draft_tokens
sglang:spec_num_steps
sglang:startup_available_gpu_memory_gb
sglang:startup_cuda_graph_time_seconds
sglang:startup_time_seconds
sglang:streaming_session_held_tokens
sglang:swa_available_tokens
sglang:swa_evictable_tokens
sglang:swa_token_usage
sglang:swa_used_tokens
sglang:token_usage
sglang:utilization
sglang:weight_load_duration_seconds
sglang:weight_memory_usage_gb
```

**GaugeHistogram**

```text
sglang:routing_key_all_req_count
sglang:routing_key_running_req_count
```

**Histogram**

```text
sglang:backup_bandwidth
sglang:backup_pgs
sglang:e2e_request_latency_seconds
sglang:encoder_mm_items_per_batch
sglang:encoder_mm_items_per_request
sglang:encoder_model_forward_seconds
sglang:encoder_preprocess_seconds
sglang:encoder_queue_wait_seconds
sglang:encoder_request_e2e_latency_seconds
sglang:encoder_transfer_seconds
sglang:eplb_gpu_physical_count
sglang:eviction_duration_seconds
sglang:generation_tokens_histogram
sglang:get_loads_duration_seconds
sglang:grammar_compilation_time_seconds
sglang:grammar_ebnf_size
sglang:grammar_schema_count
sglang:grammar_tree_traversal_time_avg
sglang:grammar_tree_traversal_time_max
sglang:hicache_backup_duration_seconds
sglang:inter_token_latency_seconds
sglang:kv_transfer_alloc_ms
sglang:kv_transfer_bootstrap_ms
sglang:kv_transfer_latency_ms
sglang:kv_transfer_speed_gb_s
sglang:kv_transfer_total_mb
sglang:load_back_duration_seconds
sglang:per_stage_req_latency_seconds
sglang:prefetch_bandwidth
sglang:prefetch_pgs
sglang:prefill_delayer_wait_forward_passes
sglang:prefill_delayer_wait_seconds
sglang:prompt_tokens_histogram
sglang:queue_time_seconds
sglang:request_time_per_output_token_seconds
sglang:time_to_first_token_seconds
sglang:uncached_prompt_tokens_histogram
```

**Summary**

```text
sglang:eplb_balancedness
```

Источник: [основной metrics_collector.py](https://github.com/sgl-project/sglang/blob/main/python/sglang/srt/observability/metrics_collector.py). Это объявления разных коллекторов, включая условные LoRA/HiCache/encoder/PD; один запуск не обязан публиковать всё. Документация также показывает `sglang:func_latency_seconds` из инструментирования функций; оно не включено в список статических объявлений этого файла.

## 5. Что уже есть в нашей реализации

Проверены `backend/app/metrics/providers.py`, `performance.py`, `service.py`.

| Часть | Сейчас |
|---|---|
| Получение | `/metrics` для трёх движков; `/v1/stats` FreeToken; NInfer unavailable |
| Prometheus parser | Читает числовые samples; отбрасывает labels и HELP/TYPE |
| Ответ нашего API | Только выбранные нормализованные поля, не полный сырой каталог |
| Decode/Prefill | Для vLLM/SGLang — разность token counters; llama.cpp также `/slots` |
| TTFT | Из histogram sum/count по новым наблюдениям |
| GPU | Отдельный системный источник; не универсальные engine metrics |
| Свежесть | После ошибки предыдущая выборка до 60 секунд может оставаться `ok`, далее stale |
| История | Сбор привязан к запросам API; график хранит историю текущего просмотра |

Сейчас labels теряются, поэтому разные серии одного имени суммируются без знания их роли. Для подробных histogram, TP/DP рангов и labels причин это недостаточно. Поля кеша требуют уточнения семантики: старый llama.cpp alias может отсутствовать, SGLang `token_usage` не равен только KV.

### Предложение для следующего обсуждения

1. Один фоновый сборщик на запуск, независимый от открытой страницы.
2. Сохранять исходные семейства и серии с labels, типом, единицей, временем измерения, идентификатором процесса и состоянием свежести.
3. Отдельные адаптеры переводят их в общие показатели с явно заданным смыслом.
4. Неизвестные показатели доступны в подробном каталоге, даже если ещё не вынесены на dashboard.
5. Для общих графиков хранить как throughput за реальное время, так и скорость исполнения там, где её можно вычислить. TTFT объединять по количеству наблюдений.

Это предложение, код приложения в рамках исследования не изменён.

## Источники снимка

Исходники скачаны с открытых веток upstream, поэтому номера строк и состав могут изменяться. SHA256 ниже идентифицирует прочитанный текст, **не Git commit**. Документ не утверждает совпадение этих веток с установленными версиями.

- `vllm` SHA256: `086d2ec61ac38574e7a251da098d05b7abe553fbe451c0147c0c3acd1a5d840a`
- `sglang` SHA256: `a254971ddf7d1487ba33ecb1ec8c61b5f99bcfc400d1eeb2deeb69cd354082e9`
- `llama` SHA256: `0fd5c8df2525e61214986e8abe3fc4edbb92f5dd652c9c2bfd1bc2712218a585`
- `freetoken` SHA256: `42dfcdddb6a7bce359713a6fd78a26ba3088624c237684ca4c002c8de52c9456`
