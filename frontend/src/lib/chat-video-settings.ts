import type { OpenAiModel } from '@/types/api'

export type VideoSettings = {
  aspect: 'square' | 'portrait' | 'landscape'
  resolution: number
  seconds: number
}
export type VideoSettingsByModel = Record<string, VideoSettings>
export const DEFAULT_VIDEO_SETTINGS: VideoSettings = { aspect: 'square', resolution: 0, seconds: 0 }
const storageKey = 'neural-deck.chat.video-settings.v1'

export function videoLimits(model?: OpenAiModel): { resolution: number; seconds: number } {
  const resolution = model?.capabilities?.max_resolution
  const seconds = model?.capabilities?.max_seconds
  return {
    resolution: typeof resolution === 'number' && Number.isSafeInteger(resolution) && resolution > 0 ? resolution : 0,
    seconds: typeof seconds === 'number' && Number.isSafeInteger(seconds) && seconds > 0 ? seconds : 0,
  }
}

function relativeChoices(maximum: number): number[] {
  if (!maximum) return []
  return [...new Set([1 / 3, 2 / 3, 1].map((fraction) => Math.max(1, Math.floor(maximum * fraction))))]
}

export function videoResolutionChoices(model?: OpenAiModel): number[] {
  return relativeChoices(videoLimits(model).resolution)
}

export function videoDurationChoices(model?: OpenAiModel): number[] {
  return relativeChoices(videoLimits(model).seconds)
}

export function normalizeVideoSettings(settings: VideoSettings, model?: OpenAiModel): VideoSettings {
  const durations = videoDurationChoices(model)
  const resolutions = videoResolutionChoices(model)
  return {
    ...settings,
    resolution: settings.resolution > 0 ? resolutions.filter((value) => value <= settings.resolution).at(-1) ?? resolutions[0] ?? 0 : resolutions[Math.floor(resolutions.length / 2)] ?? 0,
    seconds: settings.seconds > 0 ? durations.filter((value) => value <= settings.seconds).at(-1) ?? durations[0] ?? 0 : durations[0] ?? 0,
  }
}

export function videoRequestSize(settings: VideoSettings): string {
  if (settings.resolution < 1) return ''
  const short = settings.resolution
  const long = Math.max(1, Math.floor(short * 16 / 9))
  const w = settings.aspect === 'portrait' ? short : settings.aspect === 'square' ? short : long
  const h = settings.aspect === 'landscape' ? short : settings.aspect === 'square' ? short : long
  return `${w}x${h}`
}

export function readVideoSettings(): VideoSettingsByModel {
  try {
    const saved: unknown = JSON.parse(window.localStorage.getItem(storageKey) ?? '{}')
    if (!saved || typeof saved !== 'object' || Array.isArray(saved)) return {}
    return Object.fromEntries(Object.entries(saved).filter(([, value]) => value && typeof value === 'object'
      && ['square', 'portrait', 'landscape'].includes(value.aspect)
      && Number.isSafeInteger(value.resolution) && value.resolution >= 1
      && Number.isInteger(value.seconds) && value.seconds >= 1 && Number.isSafeInteger(value.seconds))
      .map(([model, value]) => [model, { aspect: value.aspect, resolution: value.resolution, seconds: value.seconds }]))
  } catch { return {} }
}

export function writeVideoSettings(settings: VideoSettingsByModel): boolean {
  try { window.localStorage.setItem(storageKey, JSON.stringify(settings)); return true }
  catch { return false }
}
