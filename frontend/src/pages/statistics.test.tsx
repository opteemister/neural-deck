import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { api } from '@/lib/api'
import { ActivityHeatmap, StatisticsPage } from './statistics'
import type { StatisticsData } from '@/types/api'

const aggregate = {
  requests: { total: 0, success: 0, failed: 0, interrupted: 0, unknown: 0 },
  tokens: { input: { known_total: 0, unknown_requests: 0 }, cached_input: { known_total: 0, unknown_requests: 0 }, output: { known_total: 0, unknown_requests: 0 } },
  performance: { latency_ms: null, latency_p95_ms: null, prefill_tps: null, decode_tps: null },
  cost: { usd: 0, state: 'ok' as const, unknown_requests: 0, prices: { input: null, cached_input: null, output: null } },
}
function fixture(period: string): StatisticsData {
  const start = period === 'year' ? '2025-09-29' : '2026-09-05'
  const days = period === 'year' ? 371 : 30
  return {
    period: { name: period, from: `${start}T00:00:00Z`, to: '2026-10-04T12:00:00Z', timezone: 'UTC', boundary: '[from,to)', bucket: 'day', request_event_time: 'finished_at_else_started_at' },
    filters: { model_name: null, profile_name: null }, filters_options: { models: [], profiles: [] },
    totals: aggregate, summary: { sessions: null, sessions_state: 'not_recorded', active_days: 0, period_days: days, streak_days: 0, success_rate: null },
    activity: Array.from({ length: days }, (_, index) => ({ ...aggregate, date: new Date(new Date(`${start}T00:00:00Z`).getTime() + index * 86400000).toISOString().slice(0, 10), benchmark: false })),
    profiles: [], clients: [], benchmarks: { intervals: [], traffic_included: true, state: 'not_recorded' }, source: 'sqlite.request_journal',
  }
}
afterEach(() => vi.restoreAllMocks())
describe('Statistics page', () => {
  it('shows the entire current Monday–Sunday column even on Monday', () => {
    const data = fixture('year')
    data.period.from = '2025-10-06T00:00:00Z'
    data.period.to = '2026-10-05T12:00:00Z'
    data.activity = Array.from({ length: 365 }, (_, index) => ({ ...aggregate, date: new Date(Date.UTC(2025, 9, 6 + index)).toISOString().slice(0, 10), benchmark: false }))
    render(<ActivityHeatmap data={data} />)
    const grid = screen.getByLabelText('53 недели, 7 дней в каждой неделе')
    expect(grid.children).toHaveLength(371)
    const currentWeek = Array.from(grid.children).slice(-7)
    expect(currentWeek[0]).toHaveAccessibleName(expect.stringContaining('2026-10-05'))
    currentWeek.slice(1).forEach(day => {
      expect(day.tagName).toBe('SPAN')
      expect(day).toHaveAttribute('aria-hidden', 'true')
      expect(day).toHaveTextContent('')
    })
    expect(screen.getByText('Пн')).toBeInTheDocument()
    expect(screen.getByText('Вс')).toBeInTheDocument()
  })
  it('keeps the year grid empty and clean, uses the shared Select and changes both tables only', async () => {
    const get = vi.spyOn(api, 'getStatistics').mockImplementation(async (filters) => fixture(filters?.period ?? 'month'))
    render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><StatisticsPage /></QueryClientProvider>)
    expect(await screen.findByRole('heading', { name: 'Последний год' })).toBeInTheDocument()
    const grid = await screen.findByLabelText('53 недели, 7 дней в каждой неделе')
    expect(grid.children).toHaveLength(371)
    expect(screen.getByTestId('activity-heatmap')).not.toHaveClass('overflow-x-auto')
    expect(grid).toHaveClass('min-w-0')
    expect(within(grid).getAllByRole('button')).toHaveLength(371)
    within(grid).getAllByRole('button').forEach(cell => expect(cell).toHaveTextContent(''))
    expect(screen.queryByText('Все модели')).not.toBeInTheDocument()
    expect(screen.queryByText('Все профили')).not.toBeInTheDocument()
    expect(screen.getAllByText('За последние 30 дней')).toHaveLength(2)
    const period = screen.getByRole('button', { name: 'Период статистики' })
    expect(period).toHaveTextContent('30 дней')
    expect(period.querySelector('svg')).toHaveClass('shrink-0')
    fireEvent.click(period)
    fireEvent.click(screen.getByRole('option', { name: '6 месяцев' }))
    await waitFor(() => expect(get).toHaveBeenCalledWith({ period: 'half_year' }))
    expect(await screen.findAllByText('За последние 6 месяцев')).toHaveLength(2)
    expect(screen.getByLabelText('53 недели, 7 дней в каждой неделе').children).toHaveLength(371)
    expect(screen.getByLabelText('Общая статистика за 30 дней')).toBeInTheDocument()
  })
})
