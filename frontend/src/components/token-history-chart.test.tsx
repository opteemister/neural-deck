import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { TokenHistoryChart } from './token-history-chart'
import type { Instance } from '@/types/api'

afterEach(() => { cleanup(); vi.unstubAllGlobals() })

it('stacks token segments, hides missing-data counts and positions the tooltip beside the cursor', async () => {
  vi.stubGlobal('PointerEvent', MouseEvent)
  const items = Array.from({ length: 24 }, (_, index) => ({ at: new Date(Date.parse('2026-10-01T12:00:00Z') + index * 3600_000).toISOString(), requests: index === 0 ? 2 : 0, input_tokens: index === 0 ? 100 : 0, output_tokens: index === 0 ? 50 : 0, cached_tokens: index === 0 ? null : 0, input_tokens_unknown: index === 0 ? 1 : 0, output_tokens_unknown: 0, cached_tokens_unknown: index === 0 ? 2 : 0 }))
  const fetchMock = vi.fn().mockImplementation(() => Promise.resolve(new Response(JSON.stringify({ data: { items, from: items[0].at, to: '2026-10-02T12:00:00Z', bucket_minutes: 60, source: 'api-journal' } }))))
  vi.stubGlobal('fetch', fetchMock)
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(<QueryClientProvider client={client}><TokenHistoryChart instance={{ id: 'run/10', profile_name: 'profile', pid: 10 } as Instance} /></QueryClientProvider>)
  const graph = screen.getByRole('img', { name: 'Tokens за 24 часа' })
  await waitFor(() => expect(graph.querySelectorAll('rect')).toHaveLength(71))
  expect(screen.getByText(/in 100 · out 50 · total 150/)).toBeInTheDocument()
  expect(graph.parentElement?.querySelector('span')).toHaveTextContent('200')
  expect(graph.querySelector('[data-series="input_tokens"]')).toHaveAttribute('height', '48')
  expect(graph.querySelector('[data-series="output_tokens"]')).toHaveAttribute('height', '24')
  Object.defineProperty(graph, 'getBoundingClientRect', { value: () => ({ left: 0, right: 400, top: 100, bottom: 228, width: 400 }) })
  fireEvent.pointerMove(graph, { clientX: 0, clientY: 140 })
  expect(screen.getByRole('tooltip')).toHaveTextContent('Cached—')
  const from = new Date(items[0].at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', timeZone: 'UTC' })
  const to = new Date(Date.parse(items[0].at) + 3600_000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', timeZone: 'UTC' })
  expect(screen.getByRole('tooltip')).toHaveTextContent(`${from}–${to}`)
  expect(screen.getByRole('tooltip')).not.toHaveTextContent('без данных')
  expect(screen.getByRole('tooltip')).toHaveStyle({ left: '12px', top: '152px' })
  fireEvent.pointerMove(graph, { clientX: 200, clientY: 160 })
  expect(screen.getByRole('tooltip')).toHaveStyle({ left: '212px', top: '172px' })
  vi.stubGlobal('innerWidth', 420)
  fireEvent.pointerMove(graph, { clientX: 390, clientY: 160 })
  expect(screen.getByRole('tooltip')).toHaveStyle({ left: '202px', top: '172px' })
  const input = graph.querySelector('[data-series="input_tokens"]')!
  const output = graph.querySelector('[data-series="output_tokens"]')!
  expect(input.getAttribute('x')).toBe(output.getAttribute('x'))
  expect(Number(output.getAttribute('y')) + Number(output.getAttribute('height'))).toBe(Number(input.getAttribute('y')))
  const url = new URL(String(fetchMock.mock.calls[0][0]), 'http://localhost')
  expect(url.searchParams.get('profile_name')).toBe('profile')
  expect(url.searchParams.has('pid')).toBe(false)
  client.clear()
})


it('counts cached input only once in a three-part bar', async () => {
  const item = { at: '2026-10-01T12:00:00Z', requests: 1, input_tokens: 100, output_tokens: 50, cached_tokens: 30, input_tokens_unknown: 0, output_tokens_unknown: 0, cached_tokens_unknown: 0 }
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({ data: { items: [item], from: item.at, to: '2026-10-02T12:00:00Z', bucket_minutes: 60, source: 'api-journal' } }))))
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(<QueryClientProvider client={client}><TokenHistoryChart /></QueryClientProvider>)
  const graph = screen.getByRole('img', { name: 'Tokens за 24 часа' })
  await waitFor(() => expect(graph.querySelectorAll('rect')).toHaveLength(3))
  const parts = [...graph.querySelectorAll('rect')]
  expect(new Set(parts.map((part) => part.getAttribute('x'))).size).toBe(1)
  expect(parts.reduce((sum, part) => sum + Number(part.getAttribute('height')), 0)).toBeCloseTo(72)
  expect(Number(parts[0].getAttribute('height'))).toBeCloseTo(70 / 200 * 96)
  expect(Number(parts[2].getAttribute('y'))).toBeCloseTo(36)
  client.clear()
})
