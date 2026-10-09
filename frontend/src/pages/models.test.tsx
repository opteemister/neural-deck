// spec: version-zero
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ModelsPage } from '@/pages/models'

const envelope = (data: unknown) => JSON.stringify({ data, request_id: 'req_test' })
function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={client}><MemoryRouter initialEntries={['/models']}><ModelsPage /></MemoryRouter></QueryClientProvider>)
}

afterEach(() => vi.unstubAllGlobals())

describe('Models workflow', () => {
  it('keeps local catalogue and HF download routes distinct', async () => {
    const fetchMock = vi.fn().mockImplementation((input: RequestInfo | URL) => {
      const url = String(input)
      if (url.endsWith('/api/v1/models/local')) return Promise.resolve(new Response(envelope({ items: [{ id: 'local_1', path: '/models/qwen', kind: 'directory', format: 'transformers', compatibility: 'vllm', size_bytes: null, hf_match: { status: 'unknown' } }] }), { status: 200 }))
      if (url.includes('/api/v1/models/hf/trending')) return Promise.resolve(new Response(envelope({ items: [{ repo_id: 'org/trending', revision: null, description: 'trending model', tags: ['text-generation'], author: 'org', avatar_url: null, downloads: 1234, likes: 42, trending_score: 9.5 }] }), { status: 200 }))
      if (url.includes('/api/v1/models/hf/search')) return Promise.resolve(new Response(envelope({ items: [{ repo_id: 'org/qwen', revision: null, description: 'test model', tags: [] }] }), { status: 200 }))
      if (url.includes('/api/v1/models/hf/repos/org/qwen')) return Promise.resolve(new Response(envelope({ repo_id: 'org/qwen', revision: 'abc123', files: [{ path: 'config.json', size: 10, etag: null, sha256: null }, { path: 'weights.bin', size: null, etag: null, sha256: null }] }), { status: 200 }))
      if (url.endsWith('/api/v1/models/downloads')) return Promise.resolve(new Response(envelope({ id: 'op_download', kind: 'download', state: 'queued', target_id: 'org/qwen', created_at: '2026-09-29T00:00:00Z', started_at: null, finished_at: null, stdout: '', stderr: '', exit_code: null, error: null }), { status: 202 }))
      if (url.endsWith('/api/v1/models/local/scan')) return Promise.resolve(new Response(envelope({ items: [] }), { status: 200 }))
      return Promise.resolve(new Response(envelope({ items: [] }), { status: 200 }))
    })
    vi.stubGlobal('fetch', fetchMock)
    renderPage()
    expect(await screen.findByText('/models/qwen')).toBeInTheDocument()
    expect(screen.getByText(/недоступно/)).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Hugging Face' }))
    fireEvent.change(screen.getByLabelText('Поиск Hugging Face'), { target: { value: 'qwen' } })
    fireEvent.click(screen.getByRole('button', { name: 'Искать' }))
    fireEvent.click(await screen.findByRole('button', { name: /org\/qwen/ }))
    fireEvent.click(await screen.findByRole('button', { name: 'Файлы' }))
    expect(await screen.findByText('weights.bin')).toBeInTheDocument()
    expect(screen.getAllByText('недоступно').length).toBeGreaterThan(0)
    fireEvent.click(await screen.findByRole('button', { name: 'Весь snapshot' }))
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith('/api/v1/models/downloads', expect.objectContaining({ method: 'POST' })))
    expect(fetchMock.mock.calls.some(([input]) => String(input).endsWith('/api/v1/models'))).toBe(false)
    expect(fetchMock.mock.calls.some(([input]) => String(input).endsWith('/api/v1/models/local'))).toBe(true)
  })

  it('loads HF trending groups and reuses the model card', async () => {
    const fetchMock = vi.fn().mockImplementation((input: RequestInfo | URL) => {
      const url = String(input)
      if (url.endsWith('/api/v1/models/local')) return Promise.resolve(new Response(envelope({ items: [] }), { status: 200 }))
      if (url.includes('/api/v1/models/hf/trending')) return Promise.resolve(new Response(envelope({ items: [{ repo_id: 'org/trending', revision: null, description: 'trending model', tags: ['text-generation'], author: 'org', avatar_url: null, downloads: 1234, likes: 42, trending_score: 9.5 }] }), { status: 200 }))
      return Promise.resolve(new Response(envelope({ items: [] }), { status: 200 }))
    })
    vi.stubGlobal('fetch', fetchMock)
    renderPage()
    fireEvent.click(screen.getByRole('button', { name: 'Hugging Face' }))
    expect(await screen.findByRole('button', { name: /org\/trending/ })).toBeInTheDocument()
    expect(screen.getByText('↓ 1 234')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'GGUF' })).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Текст' }))
    await waitFor(() => expect(fetchMock.mock.calls.filter(([input]) => String(input).includes('/api/v1/models/hf/trending')).length).toBe(1))
  })

  it('shows remote update state and dates without rendering hash values', async () => {
    const fetchMock = vi.fn().mockImplementation((input: RequestInfo | URL) => {
      const url = String(input)
      if (url.endsWith('/api/v1/models/local')) return Promise.resolve(new Response(envelope({ items: [{ id: 'local_1', path: '/models/qwen', kind: 'directory', format: 'transformers', compatibility: 'vllm', size_bytes: 100, hf_match: { status: 'downloaded', repo_id: 'org/qwen', scope: 'snapshot', downloaded_revision_date: '2026-10-01T12:00:00Z', remote_revision_date: '2026-10-03T12:00:00Z', remote_check: { availability: 'success', last_attempted_at: '2026-10-04T10:00:00Z', last_successful_at: '2026-10-04T10:00:01Z' }, remote_update: { status: 'update_available', changed_files: 1 }, changed_paths: [{ path: 'weights.gguf', status: 'changed', remote_file_date: '2026-10-03T11:00:00Z' }] } }] }), { status: 200 }))
      return Promise.resolve(new Response(envelope({ items: [] }), { status: 200 }))
    })
    vi.stubGlobal('fetch', fetchMock)
    renderPage()

    expect(await screen.findByText('Доступно обновление · 1 файлов')).toBeInTheDocument()
    const hfLink = screen.getByRole('link', { name: 'Открыть модель на Hugging Face' })
    expect(hfLink.nextElementSibling).toHaveTextContent('Доступно обновление · 1 файлов')
    expect(screen.queryByText('Сведения об обновлении')).not.toBeInTheDocument()
    expect(screen.queryByText('Проверка HF недоступна')).not.toBeInTheDocument()
    expect(screen.queryByText(/sha256|etag|hash/i)).not.toBeInTheDocument()
  })
})
