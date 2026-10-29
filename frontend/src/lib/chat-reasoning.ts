import type { OpenAiModel } from '@/types/api'

export type Reasoning = 'auto' | 'off' | 'minimal' | 'low' | 'medium' | 'high' | 'xhigh' | 'max'
const levels: Array<{ value: Reasoning; label: string }> = [
  { value: 'minimal', label: 'Minimal' }, { value: 'low', label: 'Low' },
  { value: 'medium', label: 'Medium' }, { value: 'high', label: 'High' }, { value: 'xhigh', label: 'XHigh' }, { value: 'max', label: 'Max' },
]

export function reasoningChoices(model?: OpenAiModel) {
  const capabilities = model?.reasoning_capabilities
  const upper = levels.filter((option) => capabilities?.efforts.includes(option.value))
  const lower: Array<{ value: Reasoning; label: string }> = [{ value: 'auto', label: 'Авто' }]
  if (capabilities?.can_disable_thinking || capabilities?.efforts.includes('none')) lower.push({ value: 'off', label: 'Выкл.' })
  return { upper, lower, all: [...upper, ...lower] }
}

export function reasoningRequest(mode: Reasoning, model?: OpenAiModel): {
  reasoning_effort?: string; chat_template_kwargs?: { enable_thinking?: boolean; reasoning_effort?: string }
} {
  const capabilities = model?.reasoning_capabilities
  if (!capabilities || mode === 'auto') return {}
  if (mode === 'off' && capabilities.can_disable_thinking) return { reasoning_effort: 'none' }
  const effort = mode === 'off' ? 'none' : mode
  if (!capabilities.efforts.includes(effort)) return {}
  return { reasoning_effort: effort }
}
