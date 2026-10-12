import type {
  ApiErrorResponse,
  ApiSuccess,
  EngineData,
  EnginesData,
  DownloadRequest,
  FormatScriptData,
  HfRepoData,
  HfSearchData,
  HealthData,
  Installation,
  LocalModelsData,
  LogData,
  LogSourceData,
  ModelRegistryData,
  LogsData,
  MetricsData,
  OpenAiModel,
  OpenAiModelsData,
  Operation,
  ProfileData,
  ProfileListData,
  ProfileSummaryData,
  ProxyRoute,
  ProxyRoutesData,
  SettingsData,
  StatusData,
  StatisticsData,
  SystemData,
  TokenStatsData,
} from '@/types/api'

import { openAiEndpoint } from '@/lib/openai-endpoint'

const apiBase = (import.meta.env.VITE_API_BASE_URL as string | undefined)?.replace(/\/$/, '') ?? ''
let modelApiBasePromise: Promise<string> | undefined

export async function getOpenAiEndpoint(): Promise<string> {
  const configured = import.meta.env.VITE_OPENAI_API_BASE_URL as string | undefined
  if (configured?.trim()) return openAiEndpoint(configured)
  if (!modelApiBasePromise) {
    modelApiBasePromise = request<{ url: string }>('/api/v1/openai-endpoint')
      .then((data) => data.url)
      .catch((error: unknown) => { modelApiBasePromise = undefined; throw error })
  }
  return modelApiBasePromise
}

export class NeuralDeckApiError extends Error {
  readonly status: number
  readonly code: string
  readonly retryable: boolean
  readonly requestId: string | null
  readonly details: Record<string, unknown>

  constructor(message: string, options: { status: number; code?: string; retryable?: boolean; requestId?: string | null; details?: Record<string, unknown> }) {
    super(message)
    this.name = 'NeuralDeckApiError'
    this.status = options.status
    this.code = options.code ?? 'request_failed'
    this.retryable = options.retryable ?? false
    this.requestId = options.requestId ?? null
    this.details = options.details ?? {}
  }
}

async function readError(response: Response): Promise<NeuralDeckApiError> {
  try {
    const body = (await response.json()) as Partial<ApiErrorResponse>
    if (body.error) {
      const errors = body.error.details?.errors
      const reasons = body.error.code === 'validation_error' && Array.isArray(errors)
        ? errors.flatMap((error: unknown) => {
          if (!error || typeof error !== 'object') return []
          const item = error as { loc?: unknown; msg?: unknown }
          if (!Array.isArray(item.loc) || typeof item.msg !== 'string') return []
          const field = item.loc.filter((part) => part !== 'body').join('.')
          return [`${field || 'Профиль'}: ${item.msg}`]
        })
        : []
      return new NeuralDeckApiError(reasons.length ? `${body.error.message}: ${reasons.join('; ')}` : body.error.message, {
        status: response.status,
        code: body.error.code,
        retryable: body.error.retryable,
        requestId: body.error.request_id,
        details: body.error.details,
      })
    }
  } catch {
    // The response can be empty or non-JSON; the HTTP status remains useful.
  }
  return new NeuralDeckApiError(`Запрос не выполнен (${response.status})`, { status: response.status })
}

export async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const headers = new Headers(init?.headers)
  if (init?.body && !headers.has('Content-Type')) headers.set('Content-Type', 'application/json')
  const response = await fetch(`${apiBase}${path}`, { ...init, headers })
  if (!response.ok) throw await readError(response)

  const body = (await response.json()) as ApiSuccess<T>
  if (!body || typeof body !== 'object' || !('data' in body)) {
    throw new NeuralDeckApiError('Ответ API имеет неверный формат', { status: response.status, code: 'invalid_response' })
  }
  return body.data
}

async function openAiRequest<T>(path: string, init?: RequestInit): Promise<T> {
  const headers = new Headers(init?.headers)
  if (init?.body && !headers.has('Content-Type')) headers.set('Content-Type', 'application/json')
  const response = await fetch(`${apiBase}/api/proxy${path}`, { ...init, headers })
  if (!response.ok) throw await readError(response)
  return (await response.json()) as T
}

function assertOpenAiError(response: Response): void {
  throw new NeuralDeckApiError(`Запрос не выполнен (${response.status})`, { status: response.status })
}

export type ChatStreamChunk = { content: string; done: boolean; reasoning?: string; usage?: import('@/types/api').ChatUsage; timings?: import('@/types/api').ChatTimings; finishReason?: string | null }

/** Read the standard OpenAI SSE response and the JSON fallback used by small servers. */
export async function streamChat(
  body: { model: string; messages: Array<{ role: 'user' | 'assistant'; content: string }>; stream?: boolean; reasoning_effort?: string; chat_template_kwargs?: { enable_thinking?: boolean; reasoning_effort?: string } },
  onChunk: (chunk: ChatStreamChunk) => void,
  signal?: AbortSignal,
): Promise<void> {
  const headers = new Headers({ 'Content-Type': 'application/json', Accept: 'text/event-stream, application/json' })
  const response = await fetch(`${apiBase}/api/proxy/chat/completions`, { method: 'POST', headers, signal, body: JSON.stringify({ ...body, stream: true, stream_options: { include_usage: true } }) })
  if (!response.ok) {
    try {
      const payload = (await response.json()) as ApiErrorResponse
      if (payload.error) throw new NeuralDeckApiError(payload.error.message, { status: response.status, code: payload.error.code, retryable: payload.error.retryable, requestId: payload.error.request_id })
    } catch (error) {
      if (error instanceof NeuralDeckApiError) throw error
    }
    assertOpenAiError(response)
  }

  const contentType = response.headers.get('content-type') ?? ''
  if (!contentType.includes('text/event-stream') || !response.body) {
    const payload = (await response.json()) as import('@/types/api').ChatCompletionResponse
    const message = payload.choices?.[0]?.message
    onChunk({ content: message?.content ?? '', reasoning: message?.reasoning_content ?? message?.reasoning, usage: payload.usage, timings: payload.timings, finishReason: payload.choices?.[0]?.finish_reason, done: true })
    return
  }

  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  let eventData: string[] = []
  let finished = false
  const dispatch = (raw: string) => {
    const value = raw.trim()
    if (value === '[DONE]') {
      finished = true
      onChunk({ content: '', done: true })
      return
    }
    try {
      const payload = JSON.parse(value) as import('@/types/api').ChatCompletionResponse
      const choice = payload.choices?.[0]
      const content = choice?.delta?.content ?? choice?.message?.content ?? ''
      const reasoning = choice?.delta?.reasoning_content ?? choice?.delta?.reasoning ?? choice?.message?.reasoning_content ?? choice?.message?.reasoning
      if (content || reasoning || payload.usage || payload.timings || choice?.finish_reason) onChunk({ content, reasoning, usage: payload.usage, timings: payload.timings, finishReason: choice?.finish_reason, done: false })
    } catch {
      // Keep-alives and partial events are harmless; the next event completes them.
    }
  }
  const consumeLine = (line: string) => {
    const value = line.replace(/\r$/, '')
    if (value === '') {
      if (eventData.length > 0) {
        dispatch(eventData.join('\n'))
        eventData = []
      }
      return
    }
    if (value.startsWith('data:')) eventData.push(value.slice(5).replace(/^ /, ''))
  }
  try {
    while (!finished) {
      const { done, value } = await reader.read()
      buffer += decoder.decode(value ?? new Uint8Array(), { stream: !done })
      const lines = buffer.split(/\r?\n/)
      buffer = lines.pop() ?? ''
      lines.forEach(consumeLine)
      if (done) break
    }
    if (!finished) {
      if (buffer) consumeLine(buffer)
      if (eventData.length > 0) dispatch(eventData.join('\n'))
      onChunk({ content: '', done: true })
    }
  } finally {
    await reader.cancel().catch(() => undefined)
    reader.releaseLock()
  }
}

const json = (body: unknown): RequestInit => ({ method: 'POST', body: JSON.stringify(body) })

export const api = {
  getSystem: () => request<SystemData>('/api/v1/system'),
  getStatus: () => request<StatusData>('/api/v1/status'),
  getHealth: () => request<HealthData>('/api/v1/health'),
  getSettings: () => request<SettingsData>('/api/v1/settings'),
  updateSettings: (body: { models_root: string }) => request<SettingsData>('/api/v1/settings', { method: 'PATCH', body: JSON.stringify(body) }),
  getEngines: () => request<EnginesData>('/api/v1/engines'),
  getEngine: (id: string) => request<EngineData>(`/api/v1/engines/${encodeURIComponent(id)}`),
  scanEngines: () => request<EnginesData>('/api/v1/engines/scan', json(undefined)),
  forgetInstallation: (id: string) => request<{ id: string }>(`/api/v1/installations/${encodeURIComponent(id)}`, { method: 'DELETE' }),
  getInstallations: () => request<{ items: Installation[] }>('/api/v1/installations'),
  getInstallation: (id: string) => request<Installation>(`/api/v1/installations/${encodeURIComponent(id)}`),
  createInstallation: (engineId: string, body: ({ kind: 'python_venv'; venv_name: string } | { kind: 'binary'; path: string }) & { command?: string }) => request<Operation>(`/api/v1/engines/${encodeURIComponent(engineId)}/installations`, json(body)),
  updateInstallation: (id: string) => request<Operation>(`/api/v1/installations/${encodeURIComponent(id)}/update`, json(undefined)),
  maintenance: (id: string, command: string) => request<Operation>(`/api/v1/installations/${encodeURIComponent(id)}/maintenance`, json({ command })),
  getOperation: (id: string) => request<Operation>(`/api/v1/operations/${encodeURIComponent(id)}`),
  getDownloads: () => request<{ items: Operation[] }>('/api/v1/operations?kind=download&active=true'),
  cancelOperation: (id: string) => request<Operation>(`/api/v1/operations/${encodeURIComponent(id)}/cancel`, json(undefined)),
  /** Current model registry served by observed instances. */
  getModels: () => request<ModelRegistryData>('/api/v1/models'),
  getLocalModels: () => request<LocalModelsData>('/api/v1/models/local'),
  scanLocalModels: () => request<LocalModelsData>('/api/v1/models/local/scan', json(undefined)),
  deleteLocalModel: (id: string) => request<LocalModelsData>(`/api/v1/models/local/${encodeURIComponent(id)}`, { method: 'DELETE' }),
  deleteLocalModelGroup: (modelId: string, groupId: string) => request<LocalModelsData>(`/api/v1/models/local/${encodeURIComponent(modelId)}/groups/${encodeURIComponent(groupId)}`, { method: 'DELETE' }),
  searchHf: (query: string) => request<HfSearchData>(`/api/v1/models/hf/search?q=${encodeURIComponent(query)}`),
  getHfTrending: (group = 'all', limit = 50) => request<HfSearchData>(`/api/v1/models/hf/trending?group=${encodeURIComponent(group)}&limit=${limit}`),
  getHfRepo: (namespace: string, name: string, revision?: string) => request<HfRepoData>(`/api/v1/models/hf/repos/${encodeURIComponent(namespace)}/${encodeURIComponent(name)}${revision ? `?revision=${encodeURIComponent(revision)}` : ''}`),
  getHfQuantizations: (namespace: string, name: string) => request<import('@/types/api').HfSearchData>(`/api/v1/models/hf/repos/${encodeURIComponent(namespace)}/${encodeURIComponent(name)}/quantizations`),
  getHfFiles: (namespace: string, name: string, revision?: string) => request<HfRepoData>(`/api/v1/models/hf/repos/${encodeURIComponent(namespace)}/${encodeURIComponent(name)}/files${revision ? `?revision=${encodeURIComponent(revision)}` : ''}`),
  downloadModels: (body: DownloadRequest) => request<Operation>('/api/v1/models/downloads', json(body)),
  getProfiles: () => request<ProfileListData>('/api/v1/profiles'),
  getProfileSummary: () => request<ProfileSummaryData>('/api/v1/profiles/summary'),
  getProfile: (name: string) => request<ProfileData>(`/api/v1/profiles/${encodeURIComponent(name)}`),
  getProxyRoutes: () => request<ProxyRoutesData>('/api/v1/proxy/routes'),
  createProxyAlias: (body: { name: string; profile_ids: string[] }) => request<ProxyRoute>('/api/v1/proxy/routes/aliases', json(body)),
  updateProxyRoute: (name: string, profile_ids: string[]) => request<ProxyRoute>(`/api/v1/proxy/routes/${encodeURIComponent(name)}`, { method: 'PUT', body: JSON.stringify({ profile_ids }) }),
  deleteProxyAlias: (name: string) => request<{ name: string }>(`/api/v1/proxy/routes/aliases/${encodeURIComponent(name)}`, { method: 'DELETE' }),
  previewProfile: (body: ProfileData) => request<{ command: string }>('/api/v1/profiles/preview', json(body)),
  createProfile: (body: ProfileData) => request<ProfileData>('/api/v1/profiles', { method: 'POST', body: JSON.stringify(body) }),
  updateProfile: (name: string, body: ProfileData) => request<ProfileData>(`/api/v1/profiles/${encodeURIComponent(name)}`, { method: 'PUT', body: JSON.stringify(body) }),
  deleteProfile: (name: string) => request<{ name: string }>(`/api/v1/profiles/${encodeURIComponent(name)}`, { method: 'DELETE' }),
  formatScript: (script: string) => request<FormatScriptData>('/api/v1/profiles/format-script', json({ script })),
  startProfile: (name: string) => request<import('@/types/api').Instance>(`/api/v1/profiles/${encodeURIComponent(name)}/start`, json(undefined)),
  startProfileConfiguration: (body: ProfileData) => request<import('@/types/api').Instance>('/api/v1/profiles/start', json(body)),
  getInstances: () => request<{ items: import('@/types/api').Instance[] }>('/api/v1/instances'),
  stopInstance: (id: string) => request<Operation>(`/api/v1/instances/${encodeURIComponent(id)}/stop`, json(undefined)),
  getMetrics: (id: string) => request<MetricsData>(`/api/v1/instances/${encodeURIComponent(id)}/metrics`),
  getTokenHistory: (profile_name?: string, pid?: number) => {
    const query = new URLSearchParams({ mode: 'token-history' })
    if (profile_name !== undefined) query.set('profile_name', profile_name)
    if (pid !== undefined) query.set('pid', String(pid))
    return request<import('@/types/api').TokenHistoryData>(`/api/v1/metrics?${query}`)
  },
  getMetricAggregates: (filters: { profile_name?: string; pid?: number; scope?: 'profile' | 'all' } = {}) => {
    const query = new URLSearchParams({ mode: 'aggregates' })
    if (filters.profile_name !== undefined) query.set('profile_name', filters.profile_name)
    if (filters.pid !== undefined) query.set('pid', String(filters.pid))
    if (filters.scope !== undefined) query.set('scope', filters.scope)
    return request<{ aggregates: Record<string, { avg: number; max: number }>; from: string; to: string; source: string }>(`/api/v1/metrics?${query}`)
  },
  getMetricConcurrency: (profileName?: string) => {
    const query = new URLSearchParams({ days: '7' })
    if (profileName !== undefined) query.set('profile_name', profileName)
    return request<{ items: import('@/components/concurrency-chart').ConcurrencyPoint[]; days: number; source: string }>(`/api/v1/metrics/concurrency?${query}`)
  },
  getMetricsHistory: (filters: { profile_name?: string; pid?: number; scope?: 'profile' | 'all'; mode?: 'bootstrap' | 'current' } = {}) => {
    const query = new URLSearchParams({ mode: filters.mode ?? 'bootstrap', limit: '120' })
    if (filters.profile_name !== undefined) query.set('profile_name', filters.profile_name)
    if (filters.pid !== undefined) query.set('pid', String(filters.pid))
    if (filters.scope !== undefined) query.set('scope', filters.scope)
    return request<import('@/types/api').MetricsHistoryData>(`/api/v1/metrics?${query}`)
  },
  getLogs: (id: string, tail = 200) => request<LogsData>(`/api/v1/instances/${encodeURIComponent(id)}/logs?tail=${tail}`),
  getLogSources: () => request<LogSourceData>('/api/v1/logs'),
  getLog: (id: string, before?: number, limit = 500) => {
    const query = new URLSearchParams({ limit: String(limit) })
    if (before !== undefined) query.set('before', String(before))
    return request<LogData>(`/api/v1/logs/${encodeURIComponent(id)}?${query.toString()}`)
  },
  deleteLog: (id: string) => request<{ id: string; deleted_files: number }>(`/api/v1/logs/${encodeURIComponent(id)}`, { method: 'DELETE' }),
  downloadLog: async (id: string, latest = false): Promise<Blob> => {
    const response = await fetch(`${apiBase}/api/v1/logs/${encodeURIComponent(id)}/download${latest ? '?latest=true' : ''}`)
    if (!response.ok) throw await readError(response)
    return response.blob()
  },
  getTokenStats: (filters: { profile_name?: string; from?: string; to?: string } = {}) => {
    const query = new URLSearchParams()
    if (filters.profile_name) query.set('profile_name', filters.profile_name)
    if (filters.from) query.set('from', filters.from)
    if (filters.to) query.set('to', filters.to)
    const suffix = query.toString() ? `?${query.toString()}` : ''
    return request<TokenStatsData>(`/api/v1/token-stats${suffix}`)
  },
  getStatistics: (filters: { period?: 'week' | 'month' | 'quarter' | 'half_year' | 'year'; model_name?: string; profile_name?: string } = {}) => {
    const query = new URLSearchParams({ period: filters.period ?? 'month' })
    if (filters.model_name) query.set('model_name', filters.model_name)
    if (filters.profile_name) query.set('profile_name', filters.profile_name)
    return request<StatisticsData>(`/api/v1/statistics?${query}`)
  },
  getOpenAiModels: async (): Promise<OpenAiModelsData> => {
    const response = await openAiRequest<{ data: OpenAiModel[] }>('/models')
    return { items: response.data.filter((item) => item.capabilities?.image_generation !== true) }
  },
  completeChat: (body: { model: string; messages: Array<{ role: 'user' | 'assistant'; content: string }>; stream?: boolean }) => openAiRequest<import('@/types/api').ChatCompletionResponse>('/chat/completions', { method: 'POST', body: JSON.stringify(body) }),
  streamChat,
}

export const queryKeys = {
  system: ['system'] as const,
  status: ['status'] as const,
  health: ['health'] as const,
  settings: ['settings'] as const,
  engines: ['engines'] as const,
  installations: ['installations'] as const,
  models: ['models'] as const,
  localModels: ['local-models'] as const,
  profiles: ['profiles'] as const,
  profileSummary: ['profile-summary'] as const,
  proxyRoutes: ['proxy-routes'] as const,
  instances: ['instances'] as const,
  tokens: ['token-stats'] as const,
  statistics: (filters: { period?: 'week' | 'month' | 'quarter' | 'half_year' | 'year'; model_name?: string; profile_name?: string } = {}) => ['statistics', filters] as const,
  openAiModels: ['openai-models'] as const,
  logs: ['logs'] as const,
  log: (id: string) => ['log', id] as const,
  operation: (id: string) => ['operation', id] as const,
  downloads: ['operations', 'download'] as const,
  hfSearch: (query: string) => ['hf-search', query] as const,
  hfTrending: (group: string, limit = 50) => ['hf-trending', group, limit] as const,
  hfRepo: (repoId: string, revision?: string) => ['hf-repo', repoId, revision ?? ''] as const,
  hfQuantizations: (repoId: string) => ['hf-quantizations', repoId] as const,
}
