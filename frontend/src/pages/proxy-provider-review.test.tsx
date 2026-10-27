import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { api } from '@/lib/api'
import { ProxyPage } from '@/pages/proxy'

afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals() })

function renderProxy() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={client}><ProxyPage /></QueryClientProvider>)
}

const initialConfig = {
  sources: { local: { base_url: 'http://local-comfy' } },
  models: { cached: { source: 'local', type: 'image', workflows: { generate: 'workflows/cached.json' } } },
}

describe('proxy ComfyUI provider flow', () => {
  it('keeps settings editable with cached workflow paths while the source is offline', async () => {
    vi.spyOn(api, 'getProxyRoutes').mockResolvedValue({ items: [] })
    vi.spyOn(api, 'getProfiles').mockResolvedValue({ items: [], issues: [] })
    const fetchMock = vi.fn().mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      if (url === '/api/v1/proxy/comfyui' && init?.method === 'PUT') return Promise.resolve(new Response(JSON.stringify({ data: JSON.parse(String(init.body)) }), { status: 200 }))
      if (url === '/api/v1/proxy/comfyui') return Promise.resolve(new Response(JSON.stringify({ data: initialConfig }), { status: 200 }))
      if (url.includes('/sources/local/workflows')) return Promise.reject(new TypeError('offline'))
      return Promise.resolve(new Response(JSON.stringify({ data: { items: [] } }), { status: 200 }))
    })
    vi.stubGlobal('fetch', fetchMock)
    renderProxy()

    fireEvent.click(await screen.findByRole('tab', { name: 'Изображения' }))
    expect(await screen.findByText('Источники ComfyUI')).toBeInTheDocument()
    expect((await screen.findAllByText(/Список недоступен; сохранённый путь/)).length).toBeGreaterThan(0)

    const url = screen.getByLabelText('URL источника local')
    fireEvent.change(url, { target: { value: 'http://local-comfy-edited' } })
    const save = screen.getByRole('button', { name: 'Сохранить' })
    expect(save).toBeEnabled()
    fireEvent.click(save)
    await waitFor(() => expect(fetchMock.mock.calls.some(([input, init]) => String(input) === '/api/v1/proxy/comfyui' && init?.method === 'PUT')).toBe(true))
    const saved = fetchMock.mock.calls.find(([input, init]) => String(input) === '/api/v1/proxy/comfyui' && init?.method === 'PUT')?.[1]?.body
    expect(JSON.parse(String(saved)).sources.local.base_url).toBe('http://local-comfy-edited')
  })

  it('queries a newly saved source live when an alias is moved to it', async () => {
    vi.spyOn(api, 'getProxyRoutes').mockResolvedValue({ items: [] })
    vi.spyOn(api, 'getProfiles').mockResolvedValue({ items: [], issues: [] })
    const fetchMock = vi.fn().mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      if (url === '/api/v1/proxy/comfyui' && init?.method === 'PUT') return Promise.resolve(new Response(JSON.stringify({ data: JSON.parse(String(init.body)) }), { status: 200 }))
      if (url === '/api/v1/proxy/comfyui') return Promise.resolve(new Response(JSON.stringify({ data: { sources: { local: { base_url: 'http://local-comfy' } }, models: { existing: { source: 'local', type: 'image', workflows: {} } } } }), { status: 200 }))
      if (url.includes('/sources/remote/workflows')) return Promise.resolve(new Response(JSON.stringify({ data: { items: ['workflows/remote.json'], available: true, cached: false, updated_at: 2 } }), { status: 200 }))
      return Promise.resolve(new Response(JSON.stringify({ data: { items: [], available: false, cached: false, updated_at: null } }), { status: 200 }))
    })
    vi.stubGlobal('fetch', fetchMock)
    renderProxy()
    fireEvent.click(await screen.findByRole('tab', { name: 'Изображения' }))
    await screen.findByRole('button', { name: 'Добавить источник' })
    fireEvent.change(screen.getByLabelText('Новое имя источника'), { target: { value: 'remote' } })
    fireEvent.change(screen.getByLabelText('URL нового источника'), { target: { value: 'http://remote-comfy' } })
    fireEvent.click(screen.getByRole('button', { name: 'Добавить источник' }))
    fireEvent.click(screen.getByRole('button', { name: 'Сохранить' }))
    await waitFor(() => expect(fetchMock.mock.calls.some(([input, init]) => String(input) === '/api/v1/proxy/comfyui' && init?.method === 'PUT')).toBe(true))

    const sourceButton = screen.getByRole('button', { name: 'Источник алиаса existing' })
    const sourceSelect = sourceButton.parentElement?.querySelector('select')
    expect(sourceSelect).not.toBeNull()
    fireEvent.change(sourceSelect!, { target: { value: 'remote' } })
    await screen.findByDisplayValue('existing')
    await waitFor(() => expect(screen.getByRole('button', { name: 'Источник алиаса existing' })).toHaveTextContent('remote'))
    await screen.findByLabelText('Поиск workflow generate')
    await waitFor(() => expect(fetchMock.mock.calls.some(([input]) => String(input).includes('/sources/remote/workflows'))).toBe(true))
    expect((await screen.findAllByText('API доступен')).length).toBeGreaterThan(0)
  })
})
