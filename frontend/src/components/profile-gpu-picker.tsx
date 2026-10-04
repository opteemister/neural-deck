import { ChevronDown } from 'lucide-react'
import { Button } from '@/components/ui/button'
import type { GpuData } from '@/types/api'

const variable = 'CUDA_VISIBLE_DEVICES'
const orderVariable = 'CUDA_DEVICE_ORDER'

/** Edit the environment itself so manual changes and the picker cannot diverge. */
export function ProfileGpuPicker({ gpus, environment, onChange }: { gpus: GpuData[]; environment: string; onChange: (value: string) => void }) {
  const lines = environment.split('\n')
  const assignments = lines.filter((line) => line.trim().startsWith(`${variable}=`))
  const value = assignments.at(-1)?.trim().slice(variable.length + 1)
  const tokens = value?.split(',').map((token) => token.trim()).filter(Boolean) ?? []
  const all = value === undefined
  const none = value !== undefined && (value.trim() === '' || value.trim() === '-1')
  const matches = (gpu: GpuData, token: string) => token === String(gpu.index) || token === gpu.uuid || (token.startsWith('GPU-') && gpu.uuid.startsWith(token))
  const selected = (gpu: GpuData) => all || tokens.some((token) => matches(gpu, token))
  const unknown = tokens.filter((token) => token !== '-1' && !gpus.some((gpu) => matches(gpu, token)))
  const label = all ? 'Все доступные GPU' : none ? 'Без GPU' : tokens.map((token) => {
    const gpu = gpus.find((item) => matches(item, token))
    return gpu ? `${gpu.index} · ${gpu.name}` : token
  }).join(', ')

  function setValue(next: string | undefined) {
    const multiple = next === undefined ? gpus.length > 1 : new Set(next.split(',').map((token) => token.trim()).filter((token) => token && token !== '-1')).size > 1
    const remaining = lines.filter((line) => !line.trim().startsWith(`${variable}=`) && (!multiple || !line.trim().startsWith(`${orderVariable}=`)))
    if ((next !== undefined || multiple) && remaining.length === 1 && remaining[0] === '') remaining.length = 0
    if (next !== undefined) remaining.push(`${variable}=${next}`)
    if (multiple) remaining.push(`${orderVariable}=PCI_BUS_ID`)
    onChange(remaining.join('\n'))
  }

  function toggle(gpu: GpuData) {
    // Explicit selections use stable UUIDs; keep any manually entered identifiers.
    const current = all ? gpus.map((item) => item.uuid) : tokens.filter((token) => token !== '-1')
    const next = selected(gpu) ? current.filter((token) => !matches(gpu, token)) : [...current, gpu.uuid]
    setValue(next.length ? next.join(',') : '-1')
  }

  return <div>
    <p className="mb-1.5 text-xs font-medium">Видимые GPU</p>
    <details className="group relative">
      <summary className="flex h-10 cursor-pointer list-none items-center justify-between gap-3 rounded-md border border-input bg-muted px-3 text-xs [&::-webkit-details-marker]:hidden">
        <span className="min-w-0 truncate" title={label}>{label}</span>
        <ChevronDown className="size-4 shrink-0 text-muted-foreground transition-transform group-open:rotate-180" />
      </summary>
      <div className="absolute left-0 right-0 top-full z-50 mt-1 rounded-md border border-border bg-card p-2 shadow-lg">
        <div className="mb-1 flex gap-2">
          <Button type="button" variant="ghost" size="sm" onClick={() => setValue(undefined)}>Все GPU</Button>
          <Button type="button" variant="ghost" size="sm" onClick={() => setValue('-1')}>Без GPU</Button>
        </div>
        <div className="max-h-60 overflow-auto">
          {gpus.map((gpu) => <label key={gpu.uuid} className="flex cursor-pointer items-center gap-2 rounded-sm px-2 py-2 text-xs hover:bg-accent" title={gpu.uuid}>
            <input type="checkbox" checked={selected(gpu)} onChange={() => toggle(gpu)} className="size-4 shrink-0 accent-[#c58055]" />
            <span className="font-mono text-muted-foreground">{gpu.index}</span><span>{gpu.name}</span>
          </label>)}
          {gpus.length === 0 && <p className="px-2 py-2 text-xs text-muted-foreground">GPU не обнаружены.</p>}
          {unknown.map((token) => <label key={token} className="flex items-center gap-2 px-2 py-2 text-xs" title="Идентификатор из переменных окружения">
            <input type="checkbox" checked onChange={() => { const next = tokens.filter((item) => item !== token); setValue(next.length ? next.join(',') : '-1') }} className="size-4 accent-[#c58055]" />
            <span className="break-all font-mono text-muted-foreground">{token}</span>
          </label>)}
        </div>
      </div>
    </details>
  </div>
}
