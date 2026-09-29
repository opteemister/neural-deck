export type EngineId = 'vllm' | 'llama.cpp' | 'sglang' | 'ninfer' | 'freetoken'

export type InstallationKind = 'python_venv' | 'binary'
export type InstallationStatus = 'ready' | 'broken' | 'candidate'

export interface Installation {
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
  created_at: string
  started_at: string | null
  finished_at: string | null
  stdout: string
  stderr: string
  exit_code: number | null
  error: string | null
  resolved_packages?: Record<string, string> | null
  checked_at?: string | null
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
  index: number
  name: string
  memory_used_bytes: number
  memory_total_bytes: number
  usage_percent: number | null
  temperature_celsius: number | null
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
}

export interface SettingsData {
  data_root: string
  models_root: string
  log_root: string
  extra_venv_roots: string[]
  host: string
  port: number
}

export interface LocalModelArtifact {
  id: string
  path: string
  kind: 'file' | 'directory'
  format: string
  compatibility: string
  size_bytes: number | null
  hf_match: {
    status: 'downloaded' | 'incomplete' | 'unknown'
    repo_id?: string | null
    revision?: string | null
  }
}

export interface LocalModelsData {
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

/** Models exposed by the OpenAI-compatible /v1/models endpoint. */
export interface OpenAiModel {
  id: string
  object: 'model'
  created?: number
  owned_by?: string
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

export interface Metric {
  name: string
  value: number | string | null
  unit: string | null
  source: string
  observed_at: string
  state: 'ok' | 'unavailable' | 'stale'
}

export interface MetricsData {
  items: Metric[]
}

export interface LogsData {
  instance_id: string
  lines: string[]
  truncated: boolean
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
}

export interface HfSearchData {
  items: Array<{ repo_id: string; revision?: string | null; description?: string | null; tags: string[] }>
}

export interface HfRepoData {
  repo_id: string
  revision: string
  files: HfFile[]
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

export interface ChatCompletionResponse {
  id?: string
  object?: string
  model?: string
  choices: Array<{ message?: { content?: string }; delta?: { content?: string } }>
}

export interface DownloadRequest {
  repo_id: string
  revision: string
  files: string[] | null
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
