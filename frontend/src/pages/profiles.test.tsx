// spec: version-zero
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ProfilesPage } from '@/pages/profiles'

const profile = { version: 1, name: 'demo', model_name: 'public-qwen', engine: { id: 'vllm', installation_id: 'inst_1' }, model: { path: '/models/space model' }, command: '{{engine}} serve --model {{model}}', environment: {}, autostart: false }
const engine = { id: 'vllm', display_name: 'vLLM', installation_kinds: ['binary'], installations: [{ id: 'inst_1', engine_id: 'vllm', kind: 'binary', root_path: '/opt/bin', executable_path: '/opt/bin/llama server', version: '1.0', status: 'ready', verified: true, diagnostic: null }] }
const envelope = (data: unknown) => JSON.stringify({ data, request_id: 'req_test' })

afterEach(() => vi.unstubAllGlobals())

describe('Profiles workflow', () => {
  it('creates a profile and edits it through the YAML profile form', async () => {
    let currentProfile = { ...profile }
    const fetchMock = vi.fn().mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      if (url.endsWith('/api/v1/profiles') && init?.method === 'POST') {
        currentProfile = JSON.parse(String(init.body))
        return Promise.resolve(new Response(envelope(currentProfile), { status: 201 }))
      }
      if (url.endsWith('/api/v1/profiles/demo') && init?.method === 'PUT') {
        currentProfile = JSON.parse(String(init.body))
        return Promise.resolve(new Response(envelope(currentProfile), { status: 200 }))
      }
      if (url.endsWith('/api/v1/profiles/demo')) return Promise.resolve(new Response(envelope(currentProfile), { status: 200 }))
      if (url.endsWith('/api/v1/profiles')) return Promise.resolve(new Response(envelope({ items: [currentProfile], issues: [] }), { status: 200 }))
      if (url.endsWith('/api/v1/engines')) return Promise.resolve(new Response(envelope({ items: [engine] }), { status: 200 }))
      if (url.endsWith('/api/v1/models/local')) return Promise.resolve(new Response(envelope({ items: [{ id: 'local_1', path: '/models/space model', kind: 'directory', format: 'transformers', compatibility: 'vllm', size_bytes: null, hf_match: { status: 'unknown' } }] }), { status: 200 }))
      return Promise.resolve(new Response(envelope({ items: [] }), { status: 200 }))
    })
    vi.stubGlobal('fetch', fetchMock)
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(<QueryClientProvider client={client}><MemoryRouter initialEntries={['/profiles']}><ProfilesPage /></MemoryRouter></QueryClientProvider>)
    fireEvent.click(await screen.findByRole('button', { name: 'Новый профиль' }))
    fireEvent.change(screen.getByLabelText('Имя профиля'), { target: { value: 'demo' } })
    fireEvent.change(screen.getByLabelText('Публичное имя модели'), { target: { value: 'public-qwen' } })
    fireEvent.change(screen.getByLabelText('Подтверждённая установка'), { target: { value: 'inst_1' } })
    fireEvent.change(screen.getByLabelText('Локальная модель'), { target: { value: '/models/space model' } })
    fireEvent.click(screen.getByRole('button', { name: 'Сохранить профиль' }))
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith('/api/v1/profiles', expect.objectContaining({ method: 'POST' })))
    fireEvent.click(await screen.findByRole('button', { name: 'Изменить' }))
    fireEvent.change(await screen.findByLabelText('Публичное имя модели'), { target: { value: 'public-qwen-edited' } })
    fireEvent.click(screen.getByRole('button', { name: 'Сохранить профиль' }))
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith('/api/v1/profiles/demo', expect.objectContaining({ method: 'PUT' })))
  })

  it('starts a profile when the start button is clicked', async () => {
    const fetchMock = vi.fn().mockImplementation((input: RequestInfo | URL) => {
      const url = String(input)
      if (url.endsWith('/api/v1/profiles')) return Promise.resolve(new Response(envelope({ items: [profile], issues: [] }), { status: 200 }))
      if (url.endsWith('/api/v1/engines')) return Promise.resolve(new Response(envelope({ items: [engine] }), { status: 200 }))
      if (url.endsWith('/api/v1/models/local')) return Promise.resolve(new Response(envelope({ items: [] }), { status: 200 }))
      if (url.endsWith('/start')) return Promise.resolve(new Response(envelope({ id: 'managed/demo/1-2', source: 'managed', profile_name: 'demo', engine_id: 'vllm', pid: 1, process_start_time_ns: 2, identity_verified: true, can_stop: true, port: 8000, model_name: 'public-qwen', state: 'starting', observed_at: '2026-09-29T00:00:00Z' }), { status: 202 }))
      return Promise.resolve(new Response(envelope({ items: [] }), { status: 200 }))
    })
    vi.stubGlobal('fetch', fetchMock)
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(<QueryClientProvider client={client}><MemoryRouter initialEntries={['/profiles']}><ProfilesPage /></MemoryRouter></QueryClientProvider>)
    fireEvent.click(await screen.findByRole('button', { name: 'Запустить' }))
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith('/api/v1/profiles/demo/start', expect.objectContaining({ method: 'POST' })))
  })
})
