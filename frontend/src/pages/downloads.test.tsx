import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, expect, it, vi } from 'vitest'
import { queryKeys } from '@/lib/api'
import { ModelsPage } from '@/pages/models'

afterEach(() => vi.unstubAllGlobals())

it('keeps finished downloads visible until the user closes the panel', async () => {
  let active = true
  const task = { id: 'download_1', kind: 'download', state: 'running', label: 'Test download', target_id: 'org/model', created_at: '2026-10-01T00:00:00Z', started_at: null, finished_at: null, progress: null, error: null }
  const fetchMock = vi.fn().mockImplementation((input: RequestInfo | URL) => {
    const url = String(input)
    if (url === '/api/v1/operations/download_1') return Promise.resolve(new Response(JSON.stringify({ data: { ...task, state: 'succeeded', finished_at: '2026-10-01T00:01:00Z', progress: { files_total: 1, files_completed: 1, bytes_total: 100, bytes_completed: 100, current_file: null } } })))
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
  active = false
  await first.client.invalidateQueries({ queryKey: queryKeys.downloads })
  await waitFor(() => expect(screen.getByText('Test download', { exact: false })).toBeInTheDocument())
  await waitFor(() => expect(screen.getByRole('button', { name: 'Закрыть блок загрузок' })).toBeInTheDocument())
  fireEvent.click(screen.getByRole('button', { name: 'Закрыть блок загрузок' }))
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
