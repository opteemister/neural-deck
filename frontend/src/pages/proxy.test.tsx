import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { createEvent, fireEvent, render, screen, waitFor, cleanup } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { api } from '@/lib/api'
import { ProxyPage } from '@/pages/proxy'
import type { ProxyRoute } from '@/types/api'

const route: ProxyRoute = {
  name: 'manual', kind: 'alias', available: false, diagnostics: [],
  candidate_profile_ids: ['first', 'second', 'third'],
  candidates: ['first', 'second', 'third'].map((name) => ({
    profile_id: name, profile_name: name, instance_id: null, upstream_model: null,
    engine_id: 'llama.cpp', port: null, state: 'stopped', available: false, reason: 'profile_not_running',
  })),
}

afterEach(() => { cleanup(); vi.restoreAllMocks() })

async function setup() {
  vi.spyOn(api, 'getProxyRoutes').mockResolvedValue({ items: [route] })
  vi.spyOn(api, 'getProfiles').mockResolvedValue({ items: [], issues: [] })
  const update = vi.spyOn(api, 'updateProxyRoute').mockResolvedValue(route)
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(<QueryClientProvider client={client}><ProxyPage /></QueryClientProvider>)
  await screen.findByText('first')
  const rows = ['first', 'second', 'third'].map((name, index) => {
    const row = screen.getByText(name).closest('[data-index]') as HTMLElement
    vi.spyOn(row, 'getBoundingClientRect').mockReturnValue({ top: index * 100, height: 100 } as DOMRect)
    return row
  })
  const transfer = { effectAllowed: 'move', dropEffect: 'move', setData: vi.fn(), getData: vi.fn() }
  function start(index: number) {
    transfer.getData.mockReturnValue(route.candidate_profile_ids[index])
    fireEvent.dragStart(rows[index].querySelector('[draggable]')!, { dataTransfer: transfer })
  }
  function drag(type: 'dragOver' | 'drop', row: HTMLElement, y: number) {
    const event = createEvent[type](row, { dataTransfer: transfer })
    Object.defineProperty(event, 'clientY', { value: y })
    fireEvent(row, event)
  }
  return { rows, start, drag, update }
}

describe('Proxy alias drag and drop', () => {
  it('hides the original position and accepts entry at the top edge of the last row', async () => {
    const { rows, start, drag, update } = await setup()
    start(0)
    for (const [row, y] of [[rows[0], 10], [rows[0], 90]] as const) {
      drag('dragOver', row, y)
      expect(document.querySelector('[data-drop-indicator]')).toBeNull()
    }
    drag('dragOver', rows[2], 201)
    expect(rows[2].querySelector('[data-drop-indicator="after"]')).not.toBeNull()
    drag('drop', rows[2], 201)
    await waitFor(() => expect(update).toHaveBeenCalledWith('manual', ['second', 'third', 'first']))
  })

  it('accepts insertion before a row when moving upwards', async () => {
    const { rows, start, drag, update } = await setup()
    start(2)
    drag('dragOver', rows[0], 99)
    expect(rows[0].querySelector('[data-drop-indicator="before"]')).not.toBeNull()
    drag('drop', rows[0], 99)
    await waitFor(() => expect(update).toHaveBeenCalledWith('manual', ['third', 'first', 'second']))
  })

  it('places the indicator between rows and skips drops at the original position', async () => {
    const { rows, start, drag, update } = await setup()
    start(0)
    drag('drop', rows[0], 90)
    expect(update).not.toHaveBeenCalled()
    drag('dragOver', rows[1], 101)
    expect(rows[2].querySelector('[data-drop-indicator="before"]')).not.toBeNull()
    drag('drop', rows[1], 101)
    await waitFor(() => expect(update).toHaveBeenCalledWith('manual', ['second', 'first', 'third']))
  })

  it('clears the indicator outside the alias and cancels without saving', async () => {
    const { rows, start, drag, update } = await setup()
    start(0)
    drag('dragOver', rows[2], 290)
    fireEvent.dragLeave(rows[2], { relatedTarget: document.body })
    expect(document.querySelector('[data-drop-indicator]')).toBeNull()
    fireEvent.drop(document.body)
    fireEvent.dragEnd(rows[0].querySelector('[draggable]')!)
    expect(update).not.toHaveBeenCalled()
  })
})

it('renames an alias and sends its candidates in the same update', async () => {
  await setup()
  const update = vi.spyOn(api, 'updateProxyAlias').mockResolvedValue({ ...route, name: 'smart' })
  fireEvent.click(screen.getByRole('button', { name: 'Изменить алиас manual' }))
  fireEvent.change(screen.getByLabelText('Имя алиаса'), { target: { value: 'smart' } })
  fireEvent.click(screen.getByRole('button', { name: 'Сохранить' }))
  await waitFor(() => expect(update).toHaveBeenCalledWith('manual', {
    name: 'smart', profile_ids: ['first', 'second', 'third'],
  }))
})

it('blocks renaming to an existing manual alias', async () => {
  vi.spyOn(api, 'getProxyRoutes').mockResolvedValue({ items: [route, { ...route, name: 'taken' }] })
  vi.spyOn(api, 'getProfiles').mockResolvedValue({ items: [], issues: [] })
  const update = vi.spyOn(api, 'updateProxyAlias')
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(<QueryClientProvider client={client}><ProxyPage /></QueryClientProvider>)
  fireEvent.click(await screen.findByRole('button', { name: 'Изменить алиас manual' }))
  fireEvent.change(screen.getByLabelText('Имя алиаса'), { target: { value: 'taken' } })
  expect(screen.getByRole('button', { name: 'Сохранить' })).toBeDisabled()
  expect(screen.getByText('Это имя уже занято ручным алиасом.')).toBeInTheDocument()
  expect(update).not.toHaveBeenCalled()
})
