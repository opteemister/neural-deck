import { useId, useLayoutEffect, useRef, useState } from 'react'
import { Check, ChevronDown, RectangleHorizontal, RectangleVertical, Square } from 'lucide-react'
import { Button } from '@/components/ui/button'
import type { OpenAiModel } from '@/types/api'
import { reasoningChoices, type Reasoning } from '@/lib/chat-reasoning'
import { IMAGE_SIZES, imageRequestSize, maxImageMegapixels, type ImageSettings } from '@/lib/chat-image-settings'
import { videoResolutionChoices, videoDurationChoices, videoRequestSize, type VideoSettings } from '@/lib/chat-video-settings'

export function ChatOptions({ models, model, reasoning, onModel, onReasoning, imageSettings, onImageSettings, videoSettings, onVideoSettings, disabled, loading, error, onOpen }: {
  models: OpenAiModel[]; model: string; reasoning: Reasoning
  onModel: (model: string) => void; onReasoning: (reasoning: Reasoning) => void
  imageSettings: ImageSettings; onImageSettings: (settings: ImageSettings) => void
  videoSettings: VideoSettings; onVideoSettings: (settings: VideoSettings) => void
  disabled: boolean; loading: boolean; error: boolean; onOpen: () => void
}) {
  const [open, setOpen] = useState(false)
  const [menuLayout, setMenuLayout] = useState({ left: 0, maxHeight: 384, below: false })
  const rootRef = useRef<HTMLDivElement>(null)
  const triggerRef = useRef<HTMLButtonElement>(null)
  const id = useId()
  const selectedModel = models.find((item) => item.id === model)
  const unavailable = Boolean(model && !selectedModel && !loading)
  const choices = reasoningChoices(selectedModel)
  const showReasoning = Boolean(selectedModel && (!selectedModel.types?.length || selectedModel.types.includes('text')))
  const showImageSettings = !showReasoning && selectedModel?.types?.includes('image')
  const showVideoSettings = !showReasoning && !showImageSettings && selectedModel?.types?.includes('video')
  const label = showReasoning ? choices.all.find((option) => option.value === reasoning)?.label ?? 'Авто' : null

  useLayoutEffect(() => {
    if (!open) return
    function position() {
      const rect = rootRef.current?.getBoundingClientRect()
      if (!rect) return
      const width = Math.min(320, window.innerWidth - 32)
      const above = rect.top - 88
      const below = window.innerHeight - rect.bottom - 24
      const openBelow = above < 240 && below > above
      setMenuLayout({ left: Math.min(0, rect.width - width, window.innerWidth - 16 - rect.left - width), maxHeight: Math.max(120, Math.min(448, openBelow ? below : above)), below: openBelow })
    }
    position()
    window.addEventListener('resize', position)
    function outside(event: PointerEvent) {
      if (!rootRef.current?.contains(event.target as Node)) setOpen(false)
    }
    function escape(event: KeyboardEvent) {
      if (event.key === 'Escape') { event.preventDefault(); setOpen(false); triggerRef.current?.focus() }
    }
    rootRef.current?.querySelector<HTMLInputElement>('input:checked')?.focus()
    document.addEventListener('pointerdown', outside)
    document.addEventListener('keydown', escape)
    return () => { window.removeEventListener('resize', position); document.removeEventListener('pointerdown', outside); document.removeEventListener('keydown', escape) }
  }, [open])

  return <div ref={rootRef} className="relative min-w-0">
    <span ref={(element) => { triggerRef.current = element?.querySelector('button') ?? null }}>
      <Button size="sm" shape="pill" variant="ghost" disabled={disabled} aria-label="Выбрать модель" aria-haspopup="dialog" aria-expanded={open} aria-controls={open ? id : undefined} onClick={() => { if (!open) onOpen(); setOpen(!open) }} className="max-w-full">
        <span className="max-w-[min(36vw,18rem)] truncate text-foreground">{model || 'Модель'}</span>
        {unavailable && <span className="shrink-0 text-[10px] text-muted-foreground">недоступна</span>}
        {label && <span className="shrink-0 text-muted-foreground">· {label}</span>}
        <ChevronDown className={`size-3.5 transition-transform ${open ? 'rotate-180' : ''}`} />
      </Button>
    </span>
    {open && <div id={id} role="dialog" aria-label="Модель и reasoning" style={{ left: menuLayout.left, maxHeight: menuLayout.maxHeight }} className={`absolute z-50 w-80 max-w-[calc(100vw-2rem)] overflow-y-auto rounded-2xl border border-border bg-popover p-3 shadow-xl ${menuLayout.below ? 'top-full mt-3' : 'bottom-full mb-3'}`}>
      <fieldset disabled={disabled}>
        <legend className="mb-2 px-2 text-[11px] font-medium text-muted-foreground">Модель</legend>
        <div className="max-h-48 space-y-1 overflow-y-auto">
          {models.map((item) => <label key={item.id} className={`relative flex cursor-pointer items-center gap-3 rounded-xl px-3 py-2.5 text-xs transition-colors hover:bg-accent focus-within:ring-1 focus-within:ring-inset focus-within:ring-copper ${item.id === model ? 'bg-accent text-foreground' : 'text-muted-foreground'}`}>
            <input type="radio" name={`${id}-model`} value={item.id} checked={item.id === model} aria-label={item.id} onChange={() => onModel(item.id)} className="sr-only" />
            <span className="min-w-0 flex-1 break-words">{item.id}</span>
            {item.id === model && <Check className="size-3.5 shrink-0 text-copper" />}
          </label>)}
          {!models.length && <p className="px-2 py-3 text-xs text-muted-foreground">{loading ? 'Загружаем модели…' : error ? 'Не удалось загрузить модели' : 'Нет доступных моделей'}</p>}
        </div>
      </fieldset>
      {(showImageSettings || showVideoSettings) && <fieldset disabled={disabled} className="mt-3 border-t border-border pt-3">
        <legend className="px-2 text-[11px] font-medium text-muted-foreground">Размер</legend>
        <div className="mt-1.5 grid grid-cols-3 gap-1.5">
          {IMAGE_SIZES.map((size) => {
            const Icon = size.aspect === 'square' ? Square : size.aspect === 'portrait' ? RectangleVertical : RectangleHorizontal
            const dimensions = (showVideoSettings ? videoRequestSize({ ...videoSettings, aspect: size.aspect }) : imageRequestSize({ size: size.value, maxResolution: imageSettings.maxResolution }, maxImageMegapixels(selectedModel))).replace('x', '×') || '—'
            const checked = showVideoSettings ? videoSettings.aspect === size.aspect : imageSettings.size === size.value
            return <label key={size.value} className={`relative flex cursor-pointer flex-col items-center gap-1 rounded-xl border px-1 py-2 text-center transition-colors focus-within:ring-1 focus-within:ring-copper ${checked ? 'border-copper/50 bg-accent text-foreground' : 'border-transparent text-muted-foreground hover:bg-accent'}`}>
              <input type="radio" name={`${id}-media-size`} checked={checked} aria-label={`${size.label} ${dimensions}`} onChange={() => showVideoSettings ? onVideoSettings({ ...videoSettings, aspect: size.aspect }) : onImageSettings({ ...imageSettings, size: size.value })} className="sr-only" />
              <Icon className="size-3.5" aria-hidden="true" /><span className="text-[10px]">{size.label}</span><span className="text-[9px] tabular-nums text-muted-foreground">{dimensions}</span>
            </label>
          })}
        </div>
        {showImageSettings && <div className="mt-3 flex items-center justify-between gap-2 px-2 text-[11px] text-foreground">
          <label className="flex cursor-pointer items-center gap-1.5 whitespace-nowrap"><input type="checkbox" checked={imageSettings.maxResolution} onChange={(event) => onImageSettings({ ...imageSettings, maxResolution: event.target.checked })} className="size-3.5 rounded accent-[#c58055]" />Макс. разр. (дольше)</label>
          <label className="flex cursor-pointer items-center gap-1.5 whitespace-nowrap"><input type="checkbox" checked={imageSettings.transparent} onChange={(event) => onImageSettings({ ...imageSettings, transparent: event.target.checked })} className="size-3.5 rounded accent-[#c58055]" />Прозрачный фон</label>
        </div>}
      </fieldset>}
      {showVideoSettings && [
        { label: 'Разрешение', key: 'resolution', options: videoResolutionChoices(selectedModel).map((resolution) => ({ value: resolution, label: `${resolution}p`, checked: videoSettings.resolution === resolution, select: () => onVideoSettings({ ...videoSettings, resolution }) })) },
        { label: 'Длительность', key: 'duration', options: videoDurationChoices(selectedModel).map((seconds) => ({ value: seconds, label: `${seconds} с`, checked: videoSettings.seconds === seconds, select: () => onVideoSettings({ ...videoSettings, seconds }) })) },
      ].map((control) => <fieldset key={control.key} disabled={disabled} className="mt-3 border-t border-border pt-3">
        <legend className="px-2 text-[11px] font-medium text-muted-foreground">{control.label}</legend>
        <div className="mt-1.5 grid gap-1.5" style={{ gridTemplateColumns: `repeat(${control.options.length}, minmax(0, 1fr))` }}>
          {control.options.map((option) => <label key={option.value} className={`relative cursor-pointer rounded-full border px-2 py-2 text-center text-[11px] transition-colors focus-within:ring-1 focus-within:ring-copper ${option.checked ? 'border-copper/50 bg-accent text-foreground' : 'border-transparent text-muted-foreground hover:bg-accent'}`}>
            <input type="radio" name={`${id}-${control.key}`} checked={option.checked} aria-label={option.label} onChange={option.select} className="sr-only" />{option.label}
          </label>)}
        </div>
      </fieldset>)}
      {showReasoning && <fieldset disabled={disabled || !model} className="mt-3 border-t border-border pt-3">
        <legend className="px-2 text-[11px] font-medium text-muted-foreground">Reasoning</legend>
        {[choices.upper, choices.lower].filter((row) => row.length > 0).map((row, index) => <div key={index} className={`mt-1.5 grid gap-1.5 ${row.length === 1 ? 'grid-cols-1' : row.length === 2 ? 'grid-cols-2' : 'grid-cols-3'}`}>
          {row.map((option) => <label key={option.value} className={`relative cursor-pointer rounded-full border px-2 py-2 text-center text-[11px] transition-colors focus-within:ring-1 focus-within:ring-copper ${option.value === reasoning ? 'border-copper/50 bg-accent text-foreground' : 'border-transparent text-muted-foreground hover:bg-accent'}`}>
            <input type="radio" name={`${id}-reasoning`} value={option.value} checked={option.value === reasoning} aria-label={option.label} onChange={() => onReasoning(option.value)} className="sr-only" />{option.label}
          </label>)}
        </div>)}
      </fieldset>}
    </div>}
  </div>
}
