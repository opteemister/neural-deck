// spec: version-zero
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { StatusPage } from '@/pages/status'

const envelope = (data: unknown) => JSON.stringify({ data, request_id: 'req_test' })

afterEach(() => vi.unstubAllGlobals())

describe('StatusPage', () => {
  it('renders component status and the health result', async () => {
    vi.stubGlobal('fetch', vi.fn().mockImplementation((input: RequestInfo | URL) => {
      const url = String(input)
      if (url.endsWith('/api/v1/status')) return Promise.resolve(new Response(envelope({ overall: 'degraded', components: [{ name: 'backend', state: 'ok', message: null, checked_at: '2026-09-29T00:00:00Z' }] }), { status: 200 }))
      if (url.endsWith('/api/v1/health')) return Promise.resolve(new Response(envelope({ status: 'ok' }), { status: 200 }))
      return Promise.resolve(new Response(envelope({}), { status: 200 }))
    }))
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(<QueryClientProvider client={client}><MemoryRouter initialEntries={['/status']}><StatusPage /></MemoryRouter></QueryClientProvider>)
    expect(await screen.findByText('Компоненты')).toBeInTheDocument()
    expect(screen.getByText('backend')).toBeInTheDocument()
    expect(screen.getAllByText('Работает')).toHaveLength(2)
    expect(screen.getByText('Есть ограничения')).toBeInTheDocument()
  })
})
