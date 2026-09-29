import type { ReactNode } from 'react'

export function PageHeader({ eyebrow, title, description, action }: { eyebrow?: string; title: string; description?: string; action?: ReactNode }) {
  return <header className="mb-6 flex flex-wrap items-start justify-between gap-4"><div><p className="mb-1.5 text-[11px] text-muted-foreground">{eyebrow ?? 'LLM Studio'}</p><h1 className="text-2xl font-medium tracking-tight text-foreground">{title}</h1>{description && <p className="mt-2 max-w-2xl text-xs text-muted-foreground">{description}</p>}</div>{action && <div className="shrink-0">{action}</div>}</header>
}
