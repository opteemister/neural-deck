import { useId, type ReactNode } from 'react'
import { Info } from 'lucide-react'
import { cn } from '@/lib/utils'

interface InfoHintProps {
  label: string
  children: ReactNode
  id?: string
  align?: 'left' | 'right'
  className?: string
}

export function HintContent({ id, children, align = 'left', side = 'bottom', className }: {
  id: string; children: ReactNode; align?: 'left' | 'right'; side?: 'top' | 'bottom'; className?: string
}) {
  return <span id={id} role="tooltip" className={cn('pointer-events-none absolute z-50 hidden w-72 max-w-[calc(100vw-4rem)] rounded-md border border-border bg-popover p-2 text-xs font-normal text-popover-foreground shadow-md group-hover:block group-focus-within:block', side === 'top' ? 'bottom-full mb-2' : 'top-full mt-2', align === 'right' ? 'right-0' : 'left-0', className)}>{children}</span>
}

export function InfoHint({ label, children, id, align = 'left', className }: InfoHintProps) {
  const generatedId = useId()
  const tooltipId = id ?? `info-hint-${generatedId.replaceAll(':', '')}`

  return <span className={cn('group relative inline-flex shrink-0', className)}>
    <button type="button" aria-label={label} aria-describedby={tooltipId} className="rounded-sm text-muted-foreground hover:text-foreground focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-copper">
      <Info className="size-3.5" />
    </button>
    <HintContent id={tooltipId} align={align}>{children}</HintContent>
  </span>
}
