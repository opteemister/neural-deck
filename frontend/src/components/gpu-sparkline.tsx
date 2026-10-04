import { useState } from 'react'

export function GpuSparkline({ values, label, formatValue = (value) => String(value) }: { values: Array<number | null>; label: string; formatValue?: (value: number) => string }) {
  const [hoveredIndex, setHoveredIndex] = useState<number | null>(null)
  const points = values
    .map((value, index) => ({ value, index }))
    .filter((point): point is { value: number; index: number } => point.value !== null && Number.isFinite(point.value))
  const width = 68
  const height = 16
  const padding = 1.5
  const minimum = points.length ? Math.min(...points.map((point) => point.value)) : 0
  const maximum = points.length ? Math.max(...points.map((point) => point.value)) : 1
  const spread = maximum - minimum
  const lower = spread === 0 ? minimum - 1 : minimum - spread * 0.1
  const upper = spread === 0 ? maximum + 1 : maximum + spread * 0.1
  const path = points.length > 1
    ? points.map((point, index) => {
      const x = points.length === 1 ? padding : padding + index / (points.length - 1) * (width - padding * 2)
      const y = height - padding - (point.value - lower) / (upper - lower) * (height - padding * 2)
      return `${index === 0 ? 'M' : 'L'}${x.toFixed(1)},${y.toFixed(1)}`
    }).join(' ')
    : ''
  const hovered = hoveredIndex === null ? null : points[hoveredIndex]
  const hoveredX = hovered === null ? 0 : padding + hoveredIndex! / Math.max(1, points.length - 1) * (width - padding * 2)
  return <div className="relative h-3.5 w-[4.25rem] shrink-0" onPointerLeave={() => setHoveredIndex(null)}>
    <svg aria-label={label} className="block h-full w-full text-copper" data-sparkline={label} viewBox={`0 0 ${width} ${height}`} preserveAspectRatio="none" role="img">
      {path && <path d={path} fill="none" stroke="currentColor" strokeLinecap="round" strokeLinejoin="round" strokeWidth="1.5" vectorEffect="non-scaling-stroke" />}
      {points.map((point, index) => <circle key={`${point.index}-${index}`} cx={padding + index / Math.max(1, points.length - 1) * (width - padding * 2)} cy={height - padding - (point.value - lower) / (upper - lower) * (height - padding * 2)} r="4" fill="transparent" onPointerEnter={() => setHoveredIndex(index)} />)}
    </svg>
    {hovered && <span role="tooltip" className="pointer-events-none absolute bottom-full left-0 z-20 mb-1 translate-x-1 whitespace-nowrap rounded border border-border bg-popover px-1.5 py-0.5 font-mono text-[10px] text-popover-foreground shadow-md" style={{ left: `${hoveredX / width * 100}%` }}>{formatValue(hovered.value)}</span>}
  </div>
}
