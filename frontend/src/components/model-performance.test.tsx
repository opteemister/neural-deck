import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { ModelPerformance } from './model-performance'
import type { Instance, MetricsData } from '@/types/api'

const daily = { decode_tps: { avg: 150, max: 777 }, prefill_tps: { avg: 1500, max: 1500 }, active_requests: { avg: 150, max: 200 }, ttft_mean_ms: { avg: 150, max: 300 } }
const instance = { id: 'managed/profile/10', source: 'managed', profile_name: 'profile', engine_id: 'vllm', pid: 10, state: 'ready', observed_at: '2026-10-02T10:00:00Z' } as Instance
function data(at: string, value: number): MetricsData {
  return { items: ['decode_tps', 'prefill_tps', 'active_requests'].map((name) => ({ name, value, observed_at: at, state: 'ok', source: 'test', unit: '' })) }
}
afterEach(() => { cleanup(); vi.unstubAllGlobals() })

it('restores 120 points, connects gaps, shows the hovered value and appends live without rereading history', async () => {
  const base = Date.parse('2026-10-02T09:00:00Z')
  const history = Array.from({ length: 123 }, (_, index) => ({ ...data(new Date(base + index * 10_000).toISOString(), index), observed_at: new Date(base + index * 10_000).toISOString() }))
  const fetchMock = vi.fn().mockImplementation(() => Promise.resolve(new Response(JSON.stringify({ data: { history, current: null, peaks: { decode_tps: 9999 }, source: 'sqlite', aggregates: daily }, request_id: 'req' }))))
  vi.stubGlobal('fetch', fetchMock)
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const view = (current?: MetricsData) => <QueryClientProvider client={client}><ModelPerformance instance={instance} data={current} /></QueryClientProvider>
  const rendered = render(view(data(history.at(-1)!.observed_at, 122)))
  const generation = screen.getByRole('img', { name: 'Generation' })
  await waitFor(() => expect(generation.querySelectorAll('circle')).toHaveLength(240))
  expect(generation.querySelectorAll('path')).toHaveLength(2)
  expect(screen.getByText(/total max 777/)).toBeInTheDocument()
  Object.defineProperty(generation, 'getBoundingClientRect', { value: () => ({ left: 0, width: 400 }) })
  fireEvent.pointerMove(generation, { clientX: 0 })
  expect(await screen.findByRole('tooltip')).toHaveTextContent('3')
  fireEvent.pointerLeave(generation)
  rendered.rerender(view(data('2026-10-02T10:00:00Z', 99)))
  await waitFor(() => expect(generation.querySelectorAll('circle')).toHaveLength(240))
  expect(generation.querySelectorAll('path')).toHaveLength(2)
  const bootstrapCalls = fetchMock.mock.calls.filter(([url]) => String(url).includes('mode=bootstrap'))
  expect(bootstrapCalls).toHaveLength(1)
  const historyUrl = new URL(String(bootstrapCalls[0][0]), 'http://localhost')
  expect(historyUrl.searchParams.get('profile_name')).toBe('profile')
  expect(historyUrl.searchParams.has('pid')).toBe(false)
  expect(screen.queryByText(/До 120 точек/)).not.toBeInTheDocument()
  const aggregateUrl = new URL(String(fetchMock.mock.calls.find(([url]) => String(url).includes('mode=aggregates'))![0]), 'http://localhost')
  expect(aggregateUrl.searchParams.get('profile_name')).toBe('profile')
  expect(aggregateUrl.searchParams.has('pid')).toBe(false)
  client.clear()
})

it('shows zero for missing live measurements, retains state metrics and keeps profile history across restarts', async () => {
  vi.stubGlobal('fetch', vi.fn().mockImplementation(() => Promise.resolve(new Response(JSON.stringify({ data: { history: [], current: null, peaks: {}, source: 'sqlite', aggregates: daily } })))))
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const fields = ['decode_tps', 'prefill_tps', 'ttft_mean_ms', 'active_requests', 'kv_cache_percent', 'queue_requests', 'prompt_tokens_total', 'completion_tokens_total', 'completed_requests', 'queue_time_mean_ms', 'prefix_cache_hit_percent', 'prefix_cache_hit_mean_percent', 'spec_acceptance_percent']
  const known: MetricsData = { items: fields.map((name) => ({ name, value: 42, state: 'ok', source: 'test', unit: '', observed_at: '2026-10-02T10:00:00Z' })) }
  const missing: MetricsData = { items: fields.map((name) => ({ name, value: null, state: 'unavailable', source: 'test', unit: '', observed_at: '2026-10-02T10:00:02Z' })) }
  const view = (current: MetricsData, selected = instance) => <QueryClientProvider client={client}><ModelPerformance instance={selected} data={current} /></QueryClientProvider>
  const rendered = render(view(known))
  await waitFor(() => expect(screen.getByRole('img', { name: 'Prefill' }).querySelectorAll('circle')).toHaveLength(1))
  rendered.rerender(view(missing))
  for (const label of ['Decode', 'Prefill', 'Requests', 'Queue']) {
    const card = screen.getByText(label, { selector: 'p' }).parentElement!
    expect(within(card).getByText('0', { exact: false, selector: 'p.font-medium, p.text-xl' })).toBeInTheDocument()
    expect(card).not.toHaveTextContent('Последнее измерение:')
  }
  for (const label of ['KV cache', 'Served']) {
    const card = screen.getByText(label, { selector: 'p' }).parentElement!
    expect(within(card).getByText('42', { exact: false, selector: 'p.font-medium, p.text-xl' })).toBeInTheDocument()
    expect(card).not.toHaveTextContent('Последнее измерение:')
    expect(card).toHaveAttribute('title', expect.stringContaining('Последнее измерение:'))
  }
  for (const label of ['KV Hit', 'Draft Acceptance']) {
    const card = screen.getByText(label, { selector: 'p' }).parentElement!
    expect(card).toHaveTextContent('42')
    expect(card).not.toHaveTextContent('Последнее измерение:')
    expect(card).toHaveAttribute('title', expect.stringContaining('Последнее измерение:'))
  }
  expect(screen.getByRole('img', { name: 'Prefill' }).querySelectorAll('circle')).toHaveLength(1)
  rendered.rerender(view({ items: known.items.map((item) => ({ ...item, value: 0, observed_at: '2026-10-02T10:00:04Z' })) }))
  expect(screen.queryByText(/Последнее измерение:/)).not.toBeInTheDocument()
  expect(screen.getByText('TTFT', { selector: 'p' }).parentElement).toHaveTextContent('max 300')
  rendered.rerender(view(missing, { ...instance, id: 'managed/profile/11', pid: 11 }))
  expect(screen.getByText('TTFT', { selector: 'p' }).parentElement).toHaveTextContent('max 300')
  rendered.rerender(view(missing, { ...instance, id: 'managed/other/12', profile_name: 'other', pid: 12 }))
  expect(screen.getByText('Decode', { selector: 'p' }).parentElement).toHaveTextContent('0tok/s')
  expect(screen.queryByText(/Последнее измерение:/)).not.toBeInTheDocument()
  client.clear()
})

it('restores the last known measurement for a stopped profile even when the latest snapshot is unavailable', async () => {
  const metric = { name: 'prefill_tps', value: 125, state: 'ok', source: 'test', unit: 'ms', observed_at: '2026-10-02T10:00:00Z' }
  vi.stubGlobal('fetch', vi.fn().mockImplementation(() => Promise.resolve(new Response(JSON.stringify({ data: { history: [{ items: [metric], observed_at: metric.observed_at }], current: null, peaks: {}, source: 'sqlite', aggregates: daily } })))))
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(<QueryClientProvider client={client}><ModelPerformance profileName="profile" data={{ items: [{ ...metric, value: null, state: 'stale' }] }} /></QueryClientProvider>)
  await waitFor(() => expect(screen.getByText('Prefill', { selector: 'p' }).parentElement).toHaveTextContent('125tok/s'))
  expect(screen.getByText('Prefill', { selector: 'p' }).parentElement).toHaveAttribute('title', expect.stringContaining('Данные устарели · Последнее измерение:'))
  client.clear()
})

it('uses the requested card order, weighted TTFT mean/max and separate scales for generation per request', async () => {
  vi.stubGlobal('PointerEvent', MouseEvent)
  vi.stubGlobal('fetch', vi.fn().mockImplementation(() => Promise.resolve(new Response(JSON.stringify({ data: { history: [], current: null, peaks: {}, source: 'sqlite', aggregates: daily } })))))
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const sample = (at: string, rate: number, requests: number, ttft: number, count: number): MetricsData => ({ items: [
    { name: 'decode_tps', value: rate, observed_at: at, state: 'ok', source: 'vllm:technical_metric', unit: '' },
    { name: 'active_requests', value: requests, observed_at: at, state: 'ok', source: 'test', unit: '' },
    { name: 'ttft_mean_ms', value: ttft, observed_at: at, state: 'ok', source: 'test', unit: '', metadata: { count } },
  ] })
  const view = (current: MetricsData) => <QueryClientProvider client={client}><ModelPerformance instance={instance} data={current} /></QueryClientProvider>
  const rendered = render(view(sample('2026-10-02T10:00:00Z', 500, 50, 100, 3)))
  await waitFor(() => expect(screen.getByRole('img', { name: 'Generation' }).querySelectorAll('path')).toHaveLength(2))
  rendered.rerender(view(sample('2026-10-02T10:00:02Z', 200, 10, 300, 1)))
  const ttftCard = screen.getByText('TTFT', { selector: 'p' }).parentElement!
  await waitFor(() => expect(ttftCard).toHaveTextContent('150ms'))
  expect(ttftCard).toHaveTextContent('max 300')
  expect([...rendered.container.querySelectorAll('[data-metric] > p:first-child')].map((el) => el.textContent)).toEqual(['Prefill', 'Decode / req', 'TTFT', 'Requests', 'KV Hit', 'KV cache', 'Decode', 'Served', 'Queue', 'Draft Acceptance'])
  for (const label of ['Prefill', 'Decode / req', 'Requests', 'Decode', 'Queue']) {
    expect(screen.getByText(label, { selector: 'p' })).toHaveAttribute('aria-label', `${label}, live`)
  }
  expect(rendered.container.querySelector('table')).toBeNull()
  expect(screen.getByText('Decode', { selector: 'p' }).parentElement?.title).not.toContain('vllm:')
  expect(screen.getByText('Decode / req', { selector: 'p' }).parentElement).toHaveTextContent('20tok/s')
  const paths = screen.getByRole('img', { name: 'Generation' }).querySelectorAll('path')
  expect(paths[1].getAttribute('d')).toBe('M0.0,20.7 L400.0,73.1')
  expect(paths[0].getAttribute('d')).toBe('M0.0,69.6 L400.0,31.2')
  const graph = screen.getByRole('img', { name: 'Generation' })
  Object.defineProperty(graph, 'getBoundingClientRect', { value: () => ({ left: 0, width: 400 }) })
  fireEvent.pointerMove(graph, { clientX: 400 })
  expect(screen.getByRole('tooltip')).toHaveTextContent('Per request20')
  client.clear()
})

it('keeps proportional generation lines distinguishable, labels both scales and formats avg, Prefill and TTFT', async () => {
  vi.stubGlobal('fetch', vi.fn().mockImplementation(() => Promise.resolve(new Response(JSON.stringify({ data: { history: [], current: null, peaks: {}, source: 'sqlite', aggregates: { ...daily, ttft_mean_ms: { avg: 90000, max: 90000 } } } })))))
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const sample = (at: string, rate: number, ttft: number): MetricsData => ({ items: [
    { name: 'decode_tps', value: rate, observed_at: at, state: 'ok', source: 'test', unit: '' },
    { name: 'prefill_tps', value: 1500, observed_at: at, state: 'ok', source: 'test', unit: '' },
    { name: 'active_requests', value: 2, observed_at: at, state: 'ok', source: 'test', unit: '' },
    { name: 'ttft_mean_ms', value: ttft, observed_at: at, state: 'ok', source: 'test', unit: '', metadata: { mean: ttft } },
  ] })
  const view = (current: MetricsData) => <QueryClientProvider client={client}><ModelPerformance instance={instance} data={current} /></QueryClientProvider>
  const rendered = render(view(sample('2026-10-02T10:00:00Z', 100, 10_000)))
  const graph = screen.getByRole('img', { name: 'Generation' })
  await waitFor(() => expect(graph.querySelectorAll('path')).toHaveLength(2))
  rendered.rerender(view(sample('2026-10-02T10:00:02Z', 200, 90_000)))
  await waitFor(() => expect(screen.getByText('Decode', { selector: 'p' }).parentElement).toHaveTextContent('max 777 · avg 150'))
  const paths = graph.querySelectorAll('path')
  expect(paths[0].getAttribute('d')).toBe('M0.0,76.0 L400.0,44.0')
  expect(paths[1].getAttribute('d')).toBe('M0.0,69.6 L400.0,31.2')
  expect(paths[1]).not.toHaveAttribute('stroke-dasharray')
  expect(paths[0]).toHaveAttribute('stroke-dasharray', '5 3')
  expect([...graph.querySelectorAll('[data-series]')].map((group) => group.getAttribute('data-series'))).toEqual(['perRequest', 'decode'])
  for (const circle of graph.querySelectorAll('circle')) {
    expect(circle).toHaveAttribute('r', '1.5')
    expect(circle.getAttribute('fill')).not.toBe('none')
  }
  const axes = rendered.container.querySelector('[aria-label="Generation: масштаб"]')!
  expect(axes).toHaveTextContent('250150')
  expect(screen.getByText('Prefill', { selector: 'p' }).parentElement).toHaveTextContent('1500tok/s')
  expect(screen.getByText('Prefill', { selector: 'p' }).parentElement).toHaveTextContent('max 1500 · avg 1500')
  expect(screen.getByText('TTFT', { selector: 'p' }).parentElement).toHaveTextContent('1.5min')
  expect(screen.getByText('TTFT', { selector: 'p' }).parentElement).toHaveTextContent('max 1.5 min')
  rendered.rerender(view(sample('2026-10-02T10:00:04Z', 100, 10_000)))
  expect(screen.getByText('TTFT', { selector: 'p' }).parentElement).toHaveTextContent('1.5min')
  client.clear()
})

it('keeps per-request zero points when no requests are active and leaves the background dashes visible', async () => {
  vi.stubGlobal('fetch', vi.fn().mockImplementation(() => Promise.resolve(new Response(JSON.stringify({ data: { history: [], current: null, peaks: {}, source: 'sqlite', aggregates: daily } })))))
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(<QueryClientProvider client={client}><ModelPerformance instance={instance} data={data('2026-10-02T10:00:00Z', 0)} /></QueryClientProvider>)
  const graph = screen.getByRole('img', { name: 'Generation' })
  await waitFor(() => expect(graph.querySelector('[data-series="perRequest"] circle')).not.toBeNull())
  const line = graph.querySelector('[data-series="perRequest"] path')!
  expect(line).toHaveAttribute('stroke-width', '2.5')
  expect(line).toHaveAttribute('d', 'M0.0,108.0')
  client.clear()
})


it('excludes idle zeros and unavailable values from avg while keeping zeros on the chart', async () => {
  const fields = ['decode_tps', 'prefill_tps', 'active_requests', 'ttft_mean_ms']
  const sample = (value: number | null, at: string): MetricsData => ({ items: fields.map((name) => ({ name, value, state: value === null ? 'unavailable' : 'ok', source: 'test', unit: '', observed_at: at })) })
  const history = [sample(100, '2026-10-02T10:00:00Z'), sample(0, '2026-10-02T10:00:02Z'), sample(null, '2026-10-02T10:00:04Z')].map((item) => ({ ...item, observed_at: item.items[0].observed_at }))
  vi.stubGlobal('fetch', vi.fn().mockImplementation(() => Promise.resolve(new Response(JSON.stringify({ data: { history, current: null, peaks: {}, source: 'sqlite', aggregates: daily } })))))
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const view = (current: MetricsData) => <QueryClientProvider client={client}><ModelPerformance instance={instance} data={current} /></QueryClientProvider>
  const rendered = render(view(sample(200, '2026-10-02T10:00:06Z')))
  await waitFor(() => expect(screen.getByText('Decode', { selector: 'p' }).parentElement).toHaveTextContent('avg 150'))
  rendered.rerender(view(sample(0, '2026-10-02T10:00:08Z')))
  for (const name of ['Decode', 'Requests', 'TTFT']) {
    expect(screen.getByText(name, { selector: 'p' }).parentElement).toHaveTextContent('avg 150')
  }
  const graph = screen.getByRole('img', { name: 'Generation' })
  await waitFor(() => expect(graph.querySelector('[data-series="decode"] circle:last-child')).toHaveAttribute('cy', '108'))
  expect(screen.getByText('Decode', { selector: 'p' }).parentElement).toHaveTextContent('0tok/s')
  client.clear()
})
