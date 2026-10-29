import { afterEach, describe, expect, it, vi } from 'vitest'
import { comfyApi } from '@/lib/comfy-api'

afterEach(() => vi.unstubAllGlobals())

describe('comfyApi', () => {
  it('uses same-origin config and unwraps the success envelope', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ data: { sources: {}, models: {} } }), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    }))
    vi.stubGlobal('fetch', fetchMock)

    await expect(comfyApi.getConfig()).resolves.toEqual({ sources: {}, models: {} })
    expect(fetchMock).toHaveBeenCalledWith('/api/v1/proxy/comfyui', expect.objectContaining({ headers: expect.any(Headers) }))
  })

  it('keeps a server error message for failed saves', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({ error: { message: 'invalid workflow' } }), { status: 400 })))

    await expect(comfyApi.putConfig({ sources: {}, models: {} })).rejects.toThrow('invalid workflow')
  })
})
