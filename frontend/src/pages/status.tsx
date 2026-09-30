import { useQuery } from '@tanstack/react-query'
import { Activity, CheckCircle2, Clock3, RefreshCw, XCircle } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { PageHeader } from '@/components/page-header'
import { QueryError, QueryLoading } from '@/components/query-state'
import { StatusBadge } from '@/components/status-badge'
import { api, queryKeys } from '@/lib/api'
import { formatTimestamp } from '@/lib/utils'

export function StatusPage() {
  const status = useQuery({ queryKey: queryKeys.status, queryFn: api.getStatus })
  const statusData = status.data
  return <div>
    <PageHeader eyebrow="Наблюдение / backend" title="Статус" description="Проверки компонентов backend и общего OpenAI-совместимого API." action={<Button variant="secondary" size="sm" onClick={() => void status.refetch()} disabled={status.isFetching}><RefreshCw className={`size-3.5 ${status.isFetching ? 'animate-spin' : ''}`} />Обновить</Button>} />
    {status.isLoading ? <QueryLoading label="Проверяем компоненты" /> : status.isError ? <QueryError error={status.error} retry={() => void status.refetch()} label="Страница статуса недоступна" /> : !statusData ? <QueryLoading label="Ожидаем статус" /> : <div className="space-y-5">
      <Card>
        <CardHeader>
          <CardTitle>Компоненты</CardTitle>
          <CardDescription>Последняя проверка для каждого обязательного компонента.</CardDescription>
        </CardHeader>
        <CardContent className="space-y-2">
          {statusData.components.map((component) => <div key={component.name} className="grid grid-cols-[minmax(0,1fr)_auto] items-center gap-3 rounded-md border border-border bg-secondary px-3 py-3 md:grid-cols-[minmax(0,1fr)_11rem_12rem]">
            <div className="flex min-w-0 items-start gap-3">
              <StateIcon state={component.state} />
              <div className="min-w-0">
                <p className="text-xs font-medium">{component.name}</p>
              </div>
            </div>
            <div className="flex justify-end"><StatusBadge state={component.state} title={component.message ?? (component.state === 'degraded' ? 'Причина ограничения не сообщена сервером.' : undefined)} /></div>
            <span className="col-span-2 flex items-center gap-1 whitespace-nowrap text-[11px] tabular-nums text-muted-foreground md:col-span-1 md:justify-end"><Clock3 className="size-3 shrink-0" />{formatTimestamp(component.checked_at)}</span>
          </div>)}
        </CardContent>
      </Card>
    </div>}
  </div>
}

function StateIcon({ state }: { state: string }) {
  return state === 'ok' ? <CheckCircle2 className="mt-0.5 size-4 shrink-0 text-copper" /> : state === 'error' ? <XCircle className="mt-0.5 size-4 shrink-0 text-[#d09b87]" /> : <Activity className="mt-0.5 size-4 shrink-0 text-copper" />
}
