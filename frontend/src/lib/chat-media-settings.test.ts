import { describe, expect, it } from 'vitest'
import { imageRequestSize, maxImageMegapixels } from './chat-image-settings'
import { DEFAULT_VIDEO_SETTINGS, normalizeVideoSettings, videoDurationChoices, videoRequestSize, videoResolutionChoices } from './chat-video-settings'
import type { OpenAiModel } from '@/types/api'

function model(capabilities: Record<string, unknown>): OpenAiModel {
  return { id: 'arbitrary-model', object: 'model', capabilities }
}

describe('media settings depend only on alias maxima', () => {
  it('does not invent model maxima when metadata is absent', () => {
    expect(maxImageMegapixels(model({}))).toBeUndefined()
    expect(imageRequestSize({ size: 'square', maxResolution: true }, undefined)).toBe('')
    expect(videoResolutionChoices(model({}))).toEqual([])
    expect(videoDurationChoices(model({}))).toEqual([])
    expect(normalizeVideoSettings(DEFAULT_VIDEO_SETTINGS, model({}))).toEqual(DEFAULT_VIDEO_SETTINGS)
  })
  it('computes image canvases directly from the MP budget and aspect', () => {
    const settings = { size: 'landscape' as const, maxResolution: true }
    const [w, h] = imageRequestSize(settings, 1.234).split('x').map(Number)
    expect(w * h).toBeLessThanOrEqual(1.234 * 1024 * 1024)
    expect(w).toBe(Math.floor(Math.sqrt(1.234 * 1024 * 1024 * 16 / 9)))
    expect(h).toBe(Math.floor(Math.sqrt(1.234 * 1024 * 1024 * 9 / 16)))
    expect(imageRequestSize(settings, 2.468)).not.toBe(`${w}x${h}`)
  })
  it('computes video choices and sizes from arbitrary alias maxima without a pixel grid', () => {
    const alias = model({ max_resolution: 901, max_seconds: 23 })
    expect(videoResolutionChoices(alias)).toEqual([300, 600, 901])
    expect(videoDurationChoices(alias)).toEqual([7, 15, 23])
    expect(videoRequestSize({ aspect: 'landscape', resolution: 901, seconds: 23 })).toBe('1601x901')
    const next = model({ max_resolution: 1501, max_seconds: 61 })
    expect(videoResolutionChoices(next)).toEqual([500, 1000, 1501])
    expect(videoDurationChoices(next)).toEqual([20, 40, 61])
  })
})
