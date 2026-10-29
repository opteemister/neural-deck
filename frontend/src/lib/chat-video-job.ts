export type PendingVideo = { id: string; model: string; prompt: string; createdAt: number }
const key = 'neural-deck.chat.pending-video.v1'

export function readPendingVideo(): PendingVideo | null {
  try {
    const value = JSON.parse(window.sessionStorage.getItem(key) ?? 'null')
    return value && typeof value.id === 'string' && value.id && typeof value.model === 'string'
      && typeof value.prompt === 'string' && typeof value.createdAt === 'number' && Number.isFinite(value.createdAt)
      ? { id: value.id, model: value.model, prompt: value.prompt, createdAt: value.createdAt } : null
  } catch { return null }
}

export function savePendingVideo(value: PendingVideo): boolean {
  try { window.sessionStorage.setItem(key, JSON.stringify(value)); return true }
  catch { return false }
}

export function clearPendingVideo(id: string): void {
  try { if (readPendingVideo()?.id === id) window.sessionStorage.removeItem(key) }
  catch { /* Waiting still works when storage is unavailable. */ }
}
