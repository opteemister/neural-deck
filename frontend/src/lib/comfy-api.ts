export type ComfySource = {
  base_url: string
  user_context?: string
}

export type ComfyOperation = 'generate' | 'edit' | 'edit_masked' | 'variations'
export type ComfyMediaType = 'image' | 'video'
export type ComfyModel = {
  max_megapixels?: number
  max_resolution?: number
  max_seconds?: number
  source: string
  type: ComfyMediaType
  workflows: Partial<Record<ComfyOperation, string>> & Record<string, unknown>
  [key: string]: unknown
}
export type ComfyConfig = {
  sources: Record<string, ComfySource>
  models: Record<string, ComfyModel>
}

export type ComfySourceInput = { name: string; base_url: string }
export type MediaAliasInput = { name: string; type: ComfyMediaType; source: string; workflows: Partial<Record<ComfyOperation, string>>; max_megapixels?: number; max_resolution?: number; max_seconds?: number }

export type WorkflowItem = string | { path?: string; name?: string; [key: string]: unknown }
export type WorkflowListing = {
  items: WorkflowItem[]
  available: boolean
  cached: boolean
  updated_at: number | null
}

export const comfyQueryKey = ['proxy-comfyui'] as const

function errorMessage(body: unknown, status: number): string {
  if (body && typeof body === 'object') {
    const error = (body as { error?: { message?: unknown } }).error
    if (error && typeof error.message === 'string') return error.message
    const detail = (body as { detail?: unknown }).detail
    if (typeof detail === 'string') return detail
  }
  return `Запрос не выполнен (${status})`
}

async function comfyRequest<T>(path: string, init?: RequestInit): Promise<T> {
  const headers = new Headers(init?.headers)
  if (init?.body && !headers.has('Content-Type')) headers.set('Content-Type', 'application/json')
  const response = await fetch(path, { ...init, headers })
  let body: unknown = null
  try {
    body = await response.json()
  } catch {
    // The status still gives a useful error for empty/non-JSON responses.
  }
  if (!response.ok) throw new Error(errorMessage(body, response.status))
  if (!body || typeof body !== 'object' || !('data' in body)) throw new Error('Ответ API имеет неверный формат')
  return (body as { data: T }).data
}

export const comfyApi = {
  saveSource: (source: ComfySourceInput, previousName?: string) => comfyRequest<ComfyConfig>(`/api/v1/proxy/comfyui/sources${previousName ? `/${encodeURIComponent(previousName)}` : ''}`, { method: previousName ? 'PATCH' : 'POST', body: JSON.stringify(source) }),
  deleteSource: (name: string) => comfyRequest<ComfyConfig>(`/api/v1/proxy/comfyui/sources/${encodeURIComponent(name)}`, { method: 'DELETE' }),
  saveAlias: (alias: MediaAliasInput, previousName?: string) => comfyRequest<ComfyConfig>(`/api/v1/proxy/comfyui/aliases${previousName ? `/${encodeURIComponent(previousName)}` : ''}`, { method: previousName ? 'PATCH' : 'POST', body: JSON.stringify(alias) }),
  deleteAlias: (name: string) => comfyRequest<ComfyConfig>(`/api/v1/proxy/comfyui/aliases/${encodeURIComponent(name)}`, { method: 'DELETE' }),
  validateWorkflow: (source: string, path: string) => comfyRequest<{ path: string; usable: boolean | null; message?: string }>(`/api/v1/proxy/comfyui/sources/${encodeURIComponent(source)}/workflows/validate?${new URLSearchParams({ path })}`),
  getStatus: (source: string) => comfyRequest<{ available: boolean }>(`/api/v1/proxy/comfyui/sources/${encodeURIComponent(source)}/status`),
  getConfig: () => comfyRequest<ComfyConfig>('/api/v1/proxy/comfyui'),
  putConfig: (config: ComfyConfig) => comfyRequest<ComfyConfig>('/api/v1/proxy/comfyui', {
    method: 'PUT', body: JSON.stringify(config),
  }),
  getWorkflows: (source: string) => comfyRequest<WorkflowListing>(`/api/v1/proxy/comfyui/sources/${encodeURIComponent(source)}/workflows`),
}
