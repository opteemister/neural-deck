import { useEffect, useState } from 'react'
import type { Instance, Metric, MetricsData } from '@/types/api'
import { useQuery } from '@tanstack/react-query'
import { api } from '@/lib/api'
import { Progress } from '@/components/ui/progress'
import { useDailyMetricAggregates } from '@/hooks/use-metric-averages'
import { TokenHistoryChart } from '@/components/token-history-chart'
import { ConcurrencyChart } from '@/components/concurrency-chart'

import { MetricLineChart as Chart, type Point } from '@/components/metric-line-chart'

const maxHistoryPoints = 120
type ScalarStat = {
  name: string
  field: string
  metricField?: string
  number: number | null
  unit: string
  detail: string
  hint: string
}

function pointFrom(items: Metric[], at: number): Point {
  const metrics = new Map(items.map((item) => [item.name, item]))
  const decode = numeric(metrics.get('decode_tps')), requests = numeric(metrics.get('active_requests'))
  return { at, decode, requests, prefill: numeric(metrics.get('prefill_tps')), ttft: numeric(metrics.get('ttft_mean_ms')), perRequest: decode !== null && requests !== null && requests > 0 ? decode / requests : decode !== null && requests === 0 ? 0 : null }
}
const compact = new Intl.NumberFormat('en-US', { notation: 'compact', maximumFractionDigits: 1 })
const plain = new Intl.NumberFormat('en-US', { useGrouping: false, maximumFractionDigits: 2 })
function duration(ms: number | null): { text: string; unit: string } {
  if (ms === null) return { text: '—', unit: '' }
  const divisor = ms >= 3_600_000 ? 3_600_000 : ms >= 60_000 ? 60_000 : ms >= 1000 ? 1000 : 1
  return { text: plain.format(ms / divisor), unit: divisor === 3_600_000 ? 'h' : divisor === 60_000 ? 'min' : divisor === 1000 ? 's' : 'ms' }
}
function numeric(item: Metric | undefined): number | null {
  return item?.state === 'ok' && typeof item.value === 'number' && Number.isFinite(item.value) ? item.value : null
}

export function ModelPerformance({ instance, data }: { instance?: Instance; data?: MetricsData }) {
  const profileId = instance?.profile_id ?? undefined
  const historyKey = profileId ?? instance?.profile_name ?? instance?.id ?? 'all'
  const bootstrap = useQuery({ queryKey: ['metrics-bootstrap', historyKey],
    queryFn: () => profileId ? api.getMetricsHistory({ profile_id: profileId }) : instance?.profile_name ? api.getMetricsHistory({ profile_name: instance.profile_name }) : instance ? api.getMetricsHistory({ pid: instance.pid }) : api.getMetricsHistory({ scope: 'all' }),
    staleTime: Infinity, refetchOnWindowFocus: false, retry: false })
  const [histories, setHistories] = useState<Record<string, Point[]>>({})
  const averages = useDailyMetricAggregates(historyKey, profileId ? { profile_id: profileId } : instance?.profile_name ? { profile_name: instance.profile_name } : instance ? { pid: instance.pid } : { scope: 'all' })
  const [knownMetrics, setKnownMetrics] = useState<Record<string, Map<string, Metric>>>({})
  const history = histories[historyKey] ?? []
  const currentItems = new Map(data?.items.map((item) => [item.name, item]))
  const items = new Map(knownMetrics[historyKey])
  for (const item of currentItems.values()) {
    if (numeric(item) !== null) items.set(item.name, item)
  }
  const value = (name: string) => numeric(items.get(name))
  const measurementNote = (name: string) => {
    const item = items.get(name)
    if (!item || (name !== 'ttft_mean_ms' && numeric(currentItems.get(name)) !== null)) return ''
    return `${currentItems.get(name)?.state === 'stale' ? 'Данные устарели · ' : ''}Последнее измерение: ${new Date(item.observed_at).toLocaleTimeString([], { timeZone: 'UTC' })} UTC`
  }
  useEffect(() => {
    // Retention affects only scalar display. Charts keep the original samples.
    const samples = [...(bootstrap.data?.history ?? []), ...(bootstrap.data?.current ? [bootstrap.data.current] : []), ...(data ? [data] : [])]
    setKnownMetrics((current) => {
      const next = new Map(current[historyKey])
      let changed = false
      for (const sample of samples) for (const item of sample.items) {
        if (numeric(item) === null) continue
        const previous = next.get(item.name)
        if (!previous || Date.parse(item.observed_at) >= Date.parse(previous.observed_at)) {
          if (previous !== item) { next.set(item.name, item); changed = true }
        }
      }
      return changed ? { ...current, [historyKey]: next } : current
    })
  }, [data, bootstrap.data, historyKey])
  useEffect(() => {
    if (!data || !bootstrap.isFetched) return
    const metrics = new Map(data.items.map((item) => [item.name, item]))
    const timestamps = ['decode_tps', 'prefill_tps', 'ttft_mean_ms', 'active_requests'].map((name) => metrics.get(name)).filter((item): item is Metric => Boolean(item && item.state === 'ok'))
    const at = timestamps.length ? Math.max(...timestamps.map((item) => Date.parse(item.observed_at))) : Date.parse(metrics.get('uptime_s')?.observed_at ?? '')
    if (!Number.isFinite(at)) return
    const point = pointFrom(data.items, at)
    setHistories((current) => {
      const previous = current[historyKey] ?? []
      if (previous.at(-1)?.at === at) return current
      return { ...current, [historyKey]: [...previous.filter((sample) => sample.at !== at), point].sort((a, b) => a.at - b.at).slice(-maxHistoryPoints) }
    })
  }, [data, historyKey, bootstrap.isFetched])
  useEffect(() => {
    if (!bootstrap.data) return
    const seeded = [...bootstrap.data.history, ...(bootstrap.data.current ? [bootstrap.data.current] : [])].map((sample) => {
      return pointFrom(sample.items, Date.parse(sample.observed_at))
    }).filter((point) => Number.isFinite(point.at))
    setHistories((current) => {
      const merged = new Map([...seeded, ...(current[historyKey] ?? [])].map((point) => [point.at, point]))
      return { ...current, [historyKey]: [...merged.values()].sort((a, b) => a.at - b.at).slice(-maxHistoryPoints) }
    })
  }, [bootstrap.data, historyKey])
  const extrema = (key: 'decode' | 'prefill' | 'ttft' | 'requests') => {
    const field = { decode: 'decode_tps', prefill: 'prefill_tps', ttft: 'ttft_max_ms', requests: 'active_requests' }[key]
    const aggregate = averages.get(key === 'ttft' ? 'ttft_mean_ms' : field)
    const maximum = aggregate?.max ?? null
    const average = aggregate ? aggregate.sum / aggregate.weight : null
    const formatSummary = (number: number | null) => key === 'ttft' ? `${duration(number).text}${number === null ? '' : ` ${duration(number).unit}`}` : number === null ? '—' : key === 'prefill' ? plain.format(number) : compact.format(number)
    return `max ${formatSummary(maximum)} · avg ${formatSummary(average)}`
  }
  const mean = (field: string) => {
    const average = averages.get(field)
    return average ? average.sum / average.weight : null
  }
  const format = (number: number | null) => number === null ? '—' : compact.format(number)
  const seriesSummary = (field: keyof Pick<Point, 'decode' | 'prefill' | 'ttft' | 'requests' | 'perRequest'>) => {
    const values = history.map((point) => point[field]).filter((number): number is number => number !== null && Number.isFinite(number))
    if (!values.length) return 'max — · avg —'
    return `max ${plain.format(Math.max(...values))} · avg ${plain.format(values.reduce((sum, number) => sum + number, 0) / values.length)}`
  }
  const decode = value('decode_tps'), requests = value('active_requests')
  const decodePerRequest = decode !== null && requests !== null ? requests > 0 ? decode / requests : 0 : null
  const cacheMean = mean('prefix_cache_hit_percent')
  const queue = value('queue_requests')
  // A completed-request histogram cannot tell us the age of currently waiting requests.
  const queueAge = queue === 0 ? 0 : numeric(currentItems.get('queue_wait_max_ms'))
  const stats: ScalarStat[] = [
    { name: 'Prefill', field: 'prefill_tps', number: value('prefill_tps'), unit: 'tok/s', detail: extrema('prefill'), hint: 'Скорость обработки входных токенов.' },
    { name: 'Decode / req', field: 'decode_per_request', metricField: 'decode_tps', number: decodePerRequest, unit: 'tok/s', detail: `${format(decode)} total · ${format(requests)} reqs`, hint: 'Скорость генерации выходных токенов в пересчёте на один активный запрос.' },
    { name: 'TTFT', field: 'ttft_mean_ms', number: mean('ttft_mean_ms'), unit: 'ms', detail: extrema('ttft'), hint: 'Avg времени до первого токена за последние 24 часа. Взвешено по числу запросов, если оно доступно.' },
    { name: 'Requests', field: 'active_requests', number: value('active_requests'), unit: 'live', detail: extrema('requests'), hint: 'Количество запросов, обрабатываемых сейчас.' },
    { name: 'KV Hit', field: 'prefix_cache_hit_percent', number: value('prefix_cache_hit_percent'), unit: '%', detail: `avg ${format(cacheMean)}${cacheMean === null ? '' : '%'}`, hint: 'Попадание входных токенов в prefix cache. В подписи — avg за последние 24 часа.' },
    { name: 'KV cache', field: 'kv_cache_percent', number: value('kv_cache_percent'), unit: '%', detail: '', hint: 'Занятая доля KV cache. Для всех запусков показывается максимальная занятость.' },
    { name: 'Decode', field: 'decode_tps', number: decode, unit: 'tok/s', detail: extrema('decode'), hint: 'Общая скорость генерации выходных токенов.' },
    { name: 'Served', field: 'completed_requests', number: value('completed_requests'), unit: 'reqs', detail: 'За этот запуск', hint: 'Количество завершённых запросов с начала запуска.' },
    { name: 'Queue', field: 'queue_requests', number: queue, unit: 'waiting', detail: `ожидание ${format(queueAge)}${queueAge === null ? '' : ' ms'}`, hint: 'Количество запросов в очереди. В подписи — максимальное текущее ожидание. Если движок не предоставляет его, показывается прочерк.' },
    { name: 'Draft Acceptance', field: 'spec_acceptance_percent', number: value('spec_acceptance_percent'), unit: '%', detail: `avg ${format(mean('spec_acceptance_percent'))}${mean('spec_acceptance_percent') === null ? '' : '%'}`, hint: 'Доля принятых draft-токенов. Avg за последние 24 часа, с весами по предложенным токенам, если доступны.' },
  ]
  const renderStat = (stat: ScalarStat) => <div key={stat.name} className="min-w-0" data-metric={stat.field} title={[stat.hint, 'Max и avg за последние 24 часа.', measurementNote(stat.metricField ?? stat.field)].filter(Boolean).join(' ')}>
    <p className="text-xs text-muted-foreground">{stat.name}</p>
    <p className="mt-1 whitespace-nowrap text-xl font-medium tabular-nums">{stat.field === 'ttft_mean_ms' ? duration(stat.number).text : stat.field === 'prefill_tps' || stat.field === 'decode_per_request' ? stat.number === null ? '—' : plain.format(stat.number) : format(stat.number)}{stat.number !== null && <span className="ml-1 text-[11px] font-normal text-muted-foreground">{stat.field === 'ttft_mean_ms' ? duration(stat.number).unit : stat.unit}</span>}</p>
    {stat.field === 'kv_cache_percent' && stat.number !== null && <div className="mt-1 flex h-4 items-center"><Progress value={stat.number} /></div>}
    {stat.detail && <p className="mt-1 min-h-4 text-[10px] text-muted-foreground">{stat.detail}</p>}
  </div>
  return <div className="model-performance-layout">
    <div className="model-performance-top">
      <div className="model-performance-stat-grid">{stats.map(renderStat)}</div>
      {instance && <div className="model-performance-parallel"><ConcurrencyChart instance={instance} /></div>}
    </div>
    <div className="model-performance-charts border-t border-border pt-4">
      <Chart title="Prefill" unit="tok/s" subtitle={extrema('prefill')} points={history} series={[{ key: 'prefill', label: 'Prefill', color: '#cf9a78' }]} />
      <Chart title="Generation" unit="tok/s" subtitle={`total ${extrema('decode')} · per req ${seriesSummary('perRequest')}`} points={history} series={[{ key: 'decode', label: 'Generation', color: '#cf9a78' }, { key: 'perRequest', label: 'Per request', color: '#e9e5dc', independent: true }]} />
      <Chart title="Requests" unit="live" subtitle={extrema('requests')} points={history} series={[{ key: 'requests', label: 'Active', color: '#cf9a78' }]} />
      <TokenHistoryChart instance={instance} />
    </div>
  </div>
}
