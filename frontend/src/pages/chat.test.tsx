// spec: version-zero
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ChatPage } from '@/pages/chat'
import { api } from '@/lib/api'
import type { ChatConversation, HarnessTurn } from '@/lib/chat-harness'

function completedTurn(id: string, model: string, prompt: string, answer: string): HarnessTurn {
  return { id, model, prompt, answer, reasoning: '', attachments: [], results: [], state: 'complete' }
}

function mockChat(answer: string) {
  let conversation: ChatConversation
  const send = vi.spyOn(api, 'sendChat').mockImplementation(async (id, body) => {
    conversation = { id, busy: false, turns: [completedTurn(body.id, body.model, body.prompt, answer)] }
    return conversation
  })
  vi.spyOn(api, 'getChat').mockImplementation(async () => conversation)
  return send
}

function renderChat(path: string) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={client}><MemoryRouter initialEntries={[path]}><ChatPage /></MemoryRouter></QueryClientProvider>)
}

afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals() })

describe('ChatPage', () => {
  it('keeps the model menu open to select reasoning and sends the chosen effort', async () => {
    vi.spyOn(api, 'getOpenAiModels').mockResolvedValue({ items: [
      { id: 'image-model', object: 'model', types: ['image'] },
      { id: 'thinking-model', object: 'model', types: ['text'], reasoning_capabilities: { efforts: ['low', 'high', 'xhigh'], effort_parameter: 'reasoning_effort', can_disable_thinking: true } },
    ] })
    const send = mockChat('Готово')
    renderChat('/chat?model=image-model')
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
    expect(send.mock.calls[0][1]).toMatchObject({ model: 'thinking-model', reasoning: { reasoning_effort: 'xhigh' } })
  })

  it('sends vision content parts through the common chat API', async () => {
    const getModels = vi.spyOn(api, 'getOpenAiModels').mockResolvedValue({ items: [{ id: 'vision-model', object: 'model', types: ['text'] }] })
    const send = mockChat('На изображении есть дом.')
    const view = renderChat('/chat?model=vision-model')
    await screen.findByText('vision-model')
    expect(getModels).toHaveBeenCalledWith(true)
    expect(screen.queryByRole('button', { name: 'Операция чата' })).not.toBeInTheDocument()
    const image = new File(['pixels'], 'house.png', { type: 'image/png' })
    Object.defineProperty(URL, 'createObjectURL', { configurable: true, value: vi.fn(() => 'blob:house') })
    Object.defineProperty(URL, 'revokeObjectURL', { configurable: true, value: vi.fn() })
    fireEvent.change(view.container.querySelector('input[type="file"]')!, { target: { files: [image] } })
    fireEvent.change(screen.getByLabelText('Сообщение'), { target: { value: 'Что на картинке?' } })
    fireEvent.click(screen.getByRole('button', { name: 'Отправить' }))
    await screen.findByText('На изображении есть дом.')
    expect(send.mock.calls[0][1].content).toEqual([
      { type: 'text', text: 'Что на картинке?' },
      { type: 'image_url', image_url: { url: expect.stringMatching(/^data:image\/png;base64,/) } },
    ])
  })

  it('hides empty context and preserves the last usage while the next request runs', async () => {
    vi.spyOn(api, 'getOpenAiModels').mockResolvedValue({ items: [{ id: 'local-model', object: 'model', types: ['text'], context_length: 4096 }] })
    let conversation: ChatConversation
    let finish: ((value: ChatConversation) => void) | undefined
    vi.spyOn(api, 'sendChat').mockImplementation(async (id, body) => {
      const previous = conversation?.turns ?? []
      conversation = { id, busy: true, turns: [...previous, { ...completedTurn(body.id, body.model, body.prompt, ''), state: 'streaming' }] }
      return conversation
    })
    vi.spyOn(api, 'getChat').mockImplementation(() => conversation.busy
      ? new Promise<ChatConversation>((resolve) => { finish = resolve })
      : Promise.resolve(conversation))
    renderChat('/chat?model=local-model')
    await screen.findByText('local-model')
    expect(screen.queryByRole('progressbar', { name: 'Контекст диалога' })).not.toBeInTheDocument()
    const send = async (prompt: string) => {
      fireEvent.change(screen.getByLabelText('Сообщение'), { target: { value: prompt } })
      await waitFor(() => expect(screen.queryByRole('button', { name: 'Остановить генерацию' })).not.toBeInTheDocument())
      await waitFor(() => expect(screen.getByRole('button', { name: 'Отправить' })).toBeEnabled())
      finish = undefined
      fireEvent.click(screen.getByRole('button', { name: 'Отправить' }))
      await waitFor(() => expect(finish).toBeTypeOf('function'))
    }
    const complete = async (usage?: HarnessTurn['usage']) => {
      const last = conversation.turns.at(-1)!
      conversation = { ...conversation, busy: false, turns: [...conversation.turns.slice(0, -1), { ...last, state: 'complete', answer: `Ответ ${conversation.turns.length}`, usage }] }
      await act(async () => { finish!(conversation) })
    }
    await send('Первый запрос')
    await complete({ total_tokens: 120 })
    const context = screen.getByRole('progressbar', { name: 'Контекст диалога' })
    expect(context).toHaveAttribute('aria-valuetext', `Использовано 120 из ${(4096).toLocaleString('ru-RU')} токенов`)
    expect(Number(context.getAttribute('aria-valuenow'))).toBeCloseTo(120 / 4096 * 100)
    await send('Второй запрос')
    expect(context).toHaveAttribute('aria-valuetext', `Использовано 120 из ${(4096).toLocaleString('ru-RU')} токенов`)
    await complete({ prompt_tokens: 150, completion_tokens: 50 })
    expect(context).toHaveAttribute('aria-valuetext', `Использовано 200 из ${(4096).toLocaleString('ru-RU')} токенов`)
    await send('Третий запрос')
    await complete()
    expect(context).toHaveAttribute('aria-valuetext', `Использовано 200 из ${(4096).toLocaleString('ru-RU')} токенов`)
  })

  it('uses the same-origin persistent chat endpoint with the registered model', async () => {
    const envelope = (data: unknown) => new Response(JSON.stringify({ data, request_id: 'req' }), { status: 200 })
    let conversation: ChatConversation
    const fetchMock = vi.fn().mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      if (url.includes('/api/proxy/models?all_types=true')) return new Response(JSON.stringify({ object: 'list', data: [{ id: 'local-model', object: 'model', types: ['text'] }] }), { status: 200 })
      if (url.endsWith('/turns') && init?.method === 'POST') {
        const body = JSON.parse(String(init.body))
        const id = url.split('/').at(-2)!
        conversation = { id, busy: false, turns: [completedTurn(body.id, body.model, body.prompt, 'готово')] }
        return envelope(conversation)
      }
      if (url === `/api/v1/chat/${conversation?.id}`) return envelope(conversation)
      return new Response(JSON.stringify({ error: { code: 'not_found', message: 'not found', details: {}, request_id: 'req', retryable: false } }), { status: 404 })
    })
    vi.stubGlobal('fetch', fetchMock)
    renderChat('/chat?model=local-model')
    await screen.findByText('local-model')
    fireEvent.change(screen.getByLabelText('Сообщение'), { target: { value: 'Привет' } })
    fireEvent.click(screen.getByRole('button', { name: 'Отправить' }))
    await screen.findByText('готово')
    expect(fetchMock).toHaveBeenCalledWith(expect.stringMatching(/^\/api\/v1\/chat\/[^/]+\/turns$/), expect.objectContaining({ method: 'POST', body: expect.stringContaining('"model":"local-model"') }))
    expect(fetchMock.mock.calls.some(([url]) => String(url).includes('/api/proxy/chat/completions'))).toBe(false)
  })
})
