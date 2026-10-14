import { ExternalLink, Square } from 'lucide-react'
import { useDailyMetricAggregates } from '@/hooks/use-metric-averages'
import { StatusBadge } from '@/components/status-badge'
import { TableCell, TableRow } from '@/components/ui/table'
import type { Instance, MetricsData } from '@/types/api'

export function RunSummaryRow({ instance, data, onSelect, onStop, onLogs, stopping, showPerformance = true }: { instance: Instance; data?: MetricsData; onSelect: () => void; onStop: () => void; onLogs?: () => void; stopping: boolean; showPerformance?: boolean }) {
  const historyKey = instance.profile_name ?? instance.id
  const averages = useDailyMetricAggregates(historyKey, instance.profile_name ? { profile_name: instance.profile_name } : { pid: instance.pid }, showPerformance)
  const format = (value?: number | null) => value == null ? '—' : value.toLocaleString('ru-RU', { maximumFractionDigits: 1 })
  const value = (name: string) => {
    const metric = data?.items.find((item) => item.name === name)
    return metric?.state === 'ok' && typeof metric.value === 'number' ? metric.value : null
  }
  const average = (name: string) => {
    const metric = averages.get(name)
    return metric ? metric.sum / metric.weight : null
  }
  const label = instance.profile_name ?? 'Внешний запуск'
  const canStop = instance.can_stop === true && instance.identity_verified === true
  return <TableRow>
    <TableCell><button type="button" className="max-w-64 truncate text-left hover:text-copper" title={label} onClick={onSelect}>{label}</button><p className="max-w-64 truncate text-[11px] text-muted-foreground" title={instance.model_name ?? undefined}>{instance.model_name ?? 'Модель неизвестна'}</p></TableCell>
    <TableCell><div className="flex items-center gap-2">{instance.ui_url && <a href={instance.ui_url} target="_blank" rel="noopener noreferrer" aria-label={`Открыть UI ${label}`} title="Открыть UI" className="inline-flex shrink-0 items-center gap-1 text-[11px] font-medium text-copper underline-offset-2 hover:underline"><ExternalLink className="size-3" />UI</a>}<StatusBadge state={instance.state} onClick={onLogs} title={onLogs ? `Логи профиля «${label}»` : undefined} /></div></TableCell>
    <TableCell className="text-right font-mono" title={showPerformance ? 'Avg за последние 24 часа, без нулевых и пустых измерений' : undefined}>{showPerformance ? format(average('prefill_tps')) : '—'}</TableCell>
    <TableCell className="text-right font-mono" title={showPerformance ? 'Avg за последние 24 часа, без нулевых и пустых измерений' : undefined}>{showPerformance ? format(average('decode_tps')) : '—'}</TableCell>
    <TableCell className="text-right font-mono">{showPerformance ? format(value('completed_requests')) : '—'}</TableCell>
    <TableCell className="text-right font-mono">{showPerformance ? format(value('completion_tokens_total')) : '—'}</TableCell>
    <TableCell className="text-right"><button type="button" aria-label={`Остановить ${label}`} title={canStop ? `Остановить ${label}` : 'Остановка этого процесса недоступна'} disabled={!canStop || stopping || instance.state === 'stopping'} className="inline-flex size-7 items-center justify-center rounded-sm text-red-400 hover:bg-red-400/10 disabled:cursor-not-allowed disabled:opacity-40" onClick={onStop}><Square className="size-3" fill="currentColor" /></button></TableCell>
  </TableRow>
}
