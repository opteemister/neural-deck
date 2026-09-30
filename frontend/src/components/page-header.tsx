import type { ReactNode } from 'react'

export function PageHeader({ title, action }: { eyebrow?: string | null; title: string; description?: string; action?: ReactNode }) {
  return <header className="mb-6 flex flex-wrap items-center justify-between gap-4"><h1 className="text-2xl font-medium tracking-tight text-foreground">{title}</h1>{action && <div className="shrink-0">{action}</div>}</header>
}
