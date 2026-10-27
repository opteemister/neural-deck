import { useState, type FormEvent } from 'react'
import { Pencil, Plus, Trash2 } from 'lucide-react'
import { useQuery } from '@tanstack/react-query'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Dialog } from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Select } from '@/components/ui/select'
import { comfyApi, type ComfyConfig, type ComfyMediaType, type ComfyModel, type ComfyOperation, type ComfySource } from '@/lib/comfy-api'

const aliasPattern = /^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/
const operationLabels: Record<ComfyOperation, string> = { generate: 'Generate', edit: 'Edit', edit_masked: 'Mask', variations: 'Variations' }
type Notice = (tone: 'success' | 'error', message: string) => void
type SourceDraft = { original?: string; name: string; url: string; port: string }
type AliasDraft = { original?: string; name: string; model: ComfyModel }

function useConnection(source: string) {
  return useQuery({ queryKey: ['proxy-comfy-status', source], queryFn: () => comfyApi.getStatus(source), retry: false, staleTime: 10_000, refetchInterval: 15_000 })
}

export function ProxyComfyConfig({ config, mediaType, onSave, notify }: {
  config: ComfyConfig; mediaType: ComfyMediaType; onSave: (next: ComfyConfig) => Promise<unknown>; notify: Notice
}) {
  const [sourceDraft, setSourceDraft] = useState<SourceDraft | null>(null)
  const [aliasDraft, setAliasDraft] = useState<AliasDraft | null>(null)
  const [saving, setSaving] = useState(false)
  const operations: ComfyOperation[] = mediaType === 'image' ? ['generate', 'edit', 'edit_masked', 'variations'] : ['generate', 'edit']
  const sources = Object.entries(config.sources).sort(([left], [right]) => Number(right === 'local') - Number(left === 'local'))
  const aliases = Object.entries(config.models).filter(([, model]) => model.type === mediaType)

  async function persist(next: ComfyConfig) {
    setSaving(true)
    try { await onSave(next); return true }
    catch (error) { notify('error', error instanceof Error ? error.message : 'Не удалось сохранить изменения.'); return false }
    finally { setSaving(false) }
  }

  function editSource(name: string, source: ComfySource) {
    const url = new URL(source.base_url)
    const port = url.port || (url.protocol === 'https:' ? '443' : '80')
    url.port = ''
    setSourceDraft({ original: name, name, url: url.toString().replace(/\/$/, ''), port })
  }

  async function saveSource(event: FormEvent) {
    event.preventDefault()
    if (!sourceDraft || saving) return
    const { original, name: rawName, url: rawUrl, port } = sourceDraft
    const name = rawName.trim()
    if (!aliasPattern.test(name)) return notify('error', 'Укажите имя: буквы, цифры, точка, дефис или подчёркивание.')
    if (name !== original && name in config.sources) return notify('error', 'Подключение с таким именем уже существует.')
    let url: URL
    try {
      url = new URL(rawUrl.trim())
      if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password || url.search || url.hash) throw new Error()
      if (!/^\d+$/.test(port) || Number(port) < 1 || Number(port) > 65535) throw new Error()
      url.port = port
    } catch { return notify('error', 'Укажите HTTP/HTTPS URL и порт от 1 до 65535.') }
    const nextSources = { ...config.sources }
    const previous = original ? config.sources[original] : undefined
    if (original) delete nextSources[original]
    nextSources[name] = { ...previous, base_url: url.toString().replace(/\/$/, '') }
    const models = Object.fromEntries(Object.entries(config.models).map(([alias, model]) => [alias, original && model.source === original ? { ...model, source: name } : model]))
    if (await persist({ sources: nextSources, models })) setSourceDraft(null)
  }

  async function removeSource(name: string) {
    if (Object.values(config.models).some((model) => model.source === name)) return notify('error', 'Подключение используется алиасом. Сначала выберите для него другое подключение.')
    const nextSources = { ...config.sources }
    delete nextSources[name]
    await persist({ ...config, sources: nextSources })
  }

  async function saveAlias(event: FormEvent) {
    event.preventDefault()
    if (!aliasDraft || saving) return
    const name = aliasDraft.name.trim()
    if (!aliasPattern.test(name)) return notify('error', 'Укажите алиас: буквы, цифры, точка, дефис или подчёркивание.')
    if (name !== aliasDraft.original && name in config.models) return notify('error', 'Такой алиас уже существует.')
    if (!config.sources[aliasDraft.model.source]) return notify('error', 'Выберите подключение.')
    const models = { ...config.models }
    if (aliasDraft.original) delete models[aliasDraft.original]
    models[name] = aliasDraft.model
    if (await persist({ ...config, models })) setAliasDraft(null)
  }

  function setWorkflow(operation: ComfyOperation, path: string) {
    if (!aliasDraft) return
    const model = aliasDraft.model
    const workflows = { ...model.workflows }
    const bindings = { ...model.bindings }
    if (path) workflows[operation] = path
    else delete workflows[operation]
    // Explicit legacy bindings belong to the original saved graph.
    const original = aliasDraft.original ? config.models[aliasDraft.original] : undefined
    if (original?.source === model.source && original.workflows[operation] === path && original.bindings?.[operation]) bindings[operation] = original.bindings[operation]
    else if (path !== model.workflows[operation]) delete bindings[operation]
    setAliasDraft({ ...aliasDraft, model: { ...model, workflows, ...(model.bindings ? { bindings } : {}) } })
  }

  return <div className="space-y-6">
    <section aria-label="Подключения ComfyUI" className="border-b border-border pb-4">
      <div className="mb-2 flex items-center justify-between gap-3"><h2 className="text-sm font-medium">ComfyUI</h2><Button variant="ghost" size="sm" disabled={saving} aria-label="Добавить подключение" onClick={() => setSourceDraft({ name: '', url: 'http://', port: '8188' })}><Plus className="size-3.5" />Подключение</Button></div>
      <div className="divide-y divide-border">{sources.map(([name, source]) => <ConnectionRow key={name} name={name} source={source} disabled={saving} onEdit={() => editSource(name, source)} onDelete={() => void removeSource(name)} />)}</div>
    </section>
    <section aria-label={mediaType === 'image' ? 'Алиасы изображений' : 'Алиасы видео'}>
      <div className="mb-3 flex items-center justify-between gap-3"><h2 className="text-sm font-medium">Алиасы</h2><Button variant="primary" size="sm" disabled={saving || !sources.length} onClick={() => setAliasDraft({ name: '', model: { source: sources[0]?.[0] ?? '', type: mediaType, workflows: {} } })}><Plus className="size-3.5" />Новый алиас</Button></div>
      {!aliases.length && <p className="py-6 text-center text-xs text-muted-foreground">Алиасов пока нет</p>}
      <div className="space-y-3">{aliases.map(([alias, model]) => <AliasRow key={alias} alias={alias} model={model} operations={operations} disabled={saving} onEdit={() => setAliasDraft({ original: alias, name: alias, model: { ...model, workflows: { ...model.workflows } } })} onDelete={() => { const models = { ...config.models }; delete models[alias]; void persist({ ...config, models }) }} />)}</div>
    </section>
    <Dialog open={sourceDraft !== null} title={sourceDraft?.original ? 'Изменить подключение' : 'Новое подключение'} dismissible={!saving} onOpenChange={(open) => { if (!open && !saving) setSourceDraft(null) }}>
      {sourceDraft && <form className="space-y-4 p-4" onSubmit={(event) => void saveSource(event)}>
        <label className="block space-y-1.5 text-xs">Имя<Input autoFocus value={sourceDraft.name} disabled={saving} onChange={(event) => setSourceDraft({ ...sourceDraft, name: event.target.value })} placeholder="studio" /></label>
        <div className="grid grid-cols-[minmax(0,1fr)_6rem] gap-3">
          <label className="block space-y-1.5 text-xs">URL<Input value={sourceDraft.url} disabled={saving} onChange={(event) => setSourceDraft({ ...sourceDraft, url: event.target.value })} placeholder="http://192.168.1.10" /></label>
          <label className="block space-y-1.5 text-xs">Порт<Input inputMode="numeric" value={sourceDraft.port} disabled={saving} onChange={(event) => setSourceDraft({ ...sourceDraft, port: event.target.value })} /></label>
        </div>
        <div className="flex justify-end gap-2 pt-2"><Button variant="ghost" disabled={saving} onClick={() => setSourceDraft(null)}>Отмена</Button><Button type="submit" variant="primary" disabled={saving}>{saving ? 'Сохраняем…' : sourceDraft.original ? 'Сохранить' : 'Добавить подключение'}</Button></div>
      </form>}
    </Dialog>
    <Dialog open={aliasDraft !== null} title={(aliasDraft?.original ? 'Изменить алиас ' : 'Новый алиас ') + (mediaType === 'image' ? 'изображения' : 'видео')} dismissible={!saving} onOpenChange={(open) => { if (!open && !saving) setAliasDraft(null) }}>
      {aliasDraft && <form className="space-y-4 p-4" onSubmit={(event) => void saveAlias(event)}>
        <label className="block space-y-1.5 text-xs">Алиас<Input autoFocus value={aliasDraft.name} disabled={saving} onChange={(event) => setAliasDraft({ ...aliasDraft, name: event.target.value })} placeholder={mediaType === 'image' ? 'qwen-image-turbo' : 'wan-video'} /></label>
        <label className="block space-y-1.5 text-xs">Подключение<Select aria-label="Подключение" value={aliasDraft.model.source} disabled={saving} onChange={(event) => setAliasDraft({ ...aliasDraft, model: { ...aliasDraft.model, source: event.target.value } })}>
          {!config.sources[aliasDraft.model.source] && <option value={aliasDraft.model.source}>{aliasDraft.model.source || 'Выберите подключение'}</option>}
          {sources.map(([name]) => <option key={name} value={name}>{name}</option>)}
        </Select></label>
        <WorkflowFields source={aliasDraft.model.source} model={aliasDraft.model} operations={operations} disabled={saving} onChange={setWorkflow} />
        <div className="flex justify-end gap-2 pt-2"><Button variant="ghost" disabled={saving} onClick={() => setAliasDraft(null)}>Отмена</Button><Button type="submit" variant="primary" disabled={saving}>{saving ? 'Сохраняем…' : 'Сохранить'}</Button></div>
      </form>}
    </Dialog>
  </div>
}

function ConnectionRow({ name, source, disabled, onEdit, onDelete }: { name: string; source: ComfySource; disabled: boolean; onEdit: () => void; onDelete: () => void }) {
  const status = useConnection(name)
  const available = status.data?.available === true && !status.isError
  const isLocal = name === 'local' && source.base_url.replace(/\/$/, '') === 'http://127.0.0.1:8188'
  return <div className="flex min-w-0 flex-wrap items-center gap-2 py-2.5">
    <span className={`size-2 shrink-0 rounded-full ${available ? 'bg-[#6f9a72]' : 'bg-[#c9974f]'}`} />
    <span className="text-xs font-medium">{name}</span><span className="text-[11px] text-muted-foreground">{status.isPending ? 'Проверяем…' : available ? 'Подключено' : 'Не подключено'}</span>
    <span className="min-w-0 truncate font-mono text-[10px] text-muted-foreground" title={source.base_url}>{isLocal ? ':8188' : source.base_url}</span>
    {!isLocal && <div className="ml-auto flex gap-1"><Button variant="ghost" size="icon-sm" iconOnly disabled={disabled} aria-label={`Изменить подключение ${name}`} title="Изменить подключение" onClick={onEdit}><Pencil className="size-3.5" /></Button><Button variant="ghost" size="icon-sm" iconOnly disabled={disabled} aria-label={`Удалить подключение ${name}`} title="Удалить подключение" onClick={onDelete}><Trash2 className="size-3.5" /></Button></div>}
  </div>
}

function AliasRow({ alias, model, operations, disabled, onEdit, onDelete }: { alias: string; model: ComfyModel; operations: ComfyOperation[]; disabled: boolean; onEdit: () => void; onDelete: () => void }) {
  const status = useConnection(model.source)
  const available = status.data?.available === true && !status.isError
  return <div className="rounded-md border border-border">
    <div className="flex flex-wrap items-center gap-2 border-b border-border px-3 py-2.5"><h3 className="min-w-0 break-words font-mono text-xs">{alias}</h3><Badge className={available ? 'border-[#596c4b] text-[#b2c995]' : 'border-[#72544a] text-[#d0a390]'}>{available ? 'подключено' : 'не подключено'}</Badge><span className="text-[11px] text-muted-foreground">{model.source}</span><div className="ml-auto flex gap-1"><Button variant="ghost" size="icon-sm" iconOnly disabled={disabled} aria-label={`Изменить алиас ${alias}`} title="Изменить алиас" onClick={onEdit}><Pencil className="size-3.5" /></Button><Button variant="ghost" size="icon-sm" iconOnly disabled={disabled} aria-label={`Удалить алиас ${alias}`} title="Удалить алиас" onClick={onDelete}><Trash2 className="size-3.5" /></Button></div></div>
    <div className="flex flex-wrap gap-x-4 gap-y-1 px-3 py-3 text-[11px] text-muted-foreground">{operations.filter((operation) => model.workflows[operation]).map((operation) => <span key={operation}>{operationLabels[operation]}</span>)}{!operations.some((operation) => model.workflows[operation]) && <span>Workflow не выбран</span>}</div>
  </div>
}

function WorkflowFields({ source, model, operations, disabled, onChange }: { source: string; model: ComfyModel; operations: ComfyOperation[]; disabled: boolean; onChange: (operation: ComfyOperation, path: string) => void }) {
  const workflows = useQuery({ queryKey: ['proxy-comfy-workflows', source], queryFn: () => comfyApi.getWorkflows(source), enabled: Boolean(source), staleTime: 0, refetchOnMount: 'always', retry: false })
  const catalog = workflows.data?.items.map((item) => typeof item === 'string' ? item : item.path ?? item.name ?? '').filter(Boolean) ?? []
  return <div className="space-y-3">{operations.map((operation) => {
    const value = typeof model.workflows[operation] === 'string' ? model.workflows[operation] : ''
    const paths = [...new Set([...(value ? [value] : []), ...catalog])]
    return <label key={operation} className="block space-y-1.5 text-xs">{operationLabels[operation]}<Select aria-label={`Workflow ${operation}`} value={value} disabled={disabled || !source} onChange={(event) => onChange(operation, event.target.value)}><option value="">Не выбран</option>{paths.map((path) => <option key={path} value={path}>{path.replace(/^workflows\//, '')}</option>)}</Select></label>
  })}</div>
}
