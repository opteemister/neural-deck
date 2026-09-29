import { cn } from '@/lib/utils'

export function Progress({ value, className }: { value: number | null; className?: string }) {
  return <div className={cn('h-1 w-full overflow-hidden rounded-full bg-border', className)} aria-label={value === null ? 'Прогресс недоступен' : `Прогресс ${value}%`}><div className="h-full bg-copper transition-all" style={{ width: value === null ? '0%' : `${Math.min(100, Math.max(0, value))}%` }} /></div>
}
