import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { LogsDrawerProvider } from '@/components/logs-drawer'
import { api, queryKeys } from '@/lib/api'
import { ProfilesPage } from '@/pages/profiles'
import type { ProfileData } from '@/types/api'

afterEach(() => vi.restoreAllMocks())

async function editProfile(overrides: Partial<ProfileData> = {}) {
  const profile: ProfileData = {
    version: 1, name: 'demo', model_name: 'demo-model',
    engine: { id: 'sglang', installation_id: 'inst-1' },
    command: '{{engine}} serve --port {{port}}', port: 8013,
    environment: {}, autostart: false, ...overrides,
  }
  const client = new QueryClient({ defaultOptions: { queries: { staleTime: Infinity } } })
  client.setQueryData(queryKeys.profiles, { items: [profile], issues: [] })
  client.setQueryData(['profile', 'demo'], profile)
  client.setQueryData(queryKeys.engines, { items: [{
    id: 'sglang', display_name: 'SGLang', command_template: profile.command,
    types: ['text'], api: null, ui_url: null, installations: [{
      id: 'inst-1', engine_id: 'sglang', status: 'ready', version: 'test', executable_path: '/bin/sglang',
    }],
  }] })
  client.setQueryData(queryKeys.profileSummary, { items: [] })
  client.setQueryData(queryKeys.settings, { models_root: '/models' })
  client.setQueryData(queryKeys.localModels, { items: [] })
  client.setQueryData(queryKeys.system, { instances: [], gpus: [] })
  render(<QueryClientProvider client={client}><MemoryRouter><LogsDrawerProvider><ProfilesPage /></LogsDrawerProvider></MemoryRouter></QueryClientProvider>)
  fireEvent.click(screen.getByRole('button', { name: 'Изменить' }))
  await waitFor(() => expect(screen.getByLabelText('Команда запуска')).toHaveValue(profile.command))
  return profile
}

describe('Explicit profile port', () => {
  it('shows the saved port separately and preserves the command template when saving', async () => {
    const profile = await editProfile()
    expect(screen.getByLabelText('Порт')).toHaveValue(8013)
    const update = vi.spyOn(api, 'updateProfile').mockResolvedValue(profile)
    fireEvent.change(screen.getByLabelText('Порт'), { target: { value: '8020' } })
    expect(screen.getByLabelText('Команда запуска')).toHaveValue('{{engine}} serve --port {{port}}')
    fireEvent.click(screen.getByRole('button', { name: 'Сохранить профиль' }))
    await waitFor(() => expect(update).toHaveBeenCalledWith('demo', expect.objectContaining({
      port: 8020, command: '{{engine}} serve --port {{port}}',
    })))
    const submitted = update.mock.calls[0][1]
    expect(submitted).not.toHaveProperty('types')
    expect(submitted).not.toHaveProperty('api')
    expect(submitted).not.toHaveProperty('ui_url')
    expect(submitted.model ?? {}).not.toHaveProperty('draft_path')
  })

  it('keeps an empty port empty instead of inserting a default', async () => {
    await editProfile({ port: null, command: '{{engine}} serve --port 9000' })
    expect(screen.getByLabelText('Порт')).toHaveValue(null)
    expect(screen.getByLabelText('Команда запуска')).toHaveValue('{{engine}} serve --port 9000')
  })

  it('saves a cleared port as null without changing a literal command port', async () => {
    const profile = await editProfile({ command: '{{engine}} serve --port 9000' })
    const update = vi.spyOn(api, 'updateProfile').mockResolvedValue(profile)
    fireEvent.change(screen.getByLabelText('Порт'), { target: { value: '' } })
    fireEvent.click(screen.getByRole('button', { name: 'Сохранить профиль' }))
    await waitFor(() => expect(update).toHaveBeenCalledWith('demo', expect.objectContaining({
      port: null, command: '{{engine}} serve --port 9000',
    })))
  })

  it.each(['', '0', '65536', '1.5'])('requires a valid explicit port for {{port}}: %s', async (value) => {
    await editProfile()
    fireEvent.change(screen.getByLabelText('Порт'), { target: { value } })
    expect(screen.getByRole('button', { name: 'Сохранить профиль' })).toBeDisabled()
  })
})
