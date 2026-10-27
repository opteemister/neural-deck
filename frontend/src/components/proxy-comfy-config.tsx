import { useEffect, useMemo, useState } from 'react'
import { Plus, Trash2 } from 'lucide-react'
import { useQuery } from '@tanstack/react-query'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Select } from '@/components/ui/select'
import {
  COMFY_BINDING_FIELDS,
  comfyApi,
  type ComfyBinding,
  type ComfyBindingField,
  type ComfyConfig,
  type ComfyMediaType,
  type ComfyModel,
  type ComfyOperation,
  type ComfySource,
  type WorkflowItem,
} from '@/lib/comfy-api'

const aliasPattern = /^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/
const operationLabels: Record<ComfyOperation, string> = {
  generate: 'Generate',
  edit: 'Edit',
  edit_masked: 'Edit masked',
  variations: 'Variations',
}
const bindingLabels: Record<ComfyBindingField, string> = {
  prompt: 'prompt', image: 'image', mask: 'mask', 'size.width': 'size.width',
  'size.height': 'size.height', n: 'n', quality: 'quality', background: 'background',
  output_format: 'output_format', output_compression: 'output_compression', seconds: 'seconds',
  reference: 'reference',
}

type Notice = (tone: 'success' | 'error', message: string) => void

export function ProxyComfyConfig({ config, mediaType, onChange, notify }: {
  config: ComfyConfig
  mediaType: ComfyMediaType
  onChange: (next: ComfyConfig) => void
  notify: Notice
}) {
  const [sourceName, setSourceName] = useState('')
  const [sourceUrl, setSourceUrl] = useState('')
  const [sourceContext, setSourceContext] = useState('')
  const [aliasName, setAliasName] = useState('')
  const [aliasSource, setAliasSource] = useState('')

  const sourceEntries = Object.entries(config.sources)
  const modelEntries = Object.entries(config.models).filter(([, model]) => model.type === mediaType)
  const operations: ComfyOperation[] = mediaType === 'image'
    ? ['generate', 'edit', 'edit_masked', 'variations']
    : ['generate', 'edit']

  function addSource() {
    const name = sourceName.trim()
    const baseUrl = sourceUrl.trim()
    if (!name || !baseUrl) return notify('error', 'Укажите имя и URL источника ComfyUI.')
    if (!aliasPattern.test(name) || name in config.sources) return notify('error', 'Имя источника должно быть уникальным ASCII-идентификатором.')
    const source: ComfySource = { base_url: baseUrl }
    if (sourceContext.trim()) source.user_context = sourceContext.trim()
    onChange({ ...config, sources: { ...config.sources, [name]: source } })
    setSourceName(''); setSourceUrl(''); setSourceContext('')
  }

  function updateSource(name: string, source: ComfySource) {
    onChange({ ...config, sources: { ...config.sources, [name]: source } })
  }

  function renameSource(current: string, nextName: string) {
    const next = nextName.trim()
    if (!next || next === current) return
    if (!aliasPattern.test(next) || next in config.sources) return notify('error', 'Имя источника должно быть уникальным ASCII-идентификатором.')
    const nextSources = { ...config.sources }
    nextSources[next] = nextSources[current]
    delete nextSources[current]
    const nextModels = Object.fromEntries(Object.entries(config.models).map(([alias, model]) => [
      alias, model.source === current ? { ...model, source: next } : model,
    ]))
    onChange({ sources: nextSources, models: nextModels })
  }

  function removeSource(name: string) {
    if (Object.values(config.models).some((model) => model.source === name)) return notify('error', 'Сначала перенесите или удалите алиасы этого источника.')
    const nextSources = { ...config.sources }
    delete nextSources[name]
    onChange({ ...config, sources: nextSources })
  }

  function addModel() {
    const name = aliasName.trim()
    if (!aliasPattern.test(name)) return notify('error', 'Алиас должен содержать 1–128 ASCII-символов.')
    if (config.models[name]) return notify('error', 'Такой ComfyUI-алиас уже существует.')
    if (!aliasSource || !config.sources[aliasSource]) return notify('error', 'Выберите источник для алиаса.')
    const model: ComfyModel = { source: aliasSource, type: mediaType, workflows: {} }
    onChange({ ...config, models: { ...config.models, [name]: model } })
    setAliasName('')
  }

  function updateModel(alias: string, patch: Partial<ComfyModel>) {
    onChange({ ...config, models: { ...config.models, [alias]: { ...config.models[alias], ...patch } } })
  }

  function renameModel(current: string, nextName: string) {
    const next = nextName.trim()
    if (!next || next === current) return
    if (!aliasPattern.test(next)) return notify('error', 'Алиас должен содержать 1–128 ASCII-символов.')
    if (config.models[next]) return notify('error', 'Такой ComfyUI-алиас уже существует.')
    const models = { ...config.models, [next]: config.models[current] }
    delete models[current]
    onChange({ ...config, models })
  }

  function removeModel(alias: string) {
    const models = { ...config.models }
    delete models[alias]
    onChange({ ...config, models })
  }

  return <div className="space-y-4">
    <Card>
      <CardHeader><CardTitle>Источники ComfyUI</CardTitle></CardHeader>
      <CardContent className="space-y-3">
        {sourceEntries.length === 0 && <p className="text-xs text-muted-foreground">Источники ещё не настроены. URL не подставляется автоматически.</p>}
        {sourceEntries.map(([name, source]) => <SourceRow key={name} name={name} source={source} onChange={(next) => updateSource(name, next)} onRename={(next) => renameSource(name, next)} onDelete={() => removeSource(name)} />)}
        <div className="rounded-md border border-dashed border-border p-3">
          <p className="mb-2 text-xs font-medium text-foreground">Добавить источник</p>
          <div className="grid gap-2 md:grid-cols-3">
            <Input aria-label="Новое имя источника" value={sourceName} onChange={(event) => setSourceName(event.target.value)} placeholder="local-comfy" />
            <Input aria-label="URL нового источника" value={sourceUrl} onChange={(event) => setSourceUrl(event.target.value)} placeholder="http://127.0.0.1:8188" />
            <Input aria-label="Контекст нового источника" value={sourceContext} onChange={(event) => setSourceContext(event.target.value)} placeholder="user context (необязательно)" />
          </div>
          <Button className="mt-2" variant="secondary" size="sm" onClick={addSource}><Plus className="size-3.5" />Добавить источник</Button>
        </div>
      </CardContent>
    </Card>

    <Card>
      <CardHeader><CardTitle>{mediaType === 'image' ? 'Модели изображений' : 'Модели видео'}</CardTitle></CardHeader>
      <CardContent className="space-y-3">
        {modelEntries.length === 0 && <p className="text-xs text-muted-foreground">Алиасы {mediaType === 'image' ? 'изображений' : 'видео'} ещё не настроены.</p>}
        {modelEntries.map(([alias, model]) => <ComfyAliasCard key={alias} alias={alias} model={model} sources={config.sources} operations={operations} onChange={(patch) => updateModel(alias, patch)} onRename={(next) => renameModel(alias, next)} onDelete={() => removeModel(alias)} />)}
        <div className="rounded-md border border-dashed border-border p-3">
          <p className="mb-2 text-xs font-medium text-foreground">Добавить алиас</p>
          <div className="grid gap-2 md:grid-cols-[minmax(0,1fr)_minmax(0,1fr)_auto]">
            <Input aria-label="Новый ComfyUI-алиас" value={aliasName} onChange={(event) => setAliasName(event.target.value)} placeholder="image-model" />
            <Select aria-label="Источник нового алиаса" value={aliasSource} onChange={(event) => setAliasSource((event.target as HTMLSelectElement).value)}>
              <option value="">Выберите источник</option>
              {sourceEntries.map(([name]) => <option key={name} value={name}>{name}</option>)}
            </Select>
            <Button variant="secondary" onClick={addModel} disabled={sourceEntries.length === 0}><Plus className="size-3.5" />Добавить</Button>
          </div>
        </div>
      </CardContent>
    </Card>
  </div>
}

function SourceRow({ name, source, onChange, onRename, onDelete }: { name: string; source: ComfySource; onChange: (source: ComfySource) => void; onRename: (name: string) => void; onDelete: () => void }) {
  const [draftName, setDraftName] = useState(name)
  useEffect(() => setDraftName(name), [name])
  return <div className="rounded-md border border-border bg-secondary/30 p-3">
    <div className="grid gap-2 md:grid-cols-[minmax(8rem,0.45fr)_minmax(0,1fr)_minmax(0,1fr)_auto]">
      <Input aria-label={`Имя источника ${name}`} value={draftName} onChange={(event) => setDraftName(event.target.value)} onBlur={() => onRename(draftName)} />
      <Input aria-label={`URL источника ${name}`} value={source.base_url} onChange={(event) => onChange({ ...source, base_url: event.target.value })} placeholder="http://127.0.0.1:8188" />
      <Input aria-label={`Контекст источника ${name}`} value={source.user_context ?? ''} onChange={(event) => onChange({ ...source, user_context: event.target.value || undefined })} placeholder="user context (необязательно)" />
      <Button variant="danger" size="icon-sm" iconOnly aria-label={`Удалить источник ${name}`} title="Удалить источник" onClick={onDelete}><Trash2 className="size-3.5" /></Button>
    </div>
  </div>
}

function ComfyAliasCard({ alias, model, sources, operations, onChange, onRename, onDelete }: { alias: string; model: ComfyModel; sources: Record<string, ComfySource>; operations: ComfyOperation[]; onChange: (patch: Partial<ComfyModel>) => void; onRename: (name: string) => void; onDelete: () => void }) {
  const [draftAlias, setDraftAlias] = useState(alias)
  useEffect(() => setDraftAlias(alias), [alias])
  const sourceMissing = !sources[model.source]
  return <div className="rounded-md border border-border bg-secondary/30 p-3">
    <div className="flex flex-wrap items-center gap-2">
      <Input className="max-w-64" aria-label={`Имя ComfyUI-алиаса ${alias}`} value={draftAlias} onChange={(event) => setDraftAlias(event.target.value)} onBlur={() => onRename(draftAlias)} />
      <Select className="min-w-48" aria-label={`Источник алиаса ${alias}`} value={model.source} onChange={(event) => onChange({ source: (event.target as HTMLSelectElement).value })}>
        {sourceMissing && <option value={model.source}>{model.source} (отсутствует)</option>}
        {Object.keys(sources).map((name) => <option key={name} value={name}>{name}</option>)}
      </Select>
      {sourceMissing && <Badge className="border-[#72544a] text-[#d0a390]">источник не найден</Badge>}
      <Button className="ml-auto" variant="danger" size="icon-sm" iconOnly aria-label={`Удалить ComfyUI-алиас ${alias}`} title="Удалить алиас" onClick={onDelete}><Trash2 className="size-3.5" /></Button>
    </div>
    <div className="mt-3 grid gap-3 lg:grid-cols-2">
      {operations.map((operation) => <WorkflowField key={operation} source={model.source} operation={operation} value={model.workflows[operation] as string | undefined} onChange={(value) => onChange({ workflows: { ...model.workflows, [operation]: value || undefined } })} />)}
    </div>
    <div className="mt-3 space-y-2">
      {operations.map((operation) => <BindingDetails key={operation} operation={operation} bindings={model.bindings?.[operation]} onChange={(bindings) => onChange({ bindings: { ...model.bindings, [operation]: bindings } })} />)}
    </div>
  </div>
}

function WorkflowField({ source, operation, value, onChange }: { source: string; operation: ComfyOperation; value?: string; onChange: (value: string) => void }) {
  const [search, setSearch] = useState('')
  const workflows = useQuery({ queryKey: ['proxy-comfy-workflows', source], queryFn: () => comfyApi.getWorkflows(source), enabled: Boolean(source), staleTime: 0, refetchOnMount: 'always', retry: false })
  const paths = useMemo(() => {
    const values = workflows.data?.items.map(workflowPath) ?? []
    if (value && !values.includes(value)) values.unshift(value)
    const needle = search.trim().toLocaleLowerCase()
    return values.filter((path, index) => values.indexOf(path) === index && (!needle || path.toLocaleLowerCase().includes(needle)))
  }, [search, value, workflows.data?.items])
  const available = workflows.data?.available && !workflows.isError
  const cached = workflows.data?.cached || workflows.isError && workflows.data
  const status = available ? 'API доступен' : cached ? 'API недоступен · кэш' : workflows.data || workflows.isError ? 'API недоступен' : undefined
  return <div className="space-y-1.5">
    <div className="flex items-center gap-2"><label className="text-xs text-foreground">{operationLabels[operation]}</label>{status && <Badge className={available ? 'border-[#596c4b] text-[#b2c995]' : 'border-[#72544a] text-[#d0a390]'}>{status}</Badge>}</div>
    <Input aria-label={`Поиск workflow ${operation}`} value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Поиск workflow по имени" />
    <Select aria-label={`Workflow ${operation}`} value={value ?? ''} onChange={(event) => onChange((event.target as HTMLSelectElement).value)} disabled={!source}>
      <option value="">Не выбран</option>
      {paths.map((path) => <option key={path} value={path}>{path}</option>)}
    </Select>
    {workflows.isError && <p className="text-[11px] text-muted-foreground">Список недоступен; сохранённый путь остаётся доступен для выбора.</p>}
  </div>
}

function workflowPath(item: WorkflowItem): string {
  return typeof item === 'string' ? item : item.path ?? item.name ?? ''
}

function BindingDetails({ operation, bindings, onChange }: { operation: ComfyOperation; bindings?: Partial<Record<ComfyBindingField, ComfyBinding>>; onChange: (bindings: Partial<Record<ComfyBindingField, ComfyBinding>>) => void }) {
  function setBinding(field: ComfyBindingField, key: keyof ComfyBinding, value: string) {
    const current = bindings?.[field] ?? { node: '', input: '' }
    const next = { ...current, [key]: value }
    const nextBindings = { ...bindings }
    if (!next.node && !next.input) delete nextBindings[field]
    else nextBindings[field] = next
    onChange(nextBindings)
  }
  return <details className="rounded-md border border-border px-3 py-2">
    <summary className="cursor-pointer text-xs text-foreground">Входы API · {operationLabels[operation]}</summary>
    <div className="mt-2 space-y-2">
      {COMFY_BINDING_FIELDS.map((field) => <div key={field} className="grid grid-cols-[minmax(5rem,0.45fr)_minmax(0,1fr)_minmax(0,1fr)] items-center gap-2"><span className="font-mono text-[10px] text-muted-foreground">{bindingLabels[field]}</span><Input aria-label={`${operation} ${field} node`} value={bindings?.[field]?.node ?? ''} onChange={(event) => setBinding(field, 'node', event.target.value)} placeholder="node" /><Input aria-label={`${operation} ${field} input`} value={bindings?.[field]?.input ?? ''} onChange={(event) => setBinding(field, 'input', event.target.value)} placeholder="input" /></div>)}
    </div>
  </details>
}
