import { useState, type FormEvent } from 'react'
import { Pencil, Plus, Trash2 } from 'lucide-react'
import { useQuery } from '@tanstack/react-query'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Dialog } from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { ProxyConnectionRow } from '@/components/proxy-connection-row'
import { Label } from '@/components/ui/label'
import { Select } from '@/components/ui/select'
import { comfyApi, type ComfyConfig, type ComfyMediaType, type ComfyModel, type ComfyOperation, type ComfySource } from '@/lib/comfy-api'

const aliasPattern = /^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/
const operationLabels: Record<ComfyOperation, string> = { generate: 'Generate', edit: 'Edit', edit_masked: 'Mask', variations: 'Variations' }
type Notice = (tone: 'success' | 'error', message: string) => void
type SourceDraft = { original?: string; name: string; url: string }
type AliasDraft = { original?: string; name: string; model: ComfyModel; megapixels: string; maxResolution: string; maxSeconds: string }

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
    setSourceDraft({ original: name, name, url: source.base_url })
  }

  async function saveSource(event: FormEvent) {
    event.preventDefault()
    if (!sourceDraft || saving) return
    const { original, name: rawName, url: rawUrl } = sourceDraft
    const name = rawName.trim()
    if (!aliasPattern.test(name)) return notify('error', 'Укажите имя: буквы, цифры, точка, дефис или подчёркивание.')
    if (name !== original && name in config.sources) return notify('error', 'Подключение с таким именем уже существует.')
    let url: URL
    try {
      url = new URL(rawUrl.trim())
      if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password || url.search || url.hash) throw new Error()
    } catch { return notify('error', 'Укажите полный HTTP/HTTPS адрес, например http://192.168.1.10:8188.') }
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
    const megapixels = Number(aliasDraft.megapixels)
    if (mediaType === 'image' && (!Number.isFinite(megapixels) || megapixels < 0.1 || megapixels > 128)) return notify('error', 'Укажите максимальное разрешение от 0.1 до 128 МП.')
    const maxResolution = Number(aliasDraft.maxResolution)
    const maxSeconds = Number(aliasDraft.maxSeconds)
    if (mediaType === 'video' && (!Number.isInteger(maxResolution) || maxResolution < 32 || maxResolution > 16384)) return notify('error', 'Укажите максимальное разрешение от 32 до 16384p.')
    if (mediaType === 'video' && (!Number.isInteger(maxSeconds) || maxSeconds < 1 || maxSeconds > 600)) return notify('error', 'Укажите максимальную длительность от 1 до 600 секунд.')
    const models = { ...config.models }
    if (aliasDraft.original) delete models[aliasDraft.original]
    models[name] = { ...aliasDraft.model, ...(mediaType === 'image' ? { max_megapixels: megapixels } : { max_resolution: maxResolution, max_seconds: maxSeconds }) }
    setSaving(true)
    try {
      const paths = [...new Set(Object.values(aliasDraft.model.workflows).filter((path): path is string => typeof path === 'string' && Boolean(path)))]
      const checks = await Promise.all(paths.map((path) => comfyApi.validateWorkflow(aliasDraft.model.source, path)))
      const invalid = checks.find((check) => check.usable === false)
      if (invalid) {
        const operation = operations.find((operation) => aliasDraft.model.workflows[operation] === invalid.path)
        return notify('error', (operation ? operationLabels[operation] + ': ' : '') + (invalid.message || 'Выберите API-экспорт workflow из ComfyUI.'))
      }
      if (await persist({ ...config, models })) setAliasDraft(null)
    } catch (error) { notify('error', error instanceof Error ? error.message : 'Не удалось проверить workflow.') }
    finally { setSaving(false) }
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
      <div className="mb-2 flex items-center justify-between gap-3"><h2 className="text-sm font-medium">ComfyUI</h2><Button variant="ghost" size="sm" disabled={saving} aria-label="Добавить подключение" onClick={() => setSourceDraft({ name: '', url: '' })}><Plus className="size-3.5" />Подключение</Button></div>
      <div className="divide-y divide-border">{sources.map(([name, source]) => <ConnectionRow key={name} name={name} source={source} disabled={saving} onEdit={() => editSource(name, source)} onDelete={() => void removeSource(name)} />)}</div>
    </section>
    <section aria-label={mediaType === 'image' ? 'Алиасы изображений' : 'Алиасы видео'}>
      <div className="mb-3 flex items-center justify-between gap-3"><h2 className="text-sm font-medium">Алиасы</h2><Button variant="primary" size="sm" disabled={saving || !sources.length} onClick={() => setAliasDraft({ name: '', megapixels: '4', maxResolution: '768', maxSeconds: '15', model: { source: sources[0]?.[0] ?? '', type: mediaType, workflows: {} } })}><Plus className="size-3.5" />Новый алиас</Button></div>
      {!aliases.length && <p className="py-6 text-center text-xs text-muted-foreground">Алиасов пока нет</p>}
      <div className="space-y-3">{aliases.map(([alias, model]) => <AliasRow key={alias} alias={alias} model={model} disabled={saving} onEdit={() => setAliasDraft({ original: alias, name: alias, megapixels: String(model.max_megapixels ?? 4), maxResolution: String(model.max_resolution ?? 768), maxSeconds: String(model.max_seconds ?? 15), model: { ...model, workflows: { ...model.workflows } } })} onDelete={() => { const models = { ...config.models }; delete models[alias]; void persist({ ...config, models }) }} />)}</div>
    </section>
    <Dialog open={sourceDraft !== null} title={sourceDraft?.original ? 'Изменить подключение' : 'Новое подключение'} dismissible={!saving} onOpenChange={(open) => { if (!open && !saving) setSourceDraft(null) }}>
      {sourceDraft && <form className="space-y-5 p-4" onSubmit={(event) => void saveSource(event)}>
        <div><Label className="mb-1.5 block" htmlFor="comfy-source-name">Имя</Label><Input id="comfy-source-name" autoFocus value={sourceDraft.name} disabled={saving} onChange={(event) => setSourceDraft({ ...sourceDraft, name: event.target.value })} placeholder="studio" /></div>
        <div><Label className="mb-1.5 block" htmlFor="comfy-source-url">URL</Label><Input id="comfy-source-url" value={sourceDraft.url} disabled={saving} onChange={(event) => setSourceDraft({ ...sourceDraft, url: event.target.value })} placeholder="http://192.168.1.10:8188" /></div>
        <div className="flex justify-end gap-2 border-t border-border pt-4"><Button variant="ghost" disabled={saving} onClick={() => setSourceDraft(null)}>Отмена</Button><Button type="submit" variant="primary" disabled={saving}>{saving ? 'Сохраняем…' : sourceDraft.original ? 'Сохранить' : 'Добавить подключение'}</Button></div>
      </form>}
    </Dialog>
    <Dialog open={aliasDraft !== null} title={(aliasDraft?.original ? 'Изменить алиас ' : 'Новый алиас ') + (mediaType === 'image' ? 'изображения' : 'видео')} dismissible={!saving} onOpenChange={(open) => { if (!open && !saving) setAliasDraft(null) }}>
      {aliasDraft && <form className="space-y-5 p-4" onSubmit={(event) => void saveAlias(event)}>
        <div className="grid grid-cols-[repeat(auto-fit,minmax(min(100%,12rem),1fr))] gap-4">
        <div className="min-w-0"><Label className="mb-1.5 block" htmlFor="comfy-alias-name">Алиас</Label><Input id="comfy-alias-name" autoFocus value={aliasDraft.name} disabled={saving} onChange={(event) => setAliasDraft({ ...aliasDraft, name: event.target.value })} placeholder={mediaType === 'image' ? 'qwen-image-turbo' : 'wan-video'} /></div>
        <div className="min-w-0"><Label className="mb-1.5 block" htmlFor="comfy-alias-source">Подключение</Label><Select id="comfy-alias-source" aria-label="Подключение" value={aliasDraft.model.source} disabled={saving} onChange={(event) => setAliasDraft({ ...aliasDraft, model: { ...aliasDraft.model, source: event.target.value } })}>
          {!config.sources[aliasDraft.model.source] && <option value={aliasDraft.model.source}>{aliasDraft.model.source || 'Выберите подключение'}</option>}
          {sources.map(([name]) => <option key={name} value={name}>{name}</option>)}
        </Select></div>
        </div>
        <WorkflowFields source={aliasDraft.model.source} model={aliasDraft.model} operations={operations} disabled={saving} onChange={setWorkflow} />
        {mediaType === 'image' && <div className="grid grid-cols-[minmax(0,1fr)_6rem] items-center gap-4 border-t border-border pt-4"><Label htmlFor="comfy-alias-megapixels">Максимум, МП</Label><Input id="comfy-alias-megapixels" type="number" min="0.1" max="128" step="any" required value={aliasDraft.megapixels} disabled={saving} onChange={(event) => setAliasDraft({ ...aliasDraft, megapixels: event.target.value })} /></div>}
        {mediaType === 'video' && <div className="grid grid-cols-2 gap-4 border-t border-border pt-4">
          <div><Label className="mb-1.5 block" htmlFor="comfy-video-resolution">Макс. разрешение, p</Label><Input id="comfy-video-resolution" type="number" min="32" max="16384" step="1" required value={aliasDraft.maxResolution} disabled={saving} onChange={(event) => setAliasDraft({ ...aliasDraft, maxResolution: event.target.value })} /></div>
          <div><Label className="mb-1.5 block" htmlFor="comfy-video-seconds">Макс. длительность, с</Label><Input id="comfy-video-seconds" type="number" min="1" max="600" step="1" required value={aliasDraft.maxSeconds} disabled={saving} onChange={(event) => setAliasDraft({ ...aliasDraft, maxSeconds: event.target.value })} /></div>
        </div>}
        <div className="flex justify-end gap-2 border-t border-border pt-4"><Button variant="ghost" disabled={saving} onClick={() => setAliasDraft(null)}>Отмена</Button><Button type="submit" variant="primary" disabled={saving}>{saving ? 'Сохраняем…' : 'Сохранить'}</Button></div>
      </form>}
    </Dialog>
  </div>
}

function ConnectionRow({ name, source, disabled, onEdit, onDelete }: { name: string; source: ComfySource; disabled: boolean; onEdit: () => void; onDelete: () => void }) {
  const status = useConnection(name)
  const available = status.data?.available === true && !status.isError
  const isLocal = name === 'local' && source.base_url.replace(/\/$/, '') === 'http://127.0.0.1:8188'
  return <ProxyConnectionRow name={name} url={isLocal ? ':8188' : source.base_url} pending={status.isPending} available={available} disabled={disabled} onEdit={isLocal ? undefined : onEdit} onDelete={isLocal ? undefined : onDelete} />
}

function AliasRow({ alias, model, disabled, onEdit, onDelete }: { alias: string; model: ComfyModel; disabled: boolean; onEdit: () => void; onDelete: () => void }) {
  const status = useConnection(model.source)
  const available = status.data?.available === true && !status.isError
  return <div className="rounded-md border border-border">
    <div className="flex flex-wrap items-center gap-2 px-3 py-2.5"><h3 className="min-w-0 break-words font-mono text-xs">{alias}</h3><Badge className={available ? 'border-[#596c4b] text-[#b2c995]' : 'border-[#72544a] text-[#d0a390]'}>{available ? 'подключено' : 'не подключено'}</Badge><span className="text-[11px] text-muted-foreground">{model.source}</span><div className="ml-auto flex gap-1"><Button variant="ghost" size="icon-sm" iconOnly disabled={disabled} aria-label={`Изменить алиас ${alias}`} title="Изменить алиас" onClick={onEdit}><Pencil className="size-3.5" /></Button><Button variant="ghost" size="icon-sm" iconOnly disabled={disabled} aria-label={`Удалить алиас ${alias}`} title="Удалить алиас" onClick={onDelete}><Trash2 className="size-3.5" /></Button></div></div>
  </div>
}

function WorkflowFields({ source, model, operations, disabled, onChange }: { source: string; model: ComfyModel; operations: ComfyOperation[]; disabled: boolean; onChange: (operation: ComfyOperation, path: string) => void }) {
  const workflows = useQuery({ queryKey: ['proxy-comfy-workflows', source], queryFn: () => comfyApi.getWorkflows(source), enabled: Boolean(source), staleTime: 0, refetchOnMount: 'always', retry: false })
  const catalog = workflows.data?.items.map((item) => typeof item === 'string' ? item : item.path ?? item.name ?? '').filter(Boolean) ?? []
  return <div className="space-y-3 border-t border-border pt-5">{operations.map((operation) => {
    const value = typeof model.workflows[operation] === 'string' ? model.workflows[operation] : ''
    const paths = [...new Set([...(value ? [value] : []), ...catalog])]
    return <div key={operation} className="grid grid-cols-[5rem_minmax(0,1fr)] items-center gap-4"><Label htmlFor={`comfy-workflow-${operation}`}>{operationLabels[operation]}</Label><Select id={`comfy-workflow-${operation}`} aria-label={`Workflow ${operation}`} value={value} disabled={disabled || !source} onChange={(event) => onChange(operation, event.target.value)}><option value="">Не выбран</option>{paths.map((path) => <option key={path} value={path}>{path.replace(/^workflows\//, '')}</option>)}</Select></div>
  })}</div>
}
