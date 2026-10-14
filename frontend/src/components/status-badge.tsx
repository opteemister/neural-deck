import { CircleAlert, CircleCheck, CircleDot, LoaderCircle, PauseCircle } from 'lucide-react'
import { Badge } from '@/components/ui/badge'
import { stateLabel } from '@/lib/utils'

const icons = {
  ok: CircleCheck,
  ready: CircleCheck,
  running: CircleCheck,
  starting: LoaderCircle,
  stopping: LoaderCircle,
  degraded: CircleDot,
  error: CircleAlert,
  broken: CircleAlert,
  failed: CircleAlert,
  exited: PauseCircle,
  unknown: CircleDot,
  candidate: CircleDot,
  succeeded: CircleCheck,
  queued: LoaderCircle,
  unavailable: CircleAlert,
  stale: CircleDot,
  downloaded: CircleCheck,
  incomplete: CircleAlert,
} as const

export function StatusBadge({ state, title, onClick, context = 'runtime' }: { state: string; title?: string; onClick?: () => void; context?: 'runtime' | 'installation' }) {
  const Icon = icons[state as keyof typeof icons] ?? CircleDot
  const isProblem = ['error', 'broken', 'failed'].includes(state)
  const isActive = ['ok', 'ready', 'running', 'succeeded', 'downloaded'].includes(state)
  const label = stateLabel(state, context)
  const runtimeActive = context === 'runtime' && ['ready', 'running'].includes(state)
  const runtimePending = context === 'runtime' && ['starting', 'stopping'].includes(state)
  const badge = <Badge title={onClick ? undefined : title} className={isProblem ? 'border-[#81554c] text-[#d09b87]' : runtimeActive ? 'border-[#6f9a72]/50 text-[#6f9a72]' : runtimePending ? 'border-[#c9974f]/50 text-[#c9974f]' : isActive ? 'border-[#6e5b4e] text-[#cf9a78]' : ''}><Icon className="mr-1 size-3" aria-hidden="true" />{label}</Badge>
  return onClick ? <button type="button" onClick={onClick} title={title ?? 'Открыть логи'} aria-label={`${label}: ${title ?? 'открыть логи'}`} className="rounded-sm hover:bg-secondary focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-copper">{badge}</button> : badge
}
