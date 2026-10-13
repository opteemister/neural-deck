import { LoaderCircle } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { EmptyState } from '@/components/ui/empty-state'
import { NotificationToast } from '@/components/ui/notification-toast'
import { NeuralDeckApiError } from '@/lib/api'

export function QueryLoading({ label = 'Загрузка данных' }: { label?: string }) {
  return <div className="flex min-h-28 items-center justify-center gap-2 text-xs text-muted-foreground"><LoaderCircle className="size-4 animate-spin text-copper" />{label}</div>
}

export function QueryError({ error, retry, label = 'Не удалось получить данные' }: { error: unknown; retry?: () => void; label?: string }) {
  const message = error instanceof NeuralDeckApiError ? error.message : error instanceof Error ? error.message : 'Проверьте соединение с backend.'
  return <NotificationToast tone="error" title={label} message={message} durationMs={10_000} action={retry ? <Button className="mt-3" size="sm" onClick={retry}>Повторить</Button> : undefined} />
}

export function QueryEmpty({ title, description }: { title: string; description?: string }) {
  return <EmptyState title={title} description={description} />
}
