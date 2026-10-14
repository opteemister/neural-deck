// spec: version-zero
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, useLocation } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { SettingsPage } from '@/pages/settings'

const envelope = (data: unknown) => JSON.stringify({ data, request_id: 'req_test' })
const settings = { data_root: '/data', models_root: '/models', log_root: '/logs', extra_venv_roots: [], host: '127.0.0.1', port: 8080 }
function LocationProbe() { return <span data-testid="location-state">{useLocation().search}</span> }

afterEach(() => vi.unstubAllGlobals())

describe('Settings workflow', () => {
  it('persists models_root with PATCH and keeps the URL route stable', async () => {
    const fetchMock = vi.fn().mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      if (init?.method === 'PATCH') return Promise.resolve(new Response(envelope({ ...settings, models_root: '/models/new' }), { status: 200 }))
      return Promise.resolve(new Response(envelope(settings), { status: 200 }))
    })
    vi.stubGlobal('fetch', fetchMock)
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(<QueryClientProvider client={client}><MemoryRouter initialEntries={['/settings?tab=paths']}><SettingsPage /><LocationProbe /></MemoryRouter></QueryClientProvider>)
    const input = await screen.findByLabelText('Корень моделей')
    fireEvent.change(input, { target: { value: '/models/new' } })
    fireEvent.click(screen.getByRole('button', { name: 'Сохранить' }))
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith('/api/v1/settings', expect.objectContaining({ method: 'PATCH', body: JSON.stringify({ models_root: '/models/new' }) })))
    expect(await screen.findByRole('status')).toHaveTextContent('Настройки сохранены')
    expect(screen.getByTestId('location-state')).toHaveTextContent('?tab=paths')
  })

  it('shows the backend error instead of claiming settings were saved', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({ error: { code: 'internal_error', message: 'settings unavailable', details: {}, request_id: 'req_error', retryable: false } }), { status: 500 })))
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(<QueryClientProvider client={client}><MemoryRouter initialEntries={['/settings']}><SettingsPage /></MemoryRouter></QueryClientProvider>)
    expect(await screen.findByText('Настройки недоступны')).toBeInTheDocument()
    expect(screen.getByText('settings unavailable')).toBeInTheDocument()
  })
})
