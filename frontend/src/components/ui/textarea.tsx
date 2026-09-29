import type { TextareaHTMLAttributes } from 'react'
import { cn } from '@/lib/utils'

export function Textarea({ className, ...props }: TextareaHTMLAttributes<HTMLTextAreaElement>) {
  return <textarea className={cn('min-h-28 w-full resize-y rounded-md border border-input bg-muted px-3 py-2 font-mono text-xs leading-6 text-foreground outline-none placeholder:text-muted-foreground focus-visible:border-copper focus-visible:ring-1 focus-visible:ring-copper', className)} {...props} />
}
