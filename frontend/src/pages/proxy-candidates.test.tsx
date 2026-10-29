import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { api } from '@/lib/api'
import { comfyApi } from '@/lib/comfy-api'
import { ProxyPage } from '@/pages/proxy'
import type { OpenAIConnection, ProxyRoute } from '@/types/api'

const connection: OpenAIConnection = { id: 'server', name: 'GPU server', base_url: 'http://remote.test/v1', has_api_key: true }
const saved: ProxyRoute = { name: 'saved', kind: 'alias', available: true, diagnostics: [], candidate_profile_ids: [], candidate_ids: ['openai:server/old-model'], candidates: [{ id: 'openai:server/old-model', connection_id: 'server', connection_name: 'GPU server', profile_id: null, profile_name: null, instance_id: null, upstream_model: 'old-model', engine_id: 'openai', port: null, state: 'configured', available: true, reason: null }] }

afterEach(() => { cleanup(); vi.restoreAllMocks() })

function setup(routes: ProxyRoute[] = [], connections = [connection]) {
  vi.spyOn(api, 'getProxyRoutes').mockResolvedValue({ items: routes })
  vi.spyOn(api, 'getProfiles').mockResolvedValue({ items: [{ version: 1, name: 'local-profile', model_name: 'local-model', engine: { id: 'vllm', installation_id: 'local-install' }, command: 'serve', environment: {}, autostart: false }], issues: [] })
  vi.spyOn(comfyApi, 'getConfig').mockResolvedValue({ sources: {}, models: {} })
  vi.spyOn(api, 'getOpenAIConnections').mockResolvedValue({ items: connections })
  const models = vi.spyOn(api, 'getConnectionModels').mockResolvedValue({ items: [{ id: 'remote-model', object: 'model' }] })
  const create = vi.spyOn(api, 'createProxyAlias').mockResolvedValue(saved)
  const update = vi.spyOn(api, 'updateProxyAlias').mockResolvedValue(saved)
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(<QueryClientProvider client={client}><MemoryRouter initialEntries={['/proxy?tab=models']}><ProxyPage /></MemoryRouter></QueryClientProvider>)
  return { models, create, update }
}

describe('unified alias candidates', () => {
  it('shows local and connection models in one checklist and saves both in selection order', async () => {
    const { create } = setup()
    fireEvent.click(await screen.findByRole('button', { name: 'Новый алиас' }))
    const dialog = within(screen.getByRole('dialog'))
    const remote = await dialog.findByRole('checkbox', { name: /remote-model/ })
    const local = await dialog.findByRole('checkbox', { name: /local-model/ })
    expect(dialog.queryByLabelText('Источник')).not.toBeInTheDocument()
    expect(dialog.queryByText('Локальные профили')).not.toBeInTheDocument()
    fireEvent.change(dialog.getByLabelText('Имя алиаса'), { target: { value: 'mixed' } })
    fireEvent.click(remote)
    fireEvent.click(local)
    fireEvent.click(dialog.getByRole('button', { name: 'Добавить алиас' }))
    await waitFor(() => expect(create.mock.calls[0]?.[0]).toEqual({ name: 'mixed', profile_ids: [], candidates: [{ connection_id: 'server', model: 'remote-model' }, { profile_id: 'local-profile' }] }))
  })

  it('keeps the same model from different connections as separate candidates', async () => {
    setup([], [connection, { ...connection, id: 'other-server', name: 'Other server' }])
    fireEvent.click(await screen.findByRole('button', { name: 'Новый алиас' }))
    await waitFor(() => expect(within(screen.getByRole('dialog')).getAllByRole('checkbox', { name: /remote-model/ })).toHaveLength(2))
  })

  it('preserves an already selected model when its connection catalogue is unavailable', async () => {
    const { models, update } = setup([saved])
    models.mockRejectedValue(new Error('offline'))
    fireEvent.click(await screen.findByRole('button', { name: 'Изменить алиас saved' }))
    const dialog = within(screen.getByRole('dialog'))
    expect(await dialog.findByRole('checkbox', { name: /old-model/ })).toBeChecked()
    fireEvent.click(dialog.getByRole('button', { name: 'Сохранить' }))
    await waitFor(() => expect(update).toHaveBeenCalledWith('saved', { name: 'saved', profile_ids: [], candidates: [{ connection_id: 'server', model: 'old-model' }] }))
  })
})
