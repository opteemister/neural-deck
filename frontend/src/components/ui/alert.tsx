import type { HTMLAttributes } from 'react'
import { AlertTriangle, Info } from 'lucide-react'
import { cn } from '@/lib/utils'

export function Alert({ className, ...props }: HTMLAttributes<HTMLDivElement>) {
  return <div role="alert" className={cn('flex gap-3 rounded-md border border-border bg-secondary px-3 py-3 text-xs', className)} {...props} />
}

export function InlineAlert({ title, message, tone = 'warning' }: { title: string; message?: string; tone?: 'warning' | 'info' }) {
  const Icon = tone === 'warning' ? AlertTriangle : Info
  return <Alert className={tone === 'warning' ? 'border-[#72544a] text-[#d0a390]' : 'border-border text-muted-foreground'}><Icon className="mt-0.5 size-4 shrink-0 text-copper" /><div><p className="font-medium text-foreground">{title}</p>{message && <p className="mt-1 text-muted-foreground">{message}</p>}</div></Alert>
}
