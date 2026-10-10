import { useQuery } from '@tanstack/react-query'
import { api } from '@/lib/api'

type Filters = { profile_name?: string; profile_id?: string; pid?: number; scope?: 'profile' | 'all' }
export function useDailyMetricAggregates(historyKey: string, filters: Filters) {
  const query = useQuery({
    queryKey: ['metrics-aggregates', historyKey],
    queryFn: () => api.getMetricAggregates(filters),
    refetchInterval: 30_000, refetchIntervalInBackground: false, retry: false,
  })
  return new Map(Object.entries(query.data?.aggregates ?? {}).map(([name, aggregate]) => [name, {
    sum: aggregate.avg, weight: 1, max: aggregate.max,
  }]))
}
