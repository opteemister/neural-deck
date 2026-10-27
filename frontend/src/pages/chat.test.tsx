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
  it('keeps the model menu open to select reasoning and sends the chosen effort', async () => {
    vi.spyOn(api, 'getOpenAiModels').mockResolvedValue({ items: [
      { id: 'image-model', object: 'model', types: ['image'] },
      { id: 'thinking-model', object: 'model', types: ['text'], reasoning_capabilities: { efforts: ['low', 'high', 'xhigh'], effort_parameter: 'reasoning_effort', can_disable_thinking: true } },
    ] })
    const stream = vi.spyOn(api, 'streamChat').mockImplementation(async (_request, onChunk) => { onChunk({ done: false, content: 'Готово' }) })
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(<QueryClientProvider client={client}><MemoryRouter initialEntries={['/chat?model=image-model']}><ChatPage /></MemoryRouter></QueryClientProvider>)
    await waitFor(() => expect(screen.getByRole('button', { name: 'Выбрать модель' })).toHaveTextContent('image-model'))
    fireEvent.click(screen.getByRole('button', { name: 'Выбрать модель' }))
    fireEvent.click(await screen.findByRole('radio', { name: 'thinking-model' }))
    fireEvent.click(screen.getByRole('radio', { name: 'XHigh' }))
    expect(screen.getByRole('radio', { name: 'XHigh' })).toBeChecked()
    fireEvent.keyDown(document, { key: 'Escape' })
    expect(screen.getByRole('button', { name: 'Выбрать модель' })).toHaveTextContent('XHigh')
    fireEvent.change(screen.getByLabelText('Сообщение'), { target: { value: 'Подумай' } })
    fireEvent.click(screen.getByRole('button', { name: 'Отправить' }))
    await screen.findByText('Готово')
    expect(stream.mock.calls[0][0]).toMatchObject({ model: 'thinking-model', reasoning_effort: 'xhigh' })
    client.clear()
  })

  it('sends vision content parts through the existing text stream', async () => {
    const getModels = vi.spyOn(api, 'getOpenAiModels').mockResolvedValue({ items: [{ id: 'vision-model', object: 'model', types: ['text'] }] })
    const stream = vi.spyOn(api, 'streamChat').mockImplementation(async (_request, onChunk) => { onChunk({ done: false, content: 'На изображении есть дом.' }) })
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    const view = render(<QueryClientProvider client={client}><MemoryRouter initialEntries={['/chat?model=vision-model']}><ChatPage /></MemoryRouter></QueryClientProvider>)
    await screen.findByLabelText('Сообщение')
    expect(getModels).toHaveBeenCalledWith(true)
    expect(screen.queryByRole('button', { name: 'Операция чата' })).not.toBeInTheDocument()
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
    const getModels = vi.spyOn(api, 'getOpenAiModels').mockResolvedValue({ items: [{ id: 'local-model', object: 'model', types: ['text'], context_length: 4096 }] })
    let emit: Parameters<typeof api.streamChat>[1] | undefined
    let finish: (() => void) | undefined
    vi.spyOn(api, 'streamChat').mockImplementation(async (_request, onChunk) => {
      emit = onChunk
      await new Promise<void>((resolve) => { finish = resolve })
    })
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(<QueryClientProvider client={client}><MemoryRouter initialEntries={['/chat?model=local-model']}><ChatPage /></MemoryRouter></QueryClientProvider>)
    await waitFor(() => expect(screen.getByRole('button', { name: 'Отправить' })).toBeDisabled())
    expect(getModels).toHaveBeenCalledWith(true)
    const initialContext = screen.getByLabelText('Контекст диалога')
    expect(initialContext).toHaveTextContent('Контекст —')
    expect(initialContext.closest('form')).toBeNull()
    const send = async (text: string) => {
      fireEvent.change(screen.getByLabelText('Сообщение'), { target: { value: text } })
      await waitFor(() => expect(screen.getByRole('button', { name: 'Отправить' })).toBeEnabled())
      fireEvent.click(screen.getByRole('button', { name: 'Отправить' }))
    }
    await send('Первый запрос')
    await act(async () => { emit?.({ done: false, content: 'Ответ', usage: { total_tokens: 120 } }); finish?.() })
    const context = await screen.findByLabelText('Контекст диалога')
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
      if (url.includes('/api/proxy/models?all_types=true')) return new Response(JSON.stringify({ object: 'list', data: [{ id: 'local-model', object: 'model', types: ['text'] }] }), { status: 200 })
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
