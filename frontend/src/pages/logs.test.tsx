import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { LogsViewer } from './logs'

const source = { id: 'application', kind: 'application', label: 'Application', file_count: 2, updated_at: null }
const envelope = (data: unknown) => JSON.stringify({ data })
afterEach(() => { cleanup(); vi.unstubAllGlobals() })

function setup(latestStatus = 200) {
  const clipboard = vi.fn().mockResolvedValue(undefined)
  vi.stubGlobal('navigator', { clipboard: { writeText: clipboard } })
  const fetch = vi.fn().mockImplementation((url: string) => {
    if (url.endsWith('/download?latest=true')) return Promise.resolve(new Response('complete latest file\nincluding hidden lines\n', { status: latestStatus }))
    if (url.endsWith('/api/v1/logs')) return Promise.resolve(new Response(envelope({ items: [source] })))
    return Promise.resolve(new Response(envelope({ source, lines: [{ number: 0, text: 'old file text' }, { number: 1, text: 'visible tail' }], total_lines: 2, start_line: 0, end_line: 2, has_older: false })))
  })
  vi.stubGlobal('fetch', fetch)
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(<QueryClientProvider client={client}><LogsViewer drawer /></QueryClientProvider>)
  return { clipboard, fetch, client }
}

it('copies the latest full file even when the viewer is filtered', async () => {
  const { clipboard, fetch, client } = setup()
  await screen.findByText('visible tail')
  fireEvent.change(screen.getByRole('textbox', { name: 'Фильтр по тексту' }), { target: { value: 'visible' } })
  fireEvent.click(screen.getByRole('button', { name: 'Копировать последний файл лога' }))
  await waitFor(() => expect(clipboard).toHaveBeenCalledWith('complete latest file\nincluding hidden lines\n'))
  expect(fetch).toHaveBeenCalledWith('/api/v1/logs/application/download?latest=true')
  expect(screen.getByRole('button', { name: 'Последний файл скопирован' })).toBeInTheDocument()
  client.clear()
})

it('shows the floating button away from the bottom and jumps to the end', async () => {
  const { client } = setup()
  const log = await screen.findByLabelText('Содержимое лога')
  Object.defineProperties(log, { scrollHeight: { configurable: true, value: 1000 }, clientHeight: { configurable: true, value: 200 } })
  log.scrollTop = 300
  fireEvent.scroll(log)
  fireEvent.click(screen.getByRole('button', { name: 'Прокрутить лог до конца' }))
  expect(log.scrollTop).toBe(1000)
  expect(screen.queryByRole('button', { name: 'Прокрутить лог до конца' })).not.toBeInTheDocument()
  client.clear()
})

it('reports a copy error instead of showing success', async () => {
  const { clipboard, client } = setup(503)
  await screen.findByText('visible tail')
  fireEvent.click(screen.getByRole('button', { name: 'Копировать последний файл лога' }))
  expect(await screen.findByRole('alert')).toBeInTheDocument()
  expect(clipboard).not.toHaveBeenCalled()
  client.clear()
})
