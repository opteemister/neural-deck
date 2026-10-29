import { useState } from 'react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { ChatHistory } from './chat-history'
import { api } from '@/lib/api'

const chat = { id: 'one', title: 'Нарисуй котёнка', model: 'llm', updated_at: 1234, busy: false }
afterEach(() => { cleanup(); vi.restoreAllMocks() })

function setup() {
  const onSelect = vi.fn(), onDeleted = vi.fn(), onError = vi.fn()
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  function Demo() {
    const [open, setOpen] = useState(false)
    return <ChatHistory open={open} docked currentId="one" onOpenChange={setOpen} onSelect={onSelect} onDeleted={onDeleted} onNew={vi.fn()} onError={onError} />
  }
  render(<QueryClientProvider client={client}><Demo /></QueryClientProvider>)
  return { onSelect, onDeleted, onError }
}

it('loads history only when opened, selects a chat and can collapse', async () => {
  const list = vi.spyOn(api, 'listChats').mockResolvedValue({ items: [chat] })
  const { onSelect } = setup()
  expect(list).not.toHaveBeenCalled()
  fireEvent.click(screen.getByRole('button', { name: 'Открыть историю чатов' }))
  fireEvent.click(await screen.findByRole('button', { name: /Нарисуй котёнка llm/ }))
  expect(onSelect).toHaveBeenCalledWith(chat)
  fireEvent.click(screen.getByRole('button', { name: 'Свернуть историю чатов' }))
  expect(screen.queryByRole('complementary')).not.toBeInTheDocument()
})

it('deletes only the confirmed chat and refreshes the list', async () => {
  let items = [chat]
  vi.spyOn(api, 'listChats').mockImplementation(async () => ({ items }))
  const remove = vi.spyOn(api, 'deleteChat').mockImplementation(async (id) => { items = []; return { id, deleted: true } })
  const { onDeleted } = setup()
  fireEvent.click(screen.getByRole('button', { name: 'Открыть историю чатов' }))
  fireEvent.click(await screen.findByRole('button', { name: 'Удалить чат «Нарисуй котёнка»' }))
  expect(remove).not.toHaveBeenCalled()
  fireEvent.click(screen.getByRole('button', { name: 'Удалить чат' }))
  await screen.findByText('Пока нет чатов')
  expect(remove).toHaveBeenCalledWith('one')
  expect(onDeleted).toHaveBeenCalledWith('one')
})

it('keeps the chat when deletion fails', async () => {
  vi.spyOn(api, 'listChats').mockResolvedValue({ items: [chat] })
  vi.spyOn(api, 'deleteChat').mockRejectedValue(new Error('offline'))
  const { onDeleted, onError } = setup()
  fireEvent.click(screen.getByRole('button', { name: 'Открыть историю чатов' }))
  fireEvent.click(await screen.findByRole('button', { name: 'Удалить чат «Нарисуй котёнка»' }))
  fireEvent.click(screen.getByRole('button', { name: 'Удалить чат' }))
  await waitFor(() => expect(onError).toHaveBeenCalledWith('Не удалось удалить чат', 'offline'))
  expect(onDeleted).not.toHaveBeenCalled()
  expect(screen.getByRole('button', { name: /Нарисуй котёнка llm/ })).toBeInTheDocument()
})
