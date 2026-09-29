import type { ReactNode } from 'react'
import { Inbox } from 'lucide-react'

export function EmptyState({ title, description, action }: { title: string; description?: string; action?: ReactNode }) {
  return <div className="flex min-h-40 flex-col items-center justify-center gap-2 border border-dashed border-border bg-secondary/50 px-6 py-8 text-center"><Inbox className="size-5 text-muted-foreground" /><h3 className="text-sm font-medium">{title}</h3>{description && <p className="max-w-md text-xs text-muted-foreground">{description}</p>}{action}</div>
}
