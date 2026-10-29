import type { OpenAiModel } from '@/types/api'

export type VideoSettings = {
  aspect: 'square' | 'portrait' | 'landscape'
  resolution: number
  seconds: number
}
export type VideoSettingsByModel = Record<string, VideoSettings>
export const DEFAULT_VIDEO_SETTINGS: VideoSettings = { aspect: 'square', resolution: 640, seconds: 2 }
const storageKey = 'neural-deck.chat.video-settings.v1'

export function videoLimits(model?: OpenAiModel): { resolution: number; seconds: number } {
  const resolution = model?.capabilities?.max_resolution
  const seconds = model?.capabilities?.max_seconds
  return {
    resolution: typeof resolution === 'number' && Number.isInteger(resolution) && resolution >= 32 && resolution <= 16384 ? resolution : 768,
    seconds: typeof seconds === 'number' && Number.isInteger(seconds) && seconds >= 1 && seconds <= 600 ? seconds : 15,
  }
}

export function videoResolutionChoices(model?: OpenAiModel): number[] {
  const maximum = videoLimits(model).resolution
  return [...new Set([480, 640, maximum].filter((value) => value <= maximum))]
}

export function videoDurationChoices(model?: OpenAiModel): number[] {
  const maximum = videoLimits(model).seconds
  return [...new Set([2, 5, 10, maximum].filter((value) => value <= maximum))]
}

export function normalizeVideoSettings(settings: VideoSettings, model?: OpenAiModel): VideoSettings {
  const resolutions = videoResolutionChoices(model)
  const durations = videoDurationChoices(model)
  return {
    ...settings,
    resolution: resolutions.filter((value) => value <= settings.resolution).at(-1) ?? resolutions[0],
    seconds: durations.filter((value) => value <= settings.seconds).at(-1) ?? durations[0],
  }
}

export function videoRequestSize(settings: VideoSettings): string {
  const short = Math.max(32, Math.floor(settings.resolution / 32) * 32)
  const long = Math.floor(short * 16 / 9 / 32) * 32
  return settings.aspect === 'square' ? `${short}x${short}` : settings.aspect === 'portrait' ? `${short}x${long}` : `${long}x${short}`
}

export function readVideoSettings(): VideoSettingsByModel {
  try {
    const saved: unknown = JSON.parse(window.localStorage.getItem(storageKey) ?? '{}')
    if (!saved || typeof saved !== 'object' || Array.isArray(saved)) return {}
    return Object.fromEntries(Object.entries(saved).filter(([, value]) => value && typeof value === 'object'
      && ['square', 'portrait', 'landscape'].includes(value.aspect)
      && Number.isInteger(value.resolution) && value.resolution >= 32 && value.resolution <= 16384
      && Number.isInteger(value.seconds) && value.seconds >= 1 && value.seconds <= 600)
      .map(([model, value]) => [model, { aspect: value.aspect, resolution: value.resolution, seconds: value.seconds }]))
  } catch { return {} }
}

export function writeVideoSettings(settings: VideoSettingsByModel): boolean {
  try { window.localStorage.setItem(storageKey, JSON.stringify(settings)); return true }
  catch { return false }
}
