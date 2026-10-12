import { useEffect, type ReactNode } from 'react'
import { X } from 'lucide-react'
import { Button } from '@/components/ui/button'

export function Dialog({ open, title, onOpenChange, children }: { open: boolean; title: string; onOpenChange: (open: boolean) => void; children: ReactNode }) {
  useEffect(() => {
    if (!open) return
    const closeOnEscape = (event: KeyboardEvent) => { if (event.key === 'Escape') onOpenChange(false) }
    const previousOverflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    document.addEventListener('keydown', closeOnEscape)
    return () => {
      document.body.style.overflow = previousOverflow
      document.removeEventListener('keydown', closeOnEscape)
    }
  }, [open, onOpenChange])

  if (!open) return null
  return <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) onOpenChange(false) }}>
    <section className="max-h-[min(44rem,calc(100vh-2rem))] w-full max-w-lg overflow-auto rounded-md border border-border bg-card shadow-2xl" role="dialog" aria-modal="true" aria-labelledby="dialog-title">
      <div className="flex items-center justify-between border-b border-border px-4 py-3"><h2 id="dialog-title" className="text-sm font-medium">{title}</h2><Button variant="ghost" size="icon-sm" iconOnly aria-label="Закрыть" onClick={() => onOpenChange(false)}><X className="size-4" /></Button></div>
      {children}
    </section>
  </div>
}
