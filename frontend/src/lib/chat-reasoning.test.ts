import { describe, expect, it } from 'vitest'
import { reasoningChoices, reasoningRequest } from '@/lib/chat-reasoning'
import type { OpenAiModel } from '@/types/api'

const model: OpenAiModel = { id: 'qwen', object: 'model', reasoning_capabilities: {
  efforts: ['low', 'medium', 'xhigh'], can_disable_thinking: true,
  effort_parameter: 'chat_template_kwargs', default_effort: 'xhigh',
} }

describe('reasoning transport', () => {
  it('shows exactly the advertised levels and sends standard fields for every upstream', () => {
    expect(reasoningChoices(model).all.map((option) => option.value)).toEqual(['low', 'medium', 'xhigh', 'auto', 'off'])
    expect(reasoningRequest('low', model)).toEqual({ reasoning_effort: 'low' })
    expect(reasoningRequest('off', model)).toEqual({ reasoning_effort: 'none' })
    expect(reasoningRequest('auto', model)).toEqual({})
    expect(reasoningRequest('high', model)).toEqual({})
  })
  it('supports max when advertised without offering it to other models', () => {
    const maximum = { ...model, reasoning_capabilities: { ...model.reasoning_capabilities!, efforts: ['max'] } }
    expect(reasoningChoices(maximum).upper.map((option) => option.label)).toEqual(['Max'])
    expect(reasoningRequest('max', maximum)).toEqual({ reasoning_effort: 'max' })
  })
})
