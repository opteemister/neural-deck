import { useState } from 'react'
import { createPortal } from 'react-dom'
import { useQuery } from '@tanstack/react-query'
import { api } from '@/lib/api'
import { chartScaleMaximum } from '@/lib/chart-scale'
import type { Instance } from '@/types/api'

const series = [
  { field: 'input_tokens', label: 'Input', color: '#cf9a78' },
  { field: 'output_tokens', label: 'Output', color: '#e9e5dc' },
  { field: 'cached_tokens', label: 'Cached', color: '#9d9b96' },
] as const
const format = (number: number | null) => number === null ? '—' : number.toLocaleString('ru-RU', { maximumFractionDigits: 0 })
const compact = new Intl.NumberFormat('en-US', { notation: 'compact', maximumFractionDigits: 1 })
const time = (at?: string) => at ? new Date(at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', timeZone: 'UTC' }) : ''

export function TokenHistoryChart({ instance, profileName }: { instance?: Instance; profileName?: string }) {
  const selectedProfile = profileName ?? instance?.profile_name ?? undefined
  const profileId = instance?.profile_id ?? undefined
  const historyKey = profileId ?? selectedProfile ?? instance?.id ?? 'all'
  const query = useQuery({ queryKey: ['token-history', historyKey],
    queryFn: () => api.getTokenHistory(selectedProfile, selectedProfile ? undefined : instance?.pid, profileId),
    refetchInterval: 30_000, refetchIntervalInBackground: false, retry: false })
  const [hoveredIndex, setHoveredIndex] = useState<number | null>(null)
  const [tooltipPosition, setTooltipPosition] = useState({ left: 0, top: 0 })
  const items = query.data?.items ?? []
  const hovered = hoveredIndex !== null ? items[hoveredIndex] : undefined
  const sum = (field: 'input_tokens' | 'output_tokens') => items.reduce((total, item) => total + (item[field] ?? 0), 0)
  const inputTotal = sum('input_tokens')
  const outputTotal = sum('output_tokens')
  const total = inputTotal + outputTotal
  // Cached tokens are part of Input: split Input into uncached and cached segments.
  const stacks = items.map((item) => [
    item.input_tokens === null ? null : Math.max(0, item.input_tokens - (item.cached_tokens ?? 0)),
    item.output_tokens,
    item.cached_tokens,
  ])
  const maximum = chartScaleMaximum(Math.max(0, ...stacks.map((parts) => parts.reduce<number>((sum, part) => sum + (part ?? 0), 0))))
  const step = 400 / Math.max(1, items.length)
  return <div className="min-w-0">
    <div className="mb-2 h-10 text-[11px] text-muted-foreground">
      <div className="flex items-start justify-between gap-2 whitespace-nowrap">
        <span className="min-w-0 truncate">Tokens</span>
        <div className="flex shrink-0 gap-2">{series.map((line) => <span key={line.field} style={{ color: line.color }}>{line.label}</span>)}</div>
      </div>
      <div className="mt-1 flex min-w-0 items-center gap-2 whitespace-nowrap text-[10px]">
        <span className="shrink-0 opacity-60">1 d</span>
        <span className="min-w-0 truncate opacity-60" title={`Завершённые запросы через API Neural Deck за последние 24 часа. in: ${compact.format(inputTotal)}; out: ${compact.format(outputTotal)}; total: ${compact.format(total)}. Cached — часть in, а не дополнительные токены.`}>in {compact.format(inputTotal)} · out {compact.format(outputTotal)} · total {compact.format(total)}</span>
      </div>
    </div>
    <div className="relative" onPointerLeave={() => setHoveredIndex(null)}>
      <span className="pointer-events-none absolute left-0 top-0 font-mono text-[9px] text-muted-foreground">{items.length ? format(maximum) : '—'}</span>
      <svg viewBox="0 0 400 120" preserveAspectRatio="none" className="h-32 w-full" role="img" aria-label="Tokens за 24 часа" onPointerMove={(event) => {
        const rect = event.currentTarget.getBoundingClientRect()
        if (items.length && rect.width) {
          setHoveredIndex(Math.min(items.length - 1, Math.max(0, Math.floor((event.clientX - rect.left) / rect.width * items.length))))
          const width = 176, gap = 12
          const left = event.clientX + gap + width <= window.innerWidth - 8 ? event.clientX + gap : event.clientX - gap - width
          setTooltipPosition({ left: Math.max(8, left), top: Math.max(8, Math.min(event.clientY + gap, window.innerHeight - 108)) })
        }
      }}>
        {[12, 44, 76, 108].map((y) => <line key={y} x1="0" y1={y} x2="400" y2={y} className="stroke-border" strokeWidth="1" />)}
        {items.map((item, index) => <g key={item.at}>{series.map((line, offset) => {
          const number = stacks[index][offset]
          if (number === null) return null
          const height = number / maximum * 96
          const below = stacks[index].slice(0, offset).reduce<number>((sum, part) => sum + (part ?? 0), 0) / maximum * 96
          return <rect key={line.field} data-series={line.field} x={index * step + step * 0.2} y={108 - below - height} width={step * 0.6} height={height} fill={line.color} opacity={hoveredIndex === null || hoveredIndex === index ? 1 : 0.5} />
        })}</g>)}
        {!items.length && <text x="200" y="65" textAnchor="middle" className="fill-muted-foreground text-[11px]">{query.isError ? 'Недоступно' : 'Ожидание данных'}</text>}
      </svg>
      {hovered && createPortal(<div role="tooltip" className="pointer-events-none fixed z-50 w-44 rounded-md border border-border bg-popover px-2 py-1.5 text-[11px] text-popover-foreground shadow-md" style={tooltipPosition}>
        <p className="mb-1 font-mono text-[10px] text-muted-foreground">{time(hovered.at)}–{time(new Date(Date.parse(hovered.at) + (query.data?.bucket_minutes ?? 60) * 60_000).toISOString())} · {hovered.requests} reqs</p>
        {series.map((line) => <div key={line.field} className="flex justify-between gap-2"><span>{line.label}</span><span className="font-mono tabular-nums">{hovered[line.field] === null ? '—' : format(hovered[line.field])}</span></div>)}
      </div>, document.body)}
    </div>
    <div className="mt-1 flex justify-between font-mono text-[10px] text-muted-foreground"><span>{time(query.data?.from)}</span><span>{time(query.data?.to)}</span></div>
  </div>
}
