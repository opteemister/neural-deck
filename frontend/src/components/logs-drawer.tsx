import { createContext, useContext, useEffect, useRef, useState, type ReactNode } from 'react'
import { X } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { LogsViewer } from '@/pages/logs'

const LogsDrawerContext = createContext<((profile?: string) => void) | null>(null)

export function useLogsDrawer() {
  const open = useContext(LogsDrawerContext)
  if (!open) throw new Error('LogsDrawerProvider is missing')
  return open
}

export function LogsDrawerProvider({ children }: { children: ReactNode }) {
  const panelRef = useRef<HTMLElement>(null)
  const [widthPercent, setWidthPercent] = useState(70)
  function resizeWidth(percent: number) {
    const minimum = Math.min(320 / window.innerWidth * 100, 70)
    setWidthPercent(Math.max(minimum, Math.min(100, percent)))
  }
  const [selection, setSelection] = useState<{ profile?: string; request: number } | null>(null)
  function open(profile?: string) {
    setSelection((current) => ({ profile, request: (current?.request ?? 0) + 1 }))
  }
  const visible = selection !== null
  useEffect(() => {
    if (!visible) return
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === 'Escape' && !event.defaultPrevented) setSelection(null)
    }
    function onOutsidePointer(event: PointerEvent) {
      if (event.target instanceof Node && !panelRef.current?.contains(event.target)) setSelection(null)
    }
    const panel = panelRef.current
    function onWheel(event: WheelEvent) {
      if (event.ctrlKey) return
      let element = event.target instanceof Element ? event.target : null
      while (element && element !== panel) {
        const style = window.getComputedStyle(element)
        const vertical = /auto|scroll/.test(style.overflowY) && (event.deltaY < 0 ? element.scrollTop > 0 : event.deltaY > 0 && element.scrollTop + element.clientHeight < element.scrollHeight)
        const horizontal = /auto|scroll/.test(style.overflowX) && (event.deltaX < 0 ? element.scrollLeft > 0 : event.deltaX > 0 && element.scrollLeft + element.clientWidth < element.scrollWidth)
        if (vertical || horizontal) return
        element = element.parentElement
      }
      event.preventDefault()
    }
    panel?.addEventListener('wheel', onWheel, { passive: false })
    document.addEventListener('keydown', onKeyDown)
    document.addEventListener('pointerdown', onOutsidePointer)
    return () => {
      panel?.removeEventListener('wheel', onWheel)
      document.removeEventListener('keydown', onKeyDown)
      document.removeEventListener('pointerdown', onOutsidePointer)
    }
  }, [visible])

  return <LogsDrawerContext.Provider value={open}>
    {children}
    {selection && <aside ref={panelRef} role="dialog" aria-labelledby="logs-drawer-title" style={{ width: `${widthPercent}vw`, minWidth: 'min(320px, 70vw)', maxWidth: '100vw' }} className="logs-drawer fixed bottom-0 right-0 top-16 z-50 flex flex-col border-l border-border bg-background shadow-2xl">
      <div role="separator" aria-label="Ширина панели логов" aria-orientation="vertical" aria-valuemin={Math.round(Math.min(320 / window.innerWidth * 100, 70))} aria-valuemax={100} aria-valuenow={Math.round(widthPercent)} tabIndex={0} title="Перетащите, чтобы изменить ширину" className="absolute -left-1 bottom-0 top-0 z-10 w-2 cursor-col-resize touch-none select-none hover:bg-copper/30 focus-visible:bg-copper/30 focus-visible:outline-none"
        onPointerDown={(event) => { if (event.button !== 0) return; event.preventDefault(); event.currentTarget.focus(); event.currentTarget.setPointerCapture(event.pointerId) }}
        onPointerMove={(event) => { if (event.currentTarget.hasPointerCapture(event.pointerId)) resizeWidth((window.innerWidth - event.clientX) / window.innerWidth * 100) }}
        onPointerUp={(event) => { if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId) }}
        onKeyDown={(event) => { if (event.key === 'ArrowLeft' || event.key === 'ArrowRight') { event.preventDefault(); resizeWidth(widthPercent + (event.key === 'ArrowLeft' ? 2 : -2)) } }}
      />
      <div className="flex h-12 shrink-0 items-center justify-between border-b border-border px-4">
        <h2 id="logs-drawer-title" className="text-sm font-medium">Логи</h2>
        <Button type="button" variant="ghost" size="sm" iconOnly aria-label="Закрыть логи" onClick={() => setSelection(null)}><X className="size-4" /></Button>
      </div>
      <div className="min-h-0 flex-1 overflow-auto overscroll-contain">
        <LogsViewer key={selection.request} initialProfile={selection.profile} drawer />
      </div>
    </aside>}
  </LogsDrawerContext.Provider>
}
