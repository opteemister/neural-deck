import { useEffect, useMemo, useRef, useState } from 'react'
import { useMutation, useQueries, useQuery, useQueryClient } from '@tanstack/react-query'
import { ArrowDown, ArrowUp, ArrowUpDown, ChevronRight, Cpu, Database, Info, MemoryStick, RefreshCw, Zap } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { RunTabs } from '@/components/ui/run-tabs'
import { ModelPerformance } from '@/components/model-performance'
import { useLogsDrawer } from '@/components/logs-drawer'
import { RunSummaryRow } from '@/components/run-summary-row'
import { GpuSparkline } from '@/components/gpu-sparkline'
import { Progress } from '@/components/ui/progress'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { PageHeader } from '@/components/page-header'
import { QueryEmpty, QueryError, QueryLoading } from '@/components/query-state'
import { StatusBadge } from '@/components/status-badge'
import { api, queryKeys } from '@/lib/api'
import { formatBytes, formatPercent } from '@/lib/utils'
import type { Instance, MetricsData } from '@/types/api'
import { useSearchParams } from 'react-router-dom'

const gpuColumns = [
  { key: 'index', label: 'GPU' }, { key: 'name', label: 'Видеокарта' },
  { key: 'memory_used_bytes', label: 'Память' }, { key: 'usage_percent', label: 'Загрузка' },
  { key: 'temperature_celsius', label: 'Темп.' }, { key: 'fan_speed_percent', label: 'Кулер' }, { key: 'power_watts', label: 'Мощность' },
] as const
type GpuSort = { key: typeof gpuColumns[number]['key']; direction: 'asc' | 'desc' }
type GpuSample = {
  at: number
  usage_percent: number | null
  temperature_celsius: number | null
  power_watts: number | null
}
const gpuHistoryWindowMs = 10 * 60 * 1000
const gpuKey = (gpu: { uuid?: string; index: number }) => gpu.uuid || `index:${gpu.index}`
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

function Hardware({ data, sampleAt }: { data: Awaited<ReturnType<typeof api.getSystem>>; sampleAt: number }) {
  const [sort, setSort] = useState<GpuSort>(readGpuSort)
  const [gpuHistory, setGpuHistory] = useState<Record<string, GpuSample[]>>({})
  useEffect(() => {
    if (!sampleAt) return
    setGpuHistory((current) => {
      const cutoff = sampleAt - gpuHistoryWindowMs
      const next = { ...current }
      for (const gpu of data.gpus) {
        const key = gpuKey(gpu)
        const previous = (current[key] ?? []).filter((sample) => sample.at >= cutoff)
        if (previous.at(-1)?.at === sampleAt) continue
        next[key] = [...previous, {
          at: sampleAt,
          usage_percent: gpu.usage_percent,
          temperature_celsius: gpu.temperature_celsius,
          power_watts: gpu.power_watts ?? null,
        }]
      }
      return next
    })
  }, [data.gpus, sampleAt])
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
  const historyValues = (gpu: typeof data.gpus[number], field: keyof Omit<GpuSample, 'at'>) => (gpuHistory[gpuKey(gpu)] ?? []).map((sample) => sample[field])
  const resourceClass = 'flex min-w-0 max-w-full items-center gap-2 pr-2 whitespace-nowrap text-xs tabular-nums'
  return <Card><CardHeader><CardTitle>Локальные ресурсы</CardTitle></CardHeader><CardContent className="system-resources-layout space-y-4"><div className="system-resources-grid gap-x-2 gap-y-3 border-b border-border pb-3 text-xs">
    <div className="contents"><div className={resourceClass}><Database className="size-3.5 shrink-0 text-copper" /><span className="shrink-0 text-muted-foreground">RAM</span><span className="shrink-0"><strong>{formatBytes(data.ram.used_bytes)}</strong><span className="text-muted-foreground"> / {formatBytes(data.ram.total_bytes)}</span></span>{ramPercent === null ? <span className="text-[11px] text-muted-foreground">недоступно</span> : <span className="block min-w-0 flex-1"><Progress value={ramPercent} /></span>}</div>
    <div className={resourceClass}><MemoryStick className="size-3.5 shrink-0 text-copper" /><span className="shrink-0 text-muted-foreground">VRAM</span>{vramPercent === null ? <span className="text-muted-foreground">недоступно</span> : <><span className="shrink-0"><strong>{formatBytes(vram.used)}</strong><span className="text-muted-foreground"> / {formatBytes(vram.total)}</span></span><span className="block min-w-0 flex-1"><Progress value={vramPercent} /></span></>}</div>
    </div><div className="contents"><div className={resourceClass}><Zap className="size-3.5 shrink-0 text-copper" /><span className="shrink-0 text-muted-foreground">Power</span><span className="shrink-0"><strong>{watts(power)}</strong><span className="text-muted-foreground"> / {watts(powerLimit)} Вт</span></span>{power !== null && powerLimit !== null && powerLimit > 0 && <span className="block min-w-0 flex-1"><Progress value={power / powerLimit * 100} /></span>}</div>
    <div className={resourceClass}><Cpu className="size-3.5 shrink-0 text-copper" /><span className="shrink-0 text-muted-foreground">CPU</span><span className="shrink-0"><strong>{data.cpu.cores} ядер</strong><span className="text-muted-foreground"> / загрузка {formatPercent(data.cpu.usage_percent)}</span></span></div>
  </div></div>{data.gpus.length === 0 ? <QueryEmpty title="GPU не обнаружены" description="Backend не сообщил доступные NVIDIA GPU." /> : <div className="gpu-table-container"><Table className="gpu-table table-fixed"><colgroup><col style={{ width: '53px' }} /><col /><col style={{ width: '22%' }} /><col style={{ width: '13%' }} /><col style={{ width: '13%' }} /><col style={{ width: '8%' }} /><col style={{ width: '18%' }} /></colgroup><TableHeader><TableRow>{gpuColumns.map((column) => <TableHead key={column.key} aria-sort={sort.key === column.key ? sort.direction === 'asc' ? 'ascending' : 'descending' : 'none'}><button type="button" className={`inline-flex items-center gap-1.5 whitespace-nowrap rounded-sm hover:text-foreground focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-copper ${sort.key === column.key ? 'text-foreground' : ''}`} title={column.key === 'memory_used_bytes' ? 'Сортировать по занятой памяти' : column.key === 'power_watts' ? 'Сортировать по текущему потреблению' : `Сортировать: ${column.label}`} onClick={() => setSort((current) => ({ key: column.key, direction: current.key === column.key && current.direction === 'asc' ? 'desc' : 'asc' }))}>{column.label}{sort.key !== column.key ? <ArrowUpDown className="size-3 opacity-50" /> : sort.direction === 'asc' ? <ArrowUp className="size-3 text-copper" /> : <ArrowDown className="size-3 text-copper" />}</button></TableHead>)}</TableRow></TableHeader><TableBody>{sortedGpus.map((gpu) => <TableRow key={gpu.index}><TableCell className="font-mono text-muted-foreground">{gpu.index}</TableCell><TableCell className="font-medium"><span className="block truncate" title={gpu.name}>{gpu.name}</span></TableCell><TableCell><div className="flex items-center justify-start gap-3"><span className="shrink-0 whitespace-nowrap font-mono"><span className="gpu-table-current">{formatBytes(gpu.memory_used_bytes)}</span><span className="gpu-table-limit text-muted-foreground">/ {formatBytes(gpu.memory_total_bytes)}</span></span><span className="gpu-table-visual"><Progress value={gpu.memory_total_bytes > 0 ? gpu.memory_used_bytes / gpu.memory_total_bytes * 100 : null} /></span></div></TableCell><TableCell><div className="flex items-center justify-start gap-2"><span className="shrink-0 text-left font-mono text-foreground">{gpu.usage_percent === null ? '—' : formatPercent(gpu.usage_percent)}</span><span className="gpu-table-visual"><GpuSparkline label="Загрузка GPU" values={historyValues(gpu, 'usage_percent')} formatValue={formatPercent} /></span></div></TableCell><TableCell className="text-left font-mono"><div className="flex items-center justify-start gap-2"><span className="whitespace-nowrap">{gpu.temperature_celsius === null ? '—' : `${gpu.temperature_celsius} °C`}</span><span className="gpu-table-visual"><GpuSparkline label="Температура GPU" values={historyValues(gpu, 'temperature_celsius')} formatValue={(value) => `${value} °C`} /></span></div></TableCell><TableCell className="whitespace-nowrap text-right font-mono" title="Скорость вентилятора, %"><span>{gpu.fan_speed_percent == null ? '—' : formatPercent(gpu.fan_speed_percent)}</span></TableCell><TableCell className="whitespace-nowrap text-left font-mono"><div className="flex items-center justify-start gap-2"><span><span className="gpu-table-current">{watts(gpu.power_watts)}</span><span className="gpu-table-limit text-muted-foreground">/ {watts(gpu.power_limit_watts)} Вт</span></span><span className="gpu-table-visual"><GpuSparkline label="Мощность GPU" values={historyValues(gpu, 'power_watts')} formatValue={(value) => `${watts(value)} Вт`} /></span></div></TableCell></TableRow>)}</TableBody></Table></div>}</CardContent></Card>
}

export function LogsPanel({ instance, data, loading, error, retry }: { instance: Instance; data: Awaited<ReturnType<typeof api.getLogs>> | undefined; loading: boolean; error: unknown; retry: () => void }) {
  const openLogs = useLogsDrawer()
  const [expanded, setExpanded] = useState(false)
  const [autoScroll, setAutoScroll] = useState(true)
  const scrollRef = useRef<HTMLPreElement>(null)
  useEffect(() => {
    if (expanded && autoScroll && scrollRef.current) scrollRef.current.scrollTop = scrollRef.current.scrollHeight
  }, [expanded, autoScroll, data?.lines])
  if (instance.source === 'external' && (!data || data.available === false)) return null
  const showError = Boolean(error) && !(instance.source === 'external' && data)
  return <details className="group mt-4 border-t border-border pt-3" onToggle={(event) => setExpanded(event.currentTarget.open)}><summary className="flex cursor-pointer list-none items-center gap-2 text-xs font-medium [&::-webkit-details-marker]:hidden"><ChevronRight className="size-3.5 transition-transform group-open:rotate-90" /><span>Логи запуска</span>{instance.profile_name && <button type="button" className="ml-auto text-[11px] font-normal text-muted-foreground underline underline-offset-2 hover:text-foreground" onClick={(event) => { event.preventDefault(); event.stopPropagation(); openLogs(instance.profile_name!) }}>Полные логи</button>}</summary><div className="pt-3"><label className="mb-2 flex items-center gap-2 text-xs text-muted-foreground"><input type="checkbox" checked={autoScroll} onChange={(event) => setAutoScroll(event.target.checked)} />Автоскролл</label>{loading ? <QueryLoading label="Получаем лог запуска" /> : showError ? <QueryError error={error} retry={retry} label="Лог запуска недоступен" /> : !data || data.lines.length === 0 ? <QueryEmpty title="Лог пока пуст" description="Backend не сообщил строки для выбранного экземпляра." /> : <div><pre ref={scrollRef} aria-label="Лог запуска модели" className="max-h-72 overflow-auto whitespace-pre-wrap rounded-md bg-background p-3 font-mono text-[11px] leading-5">{data.lines.join('\n')}</pre>{data.truncated && <p className="mt-2 text-[11px] text-muted-foreground">Показан только доступный хвост лога.</p>}</div>}</div></details>
}

function canStopInstance(instance: Instance | undefined): boolean {
  return instance?.can_stop === true && instance.identity_verified === true
}

export function DashboardPage() {
  const openLogs = useLogsDrawer()
  const queryClient = useQueryClient()
  const [searchParams, setSearchParams] = useSearchParams()
  const system = useQuery({ queryKey: queryKeys.system, queryFn: api.getSystem, refetchInterval: 2_000, refetchIntervalInBackground: false })
  const activeInstances = useMemo(() => (system.data?.instances ?? []).filter((instance) => ['starting', 'ready', 'running', 'stopping'].includes(instance.state) || (instance.source === 'external' && instance.state === 'unknown')), [system.data?.instances])
  const selectedId = searchParams.get('instance') ?? 'all'
  const selected = activeInstances.find((instance) => instance.id === selectedId)
  const selectedCanStop = canStopInstance(selected)
  const metricQueries = useQueries({ queries: activeInstances.map((instance) => ({ queryKey: ['metrics', instance.id], queryFn: () => api.getMetrics(instance.id), retry: instance.source === 'external' ? false as const : 1, refetchInterval: 2_000, refetchIntervalInBackground: false })) })
  const selectedMetrics = metricQueries[activeInstances.findIndex((instance) => instance.id === selectedId)]
  const allMetrics = useQuery({ queryKey: ['metrics-current', 'all'], queryFn: () => api.getMetricsHistory({ scope: 'all', mode: 'current' }), refetchInterval: 2_000, refetchIntervalInBackground: false })
  const fallbackAggregate: MetricsData = {
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
  const aggregateMetrics: MetricsData = allMetrics.data?.current ?? (activeInstances.length === 0 ? fallbackAggregate : { items: [] })
  const logs = useQuery({ queryKey: ['logs', selected?.id], queryFn: async () => {
    const data = await api.getLogs(selected!.id)
    // Keep previously observed external output if its source temporarily disappears.
    if (selected?.source === 'external' && data.available === false) {
      const previous = queryClient.getQueryData<Awaited<ReturnType<typeof api.getLogs>>>(['logs', selected.id])
      if (previous?.available !== false && previous) return previous
    }
    return data
  }, enabled: Boolean(selected), retry: selected?.source === 'external' ? false : 1, refetchInterval: 2_000, refetchIntervalInBackground: false })
  const [stopOperationId, setStopOperationId] = useState<string | null>(null)
  const [stopOperationProfile, setStopOperationProfile] = useState<string | null>(null)
  const stopOperation = useQuery({ queryKey: queryKeys.operation(stopOperationId ?? ''), queryFn: () => api.getOperation(stopOperationId!), enabled: Boolean(stopOperationId), refetchInterval: (query) => ['queued', 'running'].includes(query.state.data?.state ?? '') ? 1_500 : false })
  const stop = useMutation({ mutationFn: (id: string) => api.stopInstance(id), onSuccess: (operation, id) => { setStopOperationProfile(system.data?.instances.find((instance) => instance.id === id)?.profile_name ?? null); setStopOperationId(operation.id); void queryClient.invalidateQueries({ queryKey: queryKeys.system }) } })

  useEffect(() => {
    if (system.isLoading || selectedId === 'all' || selected) return
    setSearchParams((current) => { current.delete('instance'); return current }, { replace: true })
  }, [selected, selectedId, setSearchParams, system.isLoading])

  function selectInstance(id: string) {
    setSearchParams((current) => { if (id === 'all') current.delete('instance'); else current.set('instance', id); return current }, { replace: true })
  }

  return <div><PageHeader title="Dashboard" action={<Button variant="secondary" size="sm" onClick={() => { void system.refetch() }}><RefreshCw className="size-3.5" />Обновить</Button>} />
    {stopOperation?.data && <div className="mb-4 rounded-md border border-border bg-secondary px-4 py-3 text-xs"><div className="flex items-center gap-2"><StatusBadge state={stopOperation.data.state} onClick={stopOperationProfile ? () => openLogs(stopOperationProfile) : undefined} title={stopOperationProfile ? `Логи профиля «${stopOperationProfile}»` : undefined} /><span>Остановка экземпляра <code>{stopOperation.data.target_id}</code></span></div>{stopOperation.data.error && <p className="mt-1 text-[#d0a390]">{stopOperation.data.error}</p>}</div>}{stopOperation.isError && <div className="mb-4"><QueryError error={stopOperation.error} retry={() => void stopOperation.refetch()} label="Состояние остановки недоступно" /></div>}{stop.isError && <div className="mb-4"><QueryError error={stop.error} label="Остановка экземпляра не запущена" /></div>}
    {system.isLoading ? <QueryLoading label="Получаем состояние системы" /> : system.isError ? <QueryError error={system.error} retry={() => void system.refetch()} label="Состояние системы недоступно" /> : system.data && <div className="space-y-6">
      <Hardware data={system.data} sampleAt={system.dataUpdatedAt} />
      <section>
        <div className="mb-3 flex items-end justify-between gap-3"><div className="flex items-center gap-2"><h2 className="text-base font-medium">Активные запуски</h2><span className="group relative inline-flex shrink-0"><button type="button" aria-label="Информация об активных запусках" aria-describedby="active-runs-info" className="rounded-sm text-muted-foreground hover:text-foreground focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-copper"><Info className="size-3.5" /></button><span id="active-runs-info" role="tooltip" className="pointer-events-none absolute left-0 top-full z-20 mt-2 hidden w-64 max-w-[calc(100vw-4rem)] rounded-md border border-border bg-popover p-2 text-xs font-normal text-popover-foreground shadow-md group-hover:block group-focus-within:block">Профили и внешние процессы, которые backend видит сейчас.</span></span></div></div>
        <div>
          <RunTabs panelId="run-metrics-panel" value={selected?.id ?? 'all'} onChange={selectInstance} items={[{ value: 'all', title: 'Все', subtitle: `${activeInstances.length} активных запусков` }, ...activeInstances.map((instance) => ({ value: instance.id, title: instance.profile_name ?? instance.model_name ?? 'Внешний запуск', subtitle: instance.model_name ?? 'модель неизвестна', uptime: runUptime(instance), meta: `${instance.engine_id} · ${instance.port === null ? '—' : `:${instance.port}`}` }))]} />
          <Card className="rounded-t-none border-t-0" role="tabpanel" id="run-metrics-panel" aria-labelledby={`run-metrics-panel-tab-${encodeURIComponent(selected?.id ?? 'all')}`}>
            <CardHeader><div className="flex h-8 items-center justify-between gap-3">{selected ? <><span className="min-w-0 truncate text-xs text-muted-foreground">{selected.source === 'external' ? 'Внешний процесс' : 'Профиль LLM Studio'} · PID {selected.pid}</span><div className="flex shrink-0 items-center gap-2"><StatusBadge state={selected.state} onClick={selected.profile_name ? () => openLogs(selected.profile_name!) : undefined} title={selected.profile_name ? `Логи профиля «${selected.profile_name}»` : undefined} /><Button size="sm" variant="danger" disabled={!selectedCanStop || stop.isPending || selected.state === 'stopping'} title={selectedCanStop ? undefined : 'Backend не подтвердил право остановки этого процесса'} onClick={() => void stop.mutate(selected.id)}>{selected.source === 'external' ? 'Остановить внешний' : 'Остановить'}</Button></div></> : <span className="text-xs text-muted-foreground">Сводные показатели всех активных моделей</span>}</div></CardHeader>
            {selected ? <>

              <CardContent className="pt-4"><ModelPerformance instance={selected} data={selectedMetrics?.data} /><LogsPanel key={selected.id} instance={selected} data={logs.data} loading={logs.isLoading} error={logs.error} retry={() => void logs.refetch()} /></CardContent>
            </> : <CardContent className="pt-4"><ModelPerformance data={aggregateMetrics} />{activeInstances.length > 0 && <div className="mt-4 border-t border-border pt-3"><Table><TableHeader><TableRow><TableHead>Запуск</TableHead><TableHead>Статус</TableHead><TableHead className="text-right">Prefill avg, tok/s</TableHead><TableHead className="text-right">Decode avg, tok/s</TableHead><TableHead className="text-right">Served</TableHead><TableHead className="text-right">Output tokens</TableHead><TableHead className="text-right"><span className="sr-only">Остановить</span></TableHead></TableRow></TableHeader><TableBody>{activeInstances.map((instance, index) => <RunSummaryRow key={instance.id} instance={instance} data={metricQueries[index]?.data} onSelect={() => selectInstance(instance.id)} onLogs={instance.profile_name ? () => openLogs(instance.profile_name!) : undefined} onStop={() => stop.mutate(instance.id)} stopping={stop.isPending} />)}</TableBody></Table></div>}</CardContent>}
          </Card>
        </div>

      </section>
    </div>}
  </div>
}
