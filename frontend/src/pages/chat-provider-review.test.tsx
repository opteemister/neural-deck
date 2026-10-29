import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ChatMediaResult } from '@/components/chat-media-result'
import { api } from '@/lib/api'
import { ChatPage } from '@/pages/chat'
import type { ChatConversation, HarnessTurn } from '@/lib/chat-harness'

afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals() })

function renderChat(path: string) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={client}><MemoryRouter initialEntries={[path]}><ChatPage /></MemoryRouter></QueryClientProvider>)
}

function stubObjectUrls() {
  Object.defineProperty(URL, 'createObjectURL', { configurable: true, value: vi.fn(() => 'blob:attachment') })
  Object.defineProperty(URL, 'revokeObjectURL', { configurable: true, value: vi.fn() })
}

describe('chat provider flows', () => {
  it('keeps multimodal history when continuing the persistent conversation', async () => {
    const getModels = vi.spyOn(api, 'getOpenAiModels').mockResolvedValue({ items: [{ id: 'vision-model', object: 'model', types: ['text'] }] })
    let conversation: ChatConversation = { id: '', busy: false, turns: [] }
    const send = vi.spyOn(api, 'sendChat').mockImplementation(async (id, request) => {
      const turn: HarnessTurn = { id: request.id, model: request.model, prompt: request.prompt,
        answer: conversation.turns.length === 0 ? 'vision answer' : 'text answer', reasoning: '',
        attachments: request.image_names.map((name) => ({ name, type: 'image/png', previewUrl: '/api/v1/chat/assets/image' })), results: [], state: 'complete' }
      conversation = { id, busy: false, turns: [...conversation.turns, turn] }
      return conversation
    })
    vi.spyOn(api, 'getChat').mockImplementation(async () => conversation)
    stubObjectUrls()
    const view = renderChat('/chat?model=vision-model&operation=vision')
    const textbox = await screen.findByLabelText('Сообщение')
    expect(getModels).toHaveBeenCalledWith(true)
    expect(screen.queryByRole('button', { name: 'Операция чата' })).not.toBeInTheDocument()
    const image = new File(['pixels'], 'house.png', { type: 'image/png' })
    fireEvent.change(view.container.querySelector('input[type="file"]')!, { target: { files: [image] } })
    fireEvent.change(textbox, { target: { value: 'Что на картинке?' } })
    fireEvent.click(screen.getByRole('button', { name: 'Отправить' }))
    await screen.findByText('vision answer')

    await waitFor(() => expect(screen.getByRole('button', { name: 'Отправить' })).toBeDisabled())
    fireEvent.change(screen.getByLabelText('Сообщение'), { target: { value: 'Продолжи ответ' } })
    fireEvent.click(screen.getByRole('button', { name: 'Отправить' }))
    await screen.findByText('text answer')

    expect(send).toHaveBeenCalledTimes(2)
    expect(send.mock.calls[1][0]).toBe(send.mock.calls[0][0])
    expect(send.mock.calls[0][1].content).toEqual([
      { type: 'text', text: 'Что на картинке?' },
      { type: 'image_url', image_url: { url: expect.stringMatching(/^data:image\/png;base64,/) } },
    ])
    expect(send.mock.calls[1][1].content).toBe('Продолжи ответ')
    expect(screen.getByText('vision answer')).toBeInTheDocument()
    expect(screen.getByText('Что на картинке?')).toBeInTheDocument()
    expect(screen.getByAltText('house.png')).toBeInTheDocument()
  })

  it('uses the first attached image as source and the second as mask in edit mode', async () => {
    const getModels = vi.spyOn(api, 'getOpenAiModels').mockResolvedValue({ items: [{ id: 'image-model', object: 'model', types: ['image'] }] })
    let conversation: ChatConversation
    const sendChat = vi.spyOn(api, 'sendChat').mockImplementation(async (id, request) => {
      conversation = { id, busy: false, turns: [{ id: request.id, model: request.model, prompt: request.prompt,
        answer: '', reasoning: '', attachments: [], state: 'complete', results: [{ kind: 'image', assets: [{ b64_json: 'abc' }] }] }] }
      return conversation
    })
    vi.spyOn(api, 'getChat').mockImplementation(async () => conversation)
    stubObjectUrls()
    const view = renderChat('/chat?model=image-model&operation=image-edit')
    await screen.findByText('image-model')
    expect(getModels).toHaveBeenCalledWith(true)
    const image = new File(['image'], 'source.png', { type: 'image/png' })
    const mask = new File(['mask'], 'mask.png', { type: 'image/png' })
    const input = view.container.querySelector('input[type="file"]')!
    fireEvent.change(input, { target: { files: [image, mask] } })
    expect(screen.getByText('Изображение')).toBeInTheDocument()
    expect(screen.getByText('Маска')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Удалить source.png' }))
    expect(screen.queryByText('Изображение')).not.toBeInTheDocument()
    expect(screen.queryByText('Маска')).not.toBeInTheDocument()
    fireEvent.change(input, { target: { files: [image, mask] } })
    fireEvent.change(screen.getByLabelText('Сообщение'), { target: { value: 'Измени фон' } })
    const send = await screen.findByRole('button', { name: 'Отправить' })
    await waitFor(() => expect(send).toBeEnabled())
    fireEvent.click(send)
    await screen.findByAltText('Результат 1')

    const request = sendChat.mock.calls[0][1]
    expect(request.image_names).toEqual(['source.png', 'mask.png'])
    expect(request.content).toEqual([
      { type: 'text', text: 'Измени фон' },
      { type: 'image_url', image_url: { url: 'data:image/png;base64,aW1hZ2U=' } },
      { type: 'image_url', image_url: { url: 'data:image/png;base64,bWFzaw==' } },
    ])
  })

  it('downloads a displayed image without calling a generation endpoint again', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(new Blob(['image'], { type: 'image/png' }), { status: 200 }))
    vi.stubGlobal('fetch', fetchMock)
    stubObjectUrls()
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => undefined)
    render(<ChatMediaResult result={{ kind: 'image', assets: [{ b64_json: 'abc', mime_type: 'image/png' }] }} />)
    fireEvent.click(screen.getByRole('button', { name: 'Скачать изображение' }))
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith('data:image/png;base64,abc'))
    expect(fetchMock.mock.calls.some(([input]) => String(input).includes('/images/generations'))).toBe(false)
  })

  it('downloads a displayed video without polling or creating it again', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(new Blob(['video'], { type: 'video/mp4' }), { status: 200 }))
    vi.stubGlobal('fetch', fetchMock)
    stubObjectUrls()
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => undefined)
    render(<ChatMediaResult result={{ kind: 'video', id: 'video-1', url: 'blob:video', downloadUrl: 'blob:video' }} />)
    fireEvent.click(screen.getByRole('button', { name: 'Скачать видео' }))
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith('blob:video', { credentials: 'same-origin' }))
    expect(fetchMock.mock.calls.some(([input]) => String(input).includes('/videos'))).toBe(false)
  })
})
