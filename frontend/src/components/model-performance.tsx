import { useEffect, useState } from 'react'
import type { Instance, Metric, MetricsData } from '@/types/api'
import { useQuery } from '@tanstack/react-query'
import { api } from '@/lib/api'
import { Table, TableBody, TableCell, TableRow } from '@/components/ui/table'
import { Progress } from '@/components/ui/progress'

type Point = { at: number; decode: number | null; prefill: number | null; ttft: number | null; requests: number | null }
const maxHistoryPoints = 120
const compact = new Intl.NumberFormat('en-US', { notation: 'compact', maximumFractionDigits: 1 })
function numeric(item: Metric | undefined): number | null {
  return item?.state === 'ok' && typeof item.value === 'number' && Number.isFinite(item.value) ? item.value : null
}
function Chart({ title, unit, points, series }: { title: string; unit: string; points: Point[]; series: Array<{ key: 'decode' | 'prefill' | 'ttft' | 'requests'; label: string; color: string }> }) {
  const [hoveredAt, setHoveredAt] = useState<number | null>(null)
  const hovered = points.find((point) => point.at === hoveredAt)
  const start = points[0]?.at ?? 0
  const end = points.at(-1)?.at ?? start
  const values = points.flatMap((point) => series.map((line) => point[line.key])).filter((value): value is number => value !== null)
  const max = Math.max(1, ...values)
  const hoveredX = hovered && end > start ? (hovered.at - start) / (end - start) * 400 : 0
  return <div className="min-w-0"><div className="mb-2 flex flex-wrap items-center justify-between gap-2 text-[11px] text-muted-foreground"><span>{title} <span className="opacity-60">{unit}</span></span><div className="flex gap-3">{series.map((line) => <span key={line.key} style={{ color: line.color }}>{line.label}</span>)}</div></div><div className="relative" onPointerLeave={() => setHoveredAt(null)}><svg viewBox="0 0 400 120" preserveAspectRatio="none" className="h-32 w-full" role="img" aria-label={title} onPointerMove={(event) => {
    if (!points.length) return
    const rect = event.currentTarget.getBoundingClientRect()
    if (!rect.width) return
    const fraction = Math.min(1, Math.max(0, (event.clientX - rect.left) / rect.width))
    const timestamp = start + fraction * (end - start)
    const nearest = points.reduce((best, point) => Math.abs(point.at - timestamp) < Math.abs(best.at - timestamp) ? point : best)
    setHoveredAt(nearest.at)
  }}>{[12, 44, 76, 108].map((y) => <line key={y} x1="0" y1={y} x2="400" y2={y} className="stroke-border" strokeWidth="1" />)}{series.map((line) => {
    const segments: string[] = []
    let segment = ''
    points.forEach((point) => {
      const value = point[line.key]
      if (value === null) return
      const x = end > start ? (point.at - start) / (end - start) * 400 : 0
      const y = 108 - value / max * 96
      segment += `${segment ? ' L' : 'M'}${x.toFixed(1)},${y.toFixed(1)}`
    })
    if (segment) segments.push(segment)
    return <g key={line.key}>{segments.map((path, index) => <path key={index} d={path} fill="none" stroke={line.color} strokeWidth="1.5" />)}{points.map((point) => point[line.key] === null ? null : <circle key={point.at} cx={end > start ? (point.at - start) / (end - start) * 400 : 0} cy={108 - point[line.key]! / max * 96} r="1.5" fill={line.color} />)}</g>
  })}{hovered && <g className="pointer-events-none"><line x1={hoveredX} x2={hoveredX} y1="0" y2="120" className="stroke-muted-foreground" strokeDasharray="3 3" strokeWidth="1" />{series.map((line) => hovered[line.key] === null ? null : <circle key={line.key} cx={hoveredX} cy={108 - hovered[line.key]! / max * 96} r="3" fill={line.color} />)}</g>}{values.length === 0 && <text x="200" y="65" textAnchor="middle" className="fill-muted-foreground text-[11px]">Нет данных</text>}</svg>{hovered && <div role="tooltip" className="pointer-events-none absolute top-1 z-10 w-40 -translate-x-1/2 rounded-md border border-border bg-popover px-2 py-1.5 text-[11px] text-popover-foreground shadow-md" style={{ left: `clamp(80px, ${hoveredX / 4}%, calc(100% - 80px))` }}><p className="mb-1 font-mono text-[10px] text-muted-foreground">{new Date(hovered.at).toLocaleTimeString()}</p>{series.map((line) => <div key={line.key} className="flex items-center justify-between gap-2"><span>{line.label}</span><span className="whitespace-nowrap font-mono tabular-nums">{hovered[line.key] === null ? '—' : hovered[line.key]!.toLocaleString('ru-RU', { maximumFractionDigits: 2 })}{hovered[line.key] !== null && <span className="ml-1 text-muted-foreground">{unit}</span>}</span></div>)}</div>}</div><div className="mt-1 flex justify-between font-mono text-[10px] text-muted-foreground"><span>{start ? new Date(start).toLocaleTimeString() : 'Ожидание данных'}</span><span>{end && end !== start ? new Date(end).toLocaleTimeString() : ''}</span></div></div>
}

export function ModelPerformance({ instance, data }: { instance?: Instance; data?: MetricsData }) {
  const historyKey = instance?.id ?? 'all'
  const bootstrap = useQuery({ queryKey: ['metrics-bootstrap', historyKey],
    queryFn: () => api.getMetricsHistory(instance ? { profile_name: instance.profile_name ?? undefined, pid: instance.pid } : { scope: 'all' }),
    staleTime: Infinity, refetchOnWindowFocus: false, retry: false })
  const [histories, setHistories] = useState<Record<string, Point[]>>({})
  const history = histories[historyKey] ?? []
  const items = new Map(data?.items.map((item) => [item.name, item]))
  const value = (name: string) => numeric(items.get(name))
  useEffect(() => {
    if (!data || !bootstrap.isFetched) return
    const metrics = new Map(data.items.map((item) => [item.name, item]))
    const timestamps = ['decode_tps', 'prefill_tps', 'ttft_mean_ms', 'active_requests'].map((name) => metrics.get(name)).filter((item): item is Metric => Boolean(item && item.state === 'ok'))
    const at = timestamps.length ? Math.max(...timestamps.map((item) => Date.parse(item.observed_at))) : Date.parse(metrics.get('uptime_s')?.observed_at ?? '')
    if (!Number.isFinite(at)) return
    const point: Point = { at, decode: numeric(metrics.get('decode_tps')), prefill: numeric(metrics.get('prefill_tps')), ttft: numeric(metrics.get('ttft_mean_ms')), requests: numeric(metrics.get('active_requests')) }
    setHistories((current) => {
      const previous = current[historyKey] ?? []
      if (previous.at(-1)?.at === at) return current
      return { ...current, [historyKey]: [...previous.filter((sample) => sample.at !== at), point].sort((a, b) => a.at - b.at).slice(-maxHistoryPoints) }
    })
  }, [data, historyKey, bootstrap.isFetched])
  useEffect(() => {
    if (!bootstrap.data) return
    const seeded = [...bootstrap.data.history, ...(bootstrap.data.current ? [bootstrap.data.current] : [])].map((sample) => {
      const metrics = new Map(sample.items.map((item) => [item.name, item]))
      return { at: Date.parse(sample.observed_at), decode: numeric(metrics.get('decode_tps')), prefill: numeric(metrics.get('prefill_tps')), ttft: numeric(metrics.get('ttft_mean_ms')), requests: numeric(metrics.get('active_requests')) }
    }).filter((point) => Number.isFinite(point.at))
    setHistories((current) => {
      const merged = new Map([...seeded, ...(current[historyKey] ?? [])].map((point) => [point.at, point]))
      return { ...current, [historyKey]: [...merged.values()].sort((a, b) => a.at - b.at).slice(-maxHistoryPoints) }
    })
  }, [bootstrap.data, historyKey])
  const extrema = (key: 'decode' | 'prefill' | 'ttft' | 'requests', minimum = false) => {
    const field = { decode: 'decode_tps', prefill: 'prefill_tps', ttft: 'ttft_mean_ms', requests: 'active_requests' }[key]
    const persisted = data?.peaks?.[field] ?? bootstrap.data?.peaks?.[field]
    if (persisted !== undefined) return `${minimum ? 'min' : 'max'} ${compact.format(persisted)}`
    const values = history.map((point) => point[key]).filter((item): item is number => item !== null && (!minimum || item > 0))
    return values.length ? `${minimum ? 'min' : 'max'} ${compact.format(minimum ? Math.min(...values) : Math.max(...values))}` : 'Нет наблюдений'
  }
  const input = value('prompt_tokens_total'), output = value('completion_tokens_total')
  const inflight = value('inflight_completion_tokens')
  const stats = [
    { name: 'Decode', field: 'decode_tps', unit: 'tok/s', detail: extrema('decode') },
    { name: 'TTFT', field: 'ttft_mean_ms', unit: 'ms', detail: instance ? `${extrema('ttft', true)} · Среднее по новым запросам` : 'Среднее по запросам' },
    { name: 'Prefill', field: 'prefill_tps', unit: 'tok/s', detail: extrema('prefill') },
    { name: 'Requests', field: 'active_requests', unit: 'live', detail: extrema('requests') },
    { name: 'KV cache', field: 'kv_cache_percent', unit: '%', detail: instance ? 'Занятость кеша' : 'Максимум по запускам' },
    { name: 'Queue', field: 'queue_requests', unit: 'waiting', detail: 'Ожидающие запросы' },
    { name: 'Tokens', field: 'tokens_total', unit: inflight !== null && inflight > 0 ? 'live out' : 'total', detail: `${input === null ? '—' : compact.format(input)} in · ${output === null ? '—' : compact.format(output)} out завершено${inflight !== null && inflight > 0 ? ` · ${compact.format(inflight)} out сейчас` : ''}` },
    { name: 'Served', field: 'completed_requests', unit: 'reqs', detail: 'С начала этого процесса' },
  ]
  return <div><div className="grid grid-cols-2 gap-x-5 gap-y-5 pb-5 sm:grid-cols-4">{stats.map((stat) => {
    const number = stat.field === 'tokens_total' ? inflight !== null && inflight > 0 ? inflight : input !== null && output !== null ? input + output : null : value(stat.field)
    const item = items.get(stat.field)
    const detail = item?.state === 'stale' ? 'Данные устарели' : number === null ? 'Недоступно' : stat.detail
    return <div key={stat.name} className="min-w-0" title={`${stat.detail}${item?.source && item.source !== 'unavailable' ? ` · ${item.source}` : ''}`}><p className="text-xs text-muted-foreground">{stat.name}</p><p className="mt-1 whitespace-nowrap text-xl font-medium tabular-nums">{number === null ? '—' : compact.format(number)}{number !== null && stat.unit && <span className="ml-1 text-[11px] font-normal text-muted-foreground">{stat.unit}</span>}</p>{stat.field === 'kv_cache_percent' && number !== null && <Progress className="mt-1.5" value={number ?? 0} />}<p className="mt-1 text-[10px] text-muted-foreground">{detail}</p></div>
  })}</div><Table className="mb-4"><TableBody>{[
    ['Ожидание в очереди', 'queue_time_mean_ms', 'ms'],
    ['Попадание в prefix cache сейчас', 'prefix_cache_hit_percent', '%'],
    ['Попадание в prefix cache за запуск', 'prefix_cache_hit_mean_percent', '%'],
    ['Draft acceptance', 'spec_acceptance_percent', '%'],
  ].map(([label, field, unit]) => <TableRow key={field}><TableCell className="py-1.5 text-xs text-muted-foreground">{label}</TableCell><TableCell className="py-1.5 text-right font-mono text-xs tabular-nums">{value(field) === null ? '—' : `${value(field)!.toLocaleString('ru-RU', { maximumFractionDigits: 1 })} ${unit}`}</TableCell></TableRow>)}</TableBody></Table><div className="grid gap-5 border-t border-border pt-4 lg:grid-cols-3"><Chart title="Prefill" unit="tok/s" points={history} series={[{ key: 'prefill', label: 'Prefill', color: '#cf9a78' }]} /><Chart title="Generation" unit="tok/s" points={history} series={[{ key: 'decode', label: 'Generation', color: '#cf9a78' }]} /><Chart title="Requests" unit="live" points={history} series={[{ key: 'requests', label: 'Active', color: '#cf9a78' }]} /></div><p className="mt-3 text-[10px] text-muted-foreground">До 120 точек · история восстановлена при открытии · {instance?.engine_id ?? 'Сумма по всем активным запускам'}{instance?.engine_id === 'llama.cpp' ? items.get('decode_tps')?.source === 'llama.cpp:slots' ? ' \u00b7 Decode/Prefill: /slots (live); totals: /metrics.' : ' \u00b7 Decode/Prefill: completed requests (/metrics); /slots unavailable.' : ''}</p></div>
}
