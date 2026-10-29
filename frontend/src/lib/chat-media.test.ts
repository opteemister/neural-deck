import { afterEach, describe, expect, it, vi } from 'vitest'
import {
  classifyAttachment,
  classifyAttachments,
  createImageMultipart,
  createOcr,
  createVideo,
  downloadAsset,
  chatModelType,
  fileToDataUrl,
  imageAssetSource,
  prepareUserContent,
  resolveVideoResult,
  resolveChatOperation,
  waitForVideo,
} from '@/lib/chat-media'
import type { OpenAiModel } from '@/types/api'

afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); vi.useRealTimers() })

function stubObjectUrls(value: string) {
  Object.defineProperty(URL, 'createObjectURL', { configurable: true, value: vi.fn(() => value) })
  Object.defineProperty(URL, 'revokeObjectURL', { configurable: true, value: vi.fn() })
}

function attachment(name: string, type = '', contents = 'body') {
  return { id: name, file: new File([contents], name, { type }), previewUrl: '' }
}

function model(types?: OpenAiModel['types'], capabilities?: OpenAiModel['capabilities']): OpenAiModel {
  return { id: 'model', object: 'model', types, capabilities }
}

describe('chat media client', () => {
  it('recovers status reads after a network failure and a server restart without a new POST', async () => {
    vi.useFakeTimers()
    const fetchMock = vi.fn().mockRejectedValueOnce(new TypeError('Failed to fetch'))
      .mockResolvedValueOnce(new Response('', { status: 503 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ id: 'video-1', status: 'completed' })))
    vi.stubGlobal('fetch', fetchMock)
    const connection = vi.fn()
    const result = waitForVideo('video-1', new AbortController().signal, undefined, connection)
    await vi.advanceTimersByTimeAsync(3000)
    await expect(result).resolves.toMatchObject({ id: 'video-1', status: 'completed' })
    expect(connection).toHaveBeenCalledWith(true)
    expect(connection).toHaveBeenLastCalledWith(false)
    expect(fetchMock.mock.calls.every(([url, init]) => url === '/api/proxy/videos/video-1' && !init?.method)).toBe(true)
  })

  it('keeps waiting beyond thirty minutes for the same video', async () => {
    vi.useFakeTimers()
    const fetchMock = vi.fn().mockImplementation(async () => new Response(JSON.stringify({ id: 'long-video', status: 'in_progress' })))
    vi.stubGlobal('fetch', fetchMock)
    const result = waitForVideo('long-video', new AbortController().signal)
    await vi.advanceTimersByTimeAsync(31 * 60 * 1000)
    fetchMock.mockImplementation(async () => new Response(JSON.stringify({ id: 'long-video', status: 'completed' })))
    await vi.advanceTimersByTimeAsync(2000)
    await expect(result).resolves.toMatchObject({ status: 'completed' })
  })

  it('retries a status request that hangs without limiting overall video duration', async () => {
    vi.useFakeTimers()
    const fetchMock = vi.fn().mockImplementationOnce((_url, init) => new Promise((_resolve, reject) => {
      init.signal.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')), { once: true })
    })).mockResolvedValueOnce(new Response(JSON.stringify({ id: 'video-1', status: 'completed' })))
    vi.stubGlobal('fetch', fetchMock)
    const result = waitForVideo('video-1', new AbortController().signal)
    await vi.advanceTimersByTimeAsync(21_000)
    await expect(result).resolves.toMatchObject({ status: 'completed' })
    expect(fetchMock).toHaveBeenCalledTimes(2)
  })

  it('allows stopping reconnection and does not retry permanent HTTP errors', async () => {
    vi.useFakeTimers()
    const fetchMock = vi.fn().mockRejectedValue(new TypeError('Failed to fetch'))
    vi.stubGlobal('fetch', fetchMock)
    const controller = new AbortController()
    const result = waitForVideo('video-1', controller.signal).catch((error) => error)
    await vi.advanceTimersByTimeAsync(0)
    controller.abort()
    expect(await result).toMatchObject({ name: 'AbortError' })
    expect(fetchMock).toHaveBeenCalledOnce()
    fetchMock.mockResolvedValueOnce(new Response(JSON.stringify({ error: { message: 'Video missing' } }), { status: 404 }))
    await expect(waitForVideo('missing', new AbortController().signal)).rejects.toThrow('Video missing')
    expect(fetchMock).toHaveBeenCalledTimes(2)
  })

  it('retries downloading the existing video after a connection failure', async () => {
    vi.useFakeTimers()
    stubObjectUrls('blob:recovered')
    const fetchMock = vi.fn().mockRejectedValueOnce(new TypeError('Failed to fetch'))
      .mockResolvedValueOnce(new Response(new Blob(['video'], { type: 'video/mp4' })))
    vi.stubGlobal('fetch', fetchMock)
    const result = resolveVideoResult({ id: 'video-1', status: 'completed' }, new AbortController().signal)
    await vi.advanceTimersByTimeAsync(1000)
    await expect(result).resolves.toMatchObject({ url: 'blob:recovered' })
    expect(fetchMock.mock.calls.every(([url]) => url === '/api/proxy/videos/video-1/content')).toBe(true)
  })

  it('sends standard image variations without a spurious empty prompt', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ data: [{ b64_json: 'abc' }] })))
    vi.stubGlobal('fetch', fetchMock)
    await createImageMultipart('/images/variations', { model: 'image', image: new File(['image'], 'image.png', { type: 'image/png' }) })
    expect((fetchMock.mock.calls[0][1].body as FormData).has('prompt')).toBe(false)
  })

  it('uses the OCR image reference contract for scans', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ pages: [{ markdown: 'scan' }] })))
    vi.stubGlobal('fetch', fetchMock)
    await createOcr({ model: 'ocr', documentUrl: 'data:image/png;base64,abc' })
    expect(JSON.parse(fetchMock.mock.calls[0][1].body).document).toEqual({ type: 'image_url', image_url: 'data:image/png;base64,abc' })
  })
  it('surfaces standard video errors without continuing polling', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ id: 'video-1', status: 'failed', error: { code: 'generation_error', message: 'Workflow failed' } })))
    vi.stubGlobal('fetch', fetchMock)
    await expect(waitForVideo('video-1', new AbortController().signal)).rejects.toThrow('Workflow failed')
    expect(fetchMock).toHaveBeenCalledOnce()
  })

  it('stops video polling locally when aborted without cancelling the ComfyUI queue', async () => {
    const fetchMock = vi.fn().mockImplementation(() => Promise.resolve(new Response(JSON.stringify({ id: 'video-1', status: 'queued' }))))
    vi.stubGlobal('fetch', fetchMock)
    const controller = new AbortController()
    const promise = waitForVideo('video-1', controller.signal, () => controller.abort())
    await expect(promise).rejects.toMatchObject({ name: 'AbortError' })
    expect(fetchMock).toHaveBeenCalledOnce()
  })
  it('sends image edits as multipart with source image and optional mask', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ data: [{ b64_json: 'abc' }] }), { status: 200 }))
    vi.stubGlobal('fetch', fetchMock)
    const image = new File(['image'], 'source.png', { type: 'image/png' })
    const mask = new File(['mask'], 'mask.png', { type: 'image/png' })
    const result = await createImageMultipart('/images/edits', { model: 'image-alias', prompt: 'сделай теплее', image, mask })
    expect(result.assets).toEqual([{ b64_json: 'abc' }])
    expect(fetchMock).toHaveBeenCalledWith('/api/proxy/images/edits', expect.objectContaining({ method: 'POST' }))
    const body = fetchMock.mock.calls[0][1].body as FormData
    expect(body.get('model')).toBe('image-alias')
    expect(body.get('prompt')).toBe('сделай теплее')
    expect((body.get('image') as File).name).toBe('source.png')
    expect((body.get('mask') as File).name).toBe('mask.png')
  })

  it('posts OCR data URLs and extracts page markdown', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ pages: [{ markdown: '# Заголовок' }, { markdown: 'Текст' }] }), { status: 200 }))
    vi.stubGlobal('fetch', fetchMock)
    const result = await createOcr({ model: 'ocr-alias', documentUrl: 'data:application/pdf;base64,Zm9v' })
    expect(result.markdown).toBe('# Заголовок\n\nТекст')
    expect(fetchMock).toHaveBeenCalledWith('/api/proxy/ocr', expect.objectContaining({ method: 'POST' }))
    expect(JSON.parse(fetchMock.mock.calls[0][1].body as string)).toEqual({ model: 'ocr-alias', document: { type: 'document_url', document_url: 'data:application/pdf;base64,Zm9v' } })
  })

  it('supports video create, content download and b64 image assets', async () => {
    const createFetch = vi.fn().mockResolvedValue(new Response(JSON.stringify({ id: 'video-1', status: 'queued' }), { status: 200 }))
    vi.stubGlobal('fetch', createFetch)
    const video = await createVideo({ model: 'video-alias', prompt: 'плавное движение' })
    expect(video).toEqual({ id: 'video-1', status: 'queued' })
    expect(createFetch).toHaveBeenCalledWith('/api/proxy/videos', expect.objectContaining({ method: 'POST' }))
    const contentFetch = vi.fn().mockResolvedValue(new Response(new Blob(['video'], { type: 'video/mp4' }), { status: 200 }))
    vi.stubGlobal('fetch', contentFetch)
    stubObjectUrls('blob:video')
    const result = await resolveVideoResult({ id: 'video-1', status: 'completed' })
    expect(result).toMatchObject({ kind: 'video', id: 'video-1', url: 'blob:video' })
    expect(contentFetch).toHaveBeenCalledWith('/api/proxy/videos/video-1/content', expect.anything())
    expect(imageAssetSource({ b64_json: 'abc', mime_type: 'image/webp' })).toBe('data:image/webp;base64,abc')
  })

  it('downloads a base64 image without starting another generation', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(new Blob(['image'], { type: 'image/png' }), { status: 200 }))
    vi.stubGlobal('fetch', fetchMock)
    stubObjectUrls('blob:download')
    const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => undefined)
    await downloadAsset({ b64_json: 'abc', mime_type: 'image/png' }, 'result.png')
    expect(fetchMock).toHaveBeenCalledWith('data:image/png;base64,abc')
    expect(click).toHaveBeenCalledOnce()
  })

  it('resolves text and vision from model type and image attachments', () => {
    expect(resolveChatOperation(model(['text']), [])).toBe('text')
    expect(resolveChatOperation(model(), [attachment('photo.png', 'image/png')])).toBe('vision')
    expect(resolveChatOperation(model(['text']), [attachment('photo.png', 'image/png')])).toBe('vision')
    expect(chatModelType(model(['text', 'embeddings']))).toBe('text')
    expect(chatModelType({ id: 'legacy-text', object: 'model' })).toBe('text')
    expect(chatModelType(model(['image', 'video']))).toBe('image')
    expect(chatModelType(model(['embeddings']))).toBeNull()
  })

  it('resolves image generation, edits and capability-gated variations', () => {
    const image = attachment('source.png', 'image/png')
    expect(resolveChatOperation(model(['image']), [], '')).toBe('image-generation')
    expect(resolveChatOperation(model(['image']), [image], 'make it warm')).toBe('image-edit')
    expect(resolveChatOperation(model(['image'], { operations: ['variations'] }), [image], '  ')).toBe('image-variations')
    expect(() => resolveChatOperation(model(['image']), [image], '')).toThrow('Добавьте описание изменений')
  })

  it('rejects unknown-only model types instead of guessing an API', () => {
    expect(resolveChatOperation(model(['text', 'image']), [])).toBe('text')
    expect(() => resolveChatOperation(model(['embeddings']), [])).toThrow(/не поддерживает операции/i)
    expect(resolveChatOperation(model(['video']), [attachment('reference.jpg', 'image/jpeg')])).toBe('video')
    expect(resolveChatOperation(model(['ocr']), [attachment('scan.pdf', 'application/pdf')])).toBe('ocr')
  })

  it('classifies MIME and extension fallbacks without dropping binary files', () => {
    expect(classifyAttachment(attachment('photo.JPG'))).toBe('image')
    expect(classifyAttachment(attachment('notes.LOG', ''))).toBe('text')
    expect(classifyAttachment(attachment('document.PDF', 'application/octet-stream'))).toBe('document')
    expect(classifyAttachment(attachment('archive.zip', 'application/octet-stream'))).toBe('binary')
    expect(classifyAttachments([attachment('photo.png', 'image/png'), attachment('notes.md', 'text/markdown')])).toMatchObject({ image: [expect.anything()], text: [expect.anything()] })
  })

  it('prepares text files and images as standard user content parts', async () => {
    const notes = attachment('notes.txt', 'text/plain', 'hello')
    const result = await prepareUserContent('Опиши вложения', [notes, attachment('photo.png', 'image/png', 'image')])
    expect(result).toEqual([
      { type: 'text', text: 'Опиши вложения' },
      { type: 'text', text: '[Файл: notes.txt]\nhello' },
      { type: 'image_url', image_url: { url: expect.stringMatching(/^data:image\/png;base64,/) } },
    ])
  })

  it('keeps vision image data URLs valid when the browser supplies no MIME type', async () => {
    const result = await prepareUserContent('Опиши изображение', [attachment('photo.jpeg', '', 'image')])
    expect(result).toEqual([
      { type: 'text', text: 'Опиши изображение' },
      { type: 'image_url', image_url: { url: expect.stringMatching(/^data:image\/jpeg;base64,/) } },
    ])
  })

  it('uses the known PDF extension for document data URLs when MIME is empty', async () => {
    await expect(fileToDataUrl(new File(['pdf'], 'scan.pdf'))).resolves.toMatch(/^data:application\/pdf;base64,/)
  })

  it('prepares PDFs for server rendering without requiring an OCR model', async () => {
    expect(await prepareUserContent('Прочитай', [attachment('scan.pdf', '', '%PDF-1.4')])).toEqual([
      { type: 'text', text: 'Прочитай' },
      { type: 'file', file: { filename: 'scan.pdf', file_data: expect.stringMatching(/^data:application\/pdf;base64,/) } },
    ])
    expect(resolveChatOperation({ id: 'uocr', object: 'model', types: ['text'], capabilities: { document_adapter: 'unlimited_ocr_2' } }, [])).toBe('ocr')
  })

  it('explains that documents need a document-capable model and keeps binary errors visible', async () => {
    await expect(prepareUserContent('', [attachment('scan.docx', 'application/vnd.openxmlformats-officedocument.wordprocessingml.document')])).rejects.toThrow('выберите модель для документов')
    await expect(prepareUserContent('', [attachment('archive.zip', 'application/zip')])).rejects.toThrow('нельзя отправить')
  })
})
