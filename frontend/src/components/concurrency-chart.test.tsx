import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { ConcurrencyChart } from './concurrency-chart'
import { concurrencyScaleMaximum } from '@/lib/chart-scale'
import type { Instance } from '@/types/api'

afterEach(() => { cleanup(); vi.unstubAllGlobals() })

it('plots total and per-request on one scale and queries profile across restarts', async () => {
  vi.stubGlobal('PointerEvent', MouseEvent)
  const items = [1, 2, 3].map((requests) => ({
    profile_name: 'profile', model_name: 'model', engine_id: 'vllm', requests,
    total_avg: requests * 50, per_request_avg: 50, total_max: 170, samples: 10, seconds: 50,
  }))
  const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({
    data: { items, days: 7, source: 'sqlite' },
  })))
  vi.stubGlobal('fetch', fetchMock)
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(<QueryClientProvider client={client}><ConcurrencyChart instance={{
    id: 'profile/99', profile_name: 'profile', pid: 99,
  } as Instance} /></QueryClientProvider>)
  const graph = await screen.findByRole('img', { name: 'Generation по параллельности: profile' })
  const total = graph.querySelectorAll('[data-series="decode"] circle')
  const perRequest = graph.querySelectorAll('[data-series="perRequest"] circle')
  expect(total).toHaveLength(4)
  expect(perRequest).toHaveLength(4)
  expect(total[0]).toHaveAttribute('cx', '0')
  expect(total[0]).toHaveAttribute('cy', '108')
  expect(total[0].getAttribute('cy')).toBe(perRequest[0].getAttribute('cy'))
  expect(Number(total[2].getAttribute('cy'))).toBeLessThan(Number(perRequest[2].getAttribute('cy')))
  expect(graph).toHaveAttribute('viewBox', '0 0 400 120')
  expect(graph).toHaveClass('h-full')
  Object.defineProperty(graph, 'getBoundingClientRect', { value: () => ({ left: 0, width: 300 }) })
  fireEvent.pointerMove(graph, { clientX: 200 })
  expect(screen.getByRole('tooltip')).toHaveTextContent('2 reqs')
  expect(screen.getByRole('tooltip')).toHaveTextContent('Total100tok/s')
  expect(screen.getByRole('tooltip')).toHaveTextContent('Per request50tok/s')
  fireEvent.pointerLeave(graph.parentElement!)
  expect(screen.queryByRole('tooltip')).not.toBeInTheDocument()
  const url = new URL(String(fetchMock.mock.calls[0][0]), 'http://localhost')
  expect(url.searchParams.get('profile_name')).toBe('profile')
  expect(url.searchParams.get('days')).toBe('7')
  expect(url.searchParams.has('pid')).toBe(false)
  client.clear()
})

it('rounds the vertical limit with headroom to a readable number', () => {
  expect(concurrencyScaleMaximum(113)).toBe(150)
  expect(concurrencyScaleMaximum(178)).toBe(200)
  expect(concurrencyScaleMaximum(200)).toBe(250)
})

it('keeps an empty curve visible without inventing points', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({ data: {
    items: [], days: 7, source: 'sqlite',
  } }))))
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(<QueryClientProvider client={client}><ConcurrencyChart /></QueryClientProvider>)
  await waitFor(() => expect(screen.getByText('Нет данных')).toBeInTheDocument())
  expect(screen.getByRole('img').querySelectorAll('circle')).toHaveLength(0)
  client.clear()
})
