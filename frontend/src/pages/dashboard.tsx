import { useEffect, useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Activity, Cpu, Database, Gauge, RefreshCw, Server } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Progress } from '@/components/ui/progress'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { PageHeader } from '@/components/page-header'
import { QueryEmpty, QueryError, QueryLoading } from '@/components/query-state'
import { StatusBadge } from '@/components/status-badge'
import { api, queryKeys } from '@/lib/api'
import { formatBytes, formatPercent, formatTimestamp } from '@/lib/utils'
import type { Instance, TokenStatsItem } from '@/types/api'
import { useSearchParams } from 'react-router-dom'

function Hardware({ data }: { data: Awaited<ReturnType<typeof api.getSystem>> }) {
  const ramPercent = data.ram.total_bytes > 0 ? (data.ram.used_bytes / data.ram.total_bytes) * 100 : null
  return <Card><CardHeader><CardTitle>Ресурсы машины</CardTitle><CardDescription>Данные текущего наблюдения backend</CardDescription></CardHeader><CardContent className="space-y-4"><div className="flex flex-wrap gap-x-8 gap-y-3 border-b border-border pb-3 text-xs"><div className="flex items-center gap-2"><Cpu className="size-4 text-copper" /><span className="text-muted-foreground">CPU</span><strong>{data.cpu.cores} ядер</strong><span className="text-muted-foreground">/ загрузка {formatPercent(data.cpu.usage_percent)}</span></div><div className="flex min-w-64 items-center gap-2"><Database className="size-4 text-copper" /><span className="text-muted-foreground">RAM</span><strong>{formatBytes(data.ram.used_bytes)}</strong><span className="text-muted-foreground">/ {formatBytes(data.ram.total_bytes)}</span>{ramPercent === null ? <span className="ml-auto text-[11px] text-muted-foreground">недоступно</span> : <Progress value={ramPercent} className="ml-auto max-w-20" />}</div></div>{data.gpus.length === 0 ? <QueryEmpty title="GPU не обнаружены" description="Backend не сообщил доступные NVIDIA GPU." /> : <Table><TableHeader><TableRow><TableHead className="w-12">GPU</TableHead><TableHead>Видеокарта</TableHead><TableHead>Память</TableHead><TableHead>Загрузка</TableHead><TableHead className="text-right">Темп.</TableHead></TableRow></TableHeader><TableBody>{data.gpus.map((gpu) => <TableRow key={gpu.index}><TableCell className="font-mono text-muted-foreground">{gpu.index}</TableCell><TableCell className="font-medium">{gpu.name}</TableCell><TableCell className="whitespace-nowrap font-mono">{formatBytes(gpu.memory_used_bytes)} <span className="text-muted-foreground">/ {formatBytes(gpu.memory_total_bytes)}</span></TableCell><TableCell>{gpu.usage_percent === null ? <span className="text-muted-foreground">недоступно</span> : <div className="flex min-w-24 items-center gap-3"><Progress value={gpu.usage_percent} /><span className="w-12 text-right font-mono text-muted-foreground">{formatPercent(gpu.usage_percent)}</span></div>}</TableCell><TableCell className="text-right font-mono">{gpu.temperature_celsius === null ? <span className="text-muted-foreground">недоступно</span> : `${gpu.temperature_celsius} °C`}</TableCell></TableRow>)}</TableBody></Table>}</CardContent></Card>
}

function LogsPanel({ instance, data, loading, error, retry }: { instance: Instance; data: Awaited<ReturnType<typeof api.getLogs>> | undefined; loading: boolean; error: unknown; retry: () => void }) {
  return <Card className="mt-4"><CardHeader><CardTitle>Логи запуска</CardTitle><CardDescription>{instance.profile_name ?? instance.id} · хвост процесса</CardDescription></CardHeader><CardContent>{loading ? <QueryLoading label="Получаем лог запуска" /> : error ? <QueryError error={error} retry={retry} label="Лог запуска недоступен" /> : !data || data.lines.length === 0 ? <QueryEmpty title="Лог пока пуст" description="Backend не сообщил строки для выбранного экземпляра." /> : <div><pre className="max-h-72 overflow-auto whitespace-pre-wrap rounded-md bg-background p-3 font-mono text-[11px] leading-5">{data.lines.join('\n')}</pre>{data.truncated && <p className="mt-2 text-[11px] text-muted-foreground">Показан только доступный хвост лога.</p>}</div>}</CardContent></Card>
}

function TokenStatsPanel({ items, loading, error, retry }: { items: TokenStatsItem[]; loading: boolean; error: unknown; retry: () => void }) {
  return <Card><CardHeader><CardTitle>Статистика токенов</CardTitle><CardDescription>Учёт запросов через общий OpenAI API по профилям. Неизвестные значения сохранены как неизвестные.</CardDescription></CardHeader><CardContent>{loading ? <QueryLoading label="Получаем статистику токенов" /> : error ? <QueryError error={error} retry={retry} label="Статистика токенов недоступна" /> : items.length === 0 ? <QueryEmpty title="Статистики пока нет" description="После запросов через общий API здесь появятся наблюдаемые счётчики." /> : <Table><TableHeader><TableRow><TableHead>Профиль</TableHead><TableHead className="text-right">Запросы</TableHead><TableHead className="text-right">Вход</TableHead><TableHead className="text-right">Выход</TableHead><TableHead>Источник</TableHead></TableRow></TableHeader><TableBody>{items.map((item) => <TableRow key={item.profile_name}><TableCell className="font-mono text-xs">{item.profile_name}</TableCell><TableCell className="text-right font-mono">{item.requests}</TableCell><TableCell className="text-right font-mono">{formatTokenCount(item.input_tokens.known_total, item.input_tokens.unknown_requests)}</TableCell><TableCell className="text-right font-mono">{formatTokenCount(item.output_tokens.known_total, item.output_tokens.unknown_requests)}</TableCell><TableCell className="text-xs text-muted-foreground">{item.source}</TableCell></TableRow>)}</TableBody></Table>}</CardContent></Card>
}

function formatTokenCount(known: number | null, unknown: number): string {
  const total = known === null ? 'недоступно' : known.toLocaleString('ru-RU')
  return unknown > 0 ? `${total} + ${unknown} неизвестно` : total
}

function canStopInstance(instance: Instance | undefined): boolean {
  return instance?.can_stop === true && instance.identity_verified === true
}

export function DashboardPage() {
  const queryClient = useQueryClient()
  const [searchParams, setSearchParams] = useSearchParams()
  const system = useQuery({ queryKey: queryKeys.system, queryFn: api.getSystem, refetchInterval: 10_000 })
  const status = useQuery({ queryKey: queryKeys.status, queryFn: api.getStatus, refetchInterval: 15_000 })
  const health = useQuery({ queryKey: queryKeys.health, queryFn: api.getHealth, refetchInterval: 15_000 })
  const activeInstances = useMemo(() => (system.data?.instances ?? []).filter((instance) => ['starting', 'ready', 'running', 'stopping'].includes(instance.state) || (instance.source === 'external' && instance.state === 'unknown')), [system.data?.instances])
  const selectedId = searchParams.get('instance')
  const selected = activeInstances.find((instance) => instance.id === selectedId) ?? activeInstances[0]
  const selectedCanStop = canStopInstance(selected)
  const metrics = useQuery({ queryKey: ['metrics', selected?.id], queryFn: () => api.getMetrics(selected!.id), enabled: Boolean(selected), refetchInterval: 10_000 })
  const logs = useQuery({ queryKey: ['logs', selected?.id], queryFn: () => api.getLogs(selected!.id), enabled: Boolean(selected), refetchInterval: 10_000 })
  const tokenStats = useQuery({ queryKey: queryKeys.tokens, queryFn: () => api.getTokenStats(), refetchInterval: 15_000 })
  const [stopOperationId, setStopOperationId] = useState<string | null>(null)
  const stopOperation = useQuery({ queryKey: queryKeys.operation(stopOperationId ?? ''), queryFn: () => api.getOperation(stopOperationId!), enabled: Boolean(stopOperationId), refetchInterval: (query) => ['queued', 'running'].includes(query.state.data?.state ?? '') ? 1_500 : false })
  const stop = useMutation({ mutationFn: (id: string) => api.stopInstance(id), onSuccess: (operation) => { setStopOperationId(operation.id); void queryClient.invalidateQueries({ queryKey: queryKeys.system }) } })

  useEffect(() => {
    // Keep a deep-link selection while the first system response is loading.
    // Clearing it during the loading render would silently select the first
    // instance once the response arrives.
    if (system.isLoading) return
    if (selected?.id === selectedId) return
    setSearchParams((current) => {
      if (selected) current.set('instance', selected.id)
      else current.delete('instance')
      return current
    }, { replace: true })
  }, [selected, selectedId, setSearchParams, system.isLoading])

  function selectInstance(id: string) {
    setSearchParams((current) => { current.set('instance', id); return current }, { replace: true })
  }

  return <div><PageHeader eyebrow="Студия / обзор" title="Dashboard" description="Состояние машины и активные экземпляры, полученные от backend." action={<Button variant="secondary" size="sm" onClick={() => { void system.refetch(); void status.refetch(); void health.refetch() }}><RefreshCw className="size-3.5" />Обновить</Button>} />
    <div className="mb-6 grid gap-3 sm:grid-cols-3"><Card className="sm:col-span-2"><CardContent className="flex items-center gap-3 py-3"><Server className="size-4 text-copper" /><div><p className="text-[11px] text-muted-foreground">Backend</p>{status.isLoading ? <p className="text-xs">Проверяем состояние…</p> : status.isError ? <p className="text-xs text-[#d0a390]">Состояние недоступно</p> : <div className="mt-1 flex items-center gap-2"><StatusBadge state={status.data?.overall ?? 'unknown'} /><span className="text-xs text-muted-foreground">{status.data?.components.length ?? 0} компонентов проверено</span></div>}</div></CardContent></Card><Card><CardContent className="flex items-center gap-3 py-3"><Activity className="size-4 text-copper" /><div><p className="text-[11px] text-muted-foreground">Наблюдение</p><p className="mt-1 text-xs">{system.isLoading ? 'Загрузка…' : `${activeInstances.length} активных экземпляров`}</p></div></CardContent></Card><Card><CardContent className="flex items-center gap-3 py-3"><span className="size-2 rounded-full bg-copper" /><div><p className="text-[11px] text-muted-foreground">Health endpoint</p>{health.isLoading ? <p className="mt-1 text-xs">Проверяем…</p> : health.isError ? <p className="mt-1 text-xs text-[#d0a390]">Недоступен</p> : <div className="mt-1"><StatusBadge state={health.data?.status ?? 'degraded'} /></div>}</div></CardContent></Card></div>
    {stopOperation?.data && <div className="mb-4 rounded-md border border-border bg-secondary px-4 py-3 text-xs"><div className="flex items-center gap-2"><StatusBadge state={stopOperation.data.state} /><span>Остановка экземпляра <code>{stopOperation.data.target_id}</code></span></div>{stopOperation.data.error && <p className="mt-1 text-[#d0a390]">{stopOperation.data.error}</p>}</div>}{stopOperation.isError && <div className="mb-4"><QueryError error={stopOperation.error} retry={() => void stopOperation.refetch()} label="Состояние остановки недоступно" /></div>}{stop.isError && <div className="mb-4"><QueryError error={stop.error} label="Остановка экземпляра не запущена" /></div>}
    {system.isLoading ? <QueryLoading label="Получаем состояние системы" /> : system.isError ? <QueryError error={system.error} retry={() => void system.refetch()} label="Состояние системы недоступно" /> : system.data && <div className="space-y-6">
      <Hardware data={system.data} />
      <section>
        <div className="mb-3 flex items-end justify-between gap-3"><div><h2 className="text-base font-medium">Активные запуски</h2><p className="mt-1 text-xs text-muted-foreground">Профили и внешние процессы, которые backend видит сейчас.</p></div>{selected && <span className="font-mono text-[11px] text-muted-foreground">обновлено {formatTimestamp(selected.observed_at)}</span>}</div>
        {activeInstances.length === 0 ? <QueryEmpty title="Нет активных запусков" description="Запустите модель на странице профилей или обновите наблюдение." /> : <div className="grid gap-4 xl:grid-cols-[minmax(220px,0.4fr)_minmax(0,1fr)]">
          <div className="space-y-1 rounded-md border border-border bg-secondary p-2">{activeInstances.map((instance) => <button key={instance.id} type="button" className={`w-full rounded-md border-l-2 px-3 py-3 text-left transition-colors ${selected?.id === instance.id ? 'border-l-copper bg-card' : 'border-l-transparent hover:bg-card'}`} onClick={() => selectInstance(instance.id)}><div className="flex items-start justify-between gap-3"><span className="text-sm font-medium">{instance.profile_name ?? instance.model_name ?? 'Внешний экземпляр'}</span><span className="size-1.5 shrink-0 rounded-full bg-copper" /></div><p className="mt-1 text-xs text-muted-foreground">{instance.model_name ?? 'модель неизвестна'}</p><div className="mt-3 flex justify-between gap-2 font-mono text-[11px] text-muted-foreground"><span>{instance.engine_id}</span><span>{instance.port === null ? 'порт неизвестен' : `:${instance.port}`}</span></div></button>)}</div>
          <div>{selected && <><Card><CardHeader><div className="flex items-center justify-between gap-3"><div><CardTitle>{selected.profile_name ?? selected.model_name ?? 'Экземпляр'}</CardTitle><CardDescription>{selected.source === 'external' ? 'Внешний процесс' : 'Профиль LLM Studio'} · PID {selected.pid}</CardDescription></div><div className="flex items-center gap-2"><StatusBadge state={selected.state} /><Button size="sm" variant="danger" disabled={!selectedCanStop || stop.isPending || selected.state === 'stopping'} title={selectedCanStop ? undefined : 'Backend не подтвердил право остановки этого процесса'} onClick={() => void stop.mutate(selected.id)}>{selected.source === 'external' ? 'Остановить внешний' : 'Остановить'}</Button></div></div></CardHeader><CardContent>{selected.source === 'external' && selected.process_start_time_ns === null && <p className="mb-4 rounded-md border border-border bg-secondary px-3 py-2 text-xs text-muted-foreground">Наблюдение внешнего процесса неполное: время запуска не сообщено backend.</p>}{!selectedCanStop && <p className="mb-4 rounded-md border border-border bg-secondary px-3 py-2 text-xs text-muted-foreground">Остановка недоступна: backend не подтвердил идентичность и право управления этим процессом.</p>}{metrics.isLoading ? <QueryLoading label="Получаем метрики экземпляра" /> : metrics.isError ? <QueryError error={metrics.error} retry={() => void metrics.refetch()} label="Метрики экземпляра недоступны" /> : metrics.data?.items.length ? <div className="grid gap-x-6 gap-y-5 sm:grid-cols-2">{metrics.data.items.map((metric) => <div key={`${metric.name}-${metric.source}`}><div className="flex items-center gap-2 text-[11px] text-muted-foreground"><Gauge className="size-3.5" />{metric.name}</div><p className="mt-2 text-xl font-normal tabular-nums">{metric.state === 'ok' && metric.value !== null ? `${metric.value}${metric.unit ? ` ${metric.unit}` : ''}` : <span className="text-sm text-muted-foreground">{metric.state === 'stale' ? 'устарело' : 'недоступно'}</span>}</p><p className="mt-1 text-[11px] text-muted-foreground">Источник: {metric.source} · {formatTimestamp(metric.observed_at)}</p></div>)}</div> : <QueryEmpty title="Метрики недоступны" description="Для этого экземпляра backend пока не сообщил показатели." />}</CardContent></Card><LogsPanel instance={selected} data={logs.data} loading={logs.isLoading} error={logs.error} retry={() => void logs.refetch()} /></>}</div>
        </div>}
      </section>
      <TokenStatsPanel items={tokenStats.data?.items ?? []} loading={tokenStats.isLoading} error={tokenStats.error} retry={() => void tokenStats.refetch()} />
    </div>}
  </div>
}
