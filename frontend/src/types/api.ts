export type EngineId = 'vllm' | 'llama.cpp' | 'sglang' | 'ninfer' | 'freetoken'

export type InstallationKind = 'python_venv' | 'binary'
export type InstallationStatus = 'ready' | 'broken' | 'candidate'

export interface Installation {
  registered?: boolean
  id: string
  engine_id: EngineId
  kind: InstallationKind
  root_path: string
  executable_path: string
  version: string | null
  status: InstallationStatus
  verified: boolean
  diagnostic: string | null
}

export interface EngineData {
  id: EngineId
  display_name: string
  command_template: string
  installation_template: string
  installation_root: string
  installation_kinds: InstallationKind[]
  installations: Installation[]
}

export interface EnginesData {
  items: EngineData[]
}

export interface Operation {
  id: string
  kind: string
  state: 'queued' | 'running' | 'succeeded' | 'failed'
  target_id: string
  label?: string | null
  created_at: string
  started_at: string | null
  finished_at: string | null
  stdout: string
  stderr: string
  exit_code: number | null
  error: string | null
  resolved_packages?: Record<string, string> | null
  checked_at?: string | null
  progress?: {
    files_total: number
    files_completed: number
    bytes_total: number
    bytes_completed: number
    current_file: string | null
  } | null
}

export interface CpuData {
  cores: number
  usage_percent: number | null
}

export interface RamData {
  used_bytes: number
  total_bytes: number
}

export interface GpuData {
  uuid: string
  index: number
  name: string
  memory_used_bytes: number
  memory_total_bytes: number
  usage_percent: number | null
  temperature_celsius: number | null
  fan_speed_percent?: number | null
  power_watts?: number | null
  power_limit_watts?: number | null
}

export type InstanceState = 'starting' | 'ready' | 'running' | 'stopping' | 'exited' | 'error' | 'unknown'

export interface Instance {
  id: string
  source: 'managed' | 'external'
  profile_name: string | null
  engine_id: string
  pid: number
  process_start_time_ns: number | null
  port: number | null
  model_name: string | null
  state: InstanceState
  observed_at: string
  /** Backend-confirmed permission to send a stop request for this process. */
  can_stop: boolean
  /** True only when PID, start time and ownership have been verified. */
  identity_verified: boolean
}

export interface SystemData {
  cpu: CpuData
  ram: RamData
  gpus: GpuData[]
  instances: Instance[]
}

export type ComponentState = 'ok' | 'degraded' | 'error'

export interface StatusComponent {
  name: string
  state: ComponentState
  message?: string | null
  checked_at: string
}

export interface StatusData {
  overall: ComponentState
  components: StatusComponent[]
}

export interface HealthData {
  status: 'ok' | 'degraded'
  uptime_seconds?: number | null
}

export interface SettingsData {
  data_root: string
  models_root: string
  log_root: string
  extra_venv_roots: string[]
  host: string
  port: number
  openai_port: number
}

export interface LocalModelArtifact {
  id: string
  path: string
  kind: 'file' | 'directory'
  vendor?: string
  model_name?: string
  format: string
  compatibility: string
  size_bytes: number | null
  hf_match: {
    status: 'downloaded' | 'incomplete' | 'unknown'
    repo_id?: string | null
    revision?: string | null
    scope?: 'snapshot' | 'selection' | null
    missing_files?: string[]
    unverified_files?: string[]
    remote_check?: {
      availability: 'success' | 'unavailable' | 'not_tracked'
      last_attempted_at?: string | null
      last_successful_at?: string | null
      current_revision?: string | null
      repository_date?: string | null
      last_error?: string | null
      stale?: boolean
    } | null
    remote_update?: {
      status: 'up_to_date' | 'update_available' | 'unknown' | 'not_tracked'
      changed_files: number
    } | null
    local_integrity?: {
      baseline_status: 'matches_downloaded' | 'diverged_from_downloaded' | 'unknown' | 'unavailable'
      remote_status: 'matches_current_remote' | 'differs_from_current_remote' | 'unknown' | 'unavailable'
    } | null
    changed_paths?: Array<{
      path: string
      status: 'changed' | 'added' | 'removed' | 'local_divergence'
      downloaded_file_date?: string | null
      remote_file_date?: string | null
    }>
    downloaded_revision_date?: string | null
    remote_revision_date?: string | null
  }
  variants: Array<{ path: string; format: string; size_bytes: number | null }>
  auxiliary_files: Array<{ path: string; format: string; size_bytes: number | null }>
  groups?: Array<{
    id: string
    label: string
    kind: 'variant' | 'auxiliary_block'
    path: string | null
    files: Array<{ path: string; format: string; size_bytes: number | null }>
    file_count: number
    size_bytes: number | null
    status: 'complete' | 'incomplete' | 'unknown'
    deletable: boolean
  }>
  auxiliary_blocks?: Array<{
    id: string
    label: string
    kind: 'variant' | 'auxiliary_block'
    path: string | null
    files: Array<{ path: string; format: string; size_bytes: number | null }>
    file_count: number
    size_bytes: number | null
    status: 'complete' | 'incomplete' | 'unknown'
    deletable: boolean
  }>
  supporting_files: number
}

export interface LocalModelsData {
  scanning?: boolean
  scan_error?: string | null
  items: LocalModelArtifact[]
}

export interface ModelRegistryItem {
  id: string
  upstream_model: string | null
  instance_id: string
  engine_id: string
  profile_name: string | null
  observed_at: string
}

export interface ModelRegistryData {
  items: ModelRegistryItem[]
}

export type ProxyRouteKind = 'auto' | 'alias'

export interface ProxyRouteCandidate {
  profile_id: string
  profile_name: string | null
  instance_id: string | null
  upstream_model: string | null
  engine_id: string | null
  port: number | null
  state: string
  available: boolean
  reason: string | null
}

export interface ProxyRoute {
  name: string
  kind: ProxyRouteKind
  candidate_profile_ids: string[]
  candidates: ProxyRouteCandidate[]
  available: boolean
  diagnostics: string[]
}

export interface ProxyRoutesData {
  items: ProxyRoute[]
}

/** Models exposed by the OpenAI-compatible /v1/models endpoint. */
export interface OpenAiModel {
  id: string
  object: 'model'
  created?: number
  owned_by?: string
  context_length?: number
  engine_id?: string
  reasoning_capabilities?: {
    efforts: string[]
    effort_parameter: 'reasoning_effort' | 'chat_template_kwargs'
    can_disable_thinking: boolean
  }
}

export interface OpenAiModelsData {
  items: OpenAiModel[]
}

export interface ProfileEngineRef {
  id: EngineId
  installation_id: string
}

export interface ProfileModelRef {
  path: string
  draft_path?: string | null
}

export interface ProfileData {
  version: 1
  name: string
  model_name: string
  engine: ProfileEngineRef
  model: ProfileModelRef
  command: string
  environment: Record<string, string>
  autostart: boolean
  enable_metrics?: boolean
  input_price_usd_per_million_tokens?: number | null
  output_price_usd_per_million_tokens?: number | null
  cached_input_price_usd_per_million_tokens?: number | null
}

export interface ProfileIssue {
  file: string
  code: string
  message: string
}

export interface ProfileListData {
  items: ProfileData[]
  issues: ProfileIssue[]
}

export type ProfileSummaryState = 'ok' | 'unavailable' | 'stale' | 'unknown' | 'not_measured'

export interface ProfileSummaryMetric {
  value: number | null
  unit: string
  state: ProfileSummaryState
  source: string
  reason?: string | null
}

export interface ProfileSummary {
  profile_name: string
  period: { from: string; to: string; timezone: 'UTC' }
  performance: { prefill_tps: ProfileSummaryMetric; decode_tps: ProfileSummaryMetric }
  memory: {
    disk_bytes: ProfileSummaryMetric
    vram_current_bytes: ProfileSummaryMetric
    vram_last_bytes: ProfileSummaryMetric
    vram_avg_bytes: ProfileSummaryMetric
    vram_peak_bytes: ProfileSummaryMetric
  }
  concurrency: {
    optimal: ProfileSummaryMetric
    limit: ProfileSummaryMetric
    observed_max: ProfileSummaryMetric
    limit_source: string
    benchmark_state: 'not_measured' | 'measured'
  }
  load: { percent: ProfileSummaryMetric }
  endpoint: {
    port: number | null
    state: 'ok' | 'unknown'
    source: string
    conflict: boolean
    conflict_with: string[]
    reason?: string | null
  }
}

export interface ProfileSummaryData {
  items: ProfileSummary[]
  period: { from: string; to: string; timezone: 'UTC' }
  source: string
}

export interface StatisticsTokenMetric {
  known_total: number | null
  unknown_requests: number
}

export interface StatisticsAggregate {
  requests: {
    total: number
    success: number
    failed: number
    interrupted: number
    unknown: number
  }
  tokens: {
    input: StatisticsTokenMetric
    cached_input: StatisticsTokenMetric
    output: StatisticsTokenMetric
  }
  performance: {
    latency_p95_ms: number | null
    latency_ms: number | null
    prefill_tps: number | null
    decode_tps: number | null
  }
  cost: {
    usd: number | null
    state: 'ok' | 'partial' | 'unknown' | 'unavailable'
    unknown_requests: number
    prices: { input: number | null; cached_input: number | null; output: number | null }
  }
}

export interface StatisticsProfile extends StatisticsAggregate {
  profile_name: string
  model_name: string | null
  load: {
    percent: number
    active_seconds: number
    period_seconds: number
    max_concurrency: number
    basis: 'engine_active_requests'
    state: 'ok' | 'unknown'
  }
}

export interface StatisticsClient extends StatisticsAggregate {
  client_id: number | null
  label: string
  profiles: string[]
}

export interface StatisticsData {
  summary: {
    sessions: number | null
    sessions_state: 'not_recorded'
    active_days: number
    period_days: number
    streak_days: number
    success_rate: number | null
  }
  period: {
    name: string
    from: string
    to: string
    timezone: 'UTC'
    boundary: '[from,to)'
    bucket: 'day'
    request_event_time: string
  }
  filters: { model_name: string | null; profile_name: string | null }
  filters_options: { models: string[]; profiles: string[] }
  totals: StatisticsAggregate
  activity: Array<StatisticsAggregate & { date: string; benchmark: boolean }>
  profiles: StatisticsProfile[]
  clients: StatisticsClient[]
  benchmarks: { intervals: Array<{ from: string; to: string; label?: string }>; traffic_included: boolean; state: string }
  source: string
}

export interface Metric {
  metadata?: Record<string, unknown>
  name: string
  value: number | string | null
  unit: string | null
  source: string
  observed_at: string
  state: 'ok' | 'unavailable' | 'stale'
}

export interface MetricsData {
  source?: string
  peaks?: Record<string, number>
  items: Metric[]
}

export interface LogsData {
  instance_id: string
  lines: string[]
  truncated: boolean
  available?: boolean
}

export type LogKind = 'application' | 'profile' | 'operations'

export interface LogSource {
  id: string
  kind: LogKind
  label: string
  profile_name: string | null
  file_count: number
  updated_at: string | null
}

export interface LogSourceData {
  items: LogSource[]
}

export interface LogLine {
  number: number
  text: string
}

export interface LogData {
  source: LogSource
  lines: LogLine[]
  total_lines: number
  start_line: number
  end_line: number
  has_older: boolean
}

export interface TokenStatsItem {
  profile_name: string
  requests: number
  input_tokens: { known_total: number | null; unknown_requests: number }
  output_tokens: { known_total: number | null; unknown_requests: number }
  source: string
}

export interface TokenStatsData {
  items: TokenStatsItem[]
}

export interface HfFile {
  path: string
  size: number | null
  etag?: string | null
  sha256?: string | null
  hash_kind?: 'git_blob' | 'md5' | null
  file_date?: string | null
}

export interface HfSearchData {
  items: Array<{ repo_id: string; revision?: string | null; description?: string | null; tags: string[] }>
}

export interface HfRepoData {
  repo_id: string
  revision: string
  revision_date?: string | null
  description?: string | null
  readme?: string | null
  files: HfFile[]
  quantizations?: HfSearchData['items']
}

export interface FormatScriptData {
  script: string
  changed: boolean
  reason?: string | null
}

export interface ChatMessage {
  role: 'user' | 'assistant'
  content: string
}

export interface ChatUsage {
  prompt_tokens?: number
  completion_tokens?: number
  total_tokens?: number
  completion_tokens_details?: { reasoning_tokens?: number }
  prompt_tokens_details?: { cached_tokens?: number }
}

export interface ChatTimings {
  prompt_n?: number
  prompt_ms?: number
  predicted_n?: number
  predicted_ms?: number
  predicted_per_second?: number
}

export interface ChatCompletionResponse {
  id?: string
  object?: string
  model?: string
  choices: Array<{ message?: { content?: string; reasoning_content?: string; reasoning?: string }; delta?: { content?: string; reasoning_content?: string; reasoning?: string }; finish_reason?: string | null }>
  usage?: ChatUsage
  timings?: ChatTimings
}

export interface DownloadRequest {
  repo_id: string
  revision: string
  files: string[] | null
  label?: string | null
}

export interface ApiSuccess<T> {
  data: T
  request_id: string
}

export interface ApiErrorBody {
  code: string
  message: string
  details: Record<string, unknown>
  request_id: string
  retryable: boolean
}

export interface ApiErrorResponse {
  error: ApiErrorBody
}


export interface MetricsHistoryData {
  history: Array<MetricsData & { observed_at: string; profile_name?: string | null; pid?: number }>
  current: (MetricsData & { observed_at: string }) | null
  peaks: Record<string, number>
  source: string
}

export interface TokenHistoryData {
  items: Array<{ at: string; requests: number; input_tokens: number | null; output_tokens: number | null; cached_tokens: number | null; input_tokens_unknown: number; output_tokens_unknown: number; cached_tokens_unknown: number }>
  from: string
  to: string
  bucket_minutes: number
  source: string
}
