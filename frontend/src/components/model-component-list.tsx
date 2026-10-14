import type { ReactNode } from 'react'
import { Select } from '@/components/ui/select'
import { formatBytes } from '@/lib/utils'
import type { ModelComponent, ComponentVariant } from '@/lib/model-components'

export function ModelComponentList({ components, selected, onSelect, checked, onCheck, action }: {
  components: ModelComponent[]
  selected: Record<string, string>
  onSelect: (component: string, variant: string) => void
  checked?: string[]
  onCheck?: (component: string) => void
  action?: (component: ModelComponent, variant: ComponentVariant) => ReactNode
}) {
  const categories = [...new Set(components.map((component) => component.category))]
  return <div className="space-y-3">{categories.map((category) => <section key={category} className="space-y-2">
    <p className="text-xs font-medium">{category}</p>
    {components.filter((component) => component.category === category).map((component) => {
      const variant = component.variants.find((item) => item.id === selected[component.id]) ?? component.variants[0]
      const variantLabel = (item: ComponentVariant) => component.variants.filter((other) => other.precision === item.precision).length > 1
        ? `${item.precision} · ${item.files[0].path.split('.').at(-1)?.toUpperCase()}${item.id.endsWith('/sharded') ? ' · части' : ''}` : item.precision
      return <div key={component.id} className="flex flex-wrap items-center gap-3 rounded-md border border-border bg-secondary px-3 py-2">
        {onCheck && <input type="checkbox" aria-label={`Выбрать ${component.name}`} checked={checked?.includes(component.id) ?? false} onChange={() => onCheck(component.id)} className="size-4 accent-[#c58055]" />}
        <div className="min-w-0 flex-1"><p className="break-all font-mono text-xs" title={component.id}>{component.name}</p><details className="mt-1 text-[11px] text-muted-foreground"><summary className="cursor-pointer">{variant.files.length} файлов · {formatBytes(variant.size)}{!variant.complete && ' · не все части'}{!onCheck && ` · ${component.variants.length} вариантов`}</summary><ul className="mt-2 space-y-1 break-all font-mono">{variant.files.map((file) => <li key={file.path}>{file.path}</li>)}</ul></details></div>
        <div className="w-44 shrink-0">{component.variants.length > 1 ? <Select variant="inline" aria-label={`Точность / квант: ${component.name}`} value={variant.id} onChange={(event) => onSelect(component.id, event.target.value)}>{component.variants.map((item) => <option key={item.id} value={item.id}>{variantLabel(item)} · {formatBytes(item.size)}</option>)}</Select> : <span className="text-xs text-muted-foreground">{variant.precision}</span>}</div>
        {action?.(component, variant)}
      </div>
    })}
  </section>)}</div>
}
