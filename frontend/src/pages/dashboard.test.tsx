// spec: version-zero
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter, useLocation } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { DashboardPage } from '@/pages/dashboard'

const openLogs = vi.hoisted(() => vi.fn())
vi.mock('@/components/logs-drawer', () => ({ useLogsDrawer: () => openLogs }))

const external = { id: 'external/vllm/10-20', source: 'external', profile_name: null, engine_id: 'vllm', pid: 10, process_start_time_ns: 20, port: 8000, model_name: 'public-qwen', state: 'ready', observed_at: '2026-09-29T00:00:00Z', can_stop: true, identity_verified: true }
const envelope = (data: unknown) => JSON.stringify({ data, request_id: 'req_test' })
const emptyMetrics = { items: [] }

function LocationProbe() {
  return <span data-testid="location-state">{useLocation().search}</span>
}

function dashboardClient() {
  return new QueryClient({ defaultOptions: { queries: { retry: false } } })
}

afterEach(() => vi.unstubAllGlobals())

describe('Dashboard workflow', () => {
  it('sorts run tabs by active state, then puts text runs first', async () => {
    const activeImage = { ...external, id: 'managed/image/1-10', source: 'managed', profile_name: 'Alpha image', model_name: 'image-model', types: ['image'] }
    const activeText = { ...external, id: 'managed/text/2-20', source: 'managed', profile_name: 'Zulu text', model_name: 'text-model', types: ['text'] }
    const stoppedProfile = { version: 1, name: 'Beta stopped', model_name: 'stopped-model', engine: { id: 'vllm', installation_id: 'inst_1' }, command: '{{engine}} serve', environment: {}, autostart: false }
    const metric = (value: number) => ({ value, unit: 'tok/s', state: 'ok', source: 'sqlite' })
    const summary = { profile_name: stoppedProfile.name, period: { from: '2026-10-13T00:00:00Z', to: '2026-10-13T12:00:00Z', timezone: 'UTC' }, performance: { prefill_tps: metric(120), decode_tps: metric(30) }, memory: {}, concurrency: {}, load: {}, endpoint: { port: 8000, state: 'ok', source: 'command', conflict: false, conflict_with: [] } }
    const fetchMock = vi.fn().mockImplementation((input: RequestInfo | URL) => {
      const url = String(input)
      if (url.endsWith('/api/v1/system')) return Promise.resolve(new Response(envelope({ cpu: { cores: 8, usage_percent: 10 }, ram: { used_bytes: 10, total_bytes: 100 }, gpus: [], instances: [activeImage, activeText] }), { status: 200 }))
      if (url.endsWith('/api/v1/profiles/summary')) return Promise.resolve(new Response(envelope({ items: [summary], period: summary.period, source: 'sqlite' }), { status: 200 }))
      if (url.endsWith('/api/v1/profiles')) return Promise.resolve(new Response(envelope({ items: [stoppedProfile], issues: [] }), { status: 200 }))
      if (url.includes('/api/v1/metrics/concurrency')) return Promise.resolve(new Response(envelope({ items: [], days: 7, source: 'sqlite' }), { status: 200 }))
      if (url.includes('/api/v1/metrics?')) return Promise.resolve(new Response(envelope({ history: [], current: null, peaks: {}, aggregates: {}, source: 'sqlite' }), { status: 200 }))
      if (url.includes('/metrics')) return Promise.resolve(new Response(envelope(emptyMetrics), { status: 200 }))
      return Promise.resolve(new Response(envelope({ items: [] }), { status: 200 }))
    })
    vi.stubGlobal('fetch', fetchMock)
    render(<QueryClientProvider client={dashboardClient()}><MemoryRouter><DashboardPage /></MemoryRouter></QueryClientProvider>)
    const tabs = await screen.findAllByRole('tab')
    expect(tabs).toHaveLength(4)
    expect(tabs[0]).toHaveTextContent('Все')
    expect(tabs[1]).toHaveTextContent('Zulu text')
    expect(tabs[2]).toHaveTextContent('Alpha image')
    expect(tabs[3]).toHaveTextContent('Beta stopped')
  })

  it('keeps a stopped profile tab when it has performance metrics for the current day', async () => {
    const stoppedProfile = { version: 1, name: 'stopped-qwen', model_name: 'qwen-local', engine: { id: 'vllm', installation_id: 'inst_1' }, command: '{{engine}} serve', environment: {}, autostart: false }
    const metric = (value: number | null) => ({ value, unit: 'tok/s', state: value === null ? 'unavailable' : 'ok', source: value === null ? 'unavailable' : 'sqlite' })
    const summary = { profile_name: stoppedProfile.name, period: { from: '2026-10-13T00:00:00Z', to: '2026-10-13T12:00:00Z', timezone: 'UTC' }, performance: { prefill_tps: metric(120), decode_tps: metric(30) }, memory: {}, concurrency: {}, load: {}, endpoint: { port: 8000, state: 'ok', source: 'command', conflict: false, conflict_with: [] } }
    const fetchMock = vi.fn().mockImplementation((input: RequestInfo | URL) => {
      const url = String(input)
      if (url.endsWith('/api/v1/system')) return Promise.resolve(new Response(envelope({ cpu: { cores: 8, usage_percent: 10 }, ram: { used_bytes: 10, total_bytes: 100 }, gpus: [], instances: [] }), { status: 200 }))
      if (url.endsWith('/api/v1/profiles/summary')) return Promise.resolve(new Response(envelope({ items: [summary], period: summary.period, source: 'sqlite' }), { status: 200 }))
      if (url.endsWith('/api/v1/profiles')) return Promise.resolve(new Response(envelope({ items: [stoppedProfile], issues: [] }), { status: 200 }))
      if (url.includes('/api/v1/metrics/concurrency')) return Promise.resolve(new Response(envelope({ items: [], days: 7, source: 'sqlite' }), { status: 200 }))
      if (url.includes('/api/v1/metrics?')) return Promise.resolve(new Response(envelope({ history: [], current: null, peaks: {}, aggregates: {}, source: 'sqlite' }), { status: 200 }))
      return Promise.resolve(new Response(envelope({ items: [] }), { status: 200 }))
    })
    vi.stubGlobal('fetch', fetchMock)
    render(<QueryClientProvider client={dashboardClient()}><MemoryRouter initialEntries={[`/?instance=${encodeURIComponent('profile:stopped-qwen')}`]}><DashboardPage /></MemoryRouter></QueryClientProvider>)
    const tab = await screen.findByRole('tab', { name: /stopped-qwen/ })
    expect(tab).toHaveAttribute('aria-selected', 'true')
    expect(tab.querySelector('[data-run-status="inactive"]')).toBeInTheDocument()
    expect(await screen.findByText('сохранённые метрики за сегодня')).toBeInTheDocument()
    await waitFor(() => expect(fetchMock.mock.calls.some(([input]) => String(input).includes('profile_name=stopped-qwen'))).toBe(true))
  })

  it('shows the engine UI and hides text performance for a non-text run', async () => {
    const image = { ...external, id: 'managed/ComfyUI/75-90', source: 'managed', profile_name: 'ComfyUI', engine_id: 'comfyui', model_name: 'qwen-image', types: ['image', 'video'], api_protocol: 'comfyui', api_base_url: 'http://127.0.0.1:8188', ui_url: 'http://127.0.0.1:8188', model_paths_restart_required: false }
    const fetchMock = vi.fn().mockImplementation((input: RequestInfo | URL) => {
      const url = String(input)
      if (url.includes('/api/v1/metrics/concurrency')) return Promise.resolve(new Response(envelope({ items: [], days: 7, source: 'sqlite' }), { status: 200 }))
      if (url.endsWith('/api/v1/system')) return Promise.resolve(new Response(envelope({ cpu: { cores: 8, usage_percent: 10 }, ram: { used_bytes: 10, total_bytes: 100 }, gpus: [], instances: [image] }), { status: 200 }))
      if (url.includes('/api/v1/metrics?')) return Promise.resolve(new Response(envelope({ history: [], current: null, peaks: {}, source: 'sqlite' }), { status: 200 }))
      if (url.includes('/metrics')) return Promise.resolve(new Response(envelope(emptyMetrics), { status: 200 }))
      if (url.includes('/logs')) return Promise.resolve(new Response(envelope({ instance_id: image.id, lines: [], truncated: false }), { status: 200 }))
      return Promise.resolve(new Response(envelope({ items: [] }), { status: 200 }))
    })
    vi.stubGlobal('fetch', fetchMock)
    render(<QueryClientProvider client={dashboardClient()}><MemoryRouter initialEntries={[`/?instance=${encodeURIComponent(image.id)}`]}><DashboardPage /></MemoryRouter></QueryClientProvider>)
    expect((await screen.findByRole('tab', { name: /ComfyUI/ })).querySelector('[data-run-status="active"]')).toBeInTheDocument()
    expect(await screen.findByRole('link', { name: 'Открыть UI' })).toHaveAttribute('href', 'http://127.0.0.1:8188')
    expect(screen.queryByText('Decode')).not.toBeInTheDocument()
    expect(screen.queryByText('Prefill')).not.toBeInTheDocument()
    expect(screen.getByText('Логи запуска')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('tab', { name: /Все/ }))
    const row = (await screen.findByRole('button', { name: 'ComfyUI' })).closest('tr')!
    expect(within(row).getByRole('link', { name: 'Открыть UI ComfyUI' })).toHaveAttribute('href', 'http://127.0.0.1:8188')
  })

  it('offers stop for an external instance and surfaces the operation contract', async () => {
    const operation = { id: 'op_stop', kind: 'stop_instance', state: 'succeeded', target_id: external.id, created_at: '2026-09-29T00:00:00Z', started_at: null, finished_at: '2026-09-29T00:00:01Z', stdout: '', stderr: '', exit_code: 0, error: null }
    const fetchMock = vi.fn().mockImplementation((input: RequestInfo | URL) => {
      const url = String(input)
      if (url.includes('/api/v1/metrics/concurrency')) return Promise.resolve(new Response(envelope({ items: [], days: 7, source: 'sqlite' }), { status: 200 }))
      if (url.endsWith('/api/v1/system')) return Promise.resolve(new Response(envelope({ cpu: { cores: 8, usage_percent: 10 }, ram: { used_bytes: 10, total_bytes: 100 }, gpus: [], instances: [external] }), { status: 200 }))
      if (url.endsWith('/api/v1/status')) return Promise.resolve(new Response(envelope({ overall: 'ok', components: [] }), { status: 200 }))
      if (url.endsWith('/api/v1/health')) return Promise.resolve(new Response(envelope({ status: 'ok' }), { status: 200 }))
      if (url.includes('/api/v1/metrics?')) return Promise.resolve(new Response(envelope({ history: [], current: null, peaks: {}, source: 'sqlite' }), { status: 200 }))
      if (url.includes('/metrics')) return Promise.resolve(new Response(envelope({ items: [] }), { status: 200 }))
      if (url.includes('/logs')) return Promise.resolve(new Response(envelope({ instance_id: external.id, lines: [], truncated: false }), { status: 200 }))
      if (url.includes('/token-stats')) return Promise.resolve(new Response(envelope({ items: [] }), { status: 200 }))
      if (url.endsWith('/stop')) return Promise.resolve(new Response(envelope(operation), { status: 202 }))
      if (url.endsWith('/api/v1/operations/op_stop')) return Promise.resolve(new Response(envelope(operation), { status: 200 }))
      return Promise.resolve(new Response(envelope({ items: [] }), { status: 200 }))
    })
    vi.stubGlobal('fetch', fetchMock)
    const client = dashboardClient()
    render(<QueryClientProvider client={client}><MemoryRouter initialEntries={['/']}><DashboardPage /></MemoryRouter></QueryClientProvider>)
    expect(await screen.findByRole('tab', { name: /Все/ })).toHaveAttribute('aria-selected', 'true')
    expect(screen.queryByText('Parallel')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Остановить внешний' })).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('tab', { name: /public-qwen/ }))
    fireEvent.click(await screen.findByRole('button', { name: 'Остановить внешний' }))
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(`/api/v1/instances/${encodeURIComponent(external.id)}/stop`, expect.objectContaining({ method: 'POST' })))
    const banner = await screen.findByText(/Остановка экземпляра/)
    expect(banner).toHaveTextContent(external.id)
    expect(within(banner.parentElement!).getByText('Завершено')).toBeInTheDocument()
    expect(fetchMock.mock.calls.some(([input]) => String(input).endsWith('/api/v1/operations/op_stop'))).toBe(true)
  })

  it('keeps an incomplete external observation read-only', async () => {
    const incomplete = { ...external, id: 'external/vllm/10-unknown', state: 'unknown', process_start_time_ns: null, can_stop: false, identity_verified: false }
    const fetchMock = vi.fn().mockImplementation((input: RequestInfo | URL) => {
      const url = String(input)
      if (url.includes('/api/v1/metrics/concurrency')) return Promise.resolve(new Response(envelope({ items: [], days: 7, source: 'sqlite' }), { status: 200 }))
      if (url.endsWith('/api/v1/system')) return Promise.resolve(new Response(envelope({ cpu: { cores: 8, usage_percent: null }, ram: { used_bytes: 10, total_bytes: 100 }, gpus: [], instances: [incomplete] }), { status: 200 }))
      if (url.endsWith('/api/v1/status')) return Promise.resolve(new Response(envelope({ overall: 'ok', components: [] }), { status: 200 }))
      if (url.endsWith('/api/v1/health')) return Promise.resolve(new Response(envelope({ status: 'ok' }), { status: 200 }))
      if (url.includes('/api/v1/metrics?')) return Promise.resolve(new Response(envelope({ history: [], current: null, peaks: {}, source: 'sqlite' }), { status: 200 }))
      if (url.includes('/metrics')) return Promise.resolve(new Response(envelope(emptyMetrics), { status: 200 }))
      if (url.includes('/logs')) return Promise.resolve(new Response(envelope({ instance_id: incomplete.id, lines: [], truncated: false }), { status: 200 }))
      if (url.includes('/token-stats')) return Promise.resolve(new Response(envelope({ items: [] }), { status: 200 }))
      return Promise.resolve(new Response(envelope({ items: [] }), { status: 200 }))
    })
    vi.stubGlobal('fetch', fetchMock)
    render(<QueryClientProvider client={dashboardClient()}><MemoryRouter initialEntries={[`/?instance=${encodeURIComponent(incomplete.id)}`]}><DashboardPage /></MemoryRouter></QueryClientProvider>)
    const stopButton = await screen.findByRole('button', { name: 'Остановить внешний' })
    expect(stopButton).toBeDisabled()
    expect(stopButton).toHaveAttribute('title', 'Backend не подтвердил право остановки этого процесса')
    expect(screen.getByRole('tab', { name: /public-qwen/ })).toHaveAttribute('aria-selected', 'true')
    fireEvent.click(stopButton)
    expect(fetchMock.mock.calls.some(([input]) => String(input).endsWith('/stop'))).toBe(false)
  })

  it('keeps an external observation owned by another user read-only', async () => {
    const otherUser = { ...external, id: 'external/vllm/10-other-user', can_stop: false, identity_verified: true }
    const fetchMock = vi.fn().mockImplementation((input: RequestInfo | URL) => {
      const url = String(input)
      if (url.includes('/api/v1/metrics/concurrency')) return Promise.resolve(new Response(envelope({ items: [], days: 7, source: 'sqlite' }), { status: 200 }))
      if (url.endsWith('/api/v1/system')) return Promise.resolve(new Response(envelope({ cpu: { cores: 8, usage_percent: 10 }, ram: { used_bytes: 10, total_bytes: 100 }, gpus: [], instances: [otherUser] }), { status: 200 }))
      if (url.endsWith('/api/v1/status')) return Promise.resolve(new Response(envelope({ overall: 'ok', components: [] }), { status: 200 }))
      if (url.endsWith('/api/v1/health')) return Promise.resolve(new Response(envelope({ status: 'ok' }), { status: 200 }))
      if (url.includes('/api/v1/metrics?')) return Promise.resolve(new Response(envelope({ history: [], current: null, peaks: {}, source: 'sqlite' }), { status: 200 }))
      if (url.includes('/metrics')) return Promise.resolve(new Response(envelope(emptyMetrics), { status: 200 }))
      if (url.includes('/logs')) return Promise.resolve(new Response(envelope({ instance_id: otherUser.id, lines: [], truncated: false }), { status: 200 }))
      if (url.includes('/token-stats')) return Promise.resolve(new Response(envelope({ items: [] }), { status: 200 }))
      return Promise.resolve(new Response(envelope({ items: [] }), { status: 200 }))
    })
    vi.stubGlobal('fetch', fetchMock)
    render(<QueryClientProvider client={dashboardClient()}><MemoryRouter initialEntries={[`/?instance=${encodeURIComponent(otherUser.id)}`]}><DashboardPage /></MemoryRouter></QueryClientProvider>)
    const stopButton = await screen.findByRole('button', { name: 'Остановить внешний' })
    expect(stopButton).toBeDisabled()
    expect(stopButton).toHaveAttribute('title', 'Backend не подтвердил право остановки этого процесса')
    fireEvent.click(stopButton)
    expect(fetchMock.mock.calls.some(([input]) => String(input).endsWith('/stop'))).toBe(false)
  })

  it('renders hardware gaps, metric states, logs and the URL-selected instance', async () => {
    const first = { ...external, id: 'managed/first/1-10', source: 'managed', profile_name: 'first', model_name: 'first-model', pid: 1, process_start_time_ns: 10, can_stop: true, identity_verified: true }
    const selected = { ...external, id: 'managed/selected/2-20', source: 'managed', profile_name: 'selected', model_name: 'selected-model', pid: 2, process_start_time_ns: 20, can_stop: true, identity_verified: true }
    const fetchMock = vi.fn().mockImplementation((input: RequestInfo | URL) => {
      const url = String(input)
      if (url.includes('/api/v1/metrics/concurrency')) return Promise.resolve(new Response(envelope({ items: [], days: 7, source: 'sqlite' }), { status: 200 }))
      if (url.endsWith('/api/v1/system')) return Promise.resolve(new Response(envelope({ cpu: { cores: 8, usage_percent: null }, ram: { used_bytes: 0, total_bytes: 0 }, gpus: [{ index: 0, name: 'GPU test', memory_used_bytes: 10, memory_total_bytes: 100, usage_percent: null, temperature_celsius: null }], instances: [first, selected] }), { status: 200 }))
      if (url.endsWith('/api/v1/status')) return Promise.resolve(new Response(envelope({ overall: 'degraded', components: [] }), { status: 200 }))
      if (url.endsWith('/api/v1/health')) return Promise.resolve(new Response(envelope({ status: 'degraded' }), { status: 200 }))
      if (url.includes('/api/v1/metrics?')) return Promise.resolve(new Response(envelope({ history: [], current: null, peaks: {}, source: 'sqlite' }), { status: 200 }))
      if (url.includes('/metrics')) return Promise.resolve(new Response(envelope({ items: [{ name: 'decode_tps', value: 12, unit: 'tok/s', source: 'engine', observed_at: '2026-09-29T00:00:00Z', state: 'stale' }, { name: 'queue_requests', value: null, unit: 'waiting', source: 'engine', observed_at: '2026-09-29T00:00:00Z', state: 'unavailable' }, { name: 'active_requests', value: 0, unit: 'live', source: 'engine', observed_at: '2026-09-29T00:00:00Z', state: 'ok' }] }), { status: 200 }))
      if (url.includes('/logs')) return Promise.resolve(new Response(envelope({ instance_id: selected.id, lines: ['line one'], truncated: true }), { status: 200 }))
      if (url.includes('/token-stats')) return Promise.resolve(new Response(envelope({ items: [{ profile_name: 'selected', requests: 2, input_tokens: { known_total: null, unknown_requests: 2 }, output_tokens: { known_total: 4, unknown_requests: 1 }, source: 'journal' }] }), { status: 200 }))
      return Promise.resolve(new Response(envelope({ items: [] }), { status: 200 }))
    })
    vi.stubGlobal('fetch', fetchMock)
    render(<QueryClientProvider client={dashboardClient()}><MemoryRouter initialEntries={['/?instance=managed%2Fselected%2F2-20']}><DashboardPage /><LocationProbe /></MemoryRouter></QueryClientProvider>)
    expect(await screen.findByText('selected-model')).toBeInTheDocument()
    expect(screen.getByTestId('location-state')).toHaveTextContent('instance=managed%2Fselected%2F2-20')
    const decode = screen.getByText('Decode').parentElement!
    expect(within(decode).getByText('—')).toBeInTheDocument()
    expect(within(decode).queryByText('12')).not.toBeInTheDocument()
    const queue = screen.getByText('Queue').parentElement!
    expect(within(queue).getByText('—')).toBeInTheDocument()
    expect(within(queue).getByText('ожидание —')).toBeInTheDocument()
    expect(within(screen.getByText('Requests', { selector: 'p' }).parentElement!).getByText('0')).toBeInTheDocument()
    expect(screen.getAllByText('недоступно').length).toBeGreaterThan(0)
    expect(screen.queryByText('Статистика токенов')).not.toBeInTheDocument()
    const fullLogs = screen.getByRole('button', { name: 'Полные логи' })
    const details = fullLogs.closest('details')!
    expect(details.open).toBe(false)
    fireEvent.click(fullLogs)
    expect(openLogs).toHaveBeenCalledWith('selected')
    expect(details.open).toBe(false)
    fireEvent.click(screen.getByText('Логи запуска'))
    expect(await screen.findByText('line one')).toBeVisible()
    expect(screen.getByText(/Показан только доступный хвост/)).toBeVisible()
    expect(screen.getByText('GPU test')).toBeInTheDocument()
  })
})
