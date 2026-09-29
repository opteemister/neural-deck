// spec: version-zero
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { EnginesPage } from '@/pages/engines'

const engine = { id: 'vllm', display_name: 'vLLM', installation_kinds: ['python_venv'], installations: [] }
const envelope = (data: unknown) => JSON.stringify({ data, request_id: 'req_test' })

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={client}><MemoryRouter initialEntries={['/engines']}><EnginesPage /></MemoryRouter></QueryClientProvider>)
}

afterEach(() => vi.unstubAllGlobals())

describe('Engines workflow', () => {
  it('scans the engine catalogue and reports a created operation', async () => {
    const fetchMock = vi.fn().mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      if (url.endsWith('/api/v1/engines/scan')) return Promise.resolve(new Response(envelope({ items: [engine] }), { status: 200 }))
      if (url.endsWith('/api/v1/engines/vllm/installations') && init?.method === 'POST') return Promise.resolve(new Response(envelope({ id: 'op_install', kind: 'install', state: 'queued', target_id: 'vllm', created_at: '2026-09-29T00:00:00Z', started_at: null, finished_at: null, stdout: '', stderr: '', exit_code: null, error: null }), { status: 202 }))
      if (url.includes('/api/v1/operations/op_install')) return Promise.resolve(new Response(envelope({ id: 'op_install', kind: 'install', state: 'succeeded', target_id: 'vllm', created_at: '2026-09-29T00:00:00Z', started_at: null, finished_at: '2026-09-29T00:00:01Z', stdout: 'done', stderr: '', exit_code: 0, error: null }), { status: 200 }))
      return Promise.resolve(new Response(envelope({ items: [engine] }), { status: 200 }))
    })
    vi.stubGlobal('fetch', fetchMock)
    renderPage()
    await screen.findByRole('heading', { name: 'vLLM' })
    fireEvent.click(screen.getByRole('button', { name: 'Повторить поиск' }))
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith('/api/v1/engines/scan', expect.objectContaining({ method: 'POST' })))
    fireEvent.change(screen.getByLabelText('Имя venv'), { target: { value: 'vllm-test' } })
    fireEvent.click(screen.getByRole('button', { name: 'Создать окружение' }))
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith('/api/v1/engines/vllm/installations', expect.objectContaining({ method: 'POST' })))
    expect(await screen.findByText('op_install')).toBeInTheDocument()
  })
})
