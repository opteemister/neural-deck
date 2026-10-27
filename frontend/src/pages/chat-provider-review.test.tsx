import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ChatMediaResult } from '@/components/chat-media-result'
import { api } from '@/lib/api'
import { ChatPage } from '@/pages/chat'

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
  it('keeps multimodal history when switching back to text streaming', async () => {
    vi.spyOn(api, 'getOpenAiModels').mockResolvedValue({ items: [{ id: 'vision-model', object: 'model', types: ['text'] }] })
    const requests: Array<{ messages: Array<{ role: string; content: unknown }> }> = []
    let call = 0
    vi.spyOn(api, 'streamChat').mockImplementation(async (request, onChunk) => {
      requests.push(request as typeof requests[number])
      onChunk({ done: false, content: call++ === 0 ? 'vision answer' : 'text answer' })
    })
    stubObjectUrls()
    const view = renderChat('/chat?model=vision-model&operation=vision')
    const textbox = await screen.findByLabelText('Сообщение')
    const image = new File(['pixels'], 'house.png', { type: 'image/png' })
    fireEvent.change(view.container.querySelector('input[type="file"]')!, { target: { files: [image] } })
    fireEvent.change(textbox, { target: { value: 'Что на картинке?' } })
    fireEvent.click(screen.getByRole('button', { name: 'Отправить' }))
    await screen.findByText('vision answer')

    fireEvent.click(screen.getByRole('button', { name: 'Операция чата' }))
    fireEvent.click(screen.getByRole('option', { name: 'Текст' }))
    fireEvent.change(screen.getByLabelText('Сообщение'), { target: { value: 'Продолжи ответ' } })
    fireEvent.click(screen.getByRole('button', { name: 'Отправить' }))
    await screen.findByText('text answer')

    expect(requests).toHaveLength(2)
    expect(requests[1].messages.slice(0, 2)).toEqual([
      { role: 'user', content: [
        { type: 'text', text: 'Что на картинке?' },
        { type: 'image_url', image_url: { url: expect.stringMatching(/^data:image\/png;base64,/) } },
      ] },
      { role: 'assistant', content: 'vision answer' },
    ])
    expect(requests[1].messages.at(-1)).toEqual({ role: 'user', content: 'Продолжи ответ' })
  })

  it('uses the first attached image as source and the second as mask in edit mode', async () => {
    vi.spyOn(api, 'getOpenAiModels').mockResolvedValue({ items: [] })
    const fetchMock = vi.fn().mockImplementation((input: RequestInfo | URL) => {
      const url = String(input)
      if (url.includes('/api/proxy/models?all_types=true')) return Promise.resolve(new Response(JSON.stringify({ data: [{ id: 'image-model', object: 'model', types: ['image'] }] }), { status: 200 }))
      if (url.endsWith('/api/proxy/images/edits')) return Promise.resolve(new Response(JSON.stringify({ data: [{ b64_json: 'abc' }] }), { status: 200 }))
      return Promise.resolve(new Response(JSON.stringify({ error: { message: 'unexpected' } }), { status: 404 }))
    })
    vi.stubGlobal('fetch', fetchMock)
    stubObjectUrls()
    const view = renderChat('/chat?model=image-model&operation=image-edit')
    const image = new File(['image'], 'source.png', { type: 'image/png' })
    const mask = new File(['mask'], 'mask.png', { type: 'image/png' })
    const input = view.container.querySelector('input[type="file"]')!
    fireEvent.change(input, { target: { files: [image, mask] } })
    expect(screen.getByText('Источник')).toBeInTheDocument()
    expect(screen.getByText('Маска')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Удалить source.png' }))
    expect(screen.queryByText('Маска')).not.toBeInTheDocument()
    fireEvent.change(input, { target: { files: [image, mask] } })
    fireEvent.change(screen.getByLabelText('Сообщение'), { target: { value: 'Измени фон' } })
    const send = await screen.findByRole('button', { name: 'Отправить' })
    await waitFor(() => expect(send).toBeEnabled())
    fireEvent.click(send)
    await screen.findByAltText('Результат 1')

    const request = fetchMock.mock.calls.find(([input]) => String(input).endsWith('/api/proxy/images/edits'))
    expect(request).toBeDefined()
    const body = request?.[1]?.body as FormData
    expect((body.get('image') as File).name).toBe('source.png')
    expect((body.get('mask') as File).name).toBe('mask.png')
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
    fireEvent.click(screen.getByRole('button', { name: 'Скачать' }))
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith('blob:video', { credentials: 'same-origin' }))
    expect(fetchMock.mock.calls.some(([input]) => String(input).includes('/videos'))).toBe(false)
  })
})
