import { useId } from 'react'
import { cn } from '@/lib/utils'
import { HintContent } from '@/components/ui/info-hint'

export function Progress({ value, className, shape = 'line', label, title }: { value: number | null; className?: string; shape?: 'line' | 'circle'; label?: string; title?: string }) {
  const tooltipId = useId()
  if (shape === 'circle') return <span role="progressbar" aria-label={label ?? 'Прогресс'} aria-valuemin={0} aria-valuemax={100} aria-valuenow={value === null ? undefined : Math.min(100, Math.max(0, value))} aria-valuetext={title} aria-describedby={title ? tooltipId : undefined} tabIndex={0} className={cn('group relative inline-flex size-5 shrink-0 rounded-full focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-copper', className)}>
    <svg viewBox="0 0 24 24" className="size-full -rotate-90" fill="none" strokeWidth="3" aria-hidden="true">
      <circle cx="12" cy="12" r="9" className="stroke-border" />
      {value !== null && <circle cx="12" cy="12" r="9" pathLength="100" strokeDasharray="100" strokeDashoffset={100 - Math.min(100, Math.max(0, value))} className="stroke-copper transition-[stroke-dashoffset]" />}
    </svg>
    {title && <HintContent id={tooltipId} side="top" align="right" className="w-max max-sm:left-0 max-sm:right-auto">{title}</HintContent>}
  </span>
  return <div className={cn('h-1 w-full overflow-hidden rounded-full bg-border', className)} aria-label={value === null ? 'Прогресс недоступен' : `Прогресс ${value}%`}><div className="h-full bg-copper transition-all" style={{ width: value === null ? '0%' : `${Math.min(100, Math.max(0, value))}%` }} /></div>
}
