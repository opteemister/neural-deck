// spec: version-zero
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ChatPage } from '@/pages/chat'
import { api } from '@/lib/api'

function streamResponse(text: string): Response {
  const stream = new ReadableStream<Uint8Array>({
    start(controller) {
      controller.enqueue(new TextEncoder().encode(`data: {"choices":[{"delta":{"content":"${text}"}}]}\n\ndata: [DONE]\n\n`))
      controller.close()
    },
  })
  return new Response(stream, { status: 200, headers: { 'content-type': 'text/event-stream' } })
}

afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals() })

describe('ChatPage', () => {
  it('sends vision content parts through the existing text stream', async () => {
    vi.spyOn(api, 'getOpenAiModels').mockResolvedValue({ items: [{ id: 'vision-model', object: 'model', types: ['text'] }] })
    const stream = vi.spyOn(api, 'streamChat').mockImplementation(async (_request, onChunk) => { onChunk({ done: false, content: 'На изображении есть дом.' }) })
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    const view = render(<QueryClientProvider client={client}><MemoryRouter initialEntries={['/chat?model=vision-model']}><ChatPage /></MemoryRouter></QueryClientProvider>)
    await screen.findByLabelText('Сообщение')
    fireEvent.click(screen.getByRole('button', { name: 'Операция чата' }))
    fireEvent.click(screen.getByRole('option', { name: 'Vision' }))
    const fileInput = view.container.querySelector('input[type="file"]') as HTMLInputElement
    const image = new File(['pixels'], 'house.png', { type: 'image/png' })
    Object.defineProperty(URL, 'createObjectURL', { configurable: true, value: vi.fn(() => 'blob:house') })
    Object.defineProperty(URL, 'revokeObjectURL', { configurable: true, value: vi.fn() })
    fireEvent.change(fileInput, { target: { files: [image] } })
    fireEvent.change(screen.getByLabelText('Сообщение'), { target: { value: 'Что на картинке?' } })
    fireEvent.click(screen.getByRole('button', { name: 'Отправить' }))
    await waitFor(() => expect(screen.getByText('На изображении есть дом.')).toBeInTheDocument())
    const request = stream.mock.calls[0][0] as { messages: Array<{ content: unknown }> }
    expect(request.messages.at(-1)?.content).toEqual([
      { type: 'text', text: 'Что на картинке?' },
      { type: 'image_url', image_url: { url: expect.stringMatching(/^data:image\/png;base64,/) } },
    ])
    client.clear()
  })

  it('keeps the last measured context visible during later requests and updates it from usage', async () => {
    vi.spyOn(api, 'getOpenAiModels').mockResolvedValue({ items: [{ id: 'local-model', object: 'model', context_length: 4096 }] })
    let emit: Parameters<typeof api.streamChat>[1] | undefined
    let finish: (() => void) | undefined
    vi.spyOn(api, 'streamChat').mockImplementation(async (_request, onChunk) => {
      emit = onChunk
      await new Promise<void>((resolve) => { finish = resolve })
    })
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(<QueryClientProvider client={client}><MemoryRouter initialEntries={['/chat?model=local-model']}><ChatPage /></MemoryRouter></QueryClientProvider>)
    await waitFor(() => expect(screen.getByRole('button', { name: 'Отправить' })).toBeDisabled())
    const context = screen.getByLabelText('Контекст диалога')
    expect(context).toHaveTextContent('Контекст —')
    const send = async (text: string) => {
      fireEvent.change(screen.getByLabelText('Сообщение'), { target: { value: text } })
      await waitFor(() => expect(screen.getByRole('button', { name: 'Отправить' })).toBeEnabled())
      fireEvent.click(screen.getByRole('button', { name: 'Отправить' }))
    }
    await send('Первый запрос')
    expect(context).toHaveTextContent('Контекст —')
    await act(async () => { emit?.({ done: false, content: 'Ответ', usage: { total_tokens: 120 } }); finish?.() })
    expect(context).toHaveTextContent('Контекст 120')
    await send('Второй запрос')
    expect(context).toHaveTextContent('Контекст 120')
    await act(async () => { emit?.({ done: false, content: 'Следующий ответ' }) })
    expect(context).toHaveTextContent('Контекст 120')
    await act(async () => { emit?.({ done: false, content: '', usage: { prompt_tokens: 150, completion_tokens: 50 } }); finish?.() })
    expect(context).toHaveTextContent('Контекст 200')
    await send('Третий запрос')
    await act(async () => { finish?.() })
    expect(context).toHaveTextContent('Контекст 200')
    client.clear()
  })

  it('uses a registered model and renders an SSE assistant response', async () => {
    const fetchMock = vi.fn().mockImplementation((input: RequestInfo | URL) => {
      const url = String(input)
      if (url.endsWith('/api/proxy/models')) return new Response(JSON.stringify({ object: 'list', data: [{ id: 'local-model', object: 'model' }] }), { status: 200 })
      if (url.endsWith('/api/proxy/chat/completions')) return streamResponse('готово')
      return new Response(JSON.stringify({ error: { code: 'not_found', message: 'not found', details: {}, request_id: 'req', retryable: false } }), { status: 404 })
    })
    vi.stubGlobal('fetch', fetchMock)
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(<QueryClientProvider client={client}><MemoryRouter initialEntries={['/chat?model=local-model']}><ChatPage /></MemoryRouter></QueryClientProvider>)
    const textbox = await screen.findByLabelText('Сообщение')
    fireEvent.change(textbox, { target: { value: 'Привет' } })
    fireEvent.click(screen.getByRole('button', { name: 'Отправить' }))
    await waitFor(() => expect(screen.getByText('готово')).toBeInTheDocument())
    expect(fetchMock).toHaveBeenCalledWith('/api/proxy/chat/completions', expect.objectContaining({ method: 'POST' }))
    expect(JSON.parse(String(fetchMock.mock.calls.find(([input]) => String(input).endsWith('/api/proxy/chat/completions'))?.[1]?.body)).model).toBe('local-model')
  })
})
