import type { KeyboardEvent } from 'react'

export interface RunTabItem {
  value: string
  title: string
  subtitle: string
  meta?: string
  uptime?: string
  status?: 'active' | 'inactive'
}

export function RunTabs({ items, value, onChange, panelId }: {
  items: RunTabItem[]; value: string; onChange: (value: string) => void; panelId: string
}) {
  function navigate(event: KeyboardEvent<HTMLDivElement>) {
    if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return
    const buttons = Array.from(event.currentTarget.querySelectorAll<HTMLButtonElement>('[role="tab"]'))
    const index = buttons.indexOf(document.activeElement as HTMLButtonElement)
    if (index < 0) return
    event.preventDefault()
    const next = event.key === 'Home' ? 0 : event.key === 'End' ? buttons.length - 1 : (index + (event.key === 'ArrowRight' ? 1 : -1) + buttons.length) % buttons.length
    buttons[next]?.focus()
    buttons[next]?.click()
  }
  return <div role="tablist" aria-label="Запуски" onKeyDown={navigate} className="flex overflow-x-auto rounded-t-md border-x border-t border-border bg-secondary">
    {items.map((item) => <button key={item.value} id={`${panelId}-tab-${encodeURIComponent(item.value)}`} type="button" role="tab" aria-selected={value === item.value} aria-controls={panelId} tabIndex={value === item.value ? 0 : -1} onClick={() => onChange(item.value)} title={`${item.title}\n${item.subtitle}${item.meta ? ` · ${item.meta}` : ''}`} className={`relative flex h-11 shrink-0 ${item.value === 'all' ? 'w-auto min-w-20' : 'w-52'} flex-col justify-center gap-0.5 border-r border-border px-3 text-left leading-tight transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-copper ${value === item.value ? 'bg-card text-foreground before:absolute before:inset-x-0 before:top-0 before:h-0.5 before:bg-copper' : 'text-muted-foreground after:absolute after:inset-x-0 after:bottom-0 after:h-px after:bg-border hover:bg-accent hover:text-foreground'}`}>
      <span className="flex w-full items-center gap-1.5">{item.status && <span className={`size-1.5 shrink-0 rounded-full ${item.status === 'active' ? 'bg-[#6f9a72]' : 'bg-muted-foreground/60'}`} title={item.status === 'active' ? 'Запущен' : 'Остановлен'} aria-label={item.status === 'active' ? 'Запущен' : 'Остановлен'} data-run-status={item.status} />}<span className="min-w-0 flex-1 truncate text-[11px] font-medium">{item.title}</span>{item.uptime && <span className="shrink-0 font-mono text-[9px] font-normal tabular-nums text-muted-foreground" title={`Аптайм запуска: ${item.uptime}`} aria-label={`Аптайм запуска: ${item.uptime}`}>{item.uptime}</span>}</span>
      <span className="flex w-full items-center gap-1.5 text-[10px] font-normal text-muted-foreground"><span className="min-w-0 flex-1 truncate">{item.subtitle}</span>{item.meta && <span className="shrink-0 font-mono text-[9px]">{item.meta}</span>}</span>
    </button>)}
  </div>
}
