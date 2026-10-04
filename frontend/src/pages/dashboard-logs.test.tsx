import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { LogsPanel } from './dashboard'
import type { Instance } from '@/types/api'

vi.mock('@/components/logs-drawer', () => ({ useLogsDrawer: () => vi.fn() }))
afterEach(cleanup)

it('defaults to autoscroll on expansion and permits disabling it for subsequent updates', async () => {
  const instance = { id: 'managed/demo/10', source: 'managed', profile_name: 'demo' } as Instance
  const props = { instance, loading: false, error: null, retry: vi.fn() }
  const data = { instance_id: instance.id, lines: ['first'], truncated: false }
  const view = render(<LogsPanel {...props} data={data} />)
  const log = screen.getByLabelText('Лог запуска модели')
  Object.defineProperty(log, 'scrollHeight', { configurable: true, value: 1000 })
  const details = screen.getByText('Логи запуска').closest('details')!
  details.open = true
  fireEvent(details, new Event('toggle'))
  await waitFor(() => expect(log.scrollTop).toBe(1000))
  expect(screen.getByRole('checkbox', { name: 'Автоскролл' })).toBeChecked()
  fireEvent.click(screen.getByRole('checkbox', { name: 'Автоскролл' }))
  log.scrollTop = 50
  view.rerender(<LogsPanel {...props} data={{ ...data, lines: ['first', 'next'] }} />)
  expect(log.scrollTop).toBe(50)
  fireEvent.click(screen.getByRole('checkbox', { name: 'Автоскролл' }))
  expect(log.scrollTop).toBe(1000)
})
