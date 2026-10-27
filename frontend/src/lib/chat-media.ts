import type { ChatTimings, ChatUsage, OpenAiModel, OpenAiModelsData } from '@/types/api'

/** The media routes intentionally live here rather than in the shared API client.
 * The shared client is also used by pages which must keep the text API contract.
 */
export const mediaApiBase = '/api/proxy'

export type ChatOperation =
  | 'text'
  | 'vision'
  | 'image-generation'
  | 'image-edit'
  | 'image-variations'
  | 'video'
  | 'ocr'

export type ChatAttachment = {
  id: string
  file: File
  previewUrl: string
}

export type ImageAsset = {
  url?: string | null
  b64_json?: string | null
  mime_type?: string | null
  mimeType?: string | null
}

export type ImageResult = {
  kind: 'image'
  assets: ImageAsset[]
}

export type VideoResult = {
  kind: 'video'
  id?: string
  url: string
  downloadUrl: string
}

export type OcrResult = {
  kind: 'ocr'
  markdown: string
}

export type MediaResult = ImageResult | VideoResult | OcrResult

export type MediaStreamChunk = {
  content: string
  reasoning?: string
  usage?: ChatUsage
  timings?: ChatTimings
  finishReason?: string | null
  done: boolean
}

export type VisionContentPart =
  | { type: 'text'; text: string }
  | { type: 'image_url'; image_url: { url: string } }

export type VisionMessage = {
  role: 'user' | 'assistant'
  content: string | VisionContentPart[]
}

export class ChatMediaError extends Error {
  readonly status: number
  constructor(message: string, status = 0) {
    super(message)
    this.name = 'ChatMediaError'
    this.status = status
  }
}

async function errorMessage(response: Response): Promise<string> {
  try {
    const body = await response.json() as { error?: { message?: string } }
    if (body.error?.message) return body.error.message
  } catch {
    // The provider can return an empty or non-JSON body for a failed binary request.
  }
  return `Запрос не выполнен (${response.status})`
}

export async function mediaJson<T>(path: string, init?: RequestInit, signal?: AbortSignal): Promise<T> {
  const headers = new Headers(init?.headers)
  if (init?.body && typeof init.body === 'string' && !headers.has('Content-Type')) headers.set('Content-Type', 'application/json')
  const response = await fetch(`${mediaApiBase}${path}`, { ...init, headers, signal: signal ?? init?.signal })
  if (!response.ok) throw new ChatMediaError(await errorMessage(response), response.status)
  return await response.json() as T
}

export async function mediaBlob(path: string, signal?: AbortSignal): Promise<Blob> {
  const response = await fetch(`${mediaApiBase}${path}`, { signal })
  if (!response.ok) throw new ChatMediaError(await errorMessage(response), response.status)
  return response.blob()
}

/** Models are deliberately requested with all_types so media aliases are not hidden. */
export async function getAllChatModels(signal?: AbortSignal): Promise<OpenAiModelsData> {
  const response = await mediaJson<{ data?: OpenAiModel[]; items?: OpenAiModel[] }>('/models?all_types=true', undefined, signal)
  return { items: response.data ?? response.items ?? [] }
}

function responseData<T extends object>(response: T): T extends { data: infer D } ? D : T {
  if ('data' in response) {
    const data = (response as { data: unknown }).data
    if (data !== null && typeof data === 'object') return data as never
  }
  return response as never
}

export async function createImageGeneration(body: { model: string; prompt: string }, signal?: AbortSignal): Promise<ImageResult> {
  const response = responseData(await mediaJson<{ data?: ImageAsset[] } | { data: ImageAsset[] }>('/images/generations', { method: 'POST', body: JSON.stringify(body) }, signal))
  const assets = Array.isArray(response) ? response : (response as { data?: ImageAsset[] }).data ?? []
  return { kind: 'image', assets }
}

export async function createImageMultipart(path: '/images/edits' | '/images/variations', fields: { model: string; prompt?: string; image: File; mask?: File }, signal?: AbortSignal): Promise<ImageResult> {
  const form = new FormData()
  form.append('model', fields.model)
  if (path === '/images/edits') form.append('prompt', fields.prompt ?? '')
  form.append('image', fields.image, fields.image.name)
  if (fields.mask) form.append('mask', fields.mask, fields.mask.name)
  const response = responseData(await mediaJson<{ data?: ImageAsset[] } | { data: ImageAsset[] }>(path, { method: 'POST', body: form }, signal))
  const assets = Array.isArray(response) ? response : (response as { data?: ImageAsset[] }).data ?? []
  return { kind: 'image', assets }
}

export type VideoStatus = {
  id?: string
  status?: string
  url?: string | null
  content_url?: string | null
  download_url?: string | null
  error?: string | { code?: string; message: string } | null
}

export async function createVideo(fields: { model: string; prompt: string; inputReference?: File }, signal?: AbortSignal): Promise<VideoStatus> {
  const form = new FormData()
  form.append('model', fields.model)
  form.append('prompt', fields.prompt)
  if (fields.inputReference) form.append('input_reference', fields.inputReference, fields.inputReference.name)
  return responseData(await mediaJson<VideoStatus | { data: VideoStatus }>('/videos', { method: 'POST', body: form }, signal))
}

export async function getVideo(id: string, signal?: AbortSignal): Promise<VideoStatus> {
  return responseData(await mediaJson<VideoStatus | { data: VideoStatus }>(`/videos/${encodeURIComponent(id)}`, undefined, signal))
}

export async function waitForVideo(id: string, signal: AbortSignal, onStatus?: (status: VideoStatus) => void): Promise<VideoStatus> {
  let status = await getVideo(id, signal)
  onStatus?.(status)
  while (!isVideoDone(status)) {
    if (isVideoFailed(status)) throw new ChatMediaError(videoError(status))
    await new Promise<void>((resolve, reject) => {
      if (signal.aborted) { reject(new DOMException('Aborted', 'AbortError')); return }
      const abort = () => { window.clearTimeout(timer); reject(new DOMException('Aborted', 'AbortError')) }
      const timer = window.setTimeout(() => { signal.removeEventListener('abort', abort); resolve() }, 900)
      signal.addEventListener('abort', abort, { once: true })
    })
    status = await getVideo(id, signal)
    onStatus?.(status)
  }
  if (isVideoFailed(status)) throw new ChatMediaError(videoError(status))
  return status
}

function isVideoDone(status: VideoStatus): boolean {
  return ['completed', 'complete', 'succeeded', 'success', 'done'].includes((status.status ?? '').toLowerCase())
}

function videoError(status: VideoStatus): string {
  return typeof status.error === 'string' ? status.error : status.error?.message ?? 'Видео не создано'
}

function isVideoFailed(status: VideoStatus): boolean {
  return ['failed', 'error', 'cancelled', 'canceled'].includes((status.status ?? '').toLowerCase())
}

export async function resolveVideoResult(status: VideoStatus, signal?: AbortSignal): Promise<VideoResult> {
  const url = status.url ?? status.content_url
  if (url) return { kind: 'video', id: status.id, url, downloadUrl: status.download_url ?? url }
  if (!status.id) throw new ChatMediaError('Ответ видео не содержит идентификатор результата')
  const blob = await mediaBlob(`/videos/${encodeURIComponent(status.id)}/content`, signal)
  const objectUrl = URL.createObjectURL(blob)
  return { kind: 'video', id: status.id, url: objectUrl, downloadUrl: objectUrl }
}

export async function createOcr(fields: { model: string; documentUrl: string }, signal?: AbortSignal): Promise<OcrResult> {
  const document = fields.documentUrl.startsWith('data:image/')
    ? { type: 'image_url', image_url: fields.documentUrl }
    : { type: 'document_url', document_url: fields.documentUrl }
  const response = responseData(await mediaJson<{ pages?: Array<{ markdown?: string | null }>; data?: { pages?: Array<{ markdown?: string | null }> } }>('/ocr', {
    method: 'POST',
    body: JSON.stringify({ model: fields.model, document }),
  }, signal)) as { pages?: Array<{ markdown?: string | null }> }
  const markdown = (response.pages ?? []).map((page) => page.markdown ?? '').filter(Boolean).join('\n\n')
  return { kind: 'ocr', markdown }
}

export function fileToDataUrl(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => resolve(String(reader.result))
    reader.onerror = () => reject(reader.error ?? new Error('Не удалось прочитать файл'))
    reader.readAsDataURL(file)
  })
}

export function imageAssetSource(asset: ImageAsset): string | undefined {
  if (asset.url) return asset.url
  if (asset.b64_json) return asset.b64_json.startsWith('data:') ? asset.b64_json : `data:${asset.mime_type ?? asset.mimeType ?? 'image/png'};base64,${asset.b64_json}`
  return undefined
}

export async function downloadAsset(asset: ImageAsset | { url: string; downloadUrl?: string }, filename: string): Promise<void> {
  const source = 'b64_json' in asset ? imageAssetSource(asset) : ('downloadUrl' in asset ? asset.downloadUrl ?? asset.url : asset.url)
  if (!source) throw new ChatMediaError('Файл результата недоступен')
  const blob = source.startsWith('data:')
    ? await (await fetch(source)).blob()
    : await (await fetch(source, { credentials: 'same-origin' })).blob()
  const objectUrl = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = objectUrl
  anchor.download = filename
  anchor.rel = 'noopener'
  document.body.append(anchor)
  anchor.click()
  anchor.remove()
  window.setTimeout(() => URL.revokeObjectURL(objectUrl), 0)
}
