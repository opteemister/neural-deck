import { afterEach, describe, expect, it, vi } from 'vitest'
import { openAiEndpoint } from '@/lib/openai-endpoint'

afterEach(() => { vi.unstubAllEnvs(); vi.unstubAllGlobals(); vi.resetModules() })

describe('OpenAI endpoint', () => {
  it('accepts overrides with and without the v1 prefix', () => {
    expect(openAiEndpoint('https://ai.example.com')).toBe('https://ai.example.com/v1')
    expect(openAiEndpoint('https://ai.example.com/v1/')).toBe('https://ai.example.com/v1')
  })

  it('preserves the external override while routing chat through the internal API', async () => {
    vi.stubEnv('VITE_API_BASE_URL', '')
    vi.stubEnv('VITE_OPENAI_API_BASE_URL', 'https://ai.example.com/v1/')
    const fetchMock = vi.fn().mockImplementation((url: string) => Promise.resolve(
      url.endsWith('/models')
        ? new Response(JSON.stringify({ data: [] }))
        : new Response('data: [DONE]\n\n', { headers: { 'content-type': 'text/event-stream' } }),
    ))
    vi.stubGlobal('fetch', fetchMock)
    const { api, streamChat, getOpenAiEndpoint } = await import('@/lib/api')
    expect(await getOpenAiEndpoint()).toBe('https://ai.example.com/v1')
    await api.getOpenAiModels()
    fetchMock.mockResolvedValueOnce(new Response(JSON.stringify({ choices: [] })))
    await api.completeChat({ model: 'local', messages: [] })
    await streamChat({ model: 'local', messages: [] }, () => {})
    expect(fetchMock.mock.calls.map(([url]) => url)).toEqual([
      '/api/proxy/models',
      '/api/proxy/chat/completions',
      '/api/proxy/chat/completions',
    ])
  })

  it('uses the backend machine IP even when the interface uses a domain', async () => {
    vi.stubEnv('VITE_OPENAI_API_BASE_URL', '')
    vi.stubEnv('VITE_API_BASE_URL', 'https://ai.example.com')
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ data: { url: 'http://192.168.1.50:8080/v1' } })))
    vi.stubGlobal('fetch', fetchMock)
    const { getOpenAiEndpoint } = await import('@/lib/api')
    expect(await getOpenAiEndpoint()).toBe('http://192.168.1.50:8080/v1')
    expect(fetchMock).toHaveBeenCalledWith('https://ai.example.com/api/v1/openai-endpoint', expect.anything())
  })

  it('propagates an unavailable machine IP instead of substituting a domain', async () => {
    vi.stubEnv('VITE_OPENAI_API_BASE_URL', '')
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({ error: { code: 'machine_ip_unavailable', message: 'IP недоступен' } }), { status: 503 })))
    const { getOpenAiEndpoint } = await import('@/lib/api')
    await expect(getOpenAiEndpoint()).rejects.toMatchObject({ code: 'machine_ip_unavailable' })
  })
})
