import { useEffect, useId, type ReactNode } from 'react'
import { X } from 'lucide-react'
import { Button } from '@/components/ui/button'

type DialogSize = 'default' | 'wide'

const dialogWidths: Record<DialogSize, string> = {
  default: 'max-w-lg',
  wide: 'max-w-4xl',
}

const dialogHeights: Record<DialogSize, string> = {
  default: 'max-h-[min(44rem,calc(100dvh-2rem))]',
  wide: 'max-h-[calc(100dvh-2rem)]',
}

export function Dialog({ open, title, size = 'default', variant = 'default', dismissible = true, onOpenChange, children }: { open: boolean; title: string; size?: DialogSize; variant?: 'default' | 'media'; dismissible?: boolean; onOpenChange: (open: boolean) => void; children: ReactNode }) {
  const titleId = useId()
  useEffect(() => {
    if (!open) return
    const closeOnEscape = (event: KeyboardEvent) => { if (dismissible && event.key === 'Escape') onOpenChange(false) }
    const previousOverflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    document.addEventListener('keydown', closeOnEscape)
    return () => {
      document.body.style.overflow = previousOverflow
      document.removeEventListener('keydown', closeOnEscape)
    }
  }, [dismissible, open, onOpenChange])

  if (!open) return null
  return <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4" role="presentation" onMouseDown={(event) => { if (dismissible && event.target === event.currentTarget) onOpenChange(false) }}>
    <section className={variant === 'media' ? 'relative flex w-fit max-w-full flex-col max-h-[calc(100dvh-2rem)]' : `flex w-full flex-col rounded-md border border-border bg-card shadow-2xl ${dialogWidths[size]} ${dialogHeights[size]}`} role="dialog" aria-modal="true" aria-labelledby={titleId}>
      {variant === 'media' ? <>
        <h2 id={titleId} className="sr-only">{title}</h2>
        <Button variant="overlay" shape="pill" size="icon-sm" iconOnly aria-label="Закрыть" title="Закрыть" className="absolute right-3 top-3 z-20" onClick={() => onOpenChange(false)}><X className="size-4" /></Button>
      </> : <div className="flex shrink-0 items-center justify-between border-b border-border px-4 py-3"><h2 id={titleId} className="text-sm font-medium">{title}</h2><Button variant="ghost" size="icon-sm" iconOnly aria-label="Закрыть" onClick={() => onOpenChange(false)}><X className="size-4" /></Button></div>}
      <div className={variant === 'media' ? 'relative min-h-0' : 'min-h-0 overflow-auto'}>{children}</div>
    </section>
  </div>
}
