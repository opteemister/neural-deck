import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Info, RefreshCw } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Select } from '@/components/ui/select'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { QueryEmpty, QueryError, QueryLoading } from '@/components/query-state'
import { api, queryKeys } from '@/lib/api'
import type { StatisticsAggregate, StatisticsData, StatisticsTokenMetric } from '@/types/api'

export function StatisticsPage() {
  const [period, setPeriod] = useState<'week' | 'month' | 'quarter' | 'half_year'>('month')
  const filters = { period }
  const statistics = useQuery({ queryKey: queryKeys.statistics(filters), queryFn: () => api.getStatistics(filters) })
  const overview = useQuery({ queryKey: queryKeys.statistics({ period: 'month' }), queryFn: () => api.getStatistics({ period: 'month' }) })
  const year = useQuery({ queryKey: queryKeys.statistics({ period: 'year' }), queryFn: () => api.getStatistics({ period: 'year' }) })
  const data = statistics.data
  const refreshing = statistics.isFetching || overview.isFetching || year.isFetching
  const refresh = () => { void statistics.refetch(); if (period !== 'month') void overview.refetch(); void year.refetch() }
  const captions = { week: 'За последнюю неделю', month: 'За последние 30 дней', quarter: 'За последние 3 месяца', half_year: 'За последние 6 месяцев' }

  return <div>
    <header className="mb-5 flex flex-wrap items-start justify-between gap-4">
      <h1 className="text-xl font-medium tracking-tight">Статистика</h1>
      <Button variant="secondary" size="icon" aria-label="Обновить статистику" title="Обновить статистику" disabled={refreshing} onClick={refresh}><RefreshCw className={`size-3.5 ${refreshing ? 'animate-spin' : ''}`} /></Button>
    </header>
    <div className="space-y-4">
      {overview.isLoading ? <QueryLoading label="Собираем сводку" /> : overview.isError ? <QueryError error={overview.error} retry={() => void overview.refetch()} label="Сводка недоступна" /> : overview.data && <StatisticsSummary data={overview.data} />}
      <Card>
        <SectionHeader title="Последний год" caption="53 недели · токены по дням" />
        <CardContent>
          {year.isLoading ? <QueryLoading label="Собираем активность" /> : year.isError ? <QueryError error={year.error} retry={() => void year.refetch()} label="Активность недоступна" /> : year.data && <ActivityHeatmap data={year.data} />}
        </CardContent>
      </Card>
      <div className="flex flex-wrap items-center justify-end gap-3 pt-1"><label id="statistics-period-label" className="text-xs text-muted-foreground">Период статистики</label><Select id="statistics-period" aria-labelledby="statistics-period-label" className="w-40" value={period} onChange={(event) => setPeriod(event.target.value as typeof period)}><option value="week">Неделя</option><option value="month">30 дней</option><option value="quarter">3 месяца</option><option value="half_year">6 месяцев</option></Select></div>
      {statistics.isLoading ? <QueryLoading label="Собираем статистику" /> : statistics.isError ? <QueryError error={statistics.error} retry={() => void statistics.refetch()} label="Статистика недоступна" /> : data && <><ProfileTable profiles={data.profiles} caption={captions[period]} /><ClientTable clients={data.clients} caption={captions[period]} /></>}
    </div>
  </div>
}

function StatisticsSummary({ data }: { data: StatisticsData }) {
  const { totals, summary } = data
  const rows = [
    [{ label: 'Запросы', value: totals.requests.total.toLocaleString('ru-RU') }, { label: 'Input', value: <TokenValue metric={totals.tokens.input} /> }, { label: 'Cached input', value: <TokenValue metric={totals.tokens.cached_input} /> }, { label: 'Output', value: <TokenValue metric={totals.tokens.output} /> }, { label: 'Ошибки', value: <Errors aggregate={totals} /> }, { label: 'Стоимость', value: <Cost cost={totals.cost} /> }],
    [{ label: 'Сессии', value: <span title="Идентификаторы сессий не записываются в журнал запросов">—</span> }, { label: 'Активные дни', value: `${summary.active_days} / ${summary.period_days}` }, { label: 'Streak', value: `${summary.streak_days} дней` }, { label: 'Success rate', value: summary.success_rate === null ? '—' : `${summary.success_rate.toFixed(1)}%` }, { label: 'Latency P95', value: formatLatency(totals.performance.latency_p95_ms) }, { label: 'Средняя latency', value: formatLatency(totals.performance.latency_ms) }],
  ]
  return <Card className="px-[18px]" aria-label="Общая статистика за 30 дней">{rows.map((row, index) => <div key={index} className={`grid grid-cols-2 gap-x-3 gap-y-5 py-4 sm:grid-cols-3 lg:grid-cols-6 ${index ? 'border-t border-border' : ''}`}>{row.map((item) => <div key={item.label}><div className="mb-2 text-[11px] uppercase tracking-[0.12em] text-muted-foreground">{item.label}</div><div className="text-base tabular-nums">{item.value}</div></div>)}</div>)}</Card>
}

function SectionHeader({ title, caption }: { title: string; caption: string }) {
  return <CardHeader className="!flex-row flex-wrap items-baseline justify-between gap-2 px-[18px] py-4"><CardTitle className="text-base">{title}</CardTitle><p className="text-[11px] text-muted-foreground">{caption}</p></CardHeader>
}

export function ActivityHeatmap({ data }: { data: StatisticsData }) {
  const { activity, benchmarks } = data
  const [selected, setSelected] = useState<StatisticsData['activity'][number] | null>(null)
  const values = activity.map((item) => (item.tokens.input.known_total ?? 0) + (item.tokens.output.known_total ?? 0))
  const maximum = Math.max(...values, 0)
  const start = new Date(data.period.from)
  const today = data.period.to.slice(0, 10)
  const byDate = new Map(activity.map(item => [item.date, item]))
  const slots = Array.from({ length: 371 }, (_, index) => new Date(start.getTime() + index * 86400000))
  const months = slots.flatMap((date, index) => index % 7 === 0 && (index === 0 || date.getUTCMonth() !== slots[index - 7].getUTCMonth()) ? [{ date, week: index / 7 }] : [])
  const current = selected && byDate.get(selected.date) || activity.at(-1)
  return <div className="min-w-0 w-full" data-testid="activity-heatmap">
    <div className="min-w-0 w-full">
      <div className="relative mb-2 ml-[31px] h-4 text-[11px] text-muted-foreground">{months.map(({ date, week }, index) => <span key={week} className={`absolute whitespace-nowrap ${index % 2 ? 'hidden sm:block' : ''}`} style={index === months.length - 1 ? { right: 0 } : { left: `${week / 53 * 100}%` }}>{date.toLocaleDateString('ru-RU', { month: 'short', timeZone: 'UTC' })}</span>)}</div>
      <div className="grid grid-cols-[28px_minmax(0,1fr)] gap-[3px]">
        <div className="grid text-[11px] text-muted-foreground" style={{ gridTemplateRows: 'repeat(7, minmax(0, 1fr))' }}>{['Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб', 'Вс'].map((label, index) => <span key={index} className="flex items-center">{label}</span>)}</div>
        <div className="grid min-w-0 grid-flow-col gap-px sm:gap-[3px]" style={{ gridTemplateColumns: 'repeat(53, minmax(0, 1fr))', gridTemplateRows: 'repeat(7, minmax(0, 1fr))' }} aria-label="53 недели, 7 дней в каждой неделе">
          {slots.map(date => {
            const key = date.toISOString().slice(0, 10)
            const item = byDate.get(key)
            if (key > today || !item) return <span key={key} aria-hidden="true" className="aspect-square rounded-sm bg-background/40" />
            const tokens = (item.tokens.input.known_total ?? 0) + (item.tokens.output.known_total ?? 0)
            return <button key={key} type="button" aria-label={activityLabel(item)} title={activityLabel(item)} onMouseEnter={() => setSelected(item)} onFocus={() => setSelected(item)} onClick={() => setSelected(item)} className={`aspect-square rounded-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-copper hover:ring-1 hover:ring-copper ${item.benchmark ? 'outline outline-1 outline-offset-1 outline-copper' : ''}`} style={{ backgroundColor: heatColor(tokens, maximum) }} />
          })}
        </div>
      </div>
      <div className="mt-3 flex flex-wrap items-center justify-between gap-3 text-[11px] text-muted-foreground">
        <span>{current ? `${formatDate(current.date)} · ${activityTokens(current)} токенов · ${current.requests.total} запросов` : 'За последний год запросов нет'}</span>
        <span className="flex items-center gap-1.5">Меньше{[0, 0.25, 0.5, 0.75, 1].map(level => <i key={level} className="size-[10px] rounded-sm" style={{ backgroundColor: heatColor(level, 1) }} />)}Больше</span>
      </div>
      <div className="mt-3 flex flex-wrap justify-between gap-3 text-[11px] text-muted-foreground"><span>{benchmarks.intervals.length > 0 ? 'Контур — запуск бенчмарка' : 'Запуски бенчмарков пока не записываются'}</span><span>Весь трафик включён в статистику</span></div>
    </div>
  </div>
}

function heatColor(value: number, maximum: number) {
  if (value <= 0 || maximum <= 0) return 'hsl(var(--muted))'
  const level = Math.ceil(value / maximum * 4)
  return `color-mix(in srgb, hsl(var(--copper)) ${[0, 20, 38, 62, 90][level]}%, hsl(var(--muted)))`
}

function activityLabel(item: StatisticsData['activity'][number]) {
  return `${item.date} UTC: input ${formatTokens(item.tokens.input.known_total)}, cached ${formatTokens(item.tokens.cached_input.known_total)}, output ${formatTokens(item.tokens.output.known_total)}; запросов ${item.requests.total}, failed ${item.requests.failed}, interrupted ${item.requests.interrupted}, unknown ${item.requests.unknown}; неизвестный input у ${item.tokens.input.unknown_requests}, output у ${item.tokens.output.unknown_requests}, cached у ${item.tokens.cached_input.unknown_requests} запросов`
}

function activityTokens(item: StatisticsData['activity'][number]) {
  const { input, output } = item.tokens
  if (input.known_total === null && output.known_total === null) return '—'
  return formatTokens((input.known_total ?? 0) + (output.known_total ?? 0)) + (input.unknown_requests || output.unknown_requests ? ' (неполные данные)' : '')
}

const numberCell = 'whitespace-nowrap text-right tabular-nums'

function ProfileTable({ profiles, caption }: { profiles: StatisticsData['profiles']; caption: string }) {
  return <Card><SectionHeader title="По профилям" caption={caption} />
    {profiles.length === 0 ? <CardContent><QueryEmpty title="Нет профилей" description="Сохранённые профили и профили из истории запросов появятся здесь." /></CardContent> : <>
      <div className="px-[18px] pb-2"><Table className="min-w-[900px]"><TableHeader><TableRow><TableHead>Профиль</TableHead>{['Input', 'Cached', 'Output', 'Запросы', 'Ошибки', 'Max concurrency', 'Load', 'Prefill / Decode', 'Стоимость'].map((label) => <TableHead key={label} className="text-right whitespace-nowrap">{label === 'Load' ? <span className="inline-flex items-center gap-1.5">Load<button type="button" aria-label="Как рассчитывается Load" title="Load — среднее число активных запросов относительно исторического максимума профиля. Профиль без запросов имеет 0% загрузки." className="rounded-sm text-muted-foreground hover:text-foreground focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-copper"><Info className="size-3.5" /></button></span> : label}</TableHead>)}</TableRow></TableHeader><TableBody>{profiles.map((profile) => <TableRow key={profile.profile_name}>
        <TableCell><p className="font-medium">{profile.profile_name}</p><p className="mt-0.5 text-[11px] text-muted-foreground">{profile.model_name ?? 'Модель неизвестна'}</p></TableCell>
        <TokenCells aggregate={profile} /><TableCell className={numberCell}>{profile.requests.total}</TableCell><TableCell className={numberCell}><Errors aggregate={profile} /></TableCell><TableCell className={numberCell}>{profile.load.max_concurrency || '—'}</TableCell>
        <TableCell className={numberCell}><span className="inline-flex items-center gap-2" title={profile.load.state === 'unknown' ? 'Недостаточно истории метрик для расчёта времени активных запросов' : 'Наблюдаемое время активных запросов движка относительно его исторического максимума'}><span className="h-1.5 w-14 overflow-hidden rounded-full bg-border"><span className="block h-full rounded-full bg-copper" style={{ width: `${Math.min(100, profile.load.percent)}%` }} /></span>{profile.load.state === 'unknown' ? '—' : formatLoad(profile.load.percent)}</span></TableCell>
        <TableCell className={numberCell}>{formatRate(profile.performance.prefill_tps)} / {formatRate(profile.performance.decode_tps)} <span className="text-muted-foreground">ток/с</span></TableCell><TableCell className={numberCell}><Cost cost={profile.cost} /></TableCell>
      </TableRow>)}</TableBody></Table></div>
    </>}
  </Card>
}

function ClientTable({ clients, caption }: { clients: StatisticsData['clients']; caption: string }) {
  return <Card><SectionHeader title="По клиентам" caption={caption} />
    {clients.length === 0 ? <CardContent><QueryEmpty title="За этот период запросов клиентов нет" description="Статистика появится после первого запроса." /></CardContent> : <div className="px-[18px] pb-2"><Table className="min-w-[900px]"><TableHeader><TableRow><TableHead>Клиент</TableHead>{['Input', 'Cached', 'Output', 'Запросы', 'Ошибки'].map((label) => <TableHead key={label} className="text-right">{label}</TableHead>)}<TableHead>Профили</TableHead><TableHead className="text-right">Стоимость</TableHead></TableRow></TableHeader><TableBody>{clients.map((client) => <TableRow key={client.client_id ?? 'unknown'}><TableCell className="font-medium whitespace-nowrap">{client.label}</TableCell><TokenCells aggregate={client} /><TableCell className={numberCell}>{client.requests.total}</TableCell><TableCell className={numberCell}><Errors aggregate={client} /></TableCell><TableCell className="max-w-64 text-[11px] text-muted-foreground">{client.profiles.join(', ') || '—'}</TableCell><TableCell className={numberCell}><Cost cost={client.cost} /></TableCell></TableRow>)}</TableBody></Table></div>}
  </Card>
}

function TokenCells({ aggregate }: { aggregate: StatisticsAggregate }) { return <>{[aggregate.tokens.input, aggregate.tokens.cached_input, aggregate.tokens.output].map((metric, index) => <TableCell key={index} className={numberCell}><TokenValue metric={metric} /></TableCell>)}</> }
function TokenValue({ metric }: { metric: StatisticsTokenMetric }) { return <span title={metric.unknown_requests ? `Нет данных для ${metric.unknown_requests} запросов` : undefined}>{formatTokens(metric.known_total)}{metric.unknown_requests > 0 && metric.known_total !== null && <span className="ml-1 text-copper">*</span>}</span> }
function Errors({ aggregate }: { aggregate: StatisticsAggregate }) { const count = aggregate.requests.failed + aggregate.requests.interrupted; return <span className={count ? 'text-copper' : 'text-muted-foreground'} title={`Успешно: ${aggregate.requests.success}; failed: ${aggregate.requests.failed}; interrupted: ${aggregate.requests.interrupted}; неизвестный статус: ${aggregate.requests.unknown}`}>{count}{aggregate.requests.unknown > 0 && <span className="ml-1" title="Есть запросы с неизвестным статусом"> + {aggregate.requests.unknown} ?</span>}</span> }
function Cost({ cost }: { cost: StatisticsAggregate['cost'] }) { return <span title={cost.state === 'partial' ? `Рассчитано по доступным токенам и текущим ценам. Неполные данные у ${cost.unknown_requests} запросов: отсутствующие части пропущены; без cached input весь известный input считается по обычной ставке.` : cost.state === 'unavailable' ? 'Для доступных токенов не заданы цены в настройках профиля' : cost.state === 'unknown' ? `Для расчёта не хватает данных о токенах (${cost.unknown_requests} запросов)` : 'Рассчитано по текущим ценам профиля'}>{cost.usd === null ? '—' : `$${cost.usd.toFixed(4)}`}{cost.state === 'partial' && <span className="ml-1 text-copper">*</span>}</span> }
function formatTokens(value: number | null) { return value === null ? '—' : value.toLocaleString('ru-RU', { notation: 'compact', maximumFractionDigits: 2 }) }
function formatRate(value: number | null) { return value === null ? '—' : value.toFixed(1) }
function formatLoad(value: number) { return value > 0 && value < 0.01 ? '<0,01%' : `${value.toLocaleString('ru-RU', { maximumFractionDigits: 2 })}%` }
function formatDate(value: string) { return new Intl.DateTimeFormat('ru-RU', { day: 'numeric', month: 'long', year: 'numeric', timeZone: 'UTC' }).format(new Date(value)) }
function formatLatency(value: number | null) { return value === null ? '—' : `${Math.round(value).toLocaleString('ru-RU')} мс` }
