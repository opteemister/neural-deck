import { useEffect, useId, useRef, useState } from 'react'
import { Check, ChevronDown } from 'lucide-react'
import { Button } from '@/components/ui/button'
import type { OpenAiModel } from '@/types/api'
import { reasoningChoices, type Reasoning } from '@/lib/chat-reasoning'

export function ChatOptions({ models, model, reasoning, onModel, onReasoning, disabled, loading, error, onOpen }: {
  models: OpenAiModel[]; model: string; reasoning: Reasoning
  onModel: (model: string) => void; onReasoning: (reasoning: Reasoning) => void
  disabled: boolean; loading: boolean; error: boolean; onOpen: () => void
}) {
  const [open, setOpen] = useState(false)
  const rootRef = useRef<HTMLDivElement>(null)
  const triggerRef = useRef<HTMLButtonElement>(null)
  const id = useId()
  const choices = reasoningChoices(models.find((item) => item.id === model))
  const label = choices.all.find((option) => option.value === reasoning)?.label ?? 'Авто'

  useEffect(() => {
    if (!open) return
    function outside(event: PointerEvent) {
      if (!rootRef.current?.contains(event.target as Node)) setOpen(false)
    }
    function escape(event: KeyboardEvent) {
      if (event.key === 'Escape') { event.preventDefault(); setOpen(false); triggerRef.current?.focus() }
    }
    rootRef.current?.querySelector<HTMLInputElement>('input:checked')?.focus()
    document.addEventListener('pointerdown', outside)
    document.addEventListener('keydown', escape)
    return () => { document.removeEventListener('pointerdown', outside); document.removeEventListener('keydown', escape) }
  }, [open])

  return <div ref={rootRef} className="relative min-w-0">
    <span ref={(element) => { triggerRef.current = element?.querySelector('button') ?? null }}>
      <Button size="sm" shape="pill" variant="ghost" disabled={disabled} aria-label="Выбрать модель и reasoning" aria-haspopup="dialog" aria-expanded={open} aria-controls={open ? id : undefined} onClick={() => { if (!open) onOpen(); setOpen(!open) }} className="max-w-full">
        <span className="max-w-[min(36vw,18rem)] truncate text-foreground">{model || 'Модель'}</span>
        <span className="shrink-0 text-muted-foreground">· {label}</span>
        <ChevronDown className={`size-3.5 transition-transform ${open ? 'rotate-180' : ''}`} />
      </Button>
    </span>
    {open && <div id={id} role="dialog" aria-label="Модель и reasoning" className="absolute bottom-full right-0 z-50 mb-3 max-h-[min(65dvh,28rem)] w-80 max-w-[calc(100vw-4rem)] overflow-y-auto rounded-2xl border border-border bg-popover p-3 shadow-xl">
      <fieldset disabled={disabled}>
        <legend className="mb-2 px-2 text-[11px] font-medium text-muted-foreground">Модель</legend>
        <div className="max-h-48 space-y-1 overflow-y-auto">
          {models.map((item) => <label key={item.id} className={`relative flex cursor-pointer items-center gap-3 rounded-xl px-3 py-2.5 text-xs transition-colors hover:bg-accent focus-within:ring-1 focus-within:ring-inset focus-within:ring-copper ${item.id === model ? 'bg-accent text-foreground' : 'text-muted-foreground'}`}>
            <input type="radio" name={`${id}-model`} value={item.id} checked={item.id === model} onChange={() => onModel(item.id)} className="sr-only" />
            <span className="min-w-0 flex-1 break-words">{item.id}</span>
            {item.id === model && <Check className="size-3.5 shrink-0 text-copper" />}
          </label>)}
          {!models.length && <p className="px-2 py-3 text-xs text-muted-foreground">{loading ? 'Загружаем модели…' : error ? 'Не удалось загрузить модели' : 'Нет доступных моделей'}</p>}
        </div>
      </fieldset>
      <fieldset disabled={disabled || !model} className="mt-3 border-t border-border pt-3">
        <legend className="px-2 text-[11px] font-medium text-muted-foreground">Reasoning</legend>
        {[choices.upper, choices.lower].filter((row) => row.length > 0).map((row, index) => <div key={index} className={`mt-1.5 grid gap-1.5 ${row.length === 1 ? 'grid-cols-1' : row.length === 2 ? 'grid-cols-2' : 'grid-cols-3'}`}>
          {row.map((option) => <label key={option.value} className={`relative cursor-pointer rounded-full border px-2 py-2 text-center text-[11px] transition-colors focus-within:ring-1 focus-within:ring-copper ${option.value === reasoning ? 'border-copper/50 bg-accent text-foreground' : 'border-transparent text-muted-foreground hover:bg-accent'}`}>
            <input type="radio" name={`${id}-reasoning`} value={option.value} checked={option.value === reasoning} onChange={() => onReasoning(option.value)} className="sr-only" />{option.label}
          </label>)}
        </div>)}
      </fieldset>
    </div>}
  </div>
}
