import { StrictMode } from 'react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, useNavigate } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { api } from '@/lib/api'
import type { ChatConversation, HarnessTurn } from '@/lib/chat-harness'
import { ChatPage } from '@/pages/chat'

const turn: HarnessTurn = {
  id: 'turn-one', model: 'llm', prompt: 'Нарисуй кота', answer: 'Вот изображение', reasoning: '',
  attachments: [], results: [{ kind: 'image', assets: [{ url: '/api/v1/chat/one/assets/cat' }] }],
  state: 'complete', usage: { total_tokens: 25 },
}
const snapshot: ChatConversation = { id: 'one', busy: false, turns: [turn] }

afterEach(() => { cleanup(); vi.restoreAllMocks(); window.sessionStorage.clear() })

function Navigate() {
  const navigate = useNavigate()
  return <button onClick={() => navigate('/chat?model=llm&chat=two')}>Другой диалог</button>
}

function setup(path = '/chat?model=llm') {
  vi.spyOn(api, 'getOpenAiModels').mockResolvedValue({ items: [
    { id: 'llm', object: 'model', types: ['text'], context_length: 1000 },
    { id: 'image', object: 'model', types: ['image'] },
  ] })
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(<StrictMode><QueryClientProvider client={client}><MemoryRouter initialEntries={[path]}>
    <ChatPage /><Navigate />
  </MemoryRouter></QueryClientProvider></StrictMode>)
}

describe('chat media harness', () => {
  it('sends through the selected LLM and shows both its answer and generated image', async () => {
    let complete = snapshot
    const send = vi.spyOn(api, 'sendChat').mockImplementation(async (id, body) => {
      complete = { id, busy: false, turns: [{ ...turn, id: body.id }] }
      return { ...complete, busy: true, turns: [{ ...complete.turns[0], state: 'streaming', answer: '', results: [], phase: 'generate_image' }] }
    })
    vi.spyOn(api, 'getChat').mockImplementation(async () => complete)
    const raw = vi.spyOn(api, 'streamChat')
    setup()
    await screen.findByText('llm')
    fireEvent.change(screen.getByLabelText('Сообщение'), { target: { value: 'Нарисуй кота' } })
    fireEvent.click(screen.getByRole('button', { name: 'Отправить' }))
    await screen.findByAltText('Результат 1')
    expect(screen.getByText('Вот изображение')).toBeInTheDocument()
    expect(send.mock.calls[0][1].model).toBe('llm')
    expect(send.mock.calls[0][1].media_defaults.image).toEqual({ size: '1472x1472', background: 'opaque' })
    expect(raw).not.toHaveBeenCalled()
    expect(screen.getByRole('button', { name: 'Выбрать модель' })).toHaveTextContent('llm')
  })

  it('restores the same conversation in StrictMode without resending a message', async () => {
    vi.spyOn(api, 'getChat').mockResolvedValue(snapshot)
    const send = vi.spyOn(api, 'sendChat')
    setup('/chat?model=llm&chat=one')
    await screen.findByAltText('Результат 1')
    expect(screen.getByText('Вот изображение')).toBeInTheDocument()
    expect(send).not.toHaveBeenCalled()
    expect(screen.getByRole('button', { name: 'Отправить' })).toBeDisabled()
    fireEvent.change(screen.getByLabelText('Сообщение'), { target: { value: 'Дальше' } })
    await waitFor(() => expect(screen.getByRole('button', { name: 'Отправить' })).toBeEnabled())
  })

  it('does not import a video pending in another chat and replaces history when navigating', async () => {
    window.sessionStorage.setItem('neural-deck.chat.pending-video.v1', JSON.stringify({ id: 'unrelated-video', model: 'video', prompt: 'Чужой ролик', createdAt: Date.now() }))
    const get = vi.spyOn(api, 'getChat').mockImplementation(async (id) => id === 'one' ? snapshot : { id: 'two', busy: false, turns: [{ ...turn, id: 'other', prompt: 'Второй вопрос', answer: 'Второй ответ', results: [] }] })
    setup('/chat?model=llm&chat=one')
    await screen.findByAltText('Результат 1')
    expect(screen.queryByText('Чужой ролик')).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Другой диалог' }))
    await screen.findByText('Второй ответ')
    expect(screen.queryByText('Вот изображение')).not.toBeInTheDocument()
    expect(screen.queryByAltText('Результат 1')).not.toBeInTheDocument()
    expect(get).toHaveBeenCalledWith('two', expect.any(AbortSignal))
  })

  it('stops the backend run instead of only closing the browser request', async () => {
    let stopped = false
    vi.spyOn(api, 'getChat').mockImplementation(async () => ({ ...snapshot, busy: !stopped, turns: [{ ...turn, state: stopped ? 'cancelled' : 'streaming', phase: 'generate_video' }] }))
    const stop = vi.spyOn(api, 'stopChat').mockImplementation(async () => {
      stopped = true
      return { ...snapshot, turns: [{ ...turn, state: 'cancelled' }] }
    })
    setup('/chat?model=llm&chat=one')
    fireEvent.click(await screen.findByRole('button', { name: 'Остановить генерацию' }))
    await waitFor(() => expect(stop).toHaveBeenCalledWith('one'))
    await screen.findByText(/Генерация прервана/)
  })
})

it('opens a chat from history and returns to a blank chat after deleting the active one', async () => {
  let items = [
    { id: 'one', title: 'Первый диалог', model: 'llm', updated_at: 100, busy: false },
    { id: 'two', title: 'Второй диалог', model: 'llm', updated_at: 200, busy: false },
  ]
  vi.spyOn(api, 'listChats').mockImplementation(async () => ({ items }))
  vi.spyOn(api, 'getChat').mockImplementation(async (id) => id === 'one' ? snapshot : { id, busy: false, turns: [{ ...turn, id: 'second', prompt: 'Второй вопрос', answer: 'Второй ответ', results: [] }] })
  const remove = vi.spyOn(api, 'deleteChat').mockImplementation(async (id) => { items = items.filter((item) => item.id !== id); return { id, deleted: true } })
  setup('/chat?model=llm&chat=one')
  await screen.findByText('Вот изображение')
  fireEvent.click(screen.getByRole('button', { name: 'Открыть историю чатов' }))
  fireEvent.click(await screen.findByRole('button', { name: /^Второй диалог/ }))
  await screen.findByText('Второй ответ')
  expect(screen.queryByText('Вот изображение')).not.toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: 'Открыть историю чатов' }))
  fireEvent.click(await screen.findByRole('button', { name: 'Удалить чат «Второй диалог»' }))
  fireEvent.click(screen.getByRole('button', { name: 'Удалить чат' }))
  await screen.findByText('С чего начнём?')
  expect(remove).toHaveBeenCalledWith('two')
  expect(screen.queryByText('Второй ответ')).not.toBeInTheDocument()
})
