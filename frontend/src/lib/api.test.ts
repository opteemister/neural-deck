// spec: version-zero
import { describe, expect, it, vi } from 'vitest'
import { api, request, streamChat, StudioApiError } from '@/lib/api'

describe('management API client', () => {
  it('unwraps the success envelope', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({ data: { host: '127.0.0.1' }, request_id: 'req_1' }), { status: 200 })))
    await expect(request<{ host: string }>('/api/v1/settings')).resolves.toEqual({ host: '127.0.0.1' })
  })

  it('preserves machine-readable error information', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({ error: { code: 'profile_not_found', message: 'Профиль не найден', details: {}, request_id: 'req_2', retryable: false } }), { status: 404 })))
    const result = request('/api/v1/profiles/missing')
    await expect(result).rejects.toBeInstanceOf(StudioApiError)
    await expect(result).rejects.toMatchObject({ status: 404, code: 'profile_not_found', requestId: 'req_2' })
  })

  it('keeps OpenAI models in their standard response shape', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({ object: 'list', data: [{ id: 'local-model', object: 'model' }] }), { status: 200 })))
    await expect(api.getOpenAiModels()).resolves.toEqual({ items: [{ id: 'local-model', object: 'model' }] })
  })

  it('keeps the management model registry separate from local artifacts', async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify({ data: { items: [{ id: 'qwen-public', upstream_model: 'Qwen/Qwen', instance_id: 'managed/demo/1-2', engine_id: 'vllm', profile_name: 'demo', observed_at: '2026-09-29T00:00:00Z' }] }, request_id: 'req_1' }), { status: 200 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ data: { items: [{ id: 'model_1', path: '/models/qwen' }] }, request_id: 'req_2' }), { status: 200 }))
    vi.stubGlobal('fetch', fetchMock)
    await expect(api.getModels()).resolves.toMatchObject({ items: [{ id: 'qwen-public', instance_id: 'managed/demo/1-2' }] })
    expect(fetchMock).toHaveBeenCalledWith('/api/v1/models', expect.anything())
    await expect(api.getLocalModels()).resolves.toEqual({ items: [{ id: 'model_1', path: '/models/qwen' }] })
  })

  it('reads streamed OpenAI deltas and the terminal marker', async () => {
    const stream = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(new TextEncoder().encode('data: {"choices":[{"delta":{"content":"hel"}}]}\n\n'))
        controller.enqueue(new TextEncoder().encode('data: {"choices":[{"delta":{"content":"lo"}}]}\n\ndata: [DONE]\n\n'))
        controller.close()
      },
    })
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(stream, { status: 200, headers: { 'content-type': 'text/event-stream' } })))
    const chunks: string[] = []
    await streamChat({ model: 'local', messages: [{ role: 'user', content: 'hi' }] }, (chunk) => { if (chunk.content) chunks.push(chunk.content) })
    expect(chunks.join('')).toBe('hello')
    expect(fetch).toHaveBeenCalledWith('/api/proxy/chat/completions', expect.objectContaining({ method: 'POST' }))
  })

  it('joins multiple data lines belonging to one SSE event', async () => {
    const stream = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(new TextEncoder().encode('data: {"choices":[\n'))
        controller.enqueue(new TextEncoder().encode('data: {"delta":{"content":"ok"}}]}\n\n'))
        controller.close()
      },
    })
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(stream, { status: 200, headers: { 'content-type': 'text/event-stream' } })))
    const chunks: string[] = []
    await streamChat({ model: 'local', messages: [{ role: 'user', content: 'hi' }] }, (chunk) => { if (chunk.content) chunks.push(chunk.content) })
    expect(chunks).toEqual(['ok'])
  })

  it('serializes optional token statistics filters', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ data: { items: [] }, request_id: 'req_1' }), { status: 200 }))
    vi.stubGlobal('fetch', fetchMock)
    await api.getTokenStats({ profile_name: 'qwen/local', from: '2026-09-29T00:00:00Z', to: '2026-09-29T01:00:00Z' })
    expect(fetchMock).toHaveBeenCalledWith('/api/v1/token-stats?profile_name=qwen%2Flocal&from=2026-09-29T00%3A00%3A00Z&to=2026-09-29T01%3A00%3A00Z', expect.anything())
  })

  it('builds download and maintenance requests from contract payloads', async () => {
    const fetchMock = vi.fn().mockImplementation(() => new Response(JSON.stringify({ data: { id: 'op_1', state: 'queued' }, request_id: 'req_1' }), { status: 202, headers: { 'content-type': 'application/json' } }))
    vi.stubGlobal('fetch', fetchMock)
    await api.downloadModels({ repo_id: 'org/model', revision: 'abc', files: ['config.json'] })
    await api.maintenance('inst_1', 'python -m pip check')
    expect(fetchMock).toHaveBeenNthCalledWith(1, '/api/v1/models/downloads', expect.objectContaining({ method: 'POST', body: JSON.stringify({ repo_id: 'org/model', revision: 'abc', files: ['config.json'] }) }))
    expect(fetchMock).toHaveBeenNthCalledWith(2, '/api/v1/installations/inst_1/maintenance', expect.objectContaining({ method: 'POST', body: JSON.stringify({ command: 'python -m pip check' }) }))
  })
})
