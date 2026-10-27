import { fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ChatOptions } from '@/components/chat-options'

const models = [
  {
    id: 'text-model', object: 'model' as const, types: ['text' as const],
    reasoning_capabilities: { efforts: ['xhigh'], effort_parameter: 'reasoning_effort' as const, can_disable_thinking: true },
  },
  { id: 'image-model', object: 'model' as const, types: ['image' as const] },
  { id: 'video-model', object: 'model' as const, types: ['video' as const] },
  { id: 'ocr-model', object: 'model' as const, types: ['ocr' as const] },
]

afterEach(() => vi.restoreAllMocks())

function renderOptions(model = 'image-model') {
  const onModel = vi.fn()
  const onReasoning = vi.fn()
  render(<ChatOptions models={models} model={model} reasoning="auto" onModel={onModel} onReasoning={onReasoning} disabled={false} loading={false} error={false} onOpen={vi.fn()} />)
  return { onModel, onReasoning }
}

describe('ChatOptions', () => {
  it('keeps all models in the existing flat picker without search or replacing a media model', () => {
    const { onModel } = renderOptions()
    expect(onModel).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('button', { name: 'Выбрать модель' }))

    expect(screen.queryByRole('textbox')).not.toBeInTheDocument()
    expect(screen.queryByRole('heading')).not.toBeInTheDocument()
    expect(screen.getByRole('radio', { name: 'text-model' })).toBeInTheDocument()
    expect(screen.getByRole('radio', { name: 'image-model' })).toBeChecked()
    expect(screen.getByRole('radio', { name: 'video-model' })).toBeInTheDocument()
    expect(screen.getByRole('radio', { name: 'ocr-model' })).toBeInTheDocument()

    fireEvent.click(screen.getByRole('radio', { name: 'video-model' }))
    expect(onModel).toHaveBeenCalledWith('video-model')
    expect(screen.getByRole('dialog')).toBeInTheDocument()
  })

  it('does not offer text reasoning settings for media models', () => {
    renderOptions()
    fireEvent.click(screen.getByRole('button', { name: 'Выбрать модель' }))
    expect(screen.queryByText('Reasoning')).not.toBeInTheDocument()
    expect(screen.queryByText('Авто')).not.toBeInTheDocument()
  })

  it('preserves effort, automatic and off selection in the same model menu', () => {
    const { onReasoning } = renderOptions('text-model')
    fireEvent.click(screen.getByRole('button', { name: 'Выбрать модель' }))
    expect(screen.getByText('Reasoning')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('radio', { name: 'XHigh' }))
    expect(onReasoning).toHaveBeenLastCalledWith('xhigh')
    fireEvent.click(screen.getByRole('radio', { name: 'Выкл.' }))
    expect(onReasoning).toHaveBeenLastCalledWith('off')
    expect(screen.getByRole('radio', { name: 'Авто' })).toBeInTheDocument()
  })

  it('keeps the existing automatic reasoning control for text models without advertised effort levels', () => {
    render(<ChatOptions models={[{ id: 'plain-text', object: 'model', types: ['text'] }]} model="plain-text" reasoning="auto" onModel={vi.fn()} onReasoning={vi.fn()} disabled={false} loading={false} error={false} onOpen={vi.fn()} />)
    fireEvent.click(screen.getByRole('button', { name: 'Выбрать модель' }))
    expect(screen.getByText('Reasoning')).toBeInTheDocument()
    expect(screen.getByRole('radio', { name: 'Авто' })).toBeChecked()
  })
})
