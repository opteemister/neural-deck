import type { InputHTMLAttributes } from 'react'
import { cn } from '@/lib/utils'

export function Input({ className, ...props }: InputHTMLAttributes<HTMLInputElement>) {
  return <input className={cn('flex h-10 w-full rounded-md border border-input bg-muted px-3 text-xs text-foreground outline-none placeholder:text-muted-foreground focus-visible:border-copper focus-visible:ring-1 focus-visible:ring-copper disabled:opacity-50', className)} {...props} />
}
