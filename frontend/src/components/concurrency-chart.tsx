import { useQuery } from '@tanstack/react-query'
import { api } from '@/lib/api'
import { concurrencyScaleMaximum } from '@/lib/chart-scale'
import { MetricLineChart } from '@/components/metric-line-chart'
import type { Instance } from '@/types/api'

export interface ConcurrencyPoint {
  profile_id?: string | null
  profile_name: string
  model_name: string | null
  engine_id: string
  requests: number
  total_avg: number
  per_request_avg: number
  total_max: number
  samples: number
  seconds: number
}

export function ConcurrencyChart({ instance, profileName }: { instance?: Instance; profileName?: string }) {
  const selectedProfile = profileName ?? instance?.profile_name ?? undefined
  const query = useQuery({
    queryKey: ['metric-concurrency', instance?.profile_id ?? selectedProfile ?? 'all'],
    queryFn: () => api.getMetricConcurrency(selectedProfile, instance?.profile_id ?? undefined),
    enabled: !instance || Boolean(selectedProfile),
    refetchInterval: 30_000, refetchIntervalInBackground: false, retry: false,
  })
  const groups = new Map<string, ConcurrencyPoint[]>()
  for (const point of query.data?.items ?? []) {
    const key = JSON.stringify([point.profile_name, point.model_name, point.engine_id])
    groups.set(key, [...(groups.get(key) ?? []), point])
  }
  const entries = [...groups.entries()]
  if (!entries.length) entries.push(['empty', []])
  return <section className="min-w-0 h-full" aria-label="Скорость при параллельных запросах">
    <div className="grid h-full gap-3">
      {entries.map(([key, unsorted]) => {
        const points = unsorted.length ? [
          { at: 0, decode: 0, perRequest: 0, prefill: null, ttft: null, requests: 0 },
          ...unsorted.filter((p) => p.requests > 0).sort((a, b) => a.requests - b.requests).map((p) => ({
            at: p.requests, decode: p.total_avg, perRequest: p.per_request_avg,
            prefill: null, ttft: null, requests: p.requests,
          })),
        ] : []
        const maximum = concurrencyScaleMaximum(Math.max(1, ...points.map((p) => p.decode)))
        return <div key={key} className="min-w-0 h-full">
          {groups.size > 1 && <p className="mb-1 truncate text-[11px] text-muted-foreground" title={unsorted[0].model_name ?? undefined}>{unsorted[0].profile_name} · {unsorted[0].model_name ?? unsorted[0].engine_id}</p>}
          <MetricLineChart title="Parallel" unit="tok/s" points={points} maximum={maximum} stretch
            ariaLabel={unsorted.length ? `Generation по параллельности: ${unsorted[0].profile_name}` : 'Generation по параллельности'}
            formatX={(value) => `${value} reqs`}
            series={[
              { key: 'decode', label: 'Total', color: '#cf9a78' },
              { key: 'perRequest', label: 'Per request', color: '#e9e5dc', dashed: true },
            ]} />
        </div>
      })}
    </div>
  </section>
}
