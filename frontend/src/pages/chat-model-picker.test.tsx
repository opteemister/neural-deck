import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, expect, it, vi } from 'vitest'
import { api } from '@/lib/api'
import { ChatPage } from '@/pages/chat'

afterEach(() => vi.restoreAllMocks())

it('requests models on every picker opening and has no retry button', async () => {
  const getModels = vi.spyOn(api, 'getOpenAiModels')
    .mockRejectedValueOnce(new Error('Engine is starting'))
    .mockResolvedValue({ items: [{ id: 'ready-model', object: 'model', types: ['text'] }] })
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const view = render(<QueryClientProvider client={client}><MemoryRouter><ChatPage /></MemoryRouter></QueryClientProvider>)
  await waitFor(() => expect(client.getQueryState(['openai-models'])?.status).toBe('error'))
  const trigger = screen.getByRole('button', { name: 'Выбрать модель' })
  fireEvent.click(trigger)
  await screen.findByRole('radio', { name: 'ready-model' })
  expect(getModels).toHaveBeenNthCalledWith(1, true)
  expect(getModels).toHaveBeenNthCalledWith(2, true)
  expect(getModels).toHaveBeenCalledTimes(2)
  expect(screen.queryByRole('button', { name: 'Повторить' })).not.toBeInTheDocument()
  fireEvent.click(trigger)
  expect(getModels).toHaveBeenCalledTimes(2)
  fireEvent.click(trigger)
  await waitFor(() => expect(getModels).toHaveBeenCalledTimes(3))
  view.unmount()
  client.clear()
})
