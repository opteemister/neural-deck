// spec: version-zero
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ModelsPage } from '@/pages/models'

const envelope = (data: unknown) => JSON.stringify({ data, request_id: 'req_test' })
function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={client}><MemoryRouter initialEntries={['/models?view=registry']}><ModelsPage /></MemoryRouter></QueryClientProvider>)
}

afterEach(() => vi.unstubAllGlobals())

describe('Models workflow', () => {
  it('keeps registry, local catalogue and HF download routes distinct', async () => {
    const fetchMock = vi.fn().mockImplementation((input: RequestInfo | URL) => {
      const url = String(input)
      if (url.endsWith('/api/v1/models') && !url.endsWith('/local')) return Promise.resolve(new Response(envelope({ items: [{ id: 'public-qwen', upstream_model: 'Qwen/Qwen', instance_id: 'external/vllm/10-20', engine_id: 'vllm', profile_name: null, observed_at: '2026-09-29T00:00:00Z' }] }), { status: 200 }))
      if (url.endsWith('/api/v1/models/local')) return Promise.resolve(new Response(envelope({ items: [{ id: 'local_1', path: '/models/qwen', kind: 'directory', format: 'transformers', compatibility: 'vllm', size_bytes: null, hf_match: { status: 'unknown' } }] }), { status: 200 }))
      if (url.includes('/api/v1/models/hf/search')) return Promise.resolve(new Response(envelope({ items: [{ repo_id: 'org/qwen', revision: null, description: 'test model', tags: [] }] }), { status: 200 }))
      if (url.includes('/api/v1/models/hf/repos/org/qwen')) return Promise.resolve(new Response(envelope({ repo_id: 'org/qwen', revision: 'abc123', files: [{ path: 'config.json', size: 10, etag: null, sha256: null }, { path: 'weights.bin', size: null, etag: null, sha256: null }] }), { status: 200 }))
      if (url.endsWith('/api/v1/models/downloads')) return Promise.resolve(new Response(envelope({ id: 'op_download', kind: 'download', state: 'queued', target_id: 'org/qwen', created_at: '2026-09-29T00:00:00Z', started_at: null, finished_at: null, stdout: '', stderr: '', exit_code: null, error: null }), { status: 202 }))
      if (url.endsWith('/api/v1/models/local/scan')) return Promise.resolve(new Response(envelope({ items: [] }), { status: 200 }))
      return Promise.resolve(new Response(envelope({ items: [] }), { status: 200 }))
    })
    vi.stubGlobal('fetch', fetchMock)
    renderPage()
    expect(await screen.findByText('public-qwen')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Локальные' }))
    expect(await screen.findByText('/models/qwen')).toBeInTheDocument()
    expect(screen.getByText(/недоступно/)).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Hugging Face' }))
    fireEvent.change(screen.getByLabelText('Поиск Hugging Face'), { target: { value: 'qwen' } })
    fireEvent.click(screen.getByRole('button', { name: 'Искать' }))
    fireEvent.click(await screen.findByRole('button', { name: /org\/qwen/ }))
    expect(await screen.findByText('weights.bin')).toBeInTheDocument()
    expect(screen.getAllByText('недоступно').length).toBeGreaterThan(0)
    fireEvent.click(await screen.findByRole('button', { name: 'Весь snapshot' }))
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith('/api/v1/models/downloads', expect.objectContaining({ method: 'POST' })))
    expect(fetchMock.mock.calls.some(([input]) => String(input).endsWith('/api/v1/models'))).toBe(true)
    expect(fetchMock.mock.calls.some(([input]) => String(input).endsWith('/api/v1/models/local'))).toBe(true)
  })
})
