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

export type ChatAttachmentKind = 'image' | 'document' | 'text' | 'binary'

const IMAGE_EXTENSIONS = new Set(['avif', 'bmp', 'gif', 'jpeg', 'jpg', 'png', 'webp'])
const IMAGE_MIME_BY_EXTENSION: Record<string, string> = {
  avif: 'image/avif',
  bmp: 'image/bmp',
  gif: 'image/gif',
  jpeg: 'image/jpeg',
  jpg: 'image/jpeg',
  png: 'image/png',
  webp: 'image/webp',
}
const TEXT_EXTENSIONS = new Set([
  'cfg', 'conf', 'css', 'csv', 'env', 'html', 'htm', 'ini', 'java', 'js', 'json',
  'jsx', 'log', 'md', 'markdown', 'py', 'rs', 'sh', 'sql', 'toml', 'ts', 'tsx',
  'txt', 'tsv', 'xml', 'yaml', 'yml',
])
const DOCUMENT_EXTENSIONS = new Set(['doc', 'docx', 'odt', 'pdf', 'ppt', 'pptx', 'rtf', 'xls', 'xlsx'])
const DOCUMENT_MIME_TYPES = new Set([
  'application/msword', 'application/pdf', 'application/rtf', 'application/vnd.ms-excel',
  'application/vnd.ms-powerpoint', 'application/vnd.openxmlformats-officedocument.presentationml.presentation',
  'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
  'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
  'application/vnd.oasis.opendocument.text', 'application/vnd.oasis.opendocument.spreadsheet',
  'application/vnd.oasis.opendocument.presentation',
])

function attachmentFile(attachment: ChatAttachment | File): File {
  return 'file' in attachment ? attachment.file : attachment
}

function fileExtension(file: File): string {
  const name = file.name.replace(/\\/g, '/').split('/').pop() ?? file.name
  const dot = name.lastIndexOf('.')
  return dot < 0 ? '' : name.slice(dot + 1).toLowerCase()
}

/** Classify an attachment from MIME, with a known filename extension fallback for generic MIME. */
export function classifyAttachment(attachment: ChatAttachment | File): ChatAttachmentKind {
  const file = attachmentFile(attachment)
  const mime = file.type.toLowerCase().split(';', 1)[0]
  const extension = fileExtension(file)
  if (mime.startsWith('image/') || (!mime || mime === 'application/octet-stream' || mime === 'binary/octet-stream') && IMAGE_EXTENSIONS.has(extension)) return 'image'
  if (DOCUMENT_MIME_TYPES.has(mime) || DOCUMENT_EXTENSIONS.has(extension)) return 'document'
  if (mime.startsWith('text/') || [
    'application/json', 'application/javascript', 'application/ld+json', 'application/xml',
  ].includes(mime) || (!mime || mime === 'application/octet-stream' || mime === 'binary/octet-stream') && TEXT_EXTENSIONS.has(extension)) return 'text'
  return 'binary'
}

export function classifyAttachments(attachments: ChatAttachment[]): Record<ChatAttachmentKind, ChatAttachment[]> {
  const result: Record<ChatAttachmentKind, ChatAttachment[]> = {
    image: [], document: [], text: [], binary: [],
  }
  for (const attachment of attachments) result[classifyAttachment(attachment)].push(attachment)
  return result
}

export function isImageAttachment(attachment: ChatAttachment | File): boolean {
  return classifyAttachment(attachment) === 'image'
}

export function isPdfAttachment(attachment: ChatAttachment | File): boolean {
  const file = attachmentFile(attachment)
  return file.type.toLowerCase().split(';', 1)[0] === 'application/pdf' || fileExtension(file) === 'pdf'
}

export function isDocumentAttachment(attachment: ChatAttachment | File): boolean {
  return classifyAttachment(attachment) === 'document'
}

export function isPlainTextAttachment(attachment: ChatAttachment | File): boolean {
  return classifyAttachment(attachment) === 'text'
}

const CHAT_MODEL_TYPES = new Set(['text', 'image', 'video', 'ocr'])
export type ChatModelType = 'text' | 'image' | 'video' | 'ocr'

/** Pick the first chat-compatible output type from declared model metadata. */
export function chatModelType(model: OpenAiModel | undefined): ChatModelType | null {
  if (model?.capabilities?.document_adapter === 'unlimited_ocr_2') return 'ocr'
  if (!model?.types?.length) return 'text'
  const declared = new Set(model?.types ?? [])
  if (declared.has('text')) return 'text'
  if (declared.has('image')) return 'image'
  if (declared.has('video')) return 'video'
  if (declared.has('ocr')) return 'ocr'
  return null
}

function modelCapabilitiesOperations(model: OpenAiModel | undefined): string[] {
  const operations = model?.capabilities?.operations
  return Array.isArray(operations)
    ? operations.filter((operation): operation is string => typeof operation === 'string')
    : []
}

/** Resolve a chat operation from the model's advertised type and attachments only. */
export function resolveChatOperation(model: OpenAiModel | undefined, attachments: ChatAttachment[], prompt = ''): ChatOperation {
  const images = attachments.filter((attachment) => isImageAttachment(attachment))
  const nonImages = attachments.filter((attachment) => !isImageAttachment(attachment))
  const modelTypes = [...new Set(model?.types ?? [])]
  const modelType = chatModelType(model)

  if (modelTypes.length > 0 && !modelType) {
    const unsupported = modelTypes.filter((type) => !CHAT_MODEL_TYPES.has(type))
    throw new ChatMediaError(unsupported.length > 0
      ? 'Выбранная модель не поддерживает операции, доступные во встроенном чате.'
      : 'Не удалось определить операцию выбранной модели для этого чата.')
  }

  if (!modelType || modelType === 'text') return images.length > 0 || attachments.some(isPdfAttachment) ? 'vision' : 'text'
  if (modelType === 'image') {
    if (nonImages.length > 0) throw new ChatMediaError(nonImages.some(isDocumentAttachment) ? 'Для этого вложения выберите модель для документов.' : 'К этому запросу можно прикрепить только изображения.')
    if (images.length === 0) return 'image-generation'
    if (!prompt.trim()) {
      if (modelCapabilitiesOperations(model).includes('variations')) return 'image-variations'
      throw new ChatMediaError('Добавьте описание изменений, чтобы обработать изображение.')
    }
    return 'image-edit'
  }
  if (modelType === 'video') {
    if (nonImages.length > 0) throw new ChatMediaError('Для создания видео можно прикрепить исходное изображение.')
    return 'video'
  }
  return 'ocr'
}

export type PreparedUserContent = string | VisionContentPart[]

async function readTextFile(file: File): Promise<string> {
  const text = (file as File & { text?: () => Promise<string> }).text
  if (typeof text === 'function') return text.call(file)
  return await new Promise<string>((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => resolve(String(reader.result ?? ''))
    reader.onerror = () => reject(reader.error ?? new Error('Не удалось прочитать файл'))
    reader.readAsText(file)
  })
}

/** Prepare text/vision content without silently dropping files unsupported by a text model. */
export async function prepareUserContent(prompt: string, attachments: ChatAttachment[]): Promise<PreparedUserContent> {
  const parts: VisionContentPart[] = []
  if (prompt.trim()) parts.push({ type: 'text', text: prompt })

  for (const attachment of attachments) {
    const file = attachment.file
    const kind = classifyAttachment(file)
    if (kind === 'image') {
      parts.push({ type: 'image_url', image_url: { url: await fileToDataUrl(file) } })
      continue
    }
    if (isPdfAttachment(file)) {
      if (file.size > 25 * 1024 * 1024) throw new ChatMediaError('Максимальный размер PDF — 25 МБ')
      parts.push({ type: 'file', file: { filename: file.name, file_data: await fileToDataUrl(file) } })
      continue
    }
    if (kind === 'text') {
      let text: string
      try {
        text = await readTextFile(file)
      } catch {
        throw new ChatMediaError(`Не удалось прочитать файл ${file.name}`)
      }
      parts.push({ type: 'text', text: `[Файл: ${file.name}]\n${text}` })
      continue
    }
    if (kind === 'document') {
      throw new ChatMediaError(`Файл ${file.name} — документ; выберите модель для документов.`)
    }
    throw new ChatMediaError(`Файл ${file.name} нельзя отправить в этом чате.`)
  }

  if (parts.length === 0) return ''
  if (parts.length === 1 && parts[0].type === 'text') return parts[0].text
  return parts
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
  | { type: 'file'; file: { filename: string; file_data: string } }

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
  try {
    const response = await fetch(`${mediaApiBase}${path}`, { ...init, headers, signal: signal ?? init?.signal })
    if (!response.ok) throw new ChatMediaError(await errorMessage(response), response.status)
    return await response.json() as T
  } catch (error) {
    if (error instanceof TypeError) throw new ChatMediaError('Связь с сервером прервана')
    throw error
  }
}

export async function mediaBlob(path: string, signal?: AbortSignal): Promise<Blob> {
  try {
    const response = await fetch(`${mediaApiBase}${path}`, { signal })
    if (!response.ok) throw new ChatMediaError(await errorMessage(response), response.status)
    return await response.blob()
  } catch (error) {
    if (error instanceof TypeError) throw new ChatMediaError('Связь с сервером прервана')
    throw error
  }
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

export async function createImageGeneration(body: { model: string; prompt: string; size?: string; background?: 'transparent' | 'opaque' }, signal?: AbortSignal): Promise<ImageResult> {
  const response = responseData(await mediaJson<{ data?: ImageAsset[] } | { data: ImageAsset[] }>('/images/generations', { method: 'POST', body: JSON.stringify(body) }, signal))
  const assets = Array.isArray(response) ? response : (response as { data?: ImageAsset[] }).data ?? []
  return { kind: 'image', assets }
}

export async function createImageMultipart(path: '/images/edits' | '/images/variations', fields: { model: string; prompt?: string; image: File; mask?: File; size?: string; background?: 'transparent' | 'opaque' }, signal?: AbortSignal): Promise<ImageResult> {
  const form = new FormData()
  form.append('model', fields.model)
  if (fields.size) form.append('size', fields.size)
  if (fields.background) form.append('background', fields.background)
  if (path === '/images/edits') form.append('prompt', fields.prompt ?? '')
  form.append('image', fields.image, fields.image.name)
  if (fields.mask) form.append('mask', fields.mask, fields.mask.name)
  const response = responseData(await mediaJson<{ data?: ImageAsset[] } | { data: ImageAsset[] }>(path, { method: 'POST', body: form }, signal))
  const assets = Array.isArray(response) ? response : (response as { data?: ImageAsset[] }).data ?? []
  return { kind: 'image', assets }
}

export type VideoStatus = {
  id?: string
  model?: string
  created_at?: number
  status?: string
  url?: string | null
  content_url?: string | null
  download_url?: string | null
  error?: string | { code?: string; message: string } | null
}

export async function createVideo(fields: { model: string; prompt: string; inputReference?: File; size?: string; seconds?: number }, signal?: AbortSignal): Promise<VideoStatus> {
  const form = new FormData()
  form.append('model', fields.model)
  form.append('prompt', fields.prompt)
  if (fields.size) form.append('size', fields.size)
  if (fields.seconds !== undefined) form.append('seconds', String(fields.seconds))
  if (fields.inputReference) form.append('input_reference', fields.inputReference, fields.inputReference.name)
  return responseData(await mediaJson<VideoStatus | { data: VideoStatus }>('/videos', { method: 'POST', body: form }, signal))
}

export async function getVideo(id: string, signal?: AbortSignal): Promise<VideoStatus> {
  return responseData(await mediaJson<VideoStatus | { data: VideoStatus }>(`/videos/${encodeURIComponent(id)}`, undefined, signal))
}

function videoDelay(ms: number, signal?: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    if (signal?.aborted) { reject(new DOMException('Aborted', 'AbortError')); return }
    const abort = () => { window.clearTimeout(timer); signal?.removeEventListener('abort', abort); reject(new DOMException('Aborted', 'AbortError')) }
    const timer = window.setTimeout(() => { signal?.removeEventListener('abort', abort); resolve() }, ms)
    signal?.addEventListener('abort', abort, { once: true })
  })
}

/** Retry safe reads of an existing job, never POST /videos. No generation deadline. */
async function retryVideoRead<T>(read: (signal: AbortSignal) => Promise<T>, signal?: AbortSignal, onConnection?: (reconnecting: boolean) => void, timeoutMs?: number): Promise<T> {
  for (let attempt = 0; ; attempt++) {
    if (signal?.aborted) throw new DOMException('Aborted', 'AbortError')
    const controller = new AbortController()
    const abort = () => controller.abort()
    signal?.addEventListener('abort', abort, { once: true })
    const timer = timeoutMs ? window.setTimeout(abort, timeoutMs) : undefined
    try {
      const result = await read(controller.signal)
      onConnection?.(false)
      return result
    } catch (error) {
      if (signal?.aborted) throw new DOMException('Aborted', 'AbortError')
      const retryable = controller.signal.aborted
        || (error instanceof ChatMediaError && [0, 408, 429, 500, 502, 503, 504].includes(error.status))
      if (!retryable) throw error
    } finally {
      if (timer !== undefined) window.clearTimeout(timer)
      signal?.removeEventListener('abort', abort)
    }
    onConnection?.(true)
    await videoDelay(Math.min(15_000, 1000 * 2 ** Math.min(attempt, 4)), signal)
  }
}

export async function waitForVideo(id: string, signal: AbortSignal, onStatus?: (status: VideoStatus) => void, onConnection?: (reconnecting: boolean) => void): Promise<VideoStatus> {
  let status = await retryVideoRead((requestSignal) => getVideo(id, requestSignal), signal, onConnection, 20_000)
  onStatus?.(status)
  while (!isVideoDone(status)) {
    if (isVideoFailed(status)) throw new ChatMediaError(videoError(status))
    await videoDelay(2000, signal)
    status = await retryVideoRead((requestSignal) => getVideo(id, requestSignal), signal, onConnection, 20_000)
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

export async function resolveVideoResult(status: VideoStatus, signal?: AbortSignal, onConnection?: (reconnecting: boolean) => void): Promise<VideoResult> {
  const url = status.url ?? status.content_url
  if (url) return { kind: 'video', id: status.id, url, downloadUrl: status.download_url ?? url }
  if (!status.id) throw new ChatMediaError('Ответ видео не содержит идентификатор результата')
  const blob = await retryVideoRead((requestSignal) => mediaBlob(`/videos/${encodeURIComponent(status.id!)}/content`, requestSignal), signal, onConnection)
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
  const rawMime = file.type.toLowerCase().split(';', 1)[0]
  const genericMime = !rawMime || rawMime === 'application/octet-stream' || rawMime === 'binary/octet-stream'
  const extension = fileExtension(file)
  const fallbackMime = IMAGE_MIME_BY_EXTENSION[extension] ?? (extension === 'pdf' ? 'application/pdf' : undefined)
  const mime = genericMime ? fallbackMime ?? rawMime : rawMime
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => {
      const source = String(reader.result)
      if (!mime || !fallbackMime || !genericMime) {
        resolve(source)
        return
      }
      const separator = source.indexOf(',')
      resolve(separator < 0 ? source : `data:${mime};base64,${source.slice(separator + 1)}`)
    }
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
