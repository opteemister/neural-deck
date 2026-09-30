import { useEffect, useState } from 'react'
import { AlertTriangle, LoaderCircle, X } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { EmptyState } from '@/components/ui/empty-state'
import { StudioApiError } from '@/lib/api'

export function QueryLoading({ label = 'Загрузка данных' }: { label?: string }) {
  return <div className="flex min-h-28 items-center justify-center gap-2 text-xs text-muted-foreground"><LoaderCircle className="size-4 animate-spin text-copper" />{label}</div>
}

export function QueryError({ error, retry, label = 'Не удалось получить данные' }: { error: unknown; retry?: () => void; label?: string }) {
  const [visible, setVisible] = useState(true)
  const message = error instanceof StudioApiError ? error.message : error instanceof Error ? error.message : 'Проверьте соединение с backend.'
  useEffect(() => {
    setVisible(true)
    const timeout = window.setTimeout(() => setVisible(false), 10_000)
    return () => window.clearTimeout(timeout)
  }, [error, label])

  if (!visible) return null
  return <div role="alert" className="fixed right-4 top-20 z-50 w-[min(30rem,calc(100vw-2rem))] rounded-md border border-[#72544a] bg-secondary px-3 py-3 text-xs text-[#d0a390] shadow-xl"><div className="flex items-start gap-3"><AlertTriangle className="mt-0.5 size-4 shrink-0 text-copper" /><div className="min-w-0 flex-1"><div className="flex items-start justify-between gap-3"><p className="font-medium text-foreground">{label}</p><button type="button" className="shrink-0 text-muted-foreground transition-colors hover:text-foreground" aria-label="Закрыть уведомление" onClick={() => setVisible(false)}><X className="size-3.5" /></button></div><p className="mt-1 break-words text-muted-foreground">{message}</p>{retry && <Button className="mt-3" size="sm" onClick={retry}>Повторить</Button>}</div></div></div>
}

export function QueryEmpty({ title, description }: { title: string; description?: string }) {
  return <EmptyState title={title} description={description} />
}
