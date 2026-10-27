import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { api } from '@/lib/api'
import type { ComfyConfig } from '@/lib/comfy-api'
import { ProxyPage } from '@/pages/proxy'

const initialConfig: ComfyConfig = {
  sources: { local: { base_url: 'http://local-comfy:8188/comfy/root', user_context: 'deck-user' } },
  models: {
    cached: {
      source: 'local', type: 'image',
      workflows: { generate: 'workflows/cached.json', edit_masked: 'workflows/masked.json' },
      bindings: { generate: { prompt: { node: '6', input: 'text' }, 'size.width': { node: '5', input: 'width' } } },
    },
    videoCached: { source: 'local', type: 'video', workflows: { generate: 'workflows/video.json', edit: 'workflows/i2v.json' } },
  },
}

afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals() })

function renderProxy() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={client}><MemoryRouter><ProxyPage /></MemoryRouter></QueryClientProvider>)
}

type WorkflowValidation = { usable: boolean | null; message?: string }

function mockProxy(config: ComfyConfig = initialConfig, validation: WorkflowValidation = { usable: null }) {
  vi.spyOn(api, 'getProxyRoutes').mockResolvedValue({ items: [] })
  vi.spyOn(api, 'getProfiles').mockResolvedValue({ items: [], issues: [] })
  const puts: unknown[] = []
  let current = structuredClone(config)
  const fetchMock = vi.fn().mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    if (url === '/api/v1/proxy/comfyui' && init?.method === 'PUT') {
      current = JSON.parse(String(init.body)) as typeof current
      puts.push(current)
      return new Response(JSON.stringify({ data: current }), { status: 200 })
    }
    if (url === '/api/v1/proxy/comfyui') return new Response(JSON.stringify({ data: current }), { status: 200 })
    if (url.includes('/sources/') && url.endsWith('/status')) return new Response(JSON.stringify({ data: { available: false, cached: true, updated_at: 1 } }), { status: 200 })
    if (url.includes('/sources/') && url.includes('/workflows/validate?path=')) {
      const path = decodeURIComponent(url.split('?path=')[1] ?? '')
      return new Response(JSON.stringify({ data: { path, usable: validation.usable, ...(validation.message ? { message: validation.message } : {}) } }), { status: 200 })
    }
    if (url.includes('/sources/') && url.endsWith('/workflows')) return new Response(JSON.stringify({ data: { items: [], available: false, cached: true, updated_at: 1 } }), { status: 200 })
    return new Response(JSON.stringify({ error: { message: `unexpected ${url}` } }), { status: 404 })
  })
  vi.stubGlobal('fetch', fetchMock)
  return { fetchMock, puts }
}

async function openMediaTab(name: 'Изображения' | 'Видео') {
  fireEvent.click(await screen.findByRole('tab', { name }))
  await screen.findByRole('region', { name: 'Подключения ComfyUI' })
}

function latestPut(puts: unknown[]) {
  const value = puts.at(-1)
  expect(value).toBeDefined()
  return value as ComfyConfig
}

describe('proxy ComfyUI provider configuration', () => {
  it('keeps offline aliases editable, preserves bindings on save, and leaves data untouched on cancel', async () => {
    const { fetchMock, puts } = mockProxy()
    renderProxy()
    await openMediaTab('Изображения')

    expect(screen.queryByLabelText('URL источника local')).not.toBeInTheDocument()
    expect(screen.queryByLabelText('Поиск workflow generate')).not.toBeInTheDocument()
    expect(screen.queryByText('Входы API')).not.toBeInTheDocument()
    expect(screen.queryByText('Generate')).not.toBeInTheDocument()
    expect(screen.queryByText('Edit')).not.toBeInTheDocument()
    expect(screen.queryByText('Mask')).not.toBeInTheDocument()
    expect(screen.queryByText('Variations')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Сохранить' })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Изменить подключение local' })).toBeInTheDocument()
    expect(screen.getByText('cached')).toBeInTheDocument()
    expect(fetchMock.mock.calls.some(([input]) => String(input).endsWith('/sources/local/status'))).toBe(true)

    fireEvent.click(screen.getByRole('button', { name: 'Изменить алиас cached' }))
    const aliasDialog = screen.getByRole('dialog')
    expect(within(aliasDialog).getByRole('heading', { name: 'Изменить алиас изображения' })).toBeInTheDocument()
    await waitFor(() => expect(fetchMock.mock.calls.some(([input]) => String(input).endsWith('/sources/local/workflows'))).toBe(true))
    const generate = aliasDialog.querySelector('select[aria-label="Workflow generate"]') as HTMLSelectElement
    expect(generate.value).toBe('workflows/cached.json')
    expect(Array.from(generate.options).map((option) => option.value)).toContain('workflows/cached.json')
    fireEvent.click(within(aliasDialog).getByRole('button', { name: 'Отмена' }))
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(puts).toHaveLength(0)

    fireEvent.click(screen.getByRole('button', { name: 'Изменить алиас cached' }))
    const editDialog = screen.getByRole('dialog')
    fireEvent.click(within(editDialog).getByRole('button', { name: 'Сохранить' }))
    await waitFor(() => expect(puts).toHaveLength(1))
    const saved = latestPut(puts)
    expect(saved.sources.local.user_context).toBe('deck-user')
    expect(saved.models.cached.workflows.edit_masked).toBe('workflows/masked.json')
    expect(saved.models.cached.bindings?.generate?.prompt).toEqual({ node: '6', input: 'text' })
    expect(saved.models.cached.workflows.generate).toBe('workflows/cached.json')
  })

  it('edits a connection as one full URL without losing its path or legacy user context', async () => {
    const { puts } = mockProxy()
    renderProxy()
    await openMediaTab('Изображения')

    fireEvent.click(screen.getByRole('button', { name: 'Изменить подключение local' }))
    let dialog = screen.getByRole('dialog')
    expect(within(dialog).getByRole('heading', { name: 'Изменить подключение' })).toBeInTheDocument()
    expect(within(dialog).getByLabelText('URL')).toHaveValue('http://local-comfy:8188/comfy/root')
    fireEvent.click(within(dialog).getByRole('button', { name: 'Отмена' }))
    expect(puts).toHaveLength(0)

    fireEvent.click(screen.getByRole('button', { name: 'Изменить подключение local' }))
    dialog = screen.getByRole('dialog')
    fireEvent.click(within(dialog).getByRole('button', { name: 'Сохранить' }))
    await waitFor(() => expect(puts).toHaveLength(1))
    const saved = latestPut(puts)
    expect(saved.sources.local.base_url).toBe('http://local-comfy:8188/comfy/root')
    expect(saved.sources.local.user_context).toBe('deck-user')
  })

  it('adds a connection in a modal, then selects it in a new alias and fetches its workflow catalog', async () => {
    const config: ComfyConfig = { sources: { local: { base_url: 'http://local-comfy' } }, models: { existing: { source: 'local', type: 'image', workflows: {}, bindings: { generate: { prompt: { node: '6', input: 'text' } } } } } }
    const { fetchMock, puts } = mockProxy(config)
    fetchMock.mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      if (url === '/api/v1/proxy/comfyui' && init?.method === 'PUT') {
        const body = JSON.parse(String(init.body))
        puts.push(body)
        return new Response(JSON.stringify({ data: body }), { status: 200 })
      }
      if (url === '/api/v1/proxy/comfyui') return new Response(JSON.stringify({ data: config }), { status: 200 })
      if (url.includes('/sources/') && url.includes('/workflows/validate?path=')) {
        const path = decodeURIComponent(url.split('?path=')[1] ?? '')
        return new Response(JSON.stringify({ data: { path, usable: null } }), { status: 200 })
      }
      if (url.endsWith('/sources/remote/workflows')) return new Response(JSON.stringify({ data: { items: ['workflows/remote.json'], available: true, cached: false, updated_at: 2 } }), { status: 200 })
      if (url.includes('/sources/') && url.endsWith('/workflows')) return new Response(JSON.stringify({ data: { items: [], available: false, cached: true, updated_at: 1 } }), { status: 200 })
      if (url.includes('/sources/') && url.endsWith('/status')) return new Response(JSON.stringify({ data: { available: true, cached: false, updated_at: 2 } }), { status: 200 })
      return new Response(JSON.stringify({ error: { message: `unexpected ${url}` } }), { status: 404 })
    })
    renderProxy()
    await openMediaTab('Изображения')

    fireEvent.click(screen.getByRole('button', { name: 'Добавить подключение' }))
    const sourceDialog = screen.getByRole('dialog')
    expect(within(sourceDialog).getByRole('heading', { name: 'Новое подключение' })).toBeInTheDocument()
    fireEvent.change(within(sourceDialog).getByLabelText('Имя'), { target: { value: 'remote' } })
    fireEvent.change(within(sourceDialog).getByLabelText('URL'), { target: { value: 'http://remote-comfy:8188' } })
    fireEvent.click(within(sourceDialog).getByRole('button', { name: 'Добавить подключение' }))
    await waitFor(() => expect(puts).toHaveLength(1))
    const sourceSaved = latestPut(puts)
    expect(sourceSaved.sources.remote.base_url).toBe('http://remote-comfy:8188')

    fireEvent.click(screen.getByRole('button', { name: 'Новый алиас' }))
    const aliasDialog = screen.getByRole('dialog')
    expect(within(aliasDialog).getByRole('heading', { name: 'Новый алиас изображения' })).toBeInTheDocument()
    fireEvent.change(within(aliasDialog).getByLabelText('Алиас'), { target: { value: 'remote-image' } })
    const source = aliasDialog.querySelector('select[aria-label="Подключение"]') as HTMLSelectElement
    fireEvent.change(source, { target: { value: 'remote' } })
    await waitFor(() => expect(fetchMock.mock.calls.some(([input]) => String(input).endsWith('/sources/remote/workflows'))).toBe(true))
    await waitFor(() => expect(aliasDialog.querySelector('select[aria-label="Workflow generate"] option[value="workflows/remote.json"]')).not.toBeNull())
    fireEvent.change(aliasDialog.querySelector('select[aria-label="Workflow generate"]')!, { target: { value: 'workflows/remote.json' } })
    fireEvent.click(within(aliasDialog).getByRole('button', { name: 'Сохранить' }))
    await waitFor(() => expect(puts).toHaveLength(2))
    const saved = latestPut(puts)
    expect(saved.models.existing.bindings?.generate?.prompt).toEqual({ node: '6', input: 'text' })
    expect(saved.models['remote-image']).toMatchObject({ source: 'remote', type: 'image', workflows: { generate: 'workflows/remote.json' } })
  })

  it('uses the same compact alias UX for video and does not offer image-only variations', async () => {
    const config: ComfyConfig = { sources: { local: { base_url: 'http://local-comfy' } }, models: { video: { source: 'local', type: 'video', workflows: { generate: 'workflows/video.json', edit: 'workflows/i2v.json' } } } }
    const { puts } = mockProxy(config)
    renderProxy()
    await openMediaTab('Видео')

    expect(screen.getByRole('button', { name: 'Изменить алиас video' })).toBeInTheDocument()
    expect(screen.queryByLabelText('Workflow variations')).not.toBeInTheDocument()
    expect(screen.queryByText(/Variations/i)).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Новый алиас' }))
    const dialog = screen.getByRole('dialog')
    expect(within(dialog).getByRole('heading', { name: 'Новый алиас видео' })).toBeInTheDocument()
    expect(dialog.querySelector('select[aria-label="Workflow generate"]')).toBeInTheDocument()
    expect(dialog.querySelector('select[aria-label="Workflow edit"]')).toBeInTheDocument()
    expect(within(dialog).queryByLabelText('Workflow variations')).not.toBeInTheDocument()
    expect(within(dialog).queryByText('Входы API')).not.toBeInTheDocument()
    fireEvent.click(within(dialog).getByRole('button', { name: 'Отмена' }))
    expect(puts).toHaveLength(0)
  })

  it('blocks an unusable workflow graph and keeps the alias draft open', async () => {
    const { fetchMock, puts } = mockProxy(initialConfig, { usable: false, message: 'Граф не содержит API-входы' })
    renderProxy()
    await openMediaTab('Изображения')

    fireEvent.click(screen.getByRole('button', { name: 'Изменить алиас cached' }))
    const dialog = screen.getByRole('dialog')
    fireEvent.click(within(dialog).getByRole('button', { name: 'Сохранить' }))

    await waitFor(() => expect(fetchMock.mock.calls.some(([input]) => String(input).includes('/sources/local/workflows/validate?path='))).toBe(true))
    expect(puts).toHaveLength(0)
    expect(screen.getByRole('dialog')).toBeInTheDocument()
    expect((screen.getByRole('dialog').querySelector('select[aria-label="Workflow generate"]') as HTMLSelectElement).value).toBe('workflows/cached.json')
    expect(await screen.findByRole('alert')).toHaveTextContent(/Generate: Граф не содержит API-входы/)
  })

  it('allows an offline usable-null validation and preserves legacy bindings', async () => {
    const config: ComfyConfig = {
      sources: { local: { base_url: 'http://local-comfy:8188/offline', user_context: 'legacy' } },
      models: {
        offline: {
          source: 'local', type: 'image', workflows: { generate: 'workflows/cached.json' },
          bindings: { generate: { prompt: { node: '6', input: 'text' } } },
        },
      },
    }
    const { fetchMock, puts } = mockProxy(config, { usable: null })
    renderProxy()
    await openMediaTab('Изображения')

    fireEvent.click(screen.getByRole('button', { name: 'Изменить алиас offline' }))
    fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Сохранить' }))
    await waitFor(() => expect(puts).toHaveLength(1))

    expect(fetchMock.mock.calls.some(([input]) => String(input).includes('/sources/local/workflows/validate?path='))).toBe(true)
    const saved = latestPut(puts)
    expect(saved.sources.local.user_context).toBe('legacy')
    expect(saved.models.offline.bindings?.generate?.prompt).toEqual({ node: '6', input: 'text' })
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })
})
