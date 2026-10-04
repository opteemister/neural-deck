import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { RunSummaryRow } from './run-summary-row'
import type { Instance, MetricsData } from '@/types/api'

afterEach(() => { cleanup(); vi.unstubAllGlobals() })

it('shows observed mean speeds, counts and model and stops the corresponding instance', async () => {
  const instance = { id: 'managed/profile/10', profile_name: 'profile', model_name: 'Qwen', pid: 10, state: 'ready', can_stop: true, identity_verified: true } as Instance
  const snapshot = (rate: number, at: string): MetricsData => ({ items: ['prefill_tps', 'decode_tps', 'completed_requests', 'completion_tokens_total'].map((name) => ({ name, value: rate, observed_at: at, state: 'ok', source: 'test', unit: '' })) })
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({ data: { history: [{ ...snapshot(10, '2026-10-02T10:00:00Z'), observed_at: '2026-10-02T10:00:00Z' }], current: null, peaks: {}, source: 'sqlite', aggregates: { prefill_tps: { avg: 15, max: 100 }, decode_tps: { avg: 15, max: 100 } } } }))))
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const onStop = vi.fn(), onSelect = vi.fn()
  render(<QueryClientProvider client={client}><table><tbody><RunSummaryRow instance={instance} data={snapshot(20, '2026-10-02T10:00:02Z')} onStop={onStop} onSelect={onSelect} stopping={false} /></tbody></table></QueryClientProvider>)
  await waitFor(() => expect(screen.getAllByText('15')).toHaveLength(2))
  expect(screen.getByText('Qwen')).toBeInTheDocument()
  expect(screen.getAllByText('20')).toHaveLength(2)
  fireEvent.click(screen.getByRole('button', { name: 'Остановить profile' }))
  expect(onStop).toHaveBeenCalledOnce()
  expect(onSelect).not.toHaveBeenCalled()
  fireEvent.click(screen.getByRole('button', { name: 'profile' }))
  expect(onSelect).toHaveBeenCalledOnce()
  client.clear()
})
