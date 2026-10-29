import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { api } from '@/lib/api'
import { comfyApi, type ComfyConfig } from '@/lib/comfy-api'
import { ProxyPage } from '@/pages/proxy'

const initialConfig: ComfyConfig = {
  sources: { local: { base_url: 'http://local-comfy:8188/comfy/root', user_context: 'deck-user' } },
  models: {
    cached: { source: 'local', type: 'image', workflows: { generate: 'workflows/cached.json', edit_masked: 'workflows/masked.json' }, max_megapixels: 8 },
    video: { source: 'local', type: 'video', workflows: { generate: 'workflows/video.json', edit: 'workflows/i2v.json' }, max_resolution: 1080, max_seconds: 30 },
  },
}

afterEach(() => { cleanup(); vi.restoreAllMocks() })

function setup(config = initialConfig) {
  vi.spyOn(api, 'getProxyRoutes').mockResolvedValue({ items: [] })
  vi.spyOn(api, 'getProxyModelChoices').mockResolvedValue({ items: [], diagnostics: [] })
  vi.spyOn(api, 'getOpenAIConnections').mockResolvedValue({ items: [] })
  vi.spyOn(comfyApi, 'getConfig').mockResolvedValue(structuredClone(config))
  vi.spyOn(comfyApi, 'getStatus').mockResolvedValue({ available: false })
  const workflows = vi.spyOn(comfyApi, 'getWorkflows').mockResolvedValue({ items: ['workflows/remote.json'], available: false, cached: true, updated_at: 1 })
  const saveSource = vi.spyOn(comfyApi, 'saveSource').mockResolvedValue(initialConfig)
  const saveAlias = vi.spyOn(comfyApi, 'saveAlias').mockResolvedValue(initialConfig)
  const deleteSource = vi.spyOn(comfyApi, 'deleteSource').mockResolvedValue(initialConfig)
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(<QueryClientProvider client={client}><MemoryRouter><ProxyPage /></MemoryRouter></QueryClientProvider>)
  return { workflows, saveSource, saveAlias, deleteSource }
}

function select(label: string) {
  return screen.getByRole('dialog').querySelector(`select[aria-label="${label}"]`) as HTMLSelectElement
}

async function tab(name: 'Изображения' | 'Видео') {
  fireEvent.click(await screen.findByRole('tab', { name }))
  await screen.findByRole('region', { name: 'Подключения ComfyUI' })
}

describe('proxy media configuration uses shared backend operations', () => {
  it('shows only the MP budget directly in the image form', async () => {
    setup()
    await tab('Изображения')
    fireEvent.click(screen.getByRole('button', { name: 'Изменить алиас cached' }))
    const dialog = screen.getByRole('dialog')
    expect(within(dialog).getByLabelText('Максимум, МП')).toHaveValue('8')
    expect(dialog.querySelector('details')).toBeNull()
    expect(within(dialog).queryByText('Кратность')).not.toBeInTheDocument()
    expect(within(dialog).queryByText('Seed')).not.toBeInTheDocument()
  })

  it('keeps offline aliases editable and sends only alias fields to the entity API', async () => {
    const { saveAlias } = setup()
    await tab('Изображения')
    expect(screen.queryByText('Входы API')).not.toBeInTheDocument()
    expect(screen.queryByText('Generate')).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Изменить алиас cached' }))
    let dialog = within(screen.getByRole('dialog'))
    expect(select('Сценарий: Генерация')).toHaveValue('workflows/cached.json')
    fireEvent.click(dialog.getByRole('button', { name: 'Отмена' }))
    expect(saveAlias).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('button', { name: 'Изменить алиас cached' }))
    dialog = within(screen.getByRole('dialog'))
    fireEvent.click(dialog.getByRole('button', { name: 'Сохранить' }))
    await waitFor(() => expect(saveAlias).toHaveBeenCalledWith({ name: 'cached', type: 'image', source: 'local', workflows: { generate: 'workflows/cached.json', edit_masked: 'workflows/masked.json' }, max_megapixels: 8 }, 'cached'))
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
  })

  it('sends a connection with one full URL including its path', async () => {
    const { saveSource } = setup()
    await tab('Изображения')
    fireEvent.click(screen.getByRole('button', { name: 'Изменить подключение local' }))
    const dialog = within(screen.getByRole('dialog'))
    expect(dialog.queryByText('Выполнение workflow')).not.toBeInTheDocument()
    expect(dialog.getByLabelText('URL')).toHaveValue('http://local-comfy:8188/comfy/root')
    fireEvent.change(dialog.getByLabelText('Имя'), { target: { value: 'renamed' } })
    fireEvent.click(dialog.getByRole('button', { name: 'Сохранить' }))
    await waitFor(() => expect(saveSource).toHaveBeenCalledWith({ name: 'renamed', base_url: 'http://local-comfy:8188/comfy/root' }, 'local'))
  })

  it('adds a connection, selects its workflow and saves a new image alias', async () => {
    const { saveSource, workflows, saveAlias } = setup()
    saveSource.mockResolvedValue({ ...initialConfig, sources: { ...initialConfig.sources, remote: { base_url: 'http://remote-comfy:8188' } } })
    await tab('Изображения')
    fireEvent.click(screen.getByRole('button', { name: 'Добавить подключение' }))
    let dialog = within(screen.getByRole('dialog'))
    fireEvent.change(dialog.getByLabelText('Имя'), { target: { value: 'remote' } })
    fireEvent.change(dialog.getByLabelText('URL'), { target: { value: 'http://remote-comfy:8188' } })
    fireEvent.click(dialog.getByRole('button', { name: 'Добавить подключение' }))
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    fireEvent.click(screen.getByRole('button', { name: 'Новый алиас' }))
    dialog = within(screen.getByRole('dialog'))
    fireEvent.change(dialog.getByLabelText('Алиас'), { target: { value: 'remote-image' } })
    fireEvent.change(select('Подключение'), { target: { value: 'remote' } })
    await waitFor(() => expect(workflows).toHaveBeenCalledWith('remote'))
    await waitFor(() => expect(select('Сценарий: Генерация').querySelector('option[value="workflows/remote.json"]')).not.toBeNull())
    fireEvent.change(select('Сценарий: Генерация'), { target: { value: 'workflows/remote.json' } })
    expect(dialog.getByLabelText('Максимум, МП')).toHaveValue('')
    fireEvent.change(dialog.getByLabelText('Максимум, МП'), { target: { value: '8' } })
    fireEvent.click(dialog.getByRole('button', { name: 'Сохранить' }))
    await waitFor(() => expect(saveAlias).toHaveBeenCalledWith({ name: 'remote-image', source: 'remote', type: 'image', workflows: { generate: 'workflows/remote.json' }, max_megapixels: 8 }, undefined))
  })

  it('saves video limits without image-only operations', async () => {
    const { saveAlias } = setup()
    await tab('Видео')
    fireEvent.click(screen.getByRole('button', { name: 'Изменить алиас video' }))
    const dialog = within(screen.getByRole('dialog'))
    expect(dialog.queryByLabelText('Сценарий: Вариации')).not.toBeInTheDocument()
    fireEvent.change(dialog.getByLabelText('Макс. длительность, с'), { target: { value: '45' } })
    fireEvent.click(dialog.getByRole('button', { name: 'Сохранить' }))
    await waitFor(() => expect(saveAlias).toHaveBeenCalledWith({ name: 'video', source: 'local', type: 'video', workflows: { generate: 'workflows/video.json', edit: 'workflows/i2v.json' }, max_resolution: 1080, max_seconds: 45 }, 'video'))
  })

  it('shows a backend workflow error without closing or clearing the draft', async () => {
    const { saveAlias } = setup()
    saveAlias.mockRejectedValue(new Error('Требуется API-экспорт workflow'))
    await tab('Изображения')
    fireEvent.click(screen.getByRole('button', { name: 'Изменить алиас cached' }))
    fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Сохранить' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Требуется API-экспорт workflow')
    expect(select('Сценарий: Генерация')).toHaveValue('workflows/cached.json')
  })
})
