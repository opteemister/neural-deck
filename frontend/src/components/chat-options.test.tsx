import { fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ChatOptions } from '@/components/chat-options'
import { DEFAULT_VIDEO_SETTINGS } from '@/lib/chat-video-settings'

const models = [
  {
    id: 'text-model', object: 'model' as const, types: ['text' as const],
    reasoning_capabilities: { efforts: ['xhigh'], effort_parameter: 'reasoning_effort' as const, can_disable_thinking: true },
  },
  { id: 'image-model', object: 'model' as const, types: ['image' as const] },
  { id: 'video-model', object: 'model' as const, types: ['video' as const] },
  { id: 'ocr-model', object: 'model' as const, types: ['ocr' as const] },
]

afterEach(() => { vi.restoreAllMocks(); window.localStorage.clear() })

function renderOptions(model = 'image-model') {
  const onModel = vi.fn()
  const onReasoning = vi.fn()
  const onImageSettings = vi.fn()
  const imageSettings = { size: '1024x1024' as const, transparent: false, maxResolution: false }
  const view = render(<ChatOptions models={models} model={model} reasoning="auto" onModel={onModel} onReasoning={onReasoning} imageSettings={imageSettings} onImageSettings={onImageSettings} videoSettings={DEFAULT_VIDEO_SETTINGS} onVideoSettings={vi.fn()} disabled={false} loading={false} error={false} onOpen={vi.fn()} />)
  return { onModel, onReasoning, onImageSettings, view }
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

  it('shows image size and transparency controls only for image models', () => {
    const { onImageSettings, view } = renderOptions()
    const trigger = screen.getByRole('button', { name: 'Выбрать модель' })
    expect(trigger).not.toHaveTextContent('1024×1024')
    expect(trigger).not.toHaveTextContent('Прозрачный')
    fireEvent.click(trigger)

    expect(screen.getByRole('radio', { name: 'Квадрат 1472×1472' })).toBeChecked()
    expect(screen.getByRole('radio', { name: 'Вертикальный 1088×1920' })).toBeInTheDocument()
    expect(screen.getByRole('radio', { name: 'Горизонтальный 1920×1088' })).toBeInTheDocument()
    expect(screen.getByRole('checkbox', { name: 'Прозрачный фон' })).not.toBeChecked()
    fireEvent.click(screen.getByRole('radio', { name: 'Вертикальный 1088×1920' }))
    expect(onImageSettings).toHaveBeenLastCalledWith({ size: '768x1024', transparent: false, maxResolution: false })
    view.rerender(<ChatOptions models={models} model="image-model" reasoning="auto" onModel={vi.fn()} onReasoning={vi.fn()} imageSettings={{ size: '768x1024', transparent: false, maxResolution: false }} onImageSettings={onImageSettings} videoSettings={DEFAULT_VIDEO_SETTINGS} onVideoSettings={vi.fn()} disabled={false} loading={false} error={false} onOpen={vi.fn()} />)
    fireEvent.click(screen.getByRole('checkbox', { name: 'Прозрачный фон' }))
    expect(onImageSettings).toHaveBeenLastCalledWith({ size: '768x1024', transparent: true, maxResolution: false })
    fireEvent.click(screen.getByRole('checkbox', { name: 'Макс. разр. (дольше)' }))
    expect(onImageSettings).toHaveBeenLastCalledWith({ size: '768x1024', transparent: false, maxResolution: true })
    view.rerender(<ChatOptions models={models} model="image-model" reasoning="auto" onModel={vi.fn()} onReasoning={vi.fn()} imageSettings={{ size: '768x1024', transparent: false, maxResolution: true }} onImageSettings={onImageSettings} videoSettings={DEFAULT_VIDEO_SETTINGS} onVideoSettings={vi.fn()} disabled={false} loading={false} error={false} onOpen={vi.fn()} />)
    expect(screen.getByRole('radio', { name: 'Квадрат 2048×2048' })).toBeInTheDocument()
    expect(screen.getByRole('radio', { name: 'Вертикальный 1536×2752' })).toBeChecked()
    fireEvent.click(screen.getByRole('radio', { name: 'Горизонтальный 2752×1536' }))
    expect(onImageSettings).toHaveBeenLastCalledWith({ size: '1024x768', transparent: false, maxResolution: true })
  })

  it('does not show image settings for text models', () => {
    renderOptions('text-model')
    fireEvent.click(screen.getByRole('button', { name: 'Выбрать модель' }))
    expect(screen.queryByRole('radio', { name: 'Квадрат 1472×1472' })).not.toBeInTheDocument()
    expect(screen.queryByRole('checkbox', { name: 'Прозрачный фон' })).not.toBeInTheDocument()
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
    render(<ChatOptions models={[{ id: 'plain-text', object: 'model', types: ['text'] }]} model="plain-text" reasoning="auto" onModel={vi.fn()} onReasoning={vi.fn()} imageSettings={{ size: '1024x1024', transparent: false, maxResolution: false }} onImageSettings={vi.fn()} videoSettings={DEFAULT_VIDEO_SETTINGS} onVideoSettings={vi.fn()} disabled={false} loading={false} error={false} onOpen={vi.fn()} />)
    fireEvent.click(screen.getByRole('button', { name: 'Выбрать модель' }))
    expect(screen.getByText('Reasoning')).toBeInTheDocument()
    expect(screen.getByRole('radio', { name: 'Авто' })).toBeChecked()
  })
})
