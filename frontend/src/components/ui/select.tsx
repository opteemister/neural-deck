import type { SelectHTMLAttributes } from 'react'
import { cn } from '@/lib/utils'

export function Select({ className, ...props }: SelectHTMLAttributes<HTMLSelectElement>) {
  return (
    <select
      className={cn('h-10 w-full appearance-auto rounded-md border border-input bg-muted px-3 text-xs text-foreground outline-none focus-visible:border-copper focus-visible:ring-1 focus-visible:ring-copper', className)}
      {...props}
    />
  )
}
