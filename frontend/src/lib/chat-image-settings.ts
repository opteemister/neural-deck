import type { OpenAiModel } from '@/types/api'

export const IMAGE_SIZES = [
  { value: '1024x1024', label: 'Квадрат', dimensions: '1024×1024', aspect: 'square' },
  { value: '768x1024', label: 'Вертикальный', dimensions: '768×1024', aspect: 'portrait' },
  { value: '1024x768', label: 'Горизонтальный', dimensions: '1024×768', aspect: 'landscape' },
] as const

export type ImageSettings = { size: (typeof IMAGE_SIZES)[number]['value']; transparent: boolean; maxResolution: boolean }
export type ImageSettingsByModel = Record<string, ImageSettings>
export const DEFAULT_IMAGE_SETTINGS: ImageSettings = { size: '1024x1024', transparent: false, maxResolution: false }
const storageKey = 'neural-deck.chat.image-settings.v1'

const aspectRatios: Record<ImageSettings['size'], number> = {
  '1024x1024': 1,
  '768x1024': 9 / 16,
  '1024x768': 16 / 9,
}

export function maxImageMegapixels(model: OpenAiModel | undefined): number {
  const value = model?.capabilities?.max_megapixels
  return typeof value === 'number' && Number.isFinite(value) && value >= 0.1 && value <= 128 ? value : 4
}

export function imageRequestSize(settings: Pick<ImageSettings, 'size' | 'maxResolution'>, maximumMegapixels = 4): string {
  const pixels = maximumMegapixels * (settings.maxResolution ? 1 : 0.5) * 1024 * 1024
  const ratio = aspectRatios[settings.size]
  // Match the native image-node grid; the megapixel budget is approximate.
  const align = (value: number) => Math.max(64, Math.round(value / 64) * 64)
  return `${align(Math.sqrt(pixels * ratio))}x${align(Math.sqrt(pixels / ratio))}`
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

