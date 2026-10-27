import { useEffect, useId, useRef, useState } from 'react'
import { Check, ChevronDown } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import type { OpenAiModel } from '@/types/api'
import { reasoningChoices, type Reasoning } from '@/lib/chat-reasoning'

const modelGroups = [
  { key: 'text', label: 'Текст' },
  { key: 'image', label: 'Изображения' },
  { key: 'video', label: 'Видео' },
  { key: 'ocr', label: 'Документы' },
] as const

type ChatModelGroup = (typeof modelGroups)[number]['key']

function modelGroup(model: OpenAiModel): ChatModelGroup | null {
  // A model may advertise more than one capability. Keep one visible option
  // per model so the radio group remains valid; text/vision is the broadest
  // chat capability and gets precedence for multimodal text models.
  if (model.types?.includes('text') || !model.types?.length) return 'text'
  if (model.types?.includes('image')) return 'image'
  if (model.types?.includes('video')) return 'video'
  if (model.types?.includes('ocr')) return 'ocr'
  return null
}

function hasReasoningControls(model: OpenAiModel | undefined): boolean {
  if (!model || (model.types?.length && !model.types.includes('text'))) return false
  return reasoningChoices(model).all.length > 1
}

export function ChatOptions({ models, model, reasoning, onModel, onReasoning, disabled, loading, error, onOpen }: {
  models: OpenAiModel[]; model: string; reasoning: Reasoning
  onModel: (model: string) => void; onReasoning: (reasoning: Reasoning) => void
  disabled: boolean; loading: boolean; error: boolean; onOpen: () => void
}) {
  const [open, setOpen] = useState(false)
  const [menuLayout, setMenuLayout] = useState({ left: 0, maxHeight: 384, below: false })
  const [search, setSearch] = useState('')
  const rootRef = useRef<HTMLDivElement>(null)
  const triggerRef = useRef<HTMLButtonElement>(null)
  const id = useId()
  const selectedModel = models.find((item) => item.id === model)
  const unavailable = Boolean(model && !selectedModel && !loading)
  const choices = reasoningChoices(selectedModel)
  const showReasoning = hasReasoningControls(selectedModel)
  const label = showReasoning ? choices.all.find((option) => option.value === reasoning)?.label ?? 'Авто' : null
  const query = search.trim().toLocaleLowerCase()
  const groupedModels = modelGroups.map((group) => ({
    ...group,
    items: models.filter((item) => modelGroup(item) === group.key && (!query || item.id.toLocaleLowerCase().includes(query))),
  })).filter((group) => group.items.length > 0)

  useEffect(() => {
    if (!open) return
    function position() {
      const rect = rootRef.current?.getBoundingClientRect()
      if (!rect) return
      const width = Math.min(320, window.innerWidth - 32)
      const above = rect.top - 88
      const below = window.innerHeight - rect.bottom - 24
      const openBelow = above < 240 && below > above
      setMenuLayout({ left: Math.min(0, window.innerWidth - 16 - rect.left - width), maxHeight: Math.max(120, Math.min(448, openBelow ? below : above)), below: openBelow })
    }
    position()
    window.addEventListener('resize', position)
    function outside(event: PointerEvent) {
      if (!rootRef.current?.contains(event.target as Node)) setOpen(false)
    }
    function escape(event: KeyboardEvent) {
      if (event.key === 'Escape') { event.preventDefault(); setOpen(false); triggerRef.current?.focus() }
    }
    rootRef.current?.querySelector<HTMLInputElement>('input[aria-label="Поиск модели"]')?.focus()
    document.addEventListener('pointerdown', outside)
    document.addEventListener('keydown', escape)
    return () => { window.removeEventListener('resize', position); document.removeEventListener('pointerdown', outside); document.removeEventListener('keydown', escape) }
  }, [open])

  return <div ref={rootRef} className="relative min-w-0">
    <span ref={(element) => { triggerRef.current = element?.querySelector('button') ?? null }}>
      <Button size="sm" shape="pill" variant="ghost" disabled={disabled} aria-label="Выбрать модель" aria-haspopup="dialog" aria-expanded={open} aria-controls={open ? id : undefined} onClick={() => { if (!open) { setSearch(''); onOpen() }; setOpen(!open) }} className="!h-11 !min-h-11 max-w-full">
        <span className="max-w-[min(36vw,18rem)] truncate text-foreground">{model || 'Модель'}</span>
        {unavailable && <span className="shrink-0 text-[10px] text-muted-foreground">недоступна</span>}
        {label && <span className="shrink-0 text-muted-foreground">· {label}</span>}
        <ChevronDown className={`size-3.5 transition-transform ${open ? 'rotate-180' : ''}`} />
      </Button>
    </span>
    {open && <div id={id} role="dialog" aria-label="Выбор модели" style={{ left: menuLayout.left, maxHeight: menuLayout.maxHeight }} className={`absolute z-50 w-80 max-w-[calc(100vw-2rem)] overflow-y-auto rounded-2xl border border-border bg-popover p-3 shadow-xl ${menuLayout.below ? 'top-full mt-3' : 'bottom-full mb-3'}`}>
      <fieldset disabled={disabled}>
        <legend className="mb-2 px-2 text-[11px] font-medium text-muted-foreground">Модель</legend>
        <Input aria-label="Поиск модели" value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Поиск модели" className="mb-2 rounded-full" />
        <div className="max-h-56 space-y-3 overflow-y-auto">
          {groupedModels.map((group) => <section key={group.key} aria-labelledby={`${id}-${group.key}`}>
            <h3 id={`${id}-${group.key}`} className="px-2 pb-1 text-[10px] font-medium uppercase tracking-[0.12em] text-muted-foreground">{group.label}</h3>
            <div className="space-y-1">
              {group.items.map((item) => <label key={item.id} className={`relative flex cursor-pointer items-center gap-3 rounded-xl px-3 py-2.5 text-xs transition-colors hover:bg-accent focus-within:ring-1 focus-within:ring-inset focus-within:ring-copper ${item.id === model ? 'bg-accent text-foreground' : 'text-muted-foreground'}`}>
                <input type="radio" name={`${id}-model`} value={item.id} checked={item.id === model} aria-label={item.id} onChange={() => { setOpen(false); onModel(item.id) }} className="sr-only" />
                <span className="min-w-0 flex-1 break-words">{item.id}</span>
                {item.id === model && <Check className="size-3.5 shrink-0 text-copper" />}
              </label>)}
            </div>
          </section>)}
          {!groupedModels.length && <p className="px-2 py-3 text-xs text-muted-foreground">{loading ? 'Загружаем модели…' : error ? 'Не удалось загрузить модели' : query ? 'Модели не найдены' : 'Нет доступных моделей'}</p>}
        </div>
      </fieldset>
      {showReasoning && <fieldset disabled={disabled || !model} className="mt-3 border-t border-border pt-3">
        <legend className="px-2 text-[11px] font-medium text-muted-foreground">Рассуждение</legend>
        {[choices.upper, choices.lower].filter((row) => row.length > 0).map((row, index) => <div key={index} className={`mt-1.5 grid gap-1.5 ${row.length === 1 ? 'grid-cols-1' : row.length === 2 ? 'grid-cols-2' : 'grid-cols-3'}`}>
          {row.map((option) => <label key={option.value} className={`relative cursor-pointer rounded-full border px-2 py-2 text-center text-[11px] transition-colors focus-within:ring-1 focus-within:ring-copper ${option.value === reasoning ? 'border-copper/50 bg-accent text-foreground' : 'border-transparent text-muted-foreground hover:bg-accent'}`}>
            <input type="radio" name={`${id}-reasoning`} value={option.value} checked={option.value === reasoning} aria-label={option.label} onChange={() => onReasoning(option.value)} className="sr-only" />{option.label}
          </label>)}
        </div>)}
      </fieldset>}
    </div>}
  </div>
}
