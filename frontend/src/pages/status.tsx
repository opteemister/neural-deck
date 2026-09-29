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
  const health = useQuery({ queryKey: queryKeys.health, queryFn: api.getHealth })
  const statusData = status.data
  return <div><PageHeader eyebrow="Наблюдение / backend" title="Статус" description="Проверки компонентов backend и общего OpenAI-совместимого API." action={<Button variant="secondary" size="sm" onClick={() => { void status.refetch(); void health.refetch() }} disabled={status.isFetching || health.isFetching}><RefreshCw className={`size-3.5 ${status.isFetching || health.isFetching ? 'animate-spin' : ''}`} />Обновить</Button>} />{status.isLoading ? <QueryLoading label="Проверяем компоненты" /> : status.isError ? <QueryError error={status.error} retry={() => void status.refetch()} label="Страница статуса недоступна" /> : !statusData ? <QueryLoading label="Ожидаем статус" /> : <div className="space-y-5"><Card><CardContent className="flex flex-wrap items-center gap-4 py-4"><Activity className="size-5 text-copper" /><div><p className="text-[11px] text-muted-foreground">Общий результат</p><div className="mt-1 flex items-center gap-2"><StatusBadge state={statusData.overall} /><span className="text-xs text-muted-foreground">Состояние не означает, что каждая модель готова принимать запросы.</span></div></div><div className="ml-auto border-l border-border pl-4"><p className="text-[11px] text-muted-foreground">/api/v1/health</p>{health.isLoading ? <p className="mt-1 text-xs">Проверяем…</p> : health.isError ? <p className="mt-1 text-xs text-[#d0a390]">Недоступен</p> : <StatusBadge state={health.data?.status ?? 'degraded'} />}</div></CardContent></Card><Card><CardHeader><CardTitle>Компоненты</CardTitle><CardDescription>Последняя проверка для каждого обязательного компонента.</CardDescription></CardHeader><CardContent className="space-y-2">{statusData.components.map((component) => <div key={component.name} className="flex flex-wrap items-start justify-between gap-3 rounded-md border border-border bg-secondary px-3 py-3"><div className="flex gap-3"><StateIcon state={component.state} /><div><p className="text-xs font-medium">{component.name}</p>{component.message && <p className="mt-1 text-[11px] text-muted-foreground">{component.message}</p>}</div></div><div className="flex items-center gap-3"><StatusBadge state={component.state} /><span className="flex items-center gap-1 text-[11px] text-muted-foreground"><Clock3 className="size-3" />{formatTimestamp(component.checked_at)}</span></div></div>)}</CardContent></Card></div>}</div>
}

function StateIcon({ state }: { state: string }) { return state === 'ok' ? <CheckCircle2 className="mt-0.5 size-4 text-copper" /> : state === 'error' ? <XCircle className="mt-0.5 size-4 text-[#d09b87]" /> : <Activity className="mt-0.5 size-4 text-copper" /> }
