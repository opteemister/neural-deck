import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, expect, it, vi } from 'vitest'
import { api } from '@/lib/api'
import { ModelsPage } from '@/pages/models'
import type { LocalModelArtifact, ModelFileUpdates, Operation } from '@/types/api'

const model: LocalModelArtifact = { id: 'model-1', path: '/models/org/model', kind: 'directory', format: 'gguf', compatibility: 'llama.cpp', size_bytes: 100, supporting_files: 0,
  hf_match: { status: 'downloaded', repo_id: 'org/model', remote_check: { availability: 'success' }, remote_update: { status: 'update_available', changed_files: 2 } }, variants: [], auxiliary_files: [] }
const files: ModelFileUpdates = { model_id: model.id, repo_id: 'org/model', revision: 'b'.repeat(40), checked_at: '2026-10-29T00:00:00Z', items: [
  { path: 'weights.gguf', status: 'changed', local: { size: 100, file_date: '2026-10-01T00:00:00Z', hash: 'old-hash', hash_kind: 'sha256' }, remote: { size: 110, file_date: '2026-10-20T00:00:00Z', hash: 'new-hash', hash_kind: 'sha256' } },
  { path: 'config.json', status: 'changed', local: { size: 10, file_date: null, hash: null, hash_kind: null }, remote: { size: 12, file_date: null, hash: null, hash_kind: null } },
] }
const queued: Operation = { id: 'update-1', kind: 'download', target_id: `hf-update:${model.id}`, state: 'queued', created_at: '2026-10-29T00:00:00Z', started_at: null, finished_at: null, stdout: '', stderr: '', error: null, exit_code: null }

afterEach(() => { cleanup(); vi.restoreAllMocks() })

function setup() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  vi.spyOn(api, 'getLocalModels').mockResolvedValue({ items: [model] })
  vi.spyOn(api, 'getDownloads').mockResolvedValue({ items: [] })
  const details = vi.spyOn(api, 'getModelFileUpdates').mockResolvedValue(files)
  const update = vi.spyOn(api, 'updateModelFiles').mockResolvedValue(queued)
  let finish: ((value: Operation) => void) | undefined
  vi.spyOn(api, 'getOperation').mockImplementation(() => new Promise<Operation>((resolve) => { finish = resolve }))
  const confirm = vi.spyOn(window, 'confirm')
  render(<QueryClientProvider client={client}><MemoryRouter initialEntries={['/models']}><ModelsPage /></MemoryRouter></QueryClientProvider>)
  return { client, details, update, confirm, finish: async (value: Operation) => {
    await waitFor(() => expect(finish).toBeTypeOf('function'))
    await act(async () => { finish!(value) })
  } }
}

it('opens compact file rows with dates, updates one file without a confirmation and keeps completed rows until the dialog closes', async () => {
  const f = setup()
  fireEvent.click(await screen.findByRole('button', { name: 'Доступно обновление · 2 файлов' }))
  const dialog = await screen.findByRole('dialog', { name: 'Обновления модели' })
  expect(await within(dialog).findByText('weights.gguf')).toBeInTheDocument()
  expect(within(dialog).getByText('Локально')).toBeInTheDocument()
  expect(within(dialog).getByText('Hugging Face')).toBeInTheDocument()
  expect(within(dialog).queryByText('Подробности')).not.toBeInTheDocument()
  expect(within(dialog).queryByText('old-hash', { exact: false })).not.toBeInTheDocument()
  const row = within(dialog).getAllByTestId('model-file-update')[0]
  expect(row).toHaveTextContent(new Date(files.items[0].local.file_date!).toLocaleString('ru-RU', { dateStyle: 'medium' }))
  expect(within(row).getByRole('button', { name: 'Обновить weights.gguf' })).toBeInTheDocument()
  fireEvent.click(within(dialog).getByRole('button', { name: 'Обновить weights.gguf' }))
  await waitFor(() => expect(f.update).toHaveBeenCalledWith(model.id, ['weights.gguf']))
  expect(f.confirm).not.toHaveBeenCalled()
  expect(await within(dialog).findByRole('status', { name: 'Обновление файлов' })).toBeInTheDocument()
  f.details.mockResolvedValue({ ...files, items: files.items.slice(1) })
  await f.finish({ ...queued, state: 'succeeded' })
  await waitFor(() => expect(within(dialog).getByRole('button', { name: 'Обновлено weights.gguf' })).toBeDisabled())
  expect(within(dialog).getByText('weights.gguf')).toBeInTheDocument()
  expect(within(dialog).getByText('config.json')).toBeInTheDocument()
  expect(within(dialog).getByRole('status', { name: 'Обновление файлов' })).toHaveTextContent('Обновление завершено')
  fireEvent.click(within(dialog).getByRole('button', { name: 'Закрыть' }))
  fireEvent.click(screen.getByRole('button', { name: 'Доступно обновление · 2 файлов' }))
  const reopened = await screen.findByRole('dialog', { name: 'Обновления модели' })
  await within(reopened).findByText('config.json')
  expect(within(reopened).queryByText('weights.gguf')).not.toBeInTheDocument()
})

it('updates all files and lets the user close the dialog while the download continues', async () => {
  const f = setup()
  fireEvent.click(await screen.findByRole('button', { name: 'Доступно обновление · 2 файлов' }))
  await screen.findByText('weights.gguf')
  fireEvent.click(screen.getByRole('button', { name: 'Обновить все изменённые' }))
  await waitFor(() => expect(f.update).toHaveBeenCalledWith(model.id, null))
  fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Закрыть' }))
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  expect(f.update).toHaveBeenCalledTimes(1)
  expect(f.confirm).not.toHaveBeenCalled()
  fireEvent.click(screen.getByRole('button', { name: 'Доступно обновление · 2 файлов' }))
  expect(await screen.findByRole('status', { name: 'Обновление файлов' })).toBeInTheDocument()
})

it('shows failures through the shared toast and leaves the update available for retry', async () => {
  const f = setup()
  f.update.mockRejectedValueOnce(new Error('HF недоступен'))
  fireEvent.click(await screen.findByRole('button', { name: 'Доступно обновление · 2 файлов' }))
  fireEvent.click(await screen.findByRole('button', { name: 'Обновить weights.gguf' }))
  expect(await screen.findByRole('alert')).toHaveTextContent('HF недоступен')
  expect(screen.getByRole('button', { name: 'Обновить weights.gguf' })).toBeEnabled()
  fireEvent.click(screen.getByRole('button', { name: 'Обновить weights.gguf' }))
  await waitFor(() => expect(f.update).toHaveBeenCalledTimes(2))
})
