import type { MouseEvent, ReactNode } from 'react'
import { ChevronDown } from 'lucide-react'
import { Card } from '@/components/ui/card'
import { cn } from '@/lib/utils'

export function ExpandableCard({ expanded, onToggle, summary, actions, children, className, headerClassName, summaryClassName, contentClassName }: {
  expanded: boolean
  onToggle: () => void
  summary: ReactNode
  actions?: ReactNode
  children?: ReactNode
  className?: string
  headerClassName?: string
  summaryClassName?: string
  contentClassName?: string
}) {
  function toggleFromHeader(event: MouseEvent<HTMLDivElement>) {
    const target = event.target
    if (target instanceof Element && target.closest('button, a, input, select, textarea, [role="button"]')) return
    onToggle()
  }

  return <Card className={cn('overflow-hidden', className)}>
    <div className={cn('flex cursor-pointer items-center gap-3 px-4 py-3', expanded && 'border-b border-border', headerClassName)} onClick={toggleFromHeader}>
      <button type="button" className="shrink-0 rounded-sm text-muted-foreground transition-colors hover:text-foreground focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-copper" aria-label={expanded ? 'Свернуть' : 'Развернуть'} title={expanded ? 'Свернуть' : 'Развернуть'} aria-expanded={expanded} onClick={onToggle}>
        <ChevronDown className={cn('size-4 transition-transform', expanded && 'rotate-180')} />
      </button>
      <div className={cn('min-w-0 flex-1', summaryClassName)}>{summary}</div>
      {actions && <div className="flex shrink-0 cursor-default items-center gap-2" onClick={(event) => event.stopPropagation()}>{actions}</div>}
    </div>
    {expanded && <div className={cn('px-4 py-4', contentClassName)}>{children}</div>}
  </Card>
}
