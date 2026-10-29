import { api, NeuralDeckApiError } from '@/lib/api'
import type { ChatTimings, ChatUsage } from '@/types/api'
import type { ChatOperation, MediaResult, VisionContentPart } from '@/lib/chat-media'

export type HarnessTurn = {
  id: string
  model: string
  operation?: ChatOperation
  prompt: string
  answer: string
  reasoning: string
  attachments: Array<{ name: string; type: string; previewUrl?: string }>
  results: MediaResult[]
  state: 'streaming' | 'complete' | 'cancelled' | 'error'
  phase?: string | null
  error?: string
  usage?: ChatUsage
  timings?: ChatTimings
  firstToken?: number
  finishReason?: string
  elapsed?: number
}
export type ChatConversation = { id: string; busy: boolean; turns: HarnessTurn[] }
export type ChatSummary = {
  id: string
  title: string
  model: string | null
  updated_at: number | null
  busy: boolean
}
export type ChatHistoryData = { items: ChatSummary[] }

export type HarnessRequest = {
  operation?: 'chat' | 'ocr'
  id: string
  model: string
  prompt: string
  content: string | VisionContentPart[]
  image_names: string[]
  reasoning: Record<string, unknown>
  media_defaults: Record<string, { size: string; background?: string; seconds?: number }>
}

function delay(ms: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    const abort = () => { window.clearTimeout(timer); signal.removeEventListener('abort', abort); reject(new DOMException('Aborted', 'AbortError')) }
    const timer = window.setTimeout(() => { signal.removeEventListener('abort', abort); resolve() }, ms)
    signal.addEventListener('abort', abort, { once: true })
    if (signal.aborted) abort()
  })
}

/** Only reads are retried. Accepted generations keep running when this view closes. */
export async function watchConversation(
  id: string, onUpdate: (conversation: ChatConversation) => void,
  signal: AbortSignal, onReconnect: () => void,
): Promise<void> {
  let failures = 0
  while (!signal.aborted) {
    const controller = new AbortController()
    const abort = () => controller.abort()
    signal.addEventListener('abort', abort, { once: true })
    const timer = window.setTimeout(abort, 20_000)
    try {
      const conversation = await api.getChat(id, controller.signal)
      if (signal.aborted) return
      onUpdate(conversation)
      failures = 0
      if (!conversation.busy) return
    } catch (error) {
      if (signal.aborted) return
      if (error instanceof NeuralDeckApiError && ![408, 429, 500, 502, 503, 504].includes(error.status)) throw error
      failures++
      onReconnect()
    } finally {
      window.clearTimeout(timer)
      signal.removeEventListener('abort', abort)
    }
    await delay(failures ? Math.min(5_000, 500 * 2 ** Math.min(failures, 4)) : 350, signal)
  }
}
