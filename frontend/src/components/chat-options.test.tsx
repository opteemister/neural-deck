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
  it('lists every chat capability in understandable groups without replacing a media model', () => {
    const { onModel } = renderOptions()
    expect(onModel).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('button', { name: 'Выбрать модель' }))

    expect(screen.getByRole('heading', { name: 'Текст' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Изображения' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Видео' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Документы' })).toBeInTheDocument()
    expect(screen.getByRole('radio', { name: 'image-model' })).toBeChecked()
    expect(screen.getByRole('radio', { name: 'video-model' })).toBeInTheDocument()
    expect(screen.getByRole('radio', { name: 'ocr-model' })).toBeInTheDocument()

    fireEvent.click(screen.getByRole('radio', { name: 'video-model' }))
    expect(onModel).toHaveBeenCalledWith('video-model')
  })

  it('searches models and hides reasoning for media models', () => {
    renderOptions()
    fireEvent.click(screen.getByRole('button', { name: 'Выбрать модель' }))
    fireEvent.change(screen.getByLabelText('Поиск модели'), { target: { value: 'video' } })
    expect(screen.getByRole('radio', { name: 'video-model' })).toBeInTheDocument()
    expect(screen.queryByRole('radio', { name: 'image-model' })).not.toBeInTheDocument()
    expect(screen.queryByRole('radio', { name: 'text-model' })).not.toBeInTheDocument()
    expect(screen.queryByText('Рассуждение')).not.toBeInTheDocument()
    expect(screen.queryByText('Авто')).not.toBeInTheDocument()
  })

  it('shows reasoning only when the selected text model has real controls', () => {
    renderOptions('text-model')
    fireEvent.click(screen.getByRole('button', { name: 'Выбрать модель' }))
    expect(screen.getByText('Рассуждение')).toBeInTheDocument()
    expect(screen.getByRole('radio', { name: 'XHigh' })).toBeInTheDocument()
    expect(screen.getByRole('radio', { name: 'Авто' })).toBeInTheDocument()
  })
})
