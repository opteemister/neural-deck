import { useEffect, useMemo, useState } from 'react'
import { useMutation, useQueries, useQuery, useQueryClient } from '@tanstack/react-query'
import { ArrowDown, ArrowUp, ArrowUpDown, ChevronRight, Cpu, Database, Info, MemoryStick, RefreshCw, Zap } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { RunTabs } from '@/components/ui/run-tabs'
import { ModelPerformance } from '@/components/model-performance'
import { Progress } from '@/components/ui/progress'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { PageHeader } from '@/components/page-header'
import { QueryEmpty, QueryError, QueryLoading } from '@/components/query-state'
import { StatusBadge } from '@/components/status-badge'
import { api, queryKeys } from '@/lib/api'
import { formatBytes, formatPercent, formatTimestamp } from '@/lib/utils'
import type { Instance, MetricsData, TokenStatsItem } from '@/types/api'
import { useSearchParams } from 'react-router-dom'

const gpuColumns = [
  { key: 'index', label: 'GPU' }, { key: 'name', label: 'Видеокарта' },
  { key: 'memory_used_bytes', label: 'Память' }, { key: 'usage_percent', label: 'Загрузка' },
  { key: 'temperature_celsius', label: 'Темп.' }, { key: 'power_watts', label: 'Мощность' },
] as const
type GpuSort = { key: typeof gpuColumns[number]['key']; direction: 'asc' | 'desc' }
function runUptime(instance: Instance): string {
  if (instance.process_start_time_ns == null) return '—'
  const minutes = Math.floor((Date.parse(instance.observed_at) - instance.process_start_time_ns / 1_000_000) / 60_000)
  if (!Number.isFinite(minutes) || minutes < 0) return '—'
  return `${Math.floor(minutes / 60)}ч ${String(minutes % 60).padStart(2, '0')}м`
}

const gpuSortStorage = 'llm-studio.gpu-sort'
function readGpuSort(): GpuSort {
  try {
    const saved = JSON.parse(window.localStorage.getItem(gpuSortStorage) ?? 'null') as GpuSort | null
    if (saved && gpuColumns.some((column) => column.key === saved.key) && ['asc', 'desc'].includes(saved.direction)) return saved
  } catch { /* Browser storage may be disabled or contain an invalid preference. */ }
  return { key: 'index', direction: 'asc' }
}

function Hardware({ data }: { data: Awaited<ReturnType<typeof api.getSystem>> }) {
  const [sort, setSort] = useState<GpuSort>(readGpuSort)
  useEffect(() => {
    try { window.localStorage.setItem(gpuSortStorage, JSON.stringify(sort)) }
    catch { /* Sorting remains available when browser storage is disabled. */ }
  }, [sort])
  const sortedGpus = useMemo(() => [...data.gpus].sort((left, right) => {
    const a = left[sort.key], b = right[sort.key]
    if (a == null || b == null) return a == null && b == null ? left.index - right.index : a == null ? 1 : -1
    const comparison = typeof a === 'string' && typeof b === 'string' ? a.localeCompare(b, 'ru', { numeric: true }) : Number(a) - Number(b)
    return (sort.direction === 'asc' ? comparison : -comparison) || left.index - right.index
  }), [data.gpus, sort])
  const ramPercent = data.ram.total_bytes > 0 ? (data.ram.used_bytes / data.ram.total_bytes) * 100 : null
  const vram = data.gpus.reduce((sum, gpu) => ({ used: sum.used + gpu.memory_used_bytes, total: sum.total + gpu.memory_total_bytes }), { used: 0, total: 0 })
  const vramPercent = vram.total > 0 ? vram.used / vram.total * 100 : null
  const power = data.gpus.length && data.gpus.every((gpu) => gpu.power_watts != null) ? data.gpus.reduce((total, gpu) => total + gpu.power_watts!, 0) : null
  const powerLimit = data.gpus.length && data.gpus.every((gpu) => gpu.power_limit_watts != null) ? data.gpus.reduce((total, gpu) => total + gpu.power_limit_watts!, 0) : null
  const watts = (value: number | null | undefined) => value == null ? '—' : value.toLocaleString('ru-RU', { maximumFractionDigits: 0 })
  const resourceClass = 'flex max-w-full flex-none items-center gap-2 pr-2 whitespace-nowrap text-xs tabular-nums'
  return <Card><CardHeader><CardTitle>Локальные ресурсы</CardTitle></CardHeader><CardContent className="space-y-4"><div className="flex flex-wrap gap-x-2 gap-y-3 border-b border-border pb-3 text-xs">
    <div className="flex w-[488px] max-w-full flex-wrap gap-x-2 gap-y-3"><div className={`${resourceClass} w-60`}><Database className="size-3.5 shrink-0 text-copper" /><span className="shrink-0 text-muted-foreground">RAM</span><span className="shrink-0"><strong>{formatBytes(data.ram.used_bytes)}</strong><span className="text-muted-foreground"> / {formatBytes(data.ram.total_bytes)}</span></span>{ramPercent === null ? <span className="text-[11px] text-muted-foreground">недоступно</span> : <span className="block min-w-0 flex-1"><Progress value={ramPercent} /></span>}</div>
    <div className={`${resourceClass} w-60`}><MemoryStick className="size-3.5 shrink-0 text-copper" /><span className="shrink-0 text-muted-foreground">VRAM</span>{vramPercent === null ? <span className="text-muted-foreground">недоступно</span> : <><span className="shrink-0"><strong>{formatBytes(vram.used)}</strong><span className="text-muted-foreground"> / {formatBytes(vram.total)}</span></span><span className="block min-w-0 flex-1"><Progress value={vramPercent} /></span></>}</div>
    </div><div className="flex w-[488px] max-w-full flex-wrap gap-x-2 gap-y-3"><div className={`${resourceClass} w-60`}><Zap className="size-3.5 shrink-0 text-copper" /><span className="shrink-0 text-muted-foreground">Power</span><span className="shrink-0"><strong>{watts(power)}</strong><span className="text-muted-foreground"> / {watts(powerLimit)} Вт</span></span>{power !== null && powerLimit !== null && powerLimit > 0 && <span className="block min-w-0 flex-1"><Progress value={power / powerLimit * 100} /></span>}</div>
    <div className={`${resourceClass} w-60`}><Cpu className="size-3.5 shrink-0 text-copper" /><span className="shrink-0 text-muted-foreground">CPU</span><span className="shrink-0"><strong>{data.cpu.cores} ядер</strong><span className="text-muted-foreground"> / загрузка {formatPercent(data.cpu.usage_percent)}</span></span></div>
  </div></div>{data.gpus.length === 0 ? <QueryEmpty title="GPU не обнаружены" description="Backend не сообщил доступные NVIDIA GPU." /> : <Table><TableHeader><TableRow>{gpuColumns.map((column) => <TableHead key={column.key} aria-sort={sort.key === column.key ? sort.direction === 'asc' ? 'ascending' : 'descending' : 'none'} className={['temperature_celsius', 'power_watts'].includes(column.key) ? 'text-right' : undefined}><button type="button" className={`inline-flex items-center gap-1.5 whitespace-nowrap rounded-sm hover:text-foreground focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-copper ${sort.key === column.key ? 'text-foreground' : ''}`} title={column.key === 'memory_used_bytes' ? 'Сортировать по занятой памяти' : column.key === 'power_watts' ? 'Сортировать по текущему потреблению' : `Сортировать: ${column.label}`} onClick={() => setSort((current) => ({ key: column.key, direction: current.key === column.key && current.direction === 'asc' ? 'desc' : 'asc' }))}>{column.label}{sort.key !== column.key ? <ArrowUpDown className="size-3 opacity-50" /> : sort.direction === 'asc' ? <ArrowUp className="size-3 text-copper" /> : <ArrowDown className="size-3 text-copper" />}</button></TableHead>)}</TableRow></TableHeader><TableBody>{sortedGpus.map((gpu) => <TableRow key={gpu.index}><TableCell className="font-mono text-muted-foreground">{gpu.index}</TableCell><TableCell className="font-medium">{gpu.name}</TableCell><TableCell><div className="flex min-w-44 items-center gap-3"><Progress value={gpu.memory_total_bytes > 0 ? gpu.memory_used_bytes / gpu.memory_total_bytes * 100 : null} className="min-w-12" /><span className="shrink-0 whitespace-nowrap font-mono">{formatBytes(gpu.memory_used_bytes)} <span className="text-muted-foreground">/ {formatBytes(gpu.memory_total_bytes)}</span></span></div></TableCell><TableCell>{gpu.usage_percent === null ? <span className="text-muted-foreground">недоступно</span> : <div className="flex min-w-24 items-center gap-3"><Progress value={gpu.usage_percent} /><span className="w-12 text-right font-mono text-muted-foreground">{formatPercent(gpu.usage_percent)}</span></div>}</TableCell><TableCell className="text-right font-mono">{gpu.temperature_celsius === null ? <span className="text-muted-foreground">недоступно</span> : `${gpu.temperature_celsius} °C`}</TableCell><TableCell className="whitespace-nowrap text-right font-mono"><span>{watts(gpu.power_watts)}<span className="text-muted-foreground"> / {watts(gpu.power_limit_watts)} Вт</span></span></TableCell></TableRow>)}</TableBody></Table>}</CardContent></Card>
}

function LogsPanel({ instance, data, loading, error, retry }: { instance: Instance; data: Awaited<ReturnType<typeof api.getLogs>> | undefined; loading: boolean; error: unknown; retry: () => void }) {
  if (instance.source === 'external' && (!data || data.available === false)) return null
  const showError = Boolean(error) && !(instance.source === 'external' && data)
  return <details className="group mt-4 border-t border-border pt-3"><summary className="flex cursor-pointer list-none items-center gap-2 text-xs font-medium [&::-webkit-details-marker]:hidden"><ChevronRight className="size-3.5 transition-transform group-open:rotate-90" />Логи запуска</summary><div className="pt-3">{loading ? <QueryLoading label="Получаем лог запуска" /> : showError ? <QueryError error={error} retry={retry} label="Лог запуска недоступен" /> : !data || data.lines.length === 0 ? <QueryEmpty title="Лог пока пуст" description="Backend не сообщил строки для выбранного экземпляра." /> : <div><pre className="max-h-72 overflow-auto whitespace-pre-wrap rounded-md bg-background p-3 font-mono text-[11px] leading-5">{data.lines.join('\n')}</pre>{data.truncated && <p className="mt-2 text-[11px] text-muted-foreground">Показан только доступный хвост лога.</p>}</div>}</div></details>
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
  const system = useQuery({ queryKey: queryKeys.system, queryFn: api.getSystem, refetchInterval: 2_000, refetchIntervalInBackground: false })
  const activeInstances = useMemo(() => (system.data?.instances ?? []).filter((instance) => ['starting', 'ready', 'running', 'stopping'].includes(instance.state) || (instance.source === 'external' && instance.state === 'unknown')), [system.data?.instances])
  const selectedId = searchParams.get('instance') ?? 'all'
  const selected = activeInstances.find((instance) => instance.id === selectedId)
  const selectedCanStop = canStopInstance(selected)
  const metricQueries = useQueries({ queries: activeInstances.map((instance) => ({ queryKey: ['metrics', instance.id], queryFn: () => api.getMetrics(instance.id), retry: instance.source === 'external' ? false as const : 1, refetchInterval: 2_000, refetchIntervalInBackground: false })) })
  const selectedMetrics = metricQueries[activeInstances.findIndex((instance) => instance.id === selectedId)]
  const aggregateMetrics: MetricsData = {
    items: ['decode_tps', 'ttft_mean_ms', 'prefill_tps', 'kv_cache_percent', 'active_requests', 'queue_requests', 'prompt_tokens_total', 'completion_tokens_total', 'inflight_completion_tokens', 'completed_requests'].map((name) => {
      const observations = metricQueries.map((query) => query.isError ? undefined : query.data?.items.find((item) => item.name === name))
      const complete = observations.every((item) => item?.state === 'ok' && typeof item.value === 'number' && Number.isFinite(item.value))
      const observedAt = observations.flatMap((item) => item ? [item.observed_at] : []).sort().at(-1) ?? activeInstances[0]?.observed_at ?? new Date(system.dataUpdatedAt).toISOString()
      const values = complete ? observations.map((item) => item!.value as number) : []
      const sum = values.reduce((total, value) => total + value, 0)
      const value = !complete ? null : name === 'ttft_mean_ms' ? values.length ? sum / values.length : 0 : name === 'kv_cache_percent' ? Math.max(0, ...values) : sum
      const source = name === 'ttft_mean_ms' ? 'Среднее по активным запускам' : name === 'kv_cache_percent' ? 'Максимальная занятость среди активных запусков' : 'Сумма по всем активным запускам'
      return { name, value, unit: null, source: complete ? source : 'Не все запуски предоставили этот показатель', observed_at: observedAt, state: complete ? 'ok' as const : 'unavailable' as const }
    }),
  }
  const logs = useQuery({ queryKey: ['logs', selected?.id], queryFn: async () => {
    const data = await api.getLogs(selected!.id)
    // Keep previously observed external output if its source temporarily disappears.
    if (selected?.source === 'external' && data.available === false) {
      const previous = queryClient.getQueryData<Awaited<ReturnType<typeof api.getLogs>>>(['logs', selected.id])
      if (previous?.available !== false && previous) return previous
    }
    return data
  }, enabled: Boolean(selected), retry: selected?.source === 'external' ? false : 1, refetchInterval: 2_000, refetchIntervalInBackground: false })
  const tokenStats = useQuery({ queryKey: queryKeys.tokens, queryFn: () => api.getTokenStats(), refetchInterval: 2_000, refetchIntervalInBackground: false })
  const [stopOperationId, setStopOperationId] = useState<string | null>(null)
  const stopOperation = useQuery({ queryKey: queryKeys.operation(stopOperationId ?? ''), queryFn: () => api.getOperation(stopOperationId!), enabled: Boolean(stopOperationId), refetchInterval: (query) => ['queued', 'running'].includes(query.state.data?.state ?? '') ? 1_500 : false })
  const stop = useMutation({ mutationFn: (id: string) => api.stopInstance(id), onSuccess: (operation) => { setStopOperationId(operation.id); void queryClient.invalidateQueries({ queryKey: queryKeys.system }) } })

  useEffect(() => {
    if (system.isLoading || selectedId === 'all' || selected) return
    setSearchParams((current) => { current.delete('instance'); return current }, { replace: true })
  }, [selected, selectedId, setSearchParams, system.isLoading])

  function selectInstance(id: string) {
    setSearchParams((current) => { if (id === 'all') current.delete('instance'); else current.set('instance', id); return current }, { replace: true })
  }

  return <div><PageHeader title="Dashboard" action={<Button variant="secondary" size="sm" onClick={() => { void system.refetch() }}><RefreshCw className="size-3.5" />Обновить</Button>} />
    {stopOperation?.data && <div className="mb-4 rounded-md border border-border bg-secondary px-4 py-3 text-xs"><div className="flex items-center gap-2"><StatusBadge state={stopOperation.data.state} /><span>Остановка экземпляра <code>{stopOperation.data.target_id}</code></span></div>{stopOperation.data.error && <p className="mt-1 text-[#d0a390]">{stopOperation.data.error}</p>}</div>}{stopOperation.isError && <div className="mb-4"><QueryError error={stopOperation.error} retry={() => void stopOperation.refetch()} label="Состояние остановки недоступно" /></div>}{stop.isError && <div className="mb-4"><QueryError error={stop.error} label="Остановка экземпляра не запущена" /></div>}
    {system.isLoading ? <QueryLoading label="Получаем состояние системы" /> : system.isError ? <QueryError error={system.error} retry={() => void system.refetch()} label="Состояние системы недоступно" /> : system.data && <div className="space-y-6">
      <Hardware data={system.data} />
      <section>
        <div className="mb-3 flex items-end justify-between gap-3"><div className="flex items-center gap-2"><h2 className="text-base font-medium">Активные запуски</h2><span className="group relative inline-flex shrink-0"><button type="button" aria-label="Информация об активных запусках" aria-describedby="active-runs-info" className="rounded-sm text-muted-foreground hover:text-foreground focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-copper"><Info className="size-3.5" /></button><span id="active-runs-info" role="tooltip" className="pointer-events-none absolute left-0 top-full z-20 mt-2 hidden w-64 max-w-[calc(100vw-4rem)] rounded-md border border-border bg-popover p-2 text-xs font-normal text-popover-foreground shadow-md group-hover:block group-focus-within:block">Профили и внешние процессы, которые backend видит сейчас.</span></span></div>{selected && <span className="font-mono text-[11px] text-muted-foreground">обновлено {formatTimestamp(selected.observed_at)}</span>}</div>
        <div>
          <RunTabs panelId="run-metrics-panel" value={selected?.id ?? 'all'} onChange={selectInstance} items={[{ value: 'all', title: 'Все', subtitle: `${activeInstances.length} активных запусков` }, ...activeInstances.map((instance) => ({ value: instance.id, title: instance.profile_name ?? instance.model_name ?? 'Внешний запуск', subtitle: instance.model_name ?? 'модель неизвестна', uptime: runUptime(instance), meta: `${instance.engine_id} · ${instance.port === null ? '—' : `:${instance.port}`}` }))]} />
          <Card className="rounded-t-none border-t-0" role="tabpanel" id="run-metrics-panel" aria-labelledby={`run-metrics-panel-tab-${encodeURIComponent(selected?.id ?? 'all')}`}>
            {selected ? <>
              <CardHeader><div className="flex flex-wrap items-center justify-between gap-3"><span className="text-xs text-muted-foreground">{selected.source === 'external' ? 'Внешний процесс' : 'Профиль LLM Studio'} · PID {selected.pid}</span><div className="flex items-center gap-2"><StatusBadge state={selected.state} /><Button size="sm" variant="danger" disabled={!selectedCanStop || stop.isPending || selected.state === 'stopping'} title={selectedCanStop ? undefined : 'Backend не подтвердил право остановки этого процесса'} onClick={() => void stop.mutate(selected.id)}>{selected.source === 'external' ? 'Остановить внешний' : 'Остановить'}</Button></div></div></CardHeader>
              <CardContent><ModelPerformance instance={selected} data={selectedMetrics?.data} /><LogsPanel key={selected.id} instance={selected} data={logs.data} loading={logs.isLoading} error={logs.error} retry={() => void logs.refetch()} /></CardContent>
            </> : <CardContent className="pt-4"><ModelPerformance data={aggregateMetrics} />{activeInstances.length > 0 && <div className="mt-4 border-t border-border pt-3"><Table><TableHeader><TableRow><TableHead>Запуск</TableHead><TableHead>Статус</TableHead><TableHead className="text-right">TTFT, мс</TableHead><TableHead className="text-right">KV cache, %</TableHead></TableRow></TableHeader><TableBody>{activeInstances.map((instance, index) => {
              const value = (name: string) => {
                const query = metricQueries[index]
                const metric = query?.data?.items.find((item) => item.name === name)
                return !query?.isError && metric?.state === 'ok' && typeof metric.value === 'number' ? metric.value.toLocaleString('ru-RU', { maximumFractionDigits: 1 }) : '—'
              }
              return <TableRow key={instance.id}><TableCell><button type="button" className="max-w-64 truncate text-left hover:text-copper" title={instance.profile_name ?? instance.model_name ?? instance.id} onClick={() => selectInstance(instance.id)}>{instance.profile_name ?? instance.model_name ?? 'Внешний запуск'}</button></TableCell><TableCell><StatusBadge state={instance.state} /></TableCell><TableCell className="text-right font-mono">{value('ttft_mean_ms')}</TableCell><TableCell className="text-right font-mono">{value('kv_cache_percent')}</TableCell></TableRow>
            })}</TableBody></Table></div>}</CardContent>}
          </Card>
        </div>

      </section>
      <TokenStatsPanel items={tokenStats.data?.items ?? []} loading={tokenStats.isLoading} error={tokenStats.error} retry={() => void tokenStats.refetch()} />
    </div>}
  </div>
}
