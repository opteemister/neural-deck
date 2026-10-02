import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { ModelPerformance } from './model-performance'
import type { Instance, MetricsData } from '@/types/api'

const instance = { id: 'managed/profile/10', source: 'managed', profile_name: 'profile', engine_id: 'vllm', pid: 10, state: 'ready', observed_at: '2026-10-02T10:00:00Z' } as Instance
function data(at: string, value: number): MetricsData {
  return { items: ['decode_tps', 'prefill_tps', 'active_requests'].map((name) => ({ name, value, observed_at: at, state: 'ok', source: 'test', unit: '' })) }
}
afterEach(() => vi.unstubAllGlobals())

it('restores 120 points, connects gaps, shows the hovered value and appends live without rereading history', async () => {
  const base = Date.parse('2026-10-02T09:00:00Z')
  const history = Array.from({ length: 123 }, (_, index) => ({ ...data(new Date(base + index * 10_000).toISOString(), index), observed_at: new Date(base + index * 10_000).toISOString() }))
  const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ data: { history, current: null, peaks: { decode_tps: 777 }, source: 'sqlite' }, request_id: 'req' })))
  vi.stubGlobal('fetch', fetchMock)
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const view = (current?: MetricsData) => <QueryClientProvider client={client}><ModelPerformance instance={instance} data={current} /></QueryClientProvider>
  const rendered = render(view(data(history.at(-1)!.observed_at, 122)))
  const generation = screen.getByRole('img', { name: 'Generation' })
  await waitFor(() => expect(generation.querySelectorAll('circle')).toHaveLength(120))
  expect(generation.querySelectorAll('path')).toHaveLength(1)
  expect(screen.getByText('max 777')).toBeInTheDocument()
  Object.defineProperty(generation, 'getBoundingClientRect', { value: () => ({ left: 0, width: 400 }) })
  fireEvent.pointerMove(generation, { clientX: 0 })
  expect(await screen.findByRole('tooltip')).toHaveTextContent('3')
  fireEvent.pointerLeave(generation)
  rendered.rerender(view(data('2026-10-02T10:00:00Z', 99)))
  await waitFor(() => expect(generation.querySelectorAll('circle')).toHaveLength(120))
  expect(generation.querySelectorAll('path')).toHaveLength(1)
  expect(fetchMock).toHaveBeenCalledTimes(1)
  client.clear()
})
