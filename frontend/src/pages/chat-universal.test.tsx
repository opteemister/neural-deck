// spec: version-zero
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { api } from '@/lib/api'
import { ChatPage } from '@/pages/chat'
import { readPendingVideo } from '@/lib/chat-video-job'
import type { OpenAiModel } from '@/types/api'

const textModel: OpenAiModel = { id: 'text-model', object: 'model', types: ['text'] }
const imageModel: OpenAiModel = { id: 'image-model', object: 'model', types: ['image'] }
const videoModel: OpenAiModel = { id: 'video-model', object: 'model', types: ['video'] }
const ocrModel: OpenAiModel = { id: 'ocr-model', object: 'model', types: ['ocr'] }
const variationsImageModel: OpenAiModel = { id: 'variations-image-model', object: 'model', types: ['image'], capabilities: { operations: ['variations'] } }

afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); window.localStorage.clear(); window.sessionStorage.clear() })

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

function mockMediaChat(kind: 'image' | 'video' = 'image') {
  let snapshot: import('@/lib/chat-harness').ChatConversation
  const send = vi.spyOn(api, 'sendChat').mockImplementation(async (id, body) => {
    snapshot = { id, busy: false, turns: [{ id: body.id, model: body.model, prompt: body.prompt,
      answer: '', reasoning: '', attachments: [], state: 'complete', results: kind === 'image'
        ? [{ kind: 'image', assets: [{ url: '/api/v1/chat/test/assets/image' }] }]
        : [{ kind: 'video', url: '/api/proxy/videos/test/content', downloadUrl: '/api/proxy/videos/test/content' }],
    }] }
    return snapshot
  })
  vi.spyOn(api, 'getChat').mockImplementation(async () => snapshot)
  return send
}

describe('universal chat composer', () => {
  it('restores a legacy pending video without another generation request', async () => {
    window.sessionStorage.setItem('neural-deck.chat.pending-video.v1', JSON.stringify({ id: 'persistent-video', model: 'video-model', prompt: 'Длинный ролик', createdAt: Date.now() }))
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ id: 'persistent-video', status: 'completed', url: '/media/restored.mp4' }), { status: 200 }))
    vi.stubGlobal('fetch', fetchMock)
    renderChat([videoModel], '/chat?model=video-model')
    await screen.findByLabelText('Результат видео')
    expect(fetchMock.mock.calls.filter(([, init]) => init?.method === 'POST')).toHaveLength(0)
    expect(readPendingVideo()).toBeNull()
    expect(screen.getAllByText('Длинный ролик')).toHaveLength(1)
  })

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
    const file = new File(['pdf'], 'report.docx', { type: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document' })
    fireEvent.change(view.container.querySelector('input[type="file"]')!, { target: { files: [file] } })
    fireEvent.change(screen.getByLabelText('Сообщение'), { target: { value: 'Прочитай документ' } })
    fireEvent.click(screen.getByRole('button', { name: 'Отправить' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('выберите модель для документов')
    expect(screen.getByLabelText('Сообщение')).toHaveValue('Прочитай документ')
    expect(screen.getByText('report.docx')).toBeInTheDocument()
    expect(stream).not.toHaveBeenCalled()
  })
  it('uses one model picker and routes a selected image model to generation without an operation control', async () => {
    const send = mockMediaChat()
    stubObjectUrls()
    const { getModels } = renderChat([textModel, imageModel])

    await screen.findByText('text-model')
    expect(getModels).toHaveBeenCalledWith(true)
    expect(screen.queryByRole('button', { name: 'Операция чата' })).not.toBeInTheDocument()
    expect(screen.getAllByRole('button', { name: 'Прикрепить файлы' })).toHaveLength(1)

    fireEvent.click(screen.getByRole('button', { name: 'Выбрать модель' }))
    fireEvent.click(screen.getByRole('radio', { name: 'image-model' }))
    expect(screen.getByRole('button', { name: 'Выбрать модель' })).toHaveTextContent('image-model')
    expect(screen.queryByText('Авто')).not.toBeInTheDocument()

    fireEvent.change(screen.getByLabelText('Сообщение'), { target: { value: 'Закат над морем' } })
    fireEvent.click(screen.getByRole('button', { name: 'Отправить' }))
    await screen.findByAltText('Результат 1')

    expect(send.mock.calls[0][1]).toMatchObject({ model: 'image-model', prompt: 'Закат над морем', content: 'Закат над морем', media_defaults: { 'image-model': { size: '1472x1472', background: 'opaque' } } })
  })

  it('sends the maximum portrait size and transparent background after closing the model menu', async () => {
    const send = mockMediaChat()
    const { getModels } = renderChat([imageModel], '/chat?model=image-model')

    await screen.findByText('image-model')
    fireEvent.click(screen.getByRole('button', { name: 'Выбрать модель' }))
    fireEvent.click(await screen.findByRole('radio', { name: 'Вертикальный 1088×1920' }))
    fireEvent.click(screen.getByRole('checkbox', { name: 'Макс. разр. (дольше)' }))
    fireEvent.click(screen.getByRole('checkbox', { name: 'Прозрачный фон' }))
    fireEvent.keyDown(document, { key: 'Escape' })
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()

    fireEvent.change(screen.getByLabelText('Сообщение'), { target: { value: 'Портрет в мягком свете' } })
    fireEvent.click(screen.getByRole('button', { name: 'Отправить' }))
    await screen.findByAltText('Результат 1')

    expect(getModels).toHaveBeenCalledWith(true)
    expect(send.mock.calls[0][1].media_defaults['image-model']).toEqual({ size: '1536x2752', background: 'transparent' })
  })

  it('keeps image settings independent per alias and restores them after remount', async () => {
    const { view } = renderChat([imageModel, { ...imageModel, id: 'second-image-model' }], '/chat?model=image-model')
    await screen.findByText('image-model')

    fireEvent.click(screen.getByRole('button', { name: 'Выбрать модель' }))
    fireEvent.click(await screen.findByRole('radio', { name: 'Вертикальный 1088×1920' }))
    fireEvent.click(screen.getByRole('checkbox', { name: 'Макс. разр. (дольше)' }))
    fireEvent.click(screen.getByRole('checkbox', { name: 'Прозрачный фон' }))
    fireEvent.click(screen.getByRole('radio', { name: 'second-image-model' }))
    expect(screen.getByRole('radio', { name: 'Квадрат 1472×1472' })).toBeChecked()
    expect(screen.getByRole('checkbox', { name: 'Прозрачный фон' })).not.toBeChecked()
    fireEvent.click(screen.getByRole('radio', { name: 'image-model' }))
    expect(screen.getByRole('radio', { name: 'Вертикальный 1536×2752' })).toBeChecked()
    expect(screen.getByRole('checkbox', { name: 'Прозрачный фон' })).toBeChecked()

    view.unmount()
    renderChat([imageModel], '/chat?model=image-model')
    await screen.findByText('image-model')
    fireEvent.click(screen.getByRole('button', { name: 'Выбрать модель' }))
    await waitFor(() => expect(screen.getByRole('radio', { name: 'Вертикальный 1536×2752' })).toBeChecked())
    expect(screen.getByRole('checkbox', { name: 'Макс. разр. (дольше)' })).toBeChecked()
    expect(screen.getByRole('checkbox', { name: 'Прозрачный фон' })).toBeChecked()
  })

  it('falls back to valid defaults when persisted image settings are corrupt', async () => {
    window.localStorage.setItem('neural-deck.chat.image-settings.v1', JSON.stringify({ 'image-model': { size: 'not-a-size', transparent: 'yes' } }))
    renderChat([imageModel], '/chat?model=image-model')
    await screen.findByText('image-model')
    fireEvent.click(screen.getByRole('button', { name: 'Выбрать модель' }))
    await screen.findByRole('radio', { name: 'Квадрат 1472×1472' })
    expect(screen.getByRole('radio', { name: 'Квадрат 1472×1472' })).toBeChecked()
    expect(screen.getByRole('checkbox', { name: 'Прозрачный фон' })).not.toBeChecked()
  })

  it('routes an attached image on the same image model to edits instead of text', async () => {
    const send = mockMediaChat()
    stubObjectUrls()
    const { view } = renderChat([imageModel])
    await screen.findByText('image-model')
    const image = new File(['pixels'], 'house.png', { type: 'image/png' })
    fireEvent.change(view.container.querySelector('input[type="file"]')!, { target: { files: [image] } })
    expect(screen.getByText('Изображение')).toBeInTheDocument()
    fireEvent.change(screen.getByLabelText('Сообщение'), { target: { value: 'Сделай небо розовым' } })
    fireEvent.click(screen.getByRole('button', { name: 'Отправить' }))
    await screen.findByAltText('Результат 1')

    expect(send.mock.calls[0][1]).toMatchObject({ model: 'image-model', image_names: ['house.png'], media_defaults: { 'image-model': { size: '1472x1472', background: 'opaque' } } })
    expect(send.mock.calls[0][1].content).toEqual([
      { type: 'text', text: 'Сделай небо розовым' },
      { type: 'image_url', image_url: { url: expect.stringMatching(/^data:image\/png;base64,/) } },
    ])
  })

  it('sends variations and image settings through the common chat API', async () => {
    const send = mockMediaChat()
    stubObjectUrls()
    const { view } = renderChat([variationsImageModel], '/chat?model=variations-image-model')
    await screen.findByText('variations-image-model')
    const image = new File(['pixels'], 'house.png', { type: 'image/png' })
    fireEvent.change(view.container.querySelector('input[type="file"]')!, { target: { files: [image] } })
    fireEvent.click(screen.getByRole('button', { name: 'Отправить' }))
    await screen.findByAltText('Результат 1')

    expect(send.mock.calls[0][1]).toMatchObject({ model: 'variations-image-model', media_defaults: { 'variations-image-model': { size: '1472x1472', background: 'opaque' } } })
    expect(send.mock.calls[0][1].content).toEqual([{ type: 'image_url', image_url: { url: expect.stringMatching(/^data:image\/png;base64,/) } }])
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

  it.each([ocrModel, textModel])('sends PDF attachments through the persistent chat with $id', async (model) => {
    const completed = { id: 'pdf-chat', busy: false, turns: [{ id: 'pdf-turn', model: model.id, prompt: 'Прочитай', answer: 'Распознано', reasoning: '', attachments: [], results: [], state: 'complete' as const }] }
    const send = vi.spyOn(api, 'sendChat').mockImplementation(async (id, body) => {
      completed.id = id; completed.turns[0].id = body.id
      return completed
    })
    vi.spyOn(api, 'getChat').mockImplementation(async (id) => ({ ...completed, id }))
    stubObjectUrls()
    const { view } = renderChat([model], `/chat?model=${model.id}`)
    await screen.findByText(model.id)
    fireEvent.change(view.container.querySelector('input[type="file"]')!, { target: { files: [new File(['%PDF-1.4'], 'scan.pdf', { type: 'application/pdf' })] } })
    fireEvent.change(screen.getByLabelText('Сообщение'), { target: { value: 'Прочитай' } })
    fireEvent.click(screen.getByRole('button', { name: 'Отправить' }))
    await screen.findByText('Распознано')
    expect(send.mock.calls[0][1]).toMatchObject({ model: model.id, operation: model === ocrModel ? 'ocr' : 'chat', content: [
      { type: 'text', text: 'Прочитай' },
      { type: 'file', file: { filename: 'scan.pdf', file_data: expect.stringMatching(/^data:application\/pdf;base64,/) } },
    ] })
  })

  it('routes an optional image reference to the video create/status API', async () => {
    const send = mockMediaChat('video')
    stubObjectUrls()
    const { view } = renderChat([videoModel])
    await screen.findByText('video-model')
    fireEvent.change(view.container.querySelector('input[type="file"]')!, { target: { files: [new File(['image'], 'reference.png', { type: 'image/png' })] } })
    fireEvent.change(screen.getByLabelText('Сообщение'), { target: { value: 'Камера плавно приближается' } })
    fireEvent.click(screen.getByRole('button', { name: 'Отправить' }))
    await screen.findByLabelText('Результат видео')

    expect(send.mock.calls[0][1]).toMatchObject({ model: 'video-model', prompt: 'Камера плавно приближается', image_names: ['reference.png'], media_defaults: { 'video-model': { seconds: 2 } } })
    expect(send.mock.calls[0][1].content).toEqual([
      { type: 'text', text: 'Камера плавно приближается' },
      { type: 'image_url', image_url: { url: expect.stringMatching(/^data:image\/png;base64,/) } },
    ])
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
