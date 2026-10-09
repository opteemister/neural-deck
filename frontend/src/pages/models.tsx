import { marked } from 'marked'
import { FormEvent, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { AlertTriangle, ArrowLeft, ChevronDown, ChevronUp, Download, ExternalLink, FolderOpen, Heart, RefreshCw, Search, Star, Trash2, TrendingUp, X } from 'lucide-react'
import { useSearchParams } from 'react-router-dom'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { Card, CardContent, CardTitle } from '@/components/ui/card'
import { IconButton } from '@/components/ui/icon-button'
import { Input } from '@/components/ui/input'
import { Progress } from '@/components/ui/progress'
import { PageHeader } from '@/components/page-header'
import { QueryEmpty, QueryError, QueryLoading } from '@/components/query-state'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { api, queryKeys } from '@/lib/api'
import { formatBytes } from '@/lib/utils'
import type { HfFile, HfModelItem, HfRepoData, LocalModelArtifact, Operation } from '@/types/api'

type ModelView = 'local' | 'huggingface'
type RepoTab = 'description' | 'files' | 'quantizations'
type HfTrendingGroup = 'all' | 'text' | 'gguf' | 'vllm' | 'diffusers' | 'embeddings'
type RepoNavigation = { repo_id: string; revision?: string; tab: RepoTab }

type HfBadgeGroup = 'format' | 'library' | 'task' | 'capability' | 'generation'
type HfBadgeDefinition = { group: HfBadgeGroup; label: string; values: string[] }

const HF_BADGE_DEFINITIONS: HfBadgeDefinition[] = [
  { group: 'format', label: 'SafeTensors', values: ['safetensors'] },
  { group: 'format', label: 'GGUF', values: ['gguf'] },
  { group: 'format', label: 'GGML', values: ['ggml'] },
  { group: 'format', label: 'PyTorch', values: ['pytorch'] },
  { group: 'format', label: 'TensorFlow', values: ['tensorflow'] },
  { group: 'format', label: 'Flax', values: ['flax'] },
  { group: 'format', label: 'ONNX', values: ['onnx'] },
  { group: 'format', label: 'MLX', values: ['mlx'] },
  { group: 'format', label: 'OpenVINO', values: ['openvino'] },
  { group: 'format', label: 'Core ML', values: ['coreml'] },
  { group: 'format', label: 'TFLite', values: ['tflite'] },
  { group: 'format', label: 'AWQ', values: ['awq'] },
  { group: 'format', label: 'GPTQ', values: ['gptq'] },
  { group: 'format', label: 'EXL2', values: ['exl2'] },
  { group: 'library', label: 'Transformers', values: ['transformers'] },
  { group: 'library', label: 'Diffusers', values: ['diffusers'] },
  { group: 'library', label: 'llama.cpp', values: ['llama.cpp', 'llamacpp'] },
  { group: 'library', label: 'vLLM', values: ['vllm'] },
  { group: 'library', label: 'Sentence Transformers', values: ['sentence-transformers'] },
  { group: 'library', label: 'PEFT', values: ['peft'] },
  { group: 'task', label: 'Text generation', values: ['text-generation'] },
  { group: 'task', label: 'Image-text-to-text', values: ['image-text-to-text'] },
  { group: 'task', label: 'Text classification', values: ['text-classification'] },
  { group: 'task', label: 'Text-to-image', values: ['text-to-image'] },
  { group: 'task', label: 'Image-to-image', values: ['image-to-image'] },
  { group: 'task', label: 'Image-to-video', values: ['image-to-video'] },
  { group: 'task', label: 'Image-text-to-video', values: ['image-text-to-video'] },
  { group: 'task', label: 'Video-to-video', values: ['video-to-video'] },
  { group: 'task', label: 'Speech recognition', values: ['automatic-speech-recognition'] },
  { group: 'task', label: 'Text-to-speech', values: ['text-to-speech'] },
  { group: 'task', label: 'Feature extraction', values: ['feature-extraction'] },
  { group: 'task', label: 'Embeddings', values: ['embedding', 'embeddings', 'multimodal-embedding', 'sentence-similarity'] },
  { group: 'task', label: 'Mask generation', values: ['mask-generation'] },
  { group: 'task', label: 'Voice activity detection', values: ['voice-activity-detection'] },
  { group: 'capability', label: 'Conversational', values: ['conversational'] },
  { group: 'capability', label: 'Multimodal', values: ['multimodal'] },
  { group: 'capability', label: 'Vision', values: ['vision'] },
  { group: 'capability', label: 'Reasoning', values: ['reasoning'] },
  { group: 'capability', label: 'MoE', values: ['moe', 'mixture-of-experts'] },
  { group: 'capability', label: 'Decision model', values: ['decision-model'] },
  { group: 'capability', label: 'Audio', values: ['audio'] },
  { group: 'capability', label: 'Video', values: ['video'] },
  { group: 'capability', label: 'Multilingual', values: ['multilingual'] },
  { group: 'capability', label: 'Zero-shot', values: ['zero-shot'] },
  { group: 'capability', label: 'Uncensored', values: ['uncensored'] },
  { group: 'generation', label: 'ComfyUI', values: ['comfyui'] },
  { group: 'generation', label: 'LoRA', values: ['lora'] },
  { group: 'generation', label: 'Quantization', values: ['quantization'] },
  { group: 'generation', label: 'iMatrix', values: ['imatrix'] },
  { group: 'generation', label: 'Image generation', values: ['image-generation'] },
  { group: 'generation', label: 'Image editing', values: ['image-editing'] },
  { group: 'generation', label: 'Video generation', values: ['video-generation'] },
  { group: 'generation', label: 'Inpainting', values: ['inpainting'] },
  { group: 'generation', label: 'ControlNet', values: ['controlnet'] },
]

function parseRepoStack(value: string | null): RepoNavigation[] {
  if (!value) return []
  try {
    const parsed: unknown = JSON.parse(value)
    if (!Array.isArray(parsed)) return []
    return parsed.flatMap((item) => {
      if (!item || typeof item !== 'object') return []
      const candidate = item as Record<string, unknown>
      const repoId = candidate.repo_id
      const tab = candidate.tab
      if (typeof repoId !== 'string' || repoId.split('/').length !== 2 || !['description', 'files', 'quantizations'].includes(String(tab))) return []
      return [{ repo_id: repoId, revision: typeof candidate.revision === 'string' ? candidate.revision : undefined, tab: tab as RepoTab }]
    })
  } catch {
    return []
  }
}

export function ModelsPage() {
  const queryClient = useQueryClient()
  const [params, setParams] = useSearchParams()
  const requestedView = params.get('view')
  const view: ModelView = requestedView === 'huggingface' ? 'huggingface' : 'local'
  const localQuery = params.get('local_q') ?? ''
  const hfQuery = params.get('hf_q') ?? ''
  const hfGroup = (params.get('hf_group') as HfTrendingGroup | null) ?? 'all'
  const repoId = params.get('repo') ?? ''
  const revision = params.get('revision') ?? undefined
  const repoTab: RepoTab = params.get('repo_tab') === 'quantizations' ? 'quantizations' : 'description'
  const repoStack = parseRepoStack(params.get('repo_stack'))
  const parentRepoId = params.get('parent_repo') ?? ''
  const parentRevision = params.get('parent_revision') ?? undefined
  const parentTab: RepoTab = params.get('parent_tab') === 'quantizations' ? 'quantizations' : 'description'
  const backRepoId = repoStack.at(-1)?.repo_id ?? parentRepoId
  const [searchInput, setSearchInput] = useState(hfQuery)
  const [selectedFiles, setSelectedFiles] = useState<string[]>([])
  const [downloadsCollapsed, setDownloadsCollapsed] = useState(false)
  const [downloadsClosed, setDownloadsClosed] = useState(false)
  const [favoriteModels, setFavoriteModels] = useState<string[]>(() => {
    try {
      const saved = window.localStorage.getItem('llm-studio.favorite-models')
      const parsed = saved ? JSON.parse(saved) : []
      return Array.isArray(parsed) ? parsed.filter((value): value is string => typeof value === 'string') : []
    } catch {
      return []
    }
  })
  const localModels = useQuery({ queryKey: queryKeys.localModels, queryFn: api.getLocalModels, enabled: view === 'local', refetchInterval: (query) => query.state.data?.scanning ? 1_500 : false })
  const hfSearch = useQuery({ queryKey: queryKeys.hfSearch(hfQuery), queryFn: () => api.searchHf(hfQuery), enabled: view === 'huggingface' && hfQuery.trim().length > 1 })
  const hfTrending = useQuery({ queryKey: queryKeys.hfTrending('all'), queryFn: () => api.getHfTrending('all', 50), enabled: view === 'huggingface' && !hfQuery.trim() && !repoId })
  const repoParts = repoId.split('/')
  const hfRepo = useQuery({ queryKey: queryKeys.hfRepo(repoId, revision), queryFn: () => api.getHfRepo(repoParts[0], repoParts[1], revision), enabled: view === 'huggingface' && repoParts.length === 2 && Boolean(repoId) })
  const scan = useMutation({ mutationFn: api.scanLocalModels, onSuccess: (data) => queryClient.setQueryData(queryKeys.localModels, data) })
  const deleteModel = useMutation({ mutationFn: api.deleteLocalModel, onSuccess: (data) => queryClient.setQueryData(queryKeys.localModels, data) })
  const deleteModelGroup = useMutation({ mutationFn: ({ modelId, groupId }: { modelId: string; groupId: string }) => api.deleteLocalModelGroup(modelId, groupId), onSuccess: (data) => queryClient.setQueryData(queryKeys.localModels, data) })
  const downloads = useQuery({ queryKey: queryKeys.downloads, queryFn: api.getDownloads, refetchInterval: 1_500 })
  const previousDownloadIds = useRef<string[]>([])
  const [downloadHistory, setDownloadHistory] = useState<Operation[]>([])
  const downloadOperations = downloadHistory
  const download = useMutation({ mutationFn: api.downloadModels, onSuccess: () => { const shouldCollapse = downloadsClosed || downloadOperations.length === 0; setDownloadsClosed(false); if (shouldCollapse) setDownloadsCollapsed(true); void queryClient.invalidateQueries({ queryKey: queryKeys.downloads }); void queryClient.invalidateQueries({ queryKey: queryKeys.localModels }) } })
  const cancelDownload = useMutation({ mutationFn: api.cancelOperation, onSuccess: () => { void queryClient.invalidateQueries({ queryKey: queryKeys.downloads }); void queryClient.invalidateQueries({ queryKey: queryKeys.localModels }) } })
  useEffect(() => { window.localStorage.setItem('llm-studio.favorite-models', JSON.stringify(favoriteModels)) }, [favoriteModels])
  useEffect(() => { setSearchInput(hfQuery) }, [hfQuery])
  useEffect(() => {
    if (!downloads.data) return
    setDownloadHistory((current) => {
      const merged = new Map(current.map((item) => [item.id, item]))
      for (const item of downloads.data.items) merged.set(item.id, item)
      return [...merged.values()]
    })
    const ids = downloads.data.items.map((item) => item.id)
    const disappearedIds = previousDownloadIds.current.filter((id) => !ids.includes(id))
    if (disappearedIds.length > 0) {
      for (const id of disappearedIds) {
        void api.getOperation(id).then((operation) => {
          setDownloadHistory((current) => current.map((item) => item.id === id ? operation : item))
        }).catch(() => undefined)
      }
      void queryClient.invalidateQueries({ queryKey: queryKeys.models })
      void queryClient.invalidateQueries({ queryKey: queryKeys.localModels })
    }
    previousDownloadIds.current = ids
  }, [downloads.data, queryClient])
  const localItems = useMemo(() => (localModels.data?.items ?? []).filter((model) => `${model.path} ${model.format} ${model.compatibility}`.toLowerCase().includes(localQuery.toLowerCase())).sort((left, right) => Number(!favoriteModels.includes(left.id)) - Number(!favoriteModels.includes(right.id))), [favoriteModels, localModels.data?.items, localQuery])

  function setView(next: ModelView) { setParams((current) => { current.set('view', next); current.delete('q'); current.delete('parent_repo'); current.delete('parent_revision'); current.delete('parent_tab'); current.delete('repo_stack'); current.delete('repo_tab'); if (next !== 'huggingface') { current.delete('repo'); current.delete('revision') } return current }, { replace: true }) }
  function submitSearch(event: FormEvent<HTMLFormElement>) { event.preventDefault(); setParams((current) => { const value = searchInput.trim(); if (value) current.set('hf_q', value); else current.delete('hf_q'); current.delete('hf_group'); current.set('view', 'huggingface'); current.delete('repo'); current.delete('revision'); current.delete('parent_repo'); current.delete('parent_revision'); current.delete('parent_tab'); current.delete('repo_stack'); current.delete('repo_tab'); current.delete('q'); return current }, { replace: true }) }
  function openRepo(id: string) { setParams((current) => { current.set('view', 'huggingface'); current.set('repo', id); current.delete('parent_repo'); current.delete('parent_revision'); current.delete('parent_tab'); current.delete('repo_stack'); current.delete('repo_tab'); return current }, { replace: true }); setSelectedFiles([]) }
  function openLocalHfRepo(id: string, repoRevision?: string | null) { setParams((current) => { current.set('view', 'huggingface'); current.set('repo', id); if (repoRevision) current.set('revision', repoRevision); else current.delete('revision'); current.delete('parent_repo'); current.delete('parent_revision'); current.delete('parent_tab'); current.delete('repo_stack'); current.delete('repo_tab'); return current }, { replace: true }); setSelectedFiles([]) }
  function openQuantization(id: string, parent: HfRepoData) {
    const legacyStack = repoStack.length > 0 ? repoStack : parentRepoId ? [{ repo_id: parentRepoId, revision: parentRevision, tab: parentTab }] : []
    const nextStack = [...legacyStack, { repo_id: parent.repo_id, revision: parent.revision, tab: 'quantizations' as const }]
    setParams((current) => {
      current.set('view', 'huggingface')
      current.set('repo', id)
      current.delete('revision')
      current.set('repo_stack', JSON.stringify(nextStack))
      current.delete('parent_repo')
      current.delete('parent_revision')
      current.delete('parent_tab')
      current.delete('repo_tab')
      return current
    }, { replace: true })
    setSelectedFiles([])
  }
  function goBackFromRepo() {
    setParams((current) => {
      const legacyStack = repoStack.length > 0 ? repoStack : parentRepoId ? [{ repo_id: parentRepoId, revision: parentRevision, tab: parentTab }] : []
      const previous = legacyStack.at(-1)
      const remaining = legacyStack.slice(0, -1)
      if (previous) {
        current.set('repo', previous.repo_id)
        if (previous.revision) current.set('revision', previous.revision)
        else current.delete('revision')
        current.set('repo_tab', previous.tab)
        if (remaining.length > 0) current.set('repo_stack', JSON.stringify(remaining))
        else current.delete('repo_stack')
      } else {
        current.delete('repo')
        current.delete('revision')
        current.delete('repo_stack')
        current.delete('repo_tab')
      }
      current.delete('parent_repo')
      current.delete('parent_revision')
      current.delete('parent_tab')
      return current
    }, { replace: true })
    setSelectedFiles([])
  }
  function toggleFile(path: string) { setSelectedFiles((current) => current.includes(path) ? current.filter((item) => item !== path) : [...current, path]) }
  function startDownload(files: string[] | null, repo: HfRepoData, label?: string) { void download.mutate({ repo_id: repo.repo_id, revision: repo.revision, files, label: label ?? repo.repo_id }) }
  function closeDownloads() { setDownloadHistory([]); setDownloadsClosed(true); setDownloadsCollapsed(false) }

  return <div><PageHeader title="Модели" />
    {downloadOperations.length > 0 && <DownloadCenter operations={downloadOperations} closed={downloadsClosed} collapsed={downloadsCollapsed} onToggle={() => setDownloadsCollapsed((value) => !value)} onClose={closeDownloads} onStop={(id) => void cancelDownload.mutate(id)} stoppingId={cancelDownload.isPending ? cancelDownload.variables : null} />}
    <div className="mb-5 flex flex-wrap gap-1 border-b border-border"><Tab active={view === 'local'} onClick={() => setView('local')}>Локальные</Tab><Tab active={view === 'huggingface'} onClick={() => setView('huggingface')}>Hugging Face</Tab></div>
    {deleteModel.isError && <div className="mb-4"><QueryError error={deleteModel.error} label="Папку модели не удалось удалить" /></div>}
    {deleteModelGroup.isError && <div className="mb-4"><QueryError error={deleteModelGroup.error} label="Компонент модели не удалось удалить" /></div>}
    {view === 'local' && <LocalModels query={localQuery} items={localItems} state={localModels} scanError={scan.error ?? (localModels.data?.scan_error ? new Error(localModels.data.scan_error) : null)} onSearch={(value) => setParams((current) => { if (value) current.set('local_q', value); else current.delete('local_q'); return current }, { replace: true })} onScan={() => void scan.mutate()} scanning={scan.isPending || Boolean(localModels.data?.scanning)} favorites={favoriteModels} onToggleFavorite={(id) => setFavoriteModels((current) => current.includes(id) ? current.filter((value) => value !== id) : [...current, id])} onOpenHf={openLocalHfRepo} onDelete={(model) => { if (window.confirm(`Удалить папку модели «${model.path}»? Все файлы внутри будут удалены.`)) void deleteModel.mutate(model.id) }} onDeleteGroup={(model, group) => { const kind = group.kind === 'variant' ? 'вариант' : 'вспомогательный блок'; if (window.confirm(`Удалить ${kind} «${group.label}»?`)) void deleteModelGroup.mutate({ modelId: model.id, groupId: group.id }) }} deletingId={deleteModel.isPending ? deleteModel.variables : null} deletingGroupId={deleteModelGroup.isPending ? deleteModelGroup.variables?.groupId : null} />}
    {view === 'huggingface' && <section className="space-y-4">{!repoId && <form className="flex w-full gap-2" onSubmit={submitSearch}><div className="min-w-0 flex-1"><SearchField label="Поиск Hugging Face" value={searchInput} onChange={setSearchInput} onClear={() => { setSearchInput(''); setParams((current) => { current.delete('hf_q'); current.delete('hf_group'); current.delete('repo'); current.delete('revision'); current.delete('parent_repo'); current.delete('parent_revision'); current.delete('parent_tab'); current.delete('repo_stack'); current.delete('repo_tab'); return current }, { replace: true }) }} placeholder="Qwen, llama, mistral…" /></div><Button type="submit" variant="primary"><Search className="size-3.5" />Искать</Button></form>}{repoId ? <RepoView repo={hfRepo.data} query={hfRepo} initialTab={repoTab} backLabel={backRepoId ? `К квантам ${backRepoId}` : 'Назад'} selectedFiles={selectedFiles} toggleFile={toggleFile} onBack={goBackFromRepo} onOpenQuantization={openQuantization} onDownload={startDownload} downloadOperations={downloadOperations} /> : hfQuery ? <HfResults query={hfQuery} data={hfSearch.data} state={hfSearch} openRepo={openRepo} /> : <HfTrending group={hfGroup} data={hfTrending.data} state={hfTrending} openRepo={openRepo} onGroupChange={(group) => setParams((current) => { current.set('view', 'huggingface'); current.set('hf_group', group); current.delete('hf_q'); return current }, { replace: true })} />}</section>}
  </div>
}

function Tab({ active, onClick, children }: { active: boolean; onClick: () => void; children: string }) { return <button type="button" className={`min-h-10 border-b-2 px-3 text-xs ${active ? 'border-copper text-foreground' : 'border-transparent text-muted-foreground hover:text-foreground'}`} onClick={onClick}>{children}</button> }

function SearchField({ id, label, value, placeholder, onChange, onClear }: { id?: string; label: string; value: string; placeholder: string; onChange: (value: string) => void; onClear: () => void }) {
  return <div className="relative"><Search className="pointer-events-none absolute left-3 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground" /><Input id={id} aria-label={label} className="pl-9 pr-10" value={value} onChange={(event) => onChange(event.target.value)} placeholder={placeholder} />{value && <button type="button" className="absolute right-2 top-1/2 flex size-7 -translate-y-1/2 items-center justify-center rounded text-muted-foreground hover:bg-accent hover:text-foreground" onClick={onClear} aria-label={`Очистить: ${label}`}><X className="size-3.5" /></button>}</div>
}

function LocalModels({ query, items, state, scanError, onSearch, onScan, scanning, favorites, onToggleFavorite, onOpenHf, onDelete, onDeleteGroup, deletingId, deletingGroupId }: { query: string; items: LocalModelArtifact[]; state: ReturnType<typeof useQuery<Awaited<ReturnType<typeof api.getLocalModels>>, Error>>; scanError: unknown; onSearch: (value: string) => void; onScan: () => void; scanning: boolean; favorites: string[]; onToggleFavorite: (id: string) => void; onOpenHf: (repoId: string, revision?: string | null) => void; onDelete: (model: LocalModelArtifact) => void; onDeleteGroup: (model: LocalModelArtifact, group: LocalGroup) => void; deletingId: string | null | undefined; deletingGroupId: string | null | undefined }) {
  return <section>{Boolean(scanError) && <div className="mb-4"><QueryError error={scanError} label="Сканирование моделей не выполнено" /></div>}<div className="mb-4 flex items-center gap-2"><div className="min-w-0 flex-1"><SearchField id="model-search" label="Поиск локальной модели" value={query} onChange={onSearch} onClear={() => onSearch('')} placeholder="Путь, формат или имя" /></div><Button variant="secondary" onClick={onScan} disabled={scanning}><RefreshCw className={`size-3.5 ${scanning ? 'animate-spin' : ''}`} />{scanning ? 'Сканируем…' : 'Сканировать'}</Button></div>{(state.isLoading || (scanning && items.length === 0 && !query && !scanError)) ? <QueryLoading label="Сканируем локальные модели" /> : state.isError ? <QueryError error={state.error} retry={() => void state.refetch()} label="Каталог локальных моделей недоступен" /> : items.length === 0 ? <QueryEmpty title={query ? 'Совпадений нет' : 'Локальные модели не найдены'} description="Добавьте каталог модели в настроенную папку моделей и повторите сканирование." /> : <div className="grid gap-3">{items.map((model) => <ModelFolder key={model.id} model={model} favorite={favorites.includes(model.id)} onToggleFavorite={() => onToggleFavorite(model.id)} onOpenHf={onOpenHf} onDelete={onDelete} onDeleteGroup={onDeleteGroup} deleting={deletingId === model.id} deletingGroupId={deletingGroupId} />)}</div>}</section>
}

type LocalGroup = NonNullable<LocalModelArtifact['groups']>[number]

function ModelFolder({ model, favorite, onToggleFavorite, onOpenHf, onDelete, onDeleteGroup, deleting, deletingGroupId }: { model: LocalModelArtifact; favorite: boolean; onToggleFavorite: () => void; onOpenHf: (repoId: string, revision?: string | null) => void; onDelete: (model: LocalModelArtifact) => void; onDeleteGroup: (model: LocalModelArtifact, group: LocalGroup) => void; deleting: boolean; deletingGroupId: string | null | undefined }) {
  const variants = model.variants ?? []
  const auxiliary = model.auxiliary_files ?? []
  const variantGroups: LocalGroup[] = model.groups?.length ? model.groups : variants.map((file) => ({ id: `legacy-${file.path}`, label: file.path, kind: 'variant' as const, path: null, files: [file], file_count: 1, size_bytes: file.size_bytes, status: 'unknown' as const, deletable: false }))
  const auxiliaryGroups: LocalGroup[] = model.auxiliary_blocks?.length ? model.auxiliary_blocks : auxiliary.map((file) => ({ id: `legacy-${file.path}`, label: file.path, kind: 'auxiliary_block' as const, path: null, files: [file], file_count: 1, size_bytes: file.size_bytes, status: 'unknown' as const, deletable: false }))
  const pathParts = model.path.split('/').filter(Boolean)
  const vendor = toCamelCase(model.vendor || pathParts.at(-2) || pathParts.at(-1) || 'Неизвестный вендор')
  const modelName = toCamelCase(model.model_name || pathParts.at(-1) || 'Модель')
  return <Card><CardContent><div className="flex flex-wrap items-start justify-between gap-4"><div className="min-w-0"><div className="flex flex-wrap items-center gap-2"><h2 className="text-base font-medium tracking-tight">{vendor} / {modelName}</h2><Button size="icon" variant="ghost" aria-label={favorite ? 'Убрать из избранного' : 'Добавить в избранное'} title={favorite ? 'Убрать из избранного' : 'Добавить в избранное'} onClick={onToggleFavorite}><Star className={`size-4 ${favorite ? 'fill-copper text-copper' : 'text-muted-foreground'}`} /></Button>{model.hf_match.repo_id && <a href={`https://huggingface.co/${model.hf_match.repo_id}`} target="_blank" rel="noreferrer" aria-label="Открыть модель на Hugging Face" title="Открыть модель на Hugging Face" className="inline-flex size-8 items-center justify-center rounded-md text-muted-foreground hover:bg-accent hover:text-foreground"><ExternalLink className="size-4" /></a>}<HfUpdateBadge match={model.hf_match} /></div><div className="mt-1 flex items-center gap-2 text-[11px] text-muted-foreground"><FolderOpen className="size-3.5 shrink-0 text-copper" /><p className="break-all font-mono">{model.path}</p></div><p className="mt-2 text-[11px] text-muted-foreground">{model.format || 'формат неизвестен'} · {formatBytes(model.size_bytes)} · {variantGroups.length} вариантов · {auxiliaryGroups.length} вспомогательных блоков</p>{model.hf_match.repo_id && <button type="button" className="mt-1 inline-block text-[11px] text-copper underline-offset-2 hover:underline" onClick={() => onOpenHf(model.hf_match.repo_id!, model.hf_match.revision)}>{model.hf_match.repo_id}</button>}</div><div className="flex self-stretch flex-col items-end justify-between gap-2"><Button size="sm" variant="danger" disabled={deleting} onClick={() => onDelete(model)}><Trash2 className="size-3.5" />{deleting ? 'Удаляем…' : 'Удалить'}</Button><ModelSourceStatus match={model.hf_match} /></div></div>{variantGroups.length > 0 && <LocalGroupList title="Варианты модели" groups={variantGroups} model={model} onDelete={onDeleteGroup} deletingGroupId={deletingGroupId} />}{auxiliaryGroups.length > 0 && <LocalGroupList title="Вспомогательные блоки" groups={auxiliaryGroups} model={model} onDelete={onDeleteGroup} deletingGroupId={deletingGroupId} />}</CardContent></Card>
}

function toCamelCase(value: string): string {
  const segments = value.split('-').map((part) => part.split(/[^\p{L}\p{N}.]+/u).filter(Boolean).map((token) => `${token.slice(0, 1).toUpperCase()}${token.slice(1)}`).join(''))
  const result: string[] = []
  for (const segment of segments) {
    if (!segment) continue
    const previous = result.at(-1)
    if (!previous || /^\d/.test(segment) || /^\d/.test(previous)) result.push(segment)
    else result[result.length - 1] += segment
  }
  return result.join('-')
}

function ModelSourceStatus({ match }: { match: LocalModelArtifact['hf_match'] }) {
  const missing = match.missing_files ?? []
  const unverified = match.unverified_files ?? []
  const text = match.status === 'downloaded' ? match.scope === 'selection' ? 'HF: выбранный набор скачан' : unverified.length > 0 ? 'HF: snapshot найден' : 'HF: snapshot проверен' : match.status === 'incomplete' ? 'HF: не хватает файлов' : 'HF: источник не подтверждён'
  const hint = match.status === 'downloaded' && match.scope === 'selection'
    ? 'Скачан выбранный набор файлов, например один квант. Остальные файлы репозитория не считаются обязательными и не загружены.'
    : match.status === 'downloaded'
      ? 'Для каждого файла совпали наличие и размер с manifest Hugging Face.'
      : match.status === 'incomplete'
      ? 'Часть файлов из сохранённого набора отсутствует, имеет другой размер или не прошла локальную проверку.'
        : 'Модель найдена локально, но её источник или полнота не подтверждены.'
  const localDivergenceBadge = match.local_integrity?.baseline_status === 'diverged_from_downloaded'
    ? <span className="inline-flex rounded-sm border border-[#8b7652] px-2 py-1 text-[#d4b878]">Локальный файл изменён</span>
    : null
  return (
    <div className="flex max-w-full flex-col items-end gap-1 text-right text-[10px] leading-4 text-muted-foreground">
      <span title={hint} className="inline-flex rounded-sm border border-border px-2 py-1">{text}</span>
      {localDivergenceBadge}
      {missing.length > 0 && (
        <details className="max-w-xl">
          <summary className="cursor-pointer">Не хватает или отличаются: {missing.length}</summary>
          <ul className="mt-1 max-h-32 space-y-0.5 overflow-auto text-left">
            {missing.slice(0, 8).map((file) => <li key={file} className="break-all font-mono">{file}</li>)}
            {missing.length > 8 && <li>и ещё {missing.length - 8}</li>}
          </ul>
        </details>
      )}
      {unverified.length > 0 && (
        <details className="max-w-xl">
          <summary className="cursor-pointer">Размер совпал, локальная проверка не завершена: {unverified.length}</summary>
          <ul className="mt-1 max-h-32 space-y-0.5 overflow-auto text-left">
            {unverified.slice(0, 8).map((file) => <li key={file} className="break-all font-mono">{file}</li>)}
            {unverified.length > 8 && <li>и ещё {unverified.length - 8}</li>}
          </ul>
        </details>
      )}
    </div>
  )
}

function HfUpdateBadge({ match }: { match: LocalModelArtifact['hf_match'] }) {
  const avatar = match.repo_id
    ? <HfAuthorAvatar author={match.repo_id.split('/')[0]} avatarUrl={`https://huggingface.co/api/avatars/${encodeURIComponent(match.repo_id.split('/')[0])}`} />
    : null
  const update = match.remote_update
  const badge = match.remote_check?.availability === 'success' && update?.status === 'update_available' && update.changed_files > 0
    ? <span className="inline-flex rounded-sm border border-[#81554c] px-2 py-1 text-[10px] text-[#d09b87]">Доступно обновление · {update.changed_files} файлов</span>
    : null
  if (!avatar && !badge) return null
  return <>{avatar}{badge}</>
}

function formatHfDate(value: string): string {
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return new Intl.DateTimeFormat('ru-RU', { dateStyle: 'medium', timeStyle: 'short' }).format(date)
}

function formatHfCardDate(value: string): string {
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return new Intl.DateTimeFormat('ru-RU', { day: 'numeric', month: 'short', year: 'numeric' }).format(date)
}

function LocalGroupList({ title, groups, model, onDelete, deletingGroupId }: { title: string; groups: LocalGroup[]; model: LocalModelArtifact; onDelete: (model: LocalModelArtifact, group: LocalGroup) => void; deletingGroupId: string | null | undefined }) {
  return <details className="mt-3 border-t border-border pt-3"><summary className="cursor-pointer text-[11px] text-muted-foreground">{title} · {groups.length}</summary><div className="mt-2 grid gap-2">{groups.map((group) => <div key={group.id} className="flex items-center justify-between gap-3 rounded-md border border-border bg-secondary px-3 py-1.5"><div className="flex min-w-0 items-center gap-2">{group.status !== 'complete' && <span title={group.status === 'incomplete' ? 'Группа загружена не полностью' : 'Полнота группы не подтверждена'}><AlertTriangle className="size-3.5 shrink-0 text-[#b99a5b]" /></span>}<span className="min-w-0 truncate font-mono text-[10px]" title={group.label}>{group.label}</span><span className="shrink-0 text-[10px] text-muted-foreground">{group.file_count} файлов · {formatBytes(group.size_bytes)}</span></div>{group.deletable && <IconButton variant="ghost" className="shrink-0 text-copper hover:text-copper" disabled={deletingGroupId === group.id} onClick={() => onDelete(model, group)} label={`Удалить ${group.label}`}><Trash2 className="h-3.5 w-3.5 shrink-0" strokeWidth={2} /></IconButton>}</div>)}</div></details>
}

function HfResults({ query, data, state, openRepo }: { query: string; data: { items: HfModelItem[] } | undefined; state: ReturnType<typeof useQuery<Awaited<ReturnType<typeof api.searchHf>>, Error>>; openRepo: (id: string) => void }) { if (!query) return <QueryEmpty title="Введите запрос" description="Поиск обращается к публичному Hugging Face API после отправки формы." />; if (state.isLoading) return <QueryLoading label="Ищем публичные модели" />; if (state.isError) return <QueryError error={state.error} retry={() => void state.refetch()} label="Поиск Hugging Face не выполнен" />; if (!data?.items.length) return <QueryEmpty title="Ничего не найдено" description="Попробуйте имя модели или namespace/repository." />; return <div className="grid gap-3">{data.items.map((item) => <HfModelCard key={item.repo_id} item={item} onOpen={() => openRepo(item.repo_id)} />)}</div> }

function HfTrending({ group, data, state, openRepo, onGroupChange }: { group: HfTrendingGroup; data: { items: HfModelItem[] } | undefined; state: ReturnType<typeof useQuery<Awaited<ReturnType<typeof api.getHfTrending>>, Error>>; openRepo: (id: string) => void; onGroupChange: (group: HfTrendingGroup) => void }) {
  const groups: Array<[HfTrendingGroup, string]> = [['all', 'Все'], ['text', 'Текст'], ['gguf', 'GGUF'], ['vllm', 'vLLM'], ['diffusers', 'Diffusers'], ['embeddings', 'Embeddings']]
  const items = data?.items ?? []
  const availableGroups = groups.filter(([value]) => value === 'all' || items.some((item) => matchesHfGroup(item, value)))
  const filteredItems = group === 'all' ? items : items.filter((item) => matchesHfGroup(item, group))
  if (state.isLoading) return <QueryLoading label="Загружаем трендинг Hugging Face" />
  if (state.isError) return <QueryError error={state.error} retry={() => void state.refetch()} label="Трендинг Hugging Face не загружен" />
  return <div className="space-y-3"><div className="flex flex-wrap gap-2">{availableGroups.map(([value, label]) => <button key={value} type="button" className={`rounded-full border px-3 py-1.5 text-xs transition-colors ${group === value ? 'border-copper bg-copper/10 text-foreground' : 'border-border text-muted-foreground hover:border-[#6c625a] hover:text-foreground'}`} onClick={() => onGroupChange(value)}>{label}</button>)}</div>{!filteredItems.length ? <QueryEmpty title="Трендовые модели не найдены" description="Hugging Face не вернул модели для этой группы." /> : <div className="grid gap-3">{filteredItems.map((item) => <HfModelCard key={item.repo_id} item={item} onOpen={() => openRepo(item.repo_id)} />)}</div>}</div>
}

function matchesHfGroup(item: HfModelItem, group: HfTrendingGroup): boolean {
  if (group === 'all') return true
  const values = [item.pipeline_tag, item.library_name, ...item.tags].filter(Boolean).map((value) => value!.toLowerCase())
  if (group === 'gguf') return values.some((value) => value.includes('gguf'))
  if (group === 'vllm') return values.some((value) => value.includes('vllm'))
  if (group === 'diffusers') return values.some((value) => value.includes('diffusers'))
  if (group === 'embeddings') return values.some((value) => value.includes('embedding') || value === 'feature-extraction' || value === 'sentence-similarity')
  return values.some((value) => value.startsWith('text-') || value === 'text-generation' || value === 'text-classification')
}

function HfModelCard({ item, onOpen }: { item: HfModelItem; onOpen: () => void }) {
  const author = item.author ?? item.repo_id.split('/')[0]
  const badges = getHfBadges(item)
  return <Card className="transition-colors hover:border-[#6c625a]"><CardContent><div className="flex items-start justify-between gap-4"><div className="flex min-w-0 items-center gap-3"><HfAuthorAvatar author={author} avatarUrl={item.avatar_url} className="size-10" /><div className="min-w-0"><div className="flex items-center gap-2"><p className="truncate font-mono text-sm text-foreground">{item.repo_id}</p><a href={`https://huggingface.co/${item.repo_id}`} target="_blank" rel="noreferrer" aria-label={`Открыть ${item.repo_id} на Hugging Face`} title="Открыть на Hugging Face" className="inline-flex size-6 shrink-0 items-center justify-center rounded-md border border-border text-muted-foreground hover:border-[#6c625a] hover:text-foreground"><ExternalLink className="size-3.5" /></a></div>{formatWeightSummary(item) && <p className="mt-2 line-clamp-2 text-[11px] text-muted-foreground">{formatWeightSummary(item)}</p>}{item.description && <p className="mt-1 line-clamp-2 text-xs text-muted-foreground">{item.description}</p>}</div></div><div className="flex shrink-0 flex-col items-end gap-1 text-[11px] text-muted-foreground">{item.trending_score != null && <span className="flex items-center gap-1 text-copper"><TrendingUp className="size-3.5" />{formatCompactNumber(item.trending_score)}</span>}{item.downloads != null && <span className="flex items-center gap-1"><Download className="size-3.5" />{formatCompactNumber(item.downloads)}</span>}{item.likes != null && <span className="flex items-center gap-1"><Heart className="size-3.5" />{formatCompactNumber(item.likes)}</span>}</div></div><div className="mt-3 flex flex-wrap gap-1.5">{badges.map((badge) => <Badge key={badge} className="bg-transparent px-2 py-1">{badge}</Badge>)}</div><div className="mt-4 flex flex-wrap items-center gap-3 border-t border-border pt-3 text-[11px] text-muted-foreground"><span>автор · {author}</span>{item.created_at && <span>создана {formatHfCardDate(item.created_at)}</span>}{item.last_modified && <span>обновлена {formatHfCardDate(item.last_modified)}</span>}<button type="button" className="ml-auto shrink-0 text-copper hover:underline" onClick={onOpen}>Открыть</button></div></CardContent></Card>
}

function getHfBadges(item: HfModelItem): string[] {
  const values = new Set([item.pipeline_tag, item.library_name, ...item.tags].filter(Boolean).map((value) => value!.toLowerCase()))
  return HF_BADGE_DEFINITIONS.filter((definition) => definition.values.some((value) => values.has(value))).map((definition) => definition.label)
}

function formatCompactNumber(value: number): string {
  return new Intl.NumberFormat('ru-RU', { notation: 'compact', maximumFractionDigits: 1 }).format(value)
}

function formatWeightSummary(item: HfModelItem): string {
  const parts: string[] = []
  if (item.safetensors) {
    const parameterEntries = Object.entries(item.safetensors.parameters ?? {})
    const parameters = parameterEntries.length > 0 ? `${formatCompactNumber(parameterEntries.reduce((sum, [, value]) => sum + value, 0))} параметров` : null
    parts.push(['SafeTensors', parameters].filter(Boolean).join(' · '))
  } else if (item.gguf) {
    parts.push(['GGUF', item.gguf.architecture, item.gguf.total ? formatBytes(item.gguf.total) : null, item.gguf.totalFileSize ? `файлы ${formatBytes(item.gguf.totalFileSize)}` : null].filter(Boolean).join(' · '))
  }
  const evaluations = (item.eval_results ?? []).filter((result) => result.dataset_name || result.metric_name).slice(0, 2).map((result) => {
    const label = result.dataset_name ?? result.metric_name ?? 'benchmark'
    const metric = result.metric_name && result.metric_name !== label ? ` ${result.metric_name}` : ''
    return `${label}${metric}${result.metric_value != null ? `: ${result.metric_value}` : ''}`
  })
  if (evaluations.length > 0) parts.push(`Eval · ${evaluations.join(' · ')}`)
  return parts.join(' · ')
}

function HfAuthorAvatar({ author, avatarUrl, className = 'size-8' }: { author: string; avatarUrl?: string | null; className?: string }) {
  const [failed, setFailed] = useState(!avatarUrl)
  const initials = author.slice(0, 2).toUpperCase()
  return <div className={`flex ${className} shrink-0 items-center justify-center overflow-hidden rounded-md border border-border bg-secondary text-[10px] text-muted-foreground`}>{failed ? <span>{initials}</span> : <img src={avatarUrl ?? ''} alt="" loading="lazy" className="size-full object-cover" onError={() => setFailed(true)} />}</div>
}

function RepoView({ repo, query, initialTab, backLabel, selectedFiles, toggleFile, onBack, onOpenQuantization, onDownload, downloadOperations }: { repo: HfRepoData | undefined; query: ReturnType<typeof useQuery<Awaited<ReturnType<typeof api.getHfRepo>>, Error>>; initialTab: RepoTab; backLabel: string; selectedFiles: string[]; toggleFile: (path: string) => void; onBack: () => void; onOpenQuantization: (id: string, parent: HfRepoData) => void; onDownload: (files: string[] | null, repo: HfRepoData, label?: string) => void; downloadOperations: Operation[] }) {
  const [tab, setTab] = useState<RepoTab>(initialTab)
  useEffect(() => { setTab(initialTab) }, [initialTab, repo?.repo_id])
  const repoParts = repo?.repo_id.split('/') ?? []
  const quantizationsQuery = useQuery({
    queryKey: queryKeys.hfQuantizations(repo?.repo_id ?? ''),
    queryFn: () => api.getHfQuantizations(repoParts[0], repoParts[1]),
    enabled: tab === 'quantizations' && repoParts.length === 2,
  })
  if (query.isLoading) return <QueryLoading label="Получаем данные репозитория" />
  if (query.isError) return <QueryError error={query.error} retry={() => void query.refetch()} label="Репозиторий недоступен" />
  if (!repo) return null
  return <div className="space-y-4"><Button variant="ghost" size="sm" onClick={onBack}><ArrowLeft className="size-3.5" />{backLabel}</Button><Card><div className="border-b border-border px-4 py-3"><div className="flex items-center gap-3"><HfAuthorAvatar author={repo.author ?? repo.repo_id.split('/')[0]} avatarUrl={repo.avatar_url} /><div className="min-w-0"><CardTitle className="truncate font-mono">{repo.repo_id}</CardTitle><p className="mt-0.5 text-[11px] text-muted-foreground">{repo.author ?? repo.repo_id.split('/')[0]}</p></div><a href={`https://huggingface.co/${repo.repo_id}`} target="_blank" rel="noreferrer" aria-label="Открыть модель на Hugging Face" title="Открыть на Hugging Face" className="ml-auto inline-flex size-7 shrink-0 items-center justify-center rounded-md border border-border text-muted-foreground hover:border-[#6c625a] hover:text-foreground"><ExternalLink className="size-3.5" /></a></div></div><div className="flex gap-1 border-b border-border px-4"><button type="button" className={`min-h-10 border-b-2 px-3 text-xs ${tab === 'description' ? 'border-copper text-foreground' : 'border-transparent text-muted-foreground hover:text-foreground'}`} onClick={() => setTab('description')}>Описание</button><button type="button" className={`min-h-10 border-b-2 px-3 text-xs ${tab === 'files' ? 'border-copper text-foreground' : 'border-transparent text-muted-foreground hover:text-foreground'}`} onClick={() => setTab('files')}>Файлы</button><button type="button" className={`min-h-10 border-b-2 px-3 text-xs ${tab === 'quantizations' ? 'border-copper text-foreground' : 'border-transparent text-muted-foreground hover:text-foreground'}`} onClick={() => setTab('quantizations')}>Кванты</button></div>{tab === 'description' ? <CardContent>{repo.readme || repo.description ? <MarkdownContent source={repo.readme ?? repo.description ?? ''} repoId={repo.repo_id} revision={repo.revision} /> : <QueryEmpty title="Описание недоступно" description="В репозитории нет README или описания модели." />}</CardContent> : tab === 'files' ? <CardContent><div className="mb-4 flex flex-wrap items-center justify-between gap-3 border-b border-border pb-3"><p className="text-xs text-muted-foreground">Ревизия <code>{repo.revision}</code> · {repo.files.length} файлов{repo.revision_date ? ` · последний commit репозитория ${formatHfDate(repo.revision_date)}` : ''}</p><div className="flex flex-wrap gap-2"><Button size="sm" variant="secondary" disabled={selectedFiles.length === 0} onClick={() => onDownload(selectedFiles, repo, `${repo.repo_id} — выбранные файлы`)}><Download className="size-3.5" />Выбранные ({selectedFiles.length})</Button><Button size="sm" variant="primary" onClick={() => onDownload(null, repo, `${repo.repo_id} — весь snapshot`)}><Download className="size-3.5" />Весь snapshot</Button></div></div><DownloadBundles repo={repo} onDownload={onDownload} downloadOperations={downloadOperations} /><Table><TableHeader><TableRow><TableHead className="w-10">Выбор</TableHead><TableHead>Путь</TableHead><TableHead className="text-right">Размер</TableHead><TableHead>Дата файла на HF</TableHead></TableRow></TableHeader><TableBody>{repo.files.map((file) => <FileRow key={file.path} file={file} selected={selectedFiles.includes(file.path)} onToggle={() => toggleFile(file.path)} />)}</TableBody></Table></CardContent> : <QuantizationsContent query={quantizationsQuery} onOpen={(id) => onOpenQuantization(id, repo)} />}</Card></div>
}

function QuantizationsContent({ query, onOpen }: { query: ReturnType<typeof useQuery<Awaited<ReturnType<typeof api.getHfQuantizations>>, Error>>; onOpen: (id: string) => void }) {
  if (query.isLoading) return <CardContent><QueryLoading label="Получаем список квантов" /></CardContent>
  if (query.isError) return <CardContent><QueryError error={query.error} retry={() => void query.refetch()} label="Кванты Hugging Face недоступны" /></CardContent>
  const quantizations = query.data?.items ?? []
  if (!quantizations.length) return <CardContent><QueryEmpty title="Кванты не найдены" description="Hugging Face не вернул связанные квантованные репозитории для этой модели." /></CardContent>
  return <CardContent><div className="mb-4 flex items-center justify-between gap-3 border-b border-border pb-3"><p className="text-sm font-medium">Модели с квантами</p><span className="text-xs text-muted-foreground">{quantizations.length}</span></div><div className="grid gap-2">{quantizations.map((item) => <HfModelCard key={item.repo_id} item={item} onOpen={() => onOpen(item.repo_id)} />)}</div></CardContent>
}

function DownloadBundles({ repo, onDownload, downloadOperations }: { repo: HfRepoData; onDownload: (files: string[] | null, repo: HfRepoData, label?: string) => void; downloadOperations: Operation[] }) {
  const [extras, setExtras] = useState<string[]>([])
  useEffect(() => { setExtras([]) }, [repo.repo_id, repo.revision])
  const weights = /\.(gguf|ggml|mmproj|safetensors|bin|pt|pth|ckpt|onnx|nemo|tflite)$/i
  const auxiliary = (path: string) => /(^|[\\/_.-])(?:mtp|mmproj|projector|projection|vision(?:[-_]?(?:encoder|tower))?|audio(?:[-_]?(?:encoder|tower))?|adapter|d[\s_-]?spark)(?=$|[\\/_.-])/i.test(path)
  const groups = new Map<string, HfFile[]>()
  const extraGroups = new Map<string, HfFile[]>()
  for (const file of repo.files) {
    if (!weights.test(file.path)) continue
    const parts = file.path.split('/')
    const name = parts[parts.length - 1]
    if (auxiliary(file.path)) {
      const key = parts.length > 1 ? parts.slice(0, -1).join('/') : name.replace(/-\d{5}-of-\d{5}(?=\.)/i, '')
      extraGroups.set(key, [...(extraGroups.get(key) ?? []), file])
      continue
    }
    const key = parts.length > 1 ? parts.slice(0, -1).join('/') : /\.gguf$/i.test(name) ? name.replace(/-\d{5}-of-\d{5}(?=\.gguf$)/i, '').replace(/\.gguf$/i, '') : 'Модель'
    groups.set(key, [...(groups.get(key) ?? []), file])
  }
  const supporting = repo.files.filter((file) => !file.path.includes('/') && /(^README\.md$|^LICENSE(?:\..*)?$|config.*\.json$|^tokenizer.*\.json$|^special_tokens_map\.json$|^.*\.index\.json$|^vocab\.(json|txt)$|^merges\.txt$|^.*\.jinja$|^tokenizer\.model$)/i.test(file.path))
  if (!groups.size) return null
  function bundleFiles(key: string, files: HfFile[]): HfFile[] {
    const directoryFiles = key !== 'Модель' && files.some((file) => file.path.startsWith(`${key}/`))
      ? repo.files.filter((file) => file.path.startsWith(`${key}/`) && !auxiliary(file.path)) : files
    return [...new Map([...directoryFiles, ...supporting, ...extras.flatMap((extra) => extraGroups.get(extra) ?? [])].map((file) => [file.path, file])).values()]
  }
  return <div className="mb-5 space-y-3 border-b border-border pb-4">
    <p className="text-sm font-medium">Скачать вариант целиком</p>
    {extraGroups.size > 0 && <div className="flex flex-wrap gap-3">{[...extraGroups].map(([key, files]) => <label key={key} className="flex items-center gap-2 text-xs"><input type="checkbox" className="size-4 accent-[#c58055]" checked={extras.includes(key)} onChange={() => setExtras((current) => current.includes(key) ? current.filter((value) => value !== key) : [...current, key])} /><span>{key} · {formatBytes(files.every((file) => file.size !== null) ? files.reduce((sum, file) => sum + (file.size ?? 0), 0) : null)}</span></label>)}</div>}
    <div className="grid gap-2">{[...groups].map(([key, files]) => {
      const bundle = bundleFiles(key, files)
      const size = bundle.every((file) => file.size !== null) ? bundle.reduce((sum, file) => sum + (file.size ?? 0), 0) : null
      const label = `${repo.repo_id} — ${key}`
      const active = downloadOperations.some((operation) => operation.label === label && ['queued', 'running'].includes(operation.state))
      return <div key={key} className="flex items-center justify-between gap-3 rounded-md border border-border px-3 py-2"><div className="min-w-0"><p className="break-all font-mono text-xs">{key}</p><p className="mt-1 text-xs text-muted-foreground">{bundle.length} файлов · {formatBytes(size)}</p></div><Button size="sm" variant="secondary" disabled={active} aria-busy={active} onClick={() => onDownload(bundle.map((file) => file.path), repo, label)}><Download className={`size-3.5 ${active ? 'animate-pulse' : ''}`} />{active ? 'Скачивается…' : 'Скачать'}</Button></div>
    })}</div>
    <p className="text-xs text-muted-foreground">Все части выбранного варианта и общие конфиги включены. Дополнительные компоненты отмечаются отдельно.</p>
  </div>
}

function MarkdownContent({ source, repoId, revision }: { source: string; repoId: string; revision: string }) {
  const markdown = source.replace(/\r\n/g, '\n').replace(/^---\n[\s\S]*?\n(?:---|\.\.\.)[ \t]*(?:\n|$)/, '')
  const html = marked.parse(markdown, { async: false, gfm: true })
  const document = new DOMParser().parseFromString(html, 'text/html')
  return <div className="max-h-[70vh] w-full space-y-4 overflow-auto break-words text-xs leading-5 text-foreground">{Array.from(document.body.childNodes).map((node, index) => renderHtmlNode(node, `markdown-${index}`, repoId, revision))}</div>
}

function renderHtmlNode(node: Node, key: string, repoId: string, revision: string, stripAlertMarker = false): ReactNode {
  if (node.nodeType === Node.TEXT_NODE) {
    const text = node.textContent ?? ''
    return stripAlertMarker ? text.replace(/^\s*\[!(?:NOTE|TIP|IMPORTANT|WARNING|CAUTION)\]\s*/i, '') : text
  }
  if (node.nodeType !== Node.ELEMENT_NODE) return null
  const element = node as HTMLElement
  const tag = element.tagName.toLowerCase()
  if (['script', 'style', 'iframe', 'object', 'embed', 'form', 'input'].includes(tag)) return null
  const children = Array.from(element.childNodes).map((child, index) => renderHtmlNode(child, `${key}-${index}`, repoId, revision, stripAlertMarker))
  const alignmentClass = htmlAlignmentClass(element)
  const layoutClass = htmlLayoutClass(element)
  const containerClass = [alignmentClass, layoutClass].filter(Boolean).join(' ')
  if (tag === 'a') return <a key={key} href={safeMarkdownUrl(element.getAttribute('href') ?? '', repoId, revision)} target={element.getAttribute('href')?.startsWith('#') ? undefined : '_blank'} rel="noreferrer" className="text-copper underline-offset-2 hover:underline">{children}</a>
  if (tag === 'img') {
    const width = htmlImageDimension(element.getAttribute('width'))
    const height = htmlImageDimension(element.getAttribute('height'))
    return <img key={key} src={safeMarkdownUrl(element.getAttribute('src') ?? '', repoId, revision, true)} alt={element.getAttribute('alt') ?? ''} width={width} height={height} style={element.style.width === '100%' ? { width: '100%', height: 'auto' } : undefined} loading="lazy" className="my-2 inline-block max-h-[70vh] max-w-full rounded-md object-contain" />
  }
  if (tag === 'video') return <video key={key} src={safeMarkdownUrl(element.getAttribute('src') ?? '', repoId, revision, true)} controls playsInline preload="metadata" className="max-h-[70vh] w-full rounded-md">{children}</video>
  if (tag === 'strong' || tag === 'b') return <strong key={key}>{children}</strong>
  if (tag === 'em' || tag === 'i') return <em key={key}>{children}</em>
  if (tag === 'del' || tag === 's') return <del key={key}>{children}</del>
  if (tag === 'code') return <code key={key} className={element.parentElement?.tagName === 'PRE' ? 'font-mono' : 'rounded bg-secondary px-1 py-0.5 font-mono text-[11px]'}>{children}</code>
  if (tag === 'pre') return <pre key={key} className="overflow-auto whitespace-pre rounded-md border border-border bg-background p-3 font-mono text-xs leading-5">{children}</pre>
  if (tag === 'ul') return <ul key={key} className="list-disc space-y-1 pl-5">{children}</ul>
  if (tag === 'ol') return <ol key={key} start={Number(element.getAttribute('start')) || 1} className="list-decimal space-y-1 pl-5">{children}</ol>
  if (tag === 'li') return <li key={key}>{children}</li>
  if (tag === 'br') return <br key={key} />
  if (tag === 'hr') return <hr key={key} className="border-border" />
  if (tag === 'blockquote') {
    const alert = htmlAlertKind(element)
    const alertChildren = alert
      ? Array.from(element.childNodes).map((child, index) => renderHtmlNode(child, `${key}-${index}`, repoId, revision, true))
      : children
    const alertClass = alert === 'note'
      ? 'border-copper bg-secondary'
      : alert === 'tip'
        ? 'border-[#5b9b7c] bg-[#152521]'
        : alert === 'important'
          ? 'border-[#9b7cc5] bg-[#211a2d]'
          : alert === 'warning'
            ? 'border-[#b99a5b] bg-[#2a2519]'
            : alert === 'caution'
              ? 'border-[#b56f62] bg-[#2a1d1a]'
              : 'border-border bg-transparent'
    return <blockquote key={key} className={`space-y-3 border-l-4 px-4 py-3 ${alertClass} ${alert ? 'text-foreground' : 'text-muted-foreground'}`}>{alertChildren}</blockquote>
  }
  if (tag === 'table') return <div key={key} className="overflow-x-auto"><table className={`w-full border-collapse text-xs ${alignmentClass || 'text-left'}`}>{children}</table></div>
  if (tag === 'thead') return <thead key={key}>{children}</thead>
  if (tag === 'tbody') return <tbody key={key}>{children}</tbody>
  if (tag === 'tfoot') return <tfoot key={key}>{children}</tfoot>
  if (tag === 'tr') return <tr key={key}>{children}</tr>
  if (tag === 'td' || tag === 'th') {
    const Cell = tag
    return <Cell key={key} colSpan={htmlImageDimension(element.getAttribute('colspan'))} rowSpan={htmlImageDimension(element.getAttribute('rowspan'))} className={`border border-border px-2 py-1 align-top ${tag === 'th' ? 'bg-secondary font-medium' : ''} ${alignmentClass}`}>{children}</Cell>
  }
  if (tag === 'caption') return <caption key={key} className="mb-2 text-muted-foreground">{children}</caption>
  if (tag === 'details') return <details key={key} className="space-y-3">{children}</details>
  if (tag === 'summary') return <summary key={key} className="cursor-pointer font-medium">{children}</summary>
  if (/^h[1-6]$/.test(tag)) {
    const Heading = tag as 'h1' | 'h2' | 'h3' | 'h4' | 'h5' | 'h6'
    return <Heading key={key} id={element.getAttribute('id') ?? (element.textContent ?? '').toLowerCase().replace(/[^\p{L}\p{N} _-]/gu, '').trim().replace(/\s+/g, '-')} className={`font-medium tracking-tight ${tag === 'h1' ? 'text-lg' : tag === 'h2' ? 'text-base' : 'text-sm'} ${containerClass}`}>{children}</Heading>
  }
  if (tag === 'p') return <p key={key} className={containerClass}>{children}</p>
  if (tag === 'div' || tag === 'section' || tag === 'center') return <div key={key} className={`${layoutClass ? '' : 'space-y-3'} ${tag === 'center' ? 'text-center' : containerClass}`}>{children}</div>
  return <span key={key}>{children}</span>
}

function safeMarkdownUrl(value: string, repoId: string, revision: string, image = false): string {
  if (value.startsWith('#')) return value
  try {
    const base = `https://huggingface.co/${repoId}/${image ? 'resolve' : 'blob'}/${encodeURIComponent(revision)}/`
    const url = new URL(value, base)
    return ['http:', 'https:'].includes(url.protocol) ? url.href : '#'
  } catch {
    return '#'
  }
}

function htmlImageDimension(value: string | null): number | undefined {
  const parsed = Number.parseInt(value ?? '', 10)
  return Number.isFinite(parsed) && parsed > 0 ? Math.min(parsed, 1600) : undefined
}

function htmlAlignmentClass(element: HTMLElement): string {
  const attribute = element.getAttribute('align')?.trim().toLowerCase()
  const styleValue = element.getAttribute('style') ?? ''
  const style = styleValue.match(/text-align\s*:\s*(left|center|right|justify)/i)?.[1]?.toLowerCase()
  const centeredFlex = /(?:align-items\s*:\s*center|justify-content\s*:\s*center)/i.test(styleValue)
  const alignment = style ?? attribute ?? (centeredFlex ? 'center' : undefined)
  return alignment === 'left' ? 'text-left' : alignment === 'center' ? 'text-center' : alignment === 'right' ? 'text-right' : alignment === 'justify' ? 'text-justify' : ''
}

function htmlAlertKind(element: HTMLElement): 'note' | 'tip' | 'important' | 'warning' | 'caution' | null {
  const match = element.textContent?.match(/^\s*\[!(NOTE|TIP|IMPORTANT|WARNING|CAUTION)\]/i)
  return match ? match[1].toLowerCase() as ReturnType<typeof htmlAlertKind> : null
}

function htmlLayoutClass(element: HTMLElement): string {
  const style = element.getAttribute('style') ?? ''
  if (!/display\s*:\s*flex/i.test(style)) return ''
  const classes = ['flex', 'flex-wrap']
  if (/flex-direction\s*:\s*column/i.test(style)) classes.push('flex-col')
  if (/align-items\s*:\s*center/i.test(style)) classes.push('items-center')
  if (/justify-content\s*:\s*center/i.test(style)) classes.push('justify-center')
  if (/gap\s*:\s*(?:[1-9]|10)px/i.test(style)) classes.push('gap-1')
  else if (/gap\s*:\s*[\d.]+(?:em|rem)/i.test(style)) classes.push('gap-2')
  return classes.join(' ')
}

function FileRow({ file, selected, onToggle }: { file: HfFile; selected: boolean; onToggle: () => void }) { return <TableRow><TableCell><input type="checkbox" aria-label={`Выбрать ${file.path}`} checked={selected} onChange={onToggle} className="size-4 accent-[#c58055]" /></TableCell><TableCell className="break-all font-mono text-[11px]">{file.path}</TableCell><TableCell className="whitespace-nowrap text-right font-mono text-[11px]">{formatBytes(file.size)}</TableCell><TableCell className="whitespace-nowrap text-[11px] text-muted-foreground">{file.file_date ? formatHfDate(file.file_date) : '—'}</TableCell></TableRow> }

function DownloadCenter({ operations, closed, collapsed, onToggle, onClose, onStop, stoppingId }: { operations: Operation[]; closed: boolean; collapsed: boolean; onToggle: () => void; onClose: () => void; onStop: (id: string) => void; stoppingId: string | null | undefined }) {
  const sorted = [...operations].sort((left, right) => new Date(right.created_at).getTime() - new Date(left.created_at).getTime())
  if (closed) return null
  return <div className="mb-4 rounded-md border border-border bg-secondary px-4 py-3"><div className="flex items-center gap-2"><button type="button" className="flex min-w-0 items-center gap-2 text-left text-xs font-medium" onClick={onToggle} aria-expanded={!collapsed}><span>Загрузки</span><span className="text-[11px] text-muted-foreground">Задач: {operations.length}</span>{collapsed ? <ChevronDown className="size-3.5 text-muted-foreground" /> : <ChevronUp className="size-3.5 text-muted-foreground" />}</button><Button size="icon" variant="ghost" className="ml-auto -mr-2 -mt-2 shrink-0" aria-label="Закрыть блок загрузок" title="Закрыть" onClick={onClose}><X className="size-4" /></Button></div>{!collapsed && <div className="mt-3 space-y-2">{sorted.map((operation) => <DownloadTask key={operation.id} operation={operation} stopping={stoppingId === operation.id} onStop={() => onStop(operation.id)} />)}</div>}</div>
}

function DownloadTask({ operation, stopping, onStop }: { operation: Operation; stopping: boolean; onStop: () => void }) {
  const active = ['queued', 'running'].includes(operation.state)
  const progress = operation.progress
  const percent = progress && progress.bytes_total > 0 ? Math.min(100, (progress.bytes_completed / progress.bytes_total) * 100) : 0
  const started = operation.started_at ? new Date(operation.started_at).getTime() : null
  const finished = operation.finished_at ? new Date(operation.finished_at).getTime() : Date.now()
  const elapsedSeconds = started ? Math.max(0, (finished - started) / 1000) : 0
  const speed = progress && elapsedSeconds > 0 ? progress.bytes_completed / elapsedSeconds : 0
  const remainingBytes = progress ? Math.max(0, progress.bytes_total - progress.bytes_completed) : 0
  const remaining = progress && progress.bytes_completed >= progress.bytes_total ? '0 с' : speed > 0 ? formatElapsed(remainingBytes / speed) : 'ожидание данных'
  return <div className="h-[72px] overflow-hidden rounded-md border border-border px-3 py-2 text-xs"><p className="truncate font-medium" title={operation.label ?? operation.target_id}>{operation.label ?? operation.target_id}<span className="font-normal text-[11px] text-muted-foreground"> — Осталось: {remaining} · {speed > 0 ? `${formatBytes(speed)}/с` : 'ожидание данных'} · {formatBytes(progress?.bytes_completed ?? 0)} из {formatBytes(progress?.bytes_total ?? 0)} · {progress?.files_completed ?? 0} из {progress?.files_total ?? 0}</span></p><div className="mt-2 flex h-2 items-center gap-2"><div className="min-w-0 flex-1"><Progress value={progress ? percent : null} /></div>{active ? <button type="button" className="h-2 w-2 shrink-0 border-0 bg-red-500 p-0 hover:bg-red-400 disabled:pointer-events-none disabled:opacity-45" disabled={stopping} onClick={onStop} aria-label="Остановить загрузку" title={stopping ? 'Останавливаем…' : 'Остановить'} /> : <span className="h-2 w-2 shrink-0" aria-hidden="true" />}</div><p className={`mt-1 h-4 truncate text-[11px] ${operation.error ? 'visible text-[#d0a390]' : 'invisible'}`} title={operation.error ?? undefined}>{operation.error ?? ' '}</p></div>
}

function formatElapsed(seconds: number): string {
  const total = Math.floor(seconds)
  const minutes = Math.floor(total / 60)
  const rest = total % 60
  return minutes > 0 ? `${minutes} мин ${rest} с` : `${rest} с`
}
