import { AlertTriangle, LoaderCircle } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { EmptyState } from '@/components/ui/empty-state'
import { StudioApiError } from '@/lib/api'

export function QueryLoading({ label = 'Загрузка данных' }: { label?: string }) {
  return <div className="flex min-h-28 items-center justify-center gap-2 text-xs text-muted-foreground"><LoaderCircle className="size-4 animate-spin text-copper" />{label}</div>
}

export function QueryError({ error, retry, label = 'Не удалось получить данные' }: { error: unknown; retry?: () => void; label?: string }) {
  const message = error instanceof StudioApiError ? error.message : error instanceof Error ? error.message : 'Проверьте соединение с backend.'
  return <div className="flex min-h-28 items-center justify-center gap-3 border border-[#72544a] bg-secondary px-4 py-6 text-xs text-[#d0a390]"><AlertTriangle className="size-4 shrink-0 text-copper" /><div><p className="font-medium text-foreground">{label}</p><p className="mt-1 text-muted-foreground">{message}</p>{retry && <Button className="mt-3" size="sm" onClick={retry}>Повторить</Button>}</div></div>
}

export function QueryEmpty({ title, description }: { title: string; description?: string }) {
  return <EmptyState title={title} description={description} />
}
