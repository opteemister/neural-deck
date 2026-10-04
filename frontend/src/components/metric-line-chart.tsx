import { useState } from 'react'
import { chartScaleMaximum } from '@/lib/chart-scale'

export type Point = { at: number; decode: number | null; prefill: number | null; ttft: number | null; requests: number | null; perRequest: number | null }
const plain = new Intl.NumberFormat('en-US', { useGrouping: false, maximumFractionDigits: 2 })

export function MetricLineChart({ title, unit, points, series, maximum, formatX, ariaLabel, subtitle, stretch = false }: { title: string; unit: string; points: Point[]; series: Array<{ key: 'decode' | 'prefill' | 'ttft' | 'requests' | 'perRequest'; label: string; color: string; independent?: boolean; dashed?: boolean }>; maximum?: number; formatX?: (value: number) => string; ariaLabel?: string; subtitle?: string; stretch?: boolean }) {
  const [hoveredAt, setHoveredAt] = useState<number | null>(null)
  const hovered = points.find((point) => point.at === hoveredAt)
  const start = points[0]?.at ?? 0
  const end = points.at(-1)?.at ?? start
  const values = points.flatMap((point) => series.map((line) => point[line.key])).filter((value): value is number => value !== null)
  const maxima = new Map(series.map((line) => [line.key, chartScaleMaximum(Math.max(0, ...points.map((point) => point[line.key]).filter((value): value is number => value !== null)))]))
  const sharedMax = maximum ?? Math.max(1, ...series.filter((line) => !line.independent).map((line) => maxima.get(line.key)!))
  const scale = (line: (typeof series)[number]) => line.independent ? maxima.get(line.key)! : sharedMax
  const paintSeries = [...series].sort((a, b) => Number(Boolean(b.independent || b.dashed)) - Number(Boolean(a.independent || a.dashed)))
  const hoveredX = hovered && end > start ? (hovered.at - start) / (end - start) * 400 : 0
  const headerHeight = subtitle ? 'h-10' : 'h-5'
  return <div className={`min-w-0 ${stretch ? 'flex h-full flex-col' : ''}`}><div className={`mb-2 flex ${headerHeight} flex-wrap content-start items-start justify-between gap-x-2 gap-y-1 text-[11px] text-muted-foreground`}><div className="min-w-0 truncate whitespace-nowrap">{title} <span className="opacity-60">{unit}</span></div><div className="flex shrink-0 gap-3 whitespace-nowrap">{series.map((line) => <span key={line.key} style={{ color: line.color }}>{line.label}</span>)}</div>{subtitle && <div className="min-w-0 w-full truncate whitespace-nowrap text-[10px] opacity-60" title={subtitle}>{subtitle}</div>}</div><div className={`relative ${stretch ? 'min-h-0 flex-1' : ''}`} onPointerLeave={() => setHoveredAt(null)}><div className="pointer-events-none absolute inset-x-0 top-0 flex justify-between font-mono text-[9px] tabular-nums" aria-label={`${title}: масштаб`}><span style={{ color: series[0]?.color }}>{values.length ? plain.format(sharedMax) : '—'}</span>{series.filter((line) => line.independent).map((line) => <span key={line.key} style={{ color: line.color }} title={`Шкала ${line.label}`} data-axis={line.key}>{points.some((point) => point[line.key] !== null) ? plain.format(scale(line)) : '—'}</span>)}</div><svg viewBox="0 0 400 120" preserveAspectRatio="none" className={`${stretch ? 'absolute inset-0 h-full' : 'h-32'} w-full`} role="img" aria-label={ariaLabel ?? title} onPointerMove={(event) => {
    if (!points.length) return
    const rect = event.currentTarget.getBoundingClientRect()
    if (!rect.width) return
    const fraction = Math.min(1, Math.max(0, (event.clientX - rect.left) / rect.width))
    const timestamp = start + fraction * (end - start)
    const nearest = points.reduce((best, point) => Math.abs(point.at - timestamp) < Math.abs(best.at - timestamp) ? point : best)
    setHoveredAt(nearest.at)
  }}>{[12, 44, 76, 108].map((y) => <line key={y} x1="0" y1={y} x2="400" y2={y} className="stroke-border" strokeWidth="1" />)}{paintSeries.map((line) => {
    const segments: string[] = []
    let segment = ''
    points.forEach((point) => {
      const value = point[line.key]
      if (value === null) return
      const x = end > start ? (point.at - start) / (end - start) * 400 : 0
      const y = 108 - value / scale(line) * 96
      segment += `${segment ? ' L' : 'M'}${x.toFixed(1)},${y.toFixed(1)}`
    })
    if (segment) segments.push(segment)
    return <g key={line.key} data-series={line.key}>{segments.map((path, index) => <path key={index} d={path} fill="none" stroke={line.color} strokeWidth={line.independent || line.dashed ? 2.5 : 1.5} strokeDasharray={line.independent || line.dashed ? '5 3' : undefined} />)}{points.map((point) => point[line.key] === null ? null : <circle key={point.at} cx={end > start ? (point.at - start) / (end - start) * 400 : 0} cy={108 - point[line.key]! / scale(line) * 96} r="1.5" fill={line.color} />)}</g>
  })}{hovered && <g className="pointer-events-none"><line x1={hoveredX} x2={hoveredX} y1="0" y2="120" className="stroke-muted-foreground" strokeDasharray="3 3" strokeWidth="1" />{paintSeries.map((line) => hovered[line.key] === null ? null : <circle key={line.key} cx={hoveredX} cy={108 - hovered[line.key]! / scale(line) * 96} r="3" fill={line.color} />)}</g>}{values.length === 0 && <text x="200" y="65" textAnchor="middle" className="fill-muted-foreground text-[11px]">Нет данных</text>}</svg>{hovered && <div role="tooltip" className="pointer-events-none absolute top-1 z-10 w-40 -translate-x-1/2 rounded-md border border-border bg-popover px-2 py-1.5 text-[11px] text-popover-foreground shadow-md" style={{ left: `clamp(80px, ${hoveredX / 4}%, calc(100% - 80px))` }}><p className="mb-1 font-mono text-[10px] text-muted-foreground">{formatX ? formatX(hovered.at) : new Date(hovered.at).toLocaleTimeString()}</p>{series.map((line) => <div key={line.key} className="flex items-center justify-between gap-2"><span>{line.label}</span><span className="whitespace-nowrap font-mono tabular-nums">{hovered[line.key] === null ? '—' : hovered[line.key]!.toLocaleString('ru-RU', { maximumFractionDigits: 2 })}{hovered[line.key] !== null && <span className="ml-1 text-muted-foreground">{unit}</span>}</span></div>)}</div>}</div><div className="mt-1 flex justify-between font-mono text-[10px] text-muted-foreground"><span>{points.length ? formatX ? formatX(start) : new Date(start).toLocaleTimeString() : 'Ожидание данных'}</span><span>{end !== start ? formatX ? formatX(end) : new Date(end).toLocaleTimeString() : ''}</span></div></div>
}
