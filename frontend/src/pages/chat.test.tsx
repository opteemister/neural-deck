// spec: version-zero
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ChatPage } from '@/pages/chat'

function streamResponse(text: string): Response {
  const stream = new ReadableStream<Uint8Array>({
    start(controller) {
      controller.enqueue(new TextEncoder().encode(`data: {"choices":[{"delta":{"content":"${text}"}}]}\n\ndata: [DONE]\n\n`))
      controller.close()
    },
  })
  return new Response(stream, { status: 200, headers: { 'content-type': 'text/event-stream' } })
}

afterEach(() => vi.unstubAllGlobals())

describe('ChatPage', () => {
  it('uses a registered model and renders an SSE assistant response', async () => {
    const fetchMock = vi.fn().mockImplementation((input: RequestInfo | URL) => {
      const url = String(input)
      if (url.endsWith('/v1/models')) return new Response(JSON.stringify({ object: 'list', data: [{ id: 'local-model', object: 'model' }] }), { status: 200 })
      if (url.endsWith('/v1/chat/completions')) return streamResponse('готово')
      return new Response(JSON.stringify({ error: { code: 'not_found', message: 'not found', details: {}, request_id: 'req', retryable: false } }), { status: 404 })
    })
    vi.stubGlobal('fetch', fetchMock)
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(<QueryClientProvider client={client}><MemoryRouter initialEntries={['/chat?model=local-model']}><ChatPage /></MemoryRouter></QueryClientProvider>)
    const textbox = await screen.findByLabelText('Сообщение')
    fireEvent.change(textbox, { target: { value: 'Привет' } })
    fireEvent.click(screen.getByRole('button', { name: 'Отправить' }))
    await waitFor(() => expect(screen.getByText('готово')).toBeInTheDocument())
    expect(fetchMock).toHaveBeenCalledWith('/v1/chat/completions', expect.objectContaining({ method: 'POST' }))
    expect(JSON.parse(String(fetchMock.mock.calls.find(([input]) => String(input).endsWith('/v1/chat/completions'))?.[1]?.body)).model).toBe('local-model')
  })
})
