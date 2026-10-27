// spec: version-zero
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { api } from '@/lib/api'
import { ChatPage } from '@/pages/chat'
import type { OpenAiModel } from '@/types/api'

const textModel: OpenAiModel = { id: 'text-model', object: 'model', types: ['text'] }
const imageModel: OpenAiModel = { id: 'image-model', object: 'model', types: ['image'] }
const videoModel: OpenAiModel = { id: 'video-model', object: 'model', types: ['video'] }
const ocrModel: OpenAiModel = { id: 'ocr-model', object: 'model', types: ['ocr'] }

afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals() })

function renderChat(items: OpenAiModel[], path = '/chat?operation=vision') {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const getModels = vi.spyOn(api, 'getOpenAiModels').mockResolvedValue({ items })
  const view = render(<QueryClientProvider client={client}><MemoryRouter initialEntries={[path]}><ChatPage /></MemoryRouter></QueryClientProvider>)
  return { client, view, getModels }
}

function stubObjectUrls() {
  Object.defineProperty(URL, 'createObjectURL', { configurable: true, value: vi.fn(() => 'blob:attachment') })
  Object.defineProperty(URL, 'revokeObjectURL', { configurable: true, value: vi.fn() })
}

describe('universal chat composer', () => {
  it('keeps an unavailable chosen model instead of silently sending to another model', async () => {
    const stream = vi.spyOn(api, 'streamChat').mockResolvedValue()
    renderChat([textModel], '/chat?model=missing-image')
    await screen.findByText('недоступна')
    expect(screen.getByRole('button', { name: 'Выбрать модель' })).toHaveTextContent('missing-image')
    fireEvent.change(screen.getByLabelText('Сообщение'), { target: { value: 'Создай картинку' } })
    expect(screen.getByRole('button', { name: 'Отправить' })).toBeDisabled()
    expect(stream).not.toHaveBeenCalled()
  })

  it('keeps the draft and files when the selected model cannot read an attachment', async () => {
    const stream = vi.spyOn(api, 'streamChat').mockResolvedValue()
    stubObjectUrls()
    const { view } = renderChat([textModel])
    await screen.findByText('text-model')
    const file = new File(['pdf'], 'report.pdf', { type: 'application/pdf' })
    fireEvent.change(view.container.querySelector('input[type="file"]')!, { target: { files: [file] } })
    fireEvent.change(screen.getByLabelText('Сообщение'), { target: { value: 'Прочитай документ' } })
    fireEvent.click(screen.getByRole('button', { name: 'Отправить' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('выберите модель для документов')
    expect(screen.getByLabelText('Сообщение')).toHaveValue('Прочитай документ')
    expect(screen.getByText('report.pdf')).toBeInTheDocument()
    expect(stream).not.toHaveBeenCalled()
  })
  it('uses one model picker and routes a selected image model to generation without an operation control', async () => {
    const fetchMock = vi.fn().mockImplementation((input: RequestInfo | URL) => {
      const url = String(input)
      if (url.endsWith('/api/proxy/images/generations')) return new Response(JSON.stringify({ data: [{ b64_json: 'abc' }] }), { status: 200 })
      return new Response(JSON.stringify({ error: { message: 'unexpected' } }), { status: 404 })
    })
    vi.stubGlobal('fetch', fetchMock)
    stubObjectUrls()
    const { getModels } = renderChat([textModel, imageModel])

    await screen.findByText('text-model')
    expect(getModels).toHaveBeenCalledWith(true)
    expect(screen.queryByRole('button', { name: 'Операция чата' })).not.toBeInTheDocument()
    expect(screen.getAllByRole('button', { name: 'Прикрепить файлы' })).toHaveLength(1)

    fireEvent.click(screen.getByRole('button', { name: 'Выбрать модель' }))
    fireEvent.click(screen.getByRole('radio', { name: 'image-model' }))
    await screen.findByText('image-model')
    expect(screen.queryByText('Авто')).not.toBeInTheDocument()

    fireEvent.change(screen.getByLabelText('Сообщение'), { target: { value: 'Закат над морем' } })
    fireEvent.click(screen.getByRole('button', { name: 'Отправить' }))
    await screen.findByAltText('Результат 1')

    const request = fetchMock.mock.calls.find(([input]) => String(input).endsWith('/api/proxy/images/generations'))
    expect(request).toBeDefined()
    expect(JSON.parse(String(request?.[1]?.body))).toEqual({ model: 'image-model', prompt: 'Закат над морем' })
    expect(fetchMock.mock.calls.some(([input]) => String(input).endsWith('/api/proxy/chat/completions'))).toBe(false)
  })

  it('routes an attached image on the same image model to edits instead of text', async () => {
    const fetchMock = vi.fn().mockImplementation((input: RequestInfo | URL) => {
      const url = String(input)
      if (url.endsWith('/api/proxy/images/edits')) return new Response(JSON.stringify({ data: [{ b64_json: 'edited' }] }), { status: 200 })
      return new Response(JSON.stringify({ error: { message: 'unexpected' } }), { status: 404 })
    })
    vi.stubGlobal('fetch', fetchMock)
    stubObjectUrls()
    const { view } = renderChat([imageModel])
    await screen.findByText('image-model')
    const image = new File(['pixels'], 'house.png', { type: 'image/png' })
    fireEvent.change(view.container.querySelector('input[type="file"]')!, { target: { files: [image] } })
    expect(screen.getByText('Изображение')).toBeInTheDocument()
    fireEvent.change(screen.getByLabelText('Сообщение'), { target: { value: 'Сделай небо розовым' } })
    fireEvent.click(screen.getByRole('button', { name: 'Отправить' }))
    await screen.findByAltText('Результат 1')

    const request = fetchMock.mock.calls.find(([input]) => String(input).endsWith('/api/proxy/images/edits'))
    expect(request).toBeDefined()
    const body = request?.[1]?.body as FormData
    expect(body.get('model')).toBe('image-model')
    expect(body.get('prompt')).toBe('Сделай небо розовым')
    expect((body.get('image') as File).name).toBe('house.png')
    expect(body.get('mask')).toBeNull()
    expect(fetchMock.mock.calls.some(([input]) => String(input).endsWith('/api/proxy/chat/completions'))).toBe(false)
  })

  it('keeps one unrestricted multiple file input for images, PDFs, and plain files', async () => {
    const { view } = renderChat([textModel])
    await screen.findByText('text-model')
    const input = view.container.querySelector('input[type="file"]') as HTMLInputElement
    expect(input).toHaveAttribute('multiple')
    expect(input).not.toHaveAttribute('accept')

    stubObjectUrls()
    fireEvent.change(input, { target: { files: [
      new File(['pixels'], 'photo.png', { type: 'image/png' }),
      new File(['report'], 'report.pdf', { type: 'application/pdf' }),
      new File(['notes'], 'notes.txt', { type: 'text/plain' }),
    ] } })
    expect(screen.getByText('photo.png')).toBeInTheDocument()
    expect(screen.getByText('report.pdf')).toBeInTheDocument()
    expect(screen.getByText('notes.txt')).toBeInTheDocument()
  })

  it('routes a document attachment to OCR based on the selected model', async () => {
    const fetchMock = vi.fn().mockImplementation((input: RequestInfo | URL) => {
      const url = String(input)
      if (url.endsWith('/api/proxy/ocr')) return new Response(JSON.stringify({ pages: [{ markdown: '# Распознано' }] }), { status: 200 })
      return new Response(JSON.stringify({ error: { message: 'unexpected' } }), { status: 404 })
    })
    vi.stubGlobal('fetch', fetchMock)
    stubObjectUrls()
    const { view } = renderChat([ocrModel])
    await screen.findByText('ocr-model')
    fireEvent.change(view.container.querySelector('input[type="file"]')!, { target: { files: [new File(['pdf'], 'scan.pdf', { type: 'application/pdf' })] } })
    fireEvent.click(screen.getByRole('button', { name: 'Отправить' }))
    await screen.findByText('Распознано')

    const request = fetchMock.mock.calls.find(([input]) => String(input).endsWith('/api/proxy/ocr'))
    expect(request).toBeDefined()
    expect(JSON.parse(String(request?.[1]?.body))).toMatchObject({ model: 'ocr-model', document: { document_url: expect.stringMatching(/^data:application\/pdf;base64,/) } })
  })

  it('routes an optional image reference to the video create/status API', async () => {
    const fetchMock = vi.fn().mockImplementation((input: RequestInfo | URL) => {
      const url = String(input)
      if (url.endsWith('/api/proxy/videos')) return new Response(JSON.stringify({ data: { id: 'video-1', status: 'queued' } }), { status: 200 })
      if (url.endsWith('/api/proxy/videos/video-1')) return new Response(JSON.stringify({ data: { id: 'video-1', status: 'completed', url: '/media/video.mp4' } }), { status: 200 })
      return new Response(JSON.stringify({ error: { message: 'unexpected' } }), { status: 404 })
    })
    vi.stubGlobal('fetch', fetchMock)
    stubObjectUrls()
    const { view } = renderChat([videoModel])
    await screen.findByText('video-model')
    fireEvent.change(view.container.querySelector('input[type="file"]')!, { target: { files: [new File(['image'], 'reference.png', { type: 'image/png' })] } })
    fireEvent.change(screen.getByLabelText('Сообщение'), { target: { value: 'Камера плавно приближается' } })
    fireEvent.click(screen.getByRole('button', { name: 'Отправить' }))
    await screen.findByLabelText('Результат видео')

    const request = fetchMock.mock.calls.find(([input]) => String(input).endsWith('/api/proxy/videos'))
    expect(request).toBeDefined()
    const body = request?.[1]?.body as FormData
    expect(body.get('model')).toBe('video-model')
    expect(body.get('prompt')).toBe('Камера плавно приближается')
    expect((body.get('input_reference') as File).name).toBe('reference.png')
    expect(fetchMock.mock.calls.some(([input]) => String(input).endsWith('/api/proxy/videos/video-1'))).toBe(true)
  })

  it('accepts dropped and pasted files through the same attachment state', async () => {
    const { view } = renderChat([textModel])
    await screen.findByText('text-model')
    stubObjectUrls()
    const dropped = new File(['drop'], 'dropped.txt', { type: 'text/plain' })
    const pasted = new File(['paste'], 'pasted.txt', { type: 'text/plain' })
    const form = screen.getByLabelText('Сообщение').closest('form')!
    fireEvent.drop(form, { dataTransfer: { files: [dropped], types: ['Files'] } })
    fireEvent.paste(screen.getByLabelText('Сообщение'), { clipboardData: { files: [pasted] } })
    expect(screen.getByText('dropped.txt')).toBeInTheDocument()
    expect(screen.getByText('pasted.txt')).toBeInTheDocument()
    expect(view.container.querySelectorAll('input[type="file"]')).toHaveLength(1)
  })
})
