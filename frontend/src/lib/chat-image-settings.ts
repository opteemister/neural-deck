import type { OpenAiModel } from '@/types/api'

export const IMAGE_SIZES = [
  { value: 'square', label: 'Квадрат', aspect: 'square' },
  { value: 'portrait', label: 'Вертикальный', aspect: 'portrait' },
  { value: 'landscape', label: 'Горизонтальный', aspect: 'landscape' },
] as const

export type ImageSettings = { size: (typeof IMAGE_SIZES)[number]['value']; transparent: boolean; maxResolution: boolean }
export type ImageSettingsByModel = Record<string, ImageSettings>
export const DEFAULT_IMAGE_SETTINGS: ImageSettings = { size: 'square', transparent: false, maxResolution: false }
const storageKey = 'neural-deck.chat.image-settings.v1'

const aspectRatios: Record<ImageSettings['size'], number> = {
  'square': 1,
  'portrait': 9 / 16,
  'landscape': 16 / 9,
}

export function maxImageMegapixels(model: OpenAiModel | undefined): number | undefined {
  const value = model?.capabilities?.max_megapixels
  return typeof value === 'number' && Number.isFinite(value) && value > 0 ? value : undefined
}

export function imageRequestSize(settings: Pick<ImageSettings, 'size' | 'maxResolution'>, maximumMegapixels: number | undefined): string {
  if (maximumMegapixels === undefined) return ''
  const pixels = maximumMegapixels * (settings.maxResolution ? 1 : 0.5) * 1024 * 1024
  const ratio = aspectRatios[settings.size]
  return `${Math.max(1, Math.floor(Math.sqrt(pixels * ratio)))}x${Math.max(1, Math.floor(Math.sqrt(pixels / ratio)))}`
}

export function readImageSettings(): ImageSettingsByModel {
  try {
    const saved: unknown = JSON.parse(window.localStorage.getItem(storageKey) ?? '{}')
    if (!saved || typeof saved !== 'object' || Array.isArray(saved)) return {}
    return Object.fromEntries(Object.entries(saved).flatMap(([model, value]) => {
      if (!value || typeof value !== 'object' || !IMAGE_SIZES.some((size) => size.value === value.size) || typeof value.transparent !== 'boolean') return []
      return [[model, { size: value.size as ImageSettings['size'], transparent: value.transparent, maxResolution: value.maxResolution === true }]]
    }))
  } catch { return {} }
}

export function writeImageSettings(settings: ImageSettingsByModel): boolean {
  try { window.localStorage.setItem(storageKey, JSON.stringify(settings)); return true }
  catch { return false }
}
