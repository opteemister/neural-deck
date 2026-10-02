import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, expect, it, vi } from 'vitest'
import { queryKeys } from '@/lib/api'
import { ModelsPage } from '@/pages/models'

afterEach(() => vi.unstubAllGlobals())

it('requests active downloads and removes finished tasks across page reloads', async () => {
  let active = true
  const task = { id: 'download_1', kind: 'download', state: 'running', label: 'Test download', target_id: 'org/model', created_at: '2026-10-01T00:00:00Z', started_at: null, finished_at: null, progress: null, error: null }
  const fetchMock = vi.fn().mockImplementation((input: RequestInfo | URL) => {
    const url = String(input)
    const items = url === '/api/v1/operations?kind=download&active=true' && active ? [task] : []
    return Promise.resolve(new Response(JSON.stringify({ data: { items } })))
  })
  vi.stubGlobal('fetch', fetchMock)
  function mount() {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    const view = render(<QueryClientProvider client={client}><MemoryRouter initialEntries={['/models?view=local']}><ModelsPage /></MemoryRouter></QueryClientProvider>)
    return { client, view }
  }
  const first = mount()
  expect(await screen.findByText('Test download', { exact: false })).toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: 'Свернуть загрузки' }))
  active = false
  await first.client.invalidateQueries({ queryKey: queryKeys.downloads })
  await waitFor(() => expect(screen.queryByText('Загрузки')).not.toBeInTheDocument())
  expect(fetchMock).toHaveBeenCalledWith('/api/v1/operations?kind=download&active=true', expect.anything())
  first.view.unmount()
  first.client.clear()
  const second = mount()
  await waitFor(() => expect(second.client.getQueryData(queryKeys.downloads)).toEqual({ items: [] }))
  expect(screen.queryByText('Загрузки')).not.toBeInTheDocument()
  second.view.unmount()
  second.client.clear()
})
