// spec: version-zero
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { LogsDrawerProvider } from '@/components/logs-drawer'
import { ProfilesPage } from '@/pages/profiles'

const profile = { version: 1, name: 'demo', model_name: 'public-qwen', engine: { id: 'vllm', installation_id: 'inst_1' }, model: { path: '/models/space model' }, command: '{{engine}} serve --model {{model}}', environment: {}, autostart: false }
const engine = { id: 'vllm', display_name: 'vLLM', command_template: profile.command, installation_kinds: ['binary'], installations: [{ id: 'inst_1', engine_id: 'vllm', kind: 'binary', root_path: '/opt/bin', executable_path: '/opt/bin/llama server', version: '1.0', status: 'ready', verified: true, diagnostic: null }] }
const envelope = (data: unknown) => JSON.stringify({ data, request_id: 'req_test' })

afterEach(() => vi.unstubAllGlobals())

describe('Profiles workflow', () => {
  it.each([['llama.cpp', '--metrics'], ['sglang', '--enable-metrics']])('shows the saved final command for %s with metrics', async (engineId, flag) => {
    const command = '{{engine}} --model {{model}}'
    let savedProfile = { ...profile, engine: { ...profile.engine, id: engineId }, command, enable_metrics: true }
    const selectedEngine = { ...engine, id: engineId, command_template: command, installations: engine.installations.map((item) => ({ ...item, engine_id: engineId })) }
    const fetchMock = vi.fn().mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      if (url.endsWith('/api/v1/profiles/demo')) {
        if (init?.method === 'PUT') savedProfile = { ...JSON.parse(String(init.body)), command: `${command} ${flag}` }
        return Promise.resolve(new Response(envelope(savedProfile), { status: 200 }))
      }
      if (url.endsWith('/api/v1/profiles')) return Promise.resolve(new Response(envelope({ items: [savedProfile], issues: [] }), { status: 200 }))
      if (url.endsWith('/api/v1/engines')) return Promise.resolve(new Response(envelope({ items: [selectedEngine] }), { status: 200 }))
      if (url.endsWith('/api/v1/system')) return Promise.resolve(new Response(envelope({ cpu: { cores: 8, usage_percent: 10 }, ram: { used_bytes: 10, total_bytes: 100 }, gpus: [], instances: [] }), { status: 200 }))
      if (url.endsWith('/api/v1/settings')) return Promise.resolve(new Response(envelope({ models_root: '/models' }), { status: 200 }))
      return Promise.resolve(new Response(envelope({ items: [] }), { status: 200 }))
    })
    vi.stubGlobal('fetch', fetchMock)
    render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><MemoryRouter><LogsDrawerProvider><ProfilesPage /></LogsDrawerProvider></MemoryRouter></QueryClientProvider>)
    expect((await screen.findByTestId('profile-summary-metrics')).children).toHaveLength(8)
    fireEvent.click(await screen.findByRole('button', { name: 'Изменить' }))
    expect(await screen.findByRole('checkbox', { name: 'Включить метрики' })).toBeChecked()
    await waitFor(() => expect(screen.getByLabelText('Команда запуска')).toHaveValue(command))
    fireEvent.click(screen.getByRole('button', { name: 'Сохранить профиль' }))
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith('/api/v1/profiles/demo', expect.objectContaining({ method: 'PUT', body: expect.stringContaining('"enable_metrics":true') })))
    fireEvent.click(await screen.findByRole('button', { name: 'Развернуть' }))
    expect(await screen.findByText(`'/opt/bin/llama server' --model '/models/space model' ${flag}`)).toBeVisible()
  })

  it('creates a profile and edits it through the YAML profile form', async () => {
    let currentProfile = { ...profile, enable_metrics: true }
    const fetchMock = vi.fn().mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      if (url.endsWith('/api/v1/system')) return Promise.resolve(new Response(envelope({ cpu: { cores: 8, usage_percent: 10 }, ram: { used_bytes: 10, total_bytes: 100 }, gpus: [], instances: [] }), { status: 200 }))
      if (url.endsWith('/api/v1/settings')) return Promise.resolve(new Response(envelope({ models_root: '/models' }), { status: 200 }))
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
      if (url.endsWith('/api/v1/models/local')) return Promise.resolve(new Response(envelope({ items: [{ id: 'local_1', path: '/models/space model', kind: 'directory', format: 'transformers', compatibility: 'vllm', size_bytes: null, hf_match: { status: 'unknown' }, variants: [], auxiliary_files: [], groups: [{ id: 'transformers', label: 'Модель', path: '', status: 'complete', files: [{ path: 'model.safetensors', format: 'safetensors', size_bytes: 100 }], file_count: 1, size_bytes: 100, deletable: true }] }] }), { status: 200 }))
      return Promise.resolve(new Response(envelope({ items: [] }), { status: 200 }))
    })
    vi.stubGlobal('fetch', fetchMock)
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(<QueryClientProvider client={client}><MemoryRouter initialEntries={['/profiles']}><LogsDrawerProvider><ProfilesPage /></LogsDrawerProvider></MemoryRouter></QueryClientProvider>)
    fireEvent.click(await screen.findByRole('button', { name: 'Новый профиль' }))
    expect(screen.getByRole('checkbox', { name: 'Включить метрики' })).toBeChecked()
    expect(screen.getByRole('button', { name: 'Информация о включении метрик' })).toHaveAttribute('aria-describedby', 'profile-metrics-info')
    fireEvent.change(screen.getByLabelText('Имя профиля'), { target: { value: 'demo' } })
    fireEvent.change(screen.getByLabelText('Публичное имя модели'), { target: { value: 'public-qwen' } })
    fireEvent.change(screen.getByLabelText('Подтверждённая установка'), { target: { value: 'inst_1' } })
    fireEvent.change(screen.getByLabelText('Локальная модель'), { target: { value: '/models/space model' } })
    fireEvent.click(screen.getByRole('button', { name: 'Сохранить профиль' }))
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith('/api/v1/profiles', expect.objectContaining({ method: 'POST' })))
    fireEvent.click(await screen.findByRole('button', { name: 'Изменить' }))
    fireEvent.change(await screen.findByLabelText('Публичное имя модели'), { target: { value: 'public-qwen-edited' } })
    expect(screen.getByRole('checkbox', { name: 'Включить метрики' })).toBeChecked()
    fireEvent.click(screen.getByRole('checkbox', { name: 'Включить метрики' }))
    fireEvent.click(screen.getByRole('button', { name: 'Сохранить профиль' }))
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith('/api/v1/profiles/demo', expect.objectContaining({ method: 'PUT' })))
    expect(currentProfile.enable_metrics).toBe(false)
    expect(currentProfile.command).toBe(profile.command)
    fireEvent.click(await screen.findByRole('button', { name: 'Изменить' }))
    expect(await screen.findByRole('checkbox', { name: 'Включить метрики' })).not.toBeChecked()
  })

  it('starts a profile when the start button is clicked', async () => {
    const fetchMock = vi.fn().mockImplementation((input: RequestInfo | URL) => {
      const url = String(input)
      if (url.endsWith('/api/v1/system')) return Promise.resolve(new Response(envelope({ cpu: { cores: 8, usage_percent: 10 }, ram: { used_bytes: 10, total_bytes: 100 }, gpus: [], instances: [] }), { status: 200 }))
      if (url.endsWith('/api/v1/settings')) return Promise.resolve(new Response(envelope({ models_root: '/models' }), { status: 200 }))
      if (url.endsWith('/api/v1/profiles')) return Promise.resolve(new Response(envelope({ items: [profile], issues: [] }), { status: 200 }))
      if (url.endsWith('/api/v1/engines')) return Promise.resolve(new Response(envelope({ items: [engine] }), { status: 200 }))
      if (url.endsWith('/api/v1/models/local')) return Promise.resolve(new Response(envelope({ items: [] }), { status: 200 }))
      if (url.endsWith('/start')) return Promise.resolve(new Response(envelope({ id: 'managed/demo/1-2', source: 'managed', profile_name: 'demo', engine_id: 'vllm', pid: 1, process_start_time_ns: 2, identity_verified: true, can_stop: true, port: 8000, model_name: 'public-qwen', state: 'starting', observed_at: '2026-09-29T00:00:00Z' }), { status: 202 }))
      return Promise.resolve(new Response(envelope({ items: [] }), { status: 200 }))
    })
    vi.stubGlobal('fetch', fetchMock)
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(<QueryClientProvider client={client}><MemoryRouter initialEntries={['/profiles']}><LogsDrawerProvider><ProfilesPage /></LogsDrawerProvider></MemoryRouter></QueryClientProvider>)
    fireEvent.click(await screen.findByRole('button', { name: 'Запустить' }))
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith('/api/v1/profiles/demo/start', expect.objectContaining({ method: 'POST' })))
  })
})
