import type { HTMLAttributes } from 'react'
import { cn } from '@/lib/utils'

export function Badge({ className, children, ...props }: HTMLAttributes<HTMLSpanElement>) {
  return <span className={cn('inline-flex items-center rounded-sm border border-border px-2 py-0.5 text-[11px] text-muted-foreground', className)} {...props}>{children}</span>
}
