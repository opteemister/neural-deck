import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it } from 'vitest'
import { LogsDrawerProvider } from '@/components/logs-drawer'
import { queryKeys } from '@/lib/api'
import { ProfilesPage } from '@/pages/profiles'
import type { ProfileSummary } from '@/types/api'

describe('Profile card ports', () => {
  it.each([
    { ports: [8100], text: '8100', title: '8100', source: 'process_sockets' },
    { ports: [8100, 9100, 9200], text: '8100…', title: '8100, 9100, 9200', source: 'process_sockets' },
    { ports: [8100, 9100], text: '8100…', title: '8100, 9100', source: 'launch_configuration' },
  ])('shows $text with all ports on hover from $source', ({ ports, text, title, source }) => {
    const client = new QueryClient({ defaultOptions: { queries: { staleTime: Infinity } } })
    client.setQueryData(queryKeys.settings, { models_root: '/models' })
    client.setQueryData(queryKeys.profiles, { items: [{
      version: 1, profile_id: 'profile-1', name: 'demo', model_name: 'demo-model',
      engine: { id: 'vllm', installation_id: 'inst-1' }, command: 'serve',
      environment: {}, autostart: false,
    }], issues: [] })
    const unavailable = { value: null, unit: '', state: 'unavailable' as const, source: 'test' }
    const summary: ProfileSummary = {
      profile_id: 'profile-1', profile_name: 'demo',
      period: { from: '2026-10-28T00:00:00Z', to: '2026-10-28T12:00:00Z', timezone: 'UTC' },
      endpoint: { port: ports[0], ports, state: 'ok', source, conflict: false, conflict_with: [] },
      memory: { disk_bytes: unavailable, vram_current_bytes: unavailable, vram_last_bytes: unavailable, vram_avg_bytes: unavailable, vram_peak_bytes: unavailable },
      performance: { prefill_tps: unavailable, decode_tps: unavailable },
      concurrency: { optimal: unavailable, limit: unavailable, observed_max: unavailable, limit_source: 'test', benchmark_state: 'not_measured' },
      load: { percent: unavailable },
    }
    client.setQueryData(queryKeys.profileSummary, { items: [summary] })
    client.setQueryData(queryKeys.engines, { items: [] })
    client.setQueryData(queryKeys.localModels, { items: [] })
    client.setQueryData(queryKeys.system, { instances: [] })

    render(<QueryClientProvider client={client}><MemoryRouter><LogsDrawerProvider><ProfilesPage /></LogsDrawerProvider></MemoryRouter></QueryClientProvider>)
    expect(screen.getByText(text)).toHaveAttribute('title', title)
  })
})
