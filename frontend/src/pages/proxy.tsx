import { useMemo, useState, type DragEvent } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { AlertTriangle, GripVertical, Pencil, Plus, RefreshCw, Trash2, X } from 'lucide-react'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent } from '@/components/ui/card'
import { Dialog } from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { PageHeader } from '@/components/page-header'
import { QueryError, QueryLoading } from '@/components/query-state'
import { NotificationToast } from '@/components/ui/notification-toast'
import { RunTabs } from '@/components/ui/run-tabs'
import { api, queryKeys } from '@/lib/api'
import { comfyApi, comfyQueryKey, type ComfyConfig } from '@/lib/comfy-api'
import type { ProfileData, ProxyRoute } from '@/types/api'
import { ProxyComfyConfig } from '@/components/proxy-comfy-config'

const aliasPattern = /^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/

export function ProxyPage() {
  const queryClient = useQueryClient()
  const [tab, setTab] = useState<'models' | 'images' | 'video'>('models')
  const [toast, setToast] = useState<{ tone: 'success' | 'error'; message: string } | null>(null)
  const routes = useQuery({ queryKey: queryKeys.proxyRoutes, queryFn: api.getProxyRoutes, refetchInterval: 10_000 })
  const profiles = useQuery({ queryKey: queryKeys.profiles, queryFn: api.getProfiles })
  const comfy = useQuery({ queryKey: comfyQueryKey, queryFn: comfyApi.getConfig, retry: false })
  const [aliasName, setAliasName] = useState('')
  const [selectedProfiles, setSelectedProfiles] = useState<string[]>([])
  const [aliasDialog, setAliasDialog] = useState<{ kind: 'new' } | { kind: 'edit'; route: ProxyRoute } | null>(null)
  const [dragTarget, setDragTarget] = useState<{ route: string; index: number } | null>(null)
  const [dragSource, setDragSource] = useState<{ route: string; index: number } | null>(null)

  function notify(tone: 'success' | 'error', message: string) {
    setToast({ tone, message })
  }

  const invalidateRoutes = () => {
    void queryClient.invalidateQueries({ queryKey: queryKeys.proxyRoutes })
    void queryClient.invalidateQueries({ queryKey: queryKeys.openAiModels })
  }
  const createAlias = useMutation({
    mutationFn: api.createProxyAlias,
    onSuccess: () => { closeAliasDialog(); invalidateRoutes() },
    onError: (error) => notify('error', error instanceof Error ? error.message : 'Алиас не создан.'),
  })
  const updateRoute = useMutation({
    mutationFn: ({ name, profileIds }: { name: string; profileIds: string[] }) => api.updateProxyRoute(name, profileIds),
    onSuccess: invalidateRoutes,
    onError: (error) => notify('error', error instanceof Error ? error.message : 'Порядок маршрута не сохранён.'),
  })
  const updateAlias = useMutation({
    mutationFn: ({ currentName, name, profileIds }: { currentName: string; name: string; profileIds: string[] }) => api.updateProxyAlias(currentName, { name, profile_ids: profileIds }),
    onSuccess: () => { closeAliasDialog(); invalidateRoutes() },
    onError: (error) => notify('error', error instanceof Error ? error.message : 'Алиас не изменён.'),
  })
  const deleteAlias = useMutation({
    mutationFn: api.deleteProxyAlias,
    onSuccess: invalidateRoutes,
    onError: (error) => notify('error', error instanceof Error ? error.message : 'Алиас не удалён.'),
  })
  const saveComfy = useMutation({
    mutationFn: comfyApi.putConfig,
    onSuccess: (data) => {
      queryClient.setQueryData(comfyQueryKey, data)
      void queryClient.resetQueries({ queryKey: ['proxy-comfy-workflows'] })
      void queryClient.invalidateQueries({ queryKey: ['proxy-comfy-status'] })
      invalidateRoutes()
      notify('success', 'Конфигурация ComfyUI сохранена.')
    },
  })

  const editingRoute = aliasDialog?.kind === 'edit' ? aliasDialog.route : null
  const manualRouteNames = useMemo(
    () => new Set(routes.data?.items.filter((route) => route.kind === 'alias').map((route) => route.name) ?? []),
    [routes.data?.items],
  )
  const aliasError = aliasName.length === 0
    ? null
    : !aliasPattern.test(aliasName)
      ? 'Используйте 1–128 ASCII-символов: буквы, цифры, точку, дефис или подчёркивание.'
      : manualRouteNames.has(aliasName) && aliasName !== editingRoute?.name
        ? 'Это имя уже занято ручным алиасом.'
        : null
  const visibleRoutes = routes.data?.items.filter((route) => route.kind === 'alias' || (route.available && route.diagnostics.length === 0)) ?? []

  function toggleProfile(profile: ProfileData) {
    const reference = profile.profile_id ?? profile.name
    setSelectedProfiles((current) => current.includes(reference) ? current.filter((item) => item !== reference) : [...current, reference])
  }

  function openNewAlias() {
    createAlias.reset()
    updateAlias.reset()
    setAliasName('')
    setSelectedProfiles([])
    setAliasDialog({ kind: 'new' })
  }

  function openEditAlias(route: ProxyRoute) {
    createAlias.reset()
    updateAlias.reset()
    setAliasName(route.name)
    setSelectedProfiles([...route.candidate_profile_ids])
    setAliasDialog({ kind: 'edit', route })
  }

  function closeAliasDialog() {
    setAliasName('')
    setSelectedProfiles([])
    setAliasDialog(null)
  }

  function submitAlias() {
    if (!aliasName || aliasError || selectedProfiles.length === 0) return
    if (editingRoute) {
      updateAlias.mutate({ currentName: editingRoute.name, name: aliasName, profileIds: selectedProfiles })
      return
    }
    createAlias.mutate({ name: aliasName, profile_ids: selectedProfiles })
  }

  function reorder(route: ProxyRoute, from: number, to: number) {
    if (!Number.isInteger(to) || to < 0 || to > route.candidate_profile_ids.length || from === to || from + 1 === to) return
    const profileIds = [...route.candidate_profile_ids]
    const [item] = profileIds.splice(from, 1)
    profileIds.splice(from < to ? to - 1 : to, 0, item)
    updateRoute.mutate({ name: route.name, profileIds })
  }

  function drop(route: ProxyRoute, event: DragEvent<HTMLDivElement>, to: number) {
    event.preventDefault()
    const profileId = event.dataTransfer.getData('text/plain')
    const from = route.candidate_profile_ids.indexOf(profileId)
    if (from >= 0 && dragSource?.route === route.name) reorder(route, from, to)
    setDragTarget(null)
    setDragSource(null)
  }

  return <div>
    <PageHeader title="Прокси" action={<div className="flex gap-2"><Button variant="secondary" size="sm" onClick={() => { void routes.refetch(); void profiles.refetch(); void comfy.refetch(); void queryClient.resetQueries({ queryKey: ['proxy-comfy-workflows'] }); void queryClient.invalidateQueries({ queryKey: ['proxy-comfy-status'] }) }}><RefreshCw className="size-3.5" />Обновить</Button>{tab === 'models' ? <Button variant="primary" size="sm" onClick={openNewAlias}><Plus className="size-3.5" />Новый алиас</Button> : null}</div>} />
    <RunTabs ariaLabel="Разделы прокси" compact panelId="proxy-config-panel" value={tab} onChange={(value) => setTab(value as 'models' | 'images' | 'video')} items={[{ value: 'models', title: 'Модели', subtitle: 'Текстовые профили и алиасы' }, { value: 'images', title: 'Изображения', subtitle: 'ComfyUI workflow' }, { value: 'video', title: 'Видео', subtitle: 'ComfyUI workflow' }]} />
    <Card id="proxy-config-panel" role="tabpanel" aria-labelledby={`proxy-config-panel-tab-${tab}`} className="mb-6 rounded-t-none border-t-0">
      <CardContent>
      {tab !== 'models' && comfy.isLoading && <QueryLoading label="Читаем конфигурацию ComfyUI" />}
      {tab !== 'models' && comfy.isError && !comfy.data && <QueryError error={comfy.error} retry={() => void comfy.refetch()} label="Конфигурация ComfyUI недоступна" />}
      {tab !== 'models' && comfy.data && <ProxyComfyConfig key={tab} config={comfy.data} mediaType={tab === 'images' ? 'image' : 'video'} onSave={(next: ComfyConfig) => saveComfy.mutateAsync(next)} notify={notify} />}
      {tab === 'models' && <>
      {routes.isLoading ? <QueryLoading label="Читаем маршруты" /> : routes.isError ? <QueryError error={routes.error} retry={() => void routes.refetch()} label="Маршруты недоступны" /> : visibleRoutes.length > 0 && <div className="space-y-3">{visibleRoutes.map((route) => <RouteCard key={`${route.kind}:${route.name}`} route={route} dragSource={dragSource} onDragSource={setDragSource} dragTarget={dragTarget} onDragTarget={setDragTarget} onDrop={drop} onDelete={() => deleteAlias.mutate(route.name)} onEdit={() => openEditAlias(route)} onRemove={(profileId) => updateRoute.mutate({ name: route.name, profileIds: route.candidate_profile_ids.filter((item) => item !== profileId) })} updating={updateRoute.isPending} deleting={deleteAlias.isPending && deleteAlias.variables === route.name} />)}</div>}
    <Dialog open={aliasDialog !== null} title={editingRoute ? 'Изменить алиас' : 'Новый алиас'} onOpenChange={(open) => { if (!open) closeAliasDialog() }}><div className="space-y-4 px-4 py-4"><div><label htmlFor="proxy-alias-name" className="text-xs text-foreground">Имя алиаса</label><Input id="proxy-alias-name" className="mt-1.5" value={aliasName} onChange={(event) => setAliasName(event.target.value)} placeholder="my-chat-model" aria-invalid={Boolean(aliasError)} />{aliasError && <p className="mt-1.5 text-[11px] text-[#d09b87]">{aliasError}</p>}</div><div><p className="text-xs text-foreground">Профили-кандидаты</p><div className="mt-1.5 max-h-64 space-y-1 overflow-auto rounded-md border border-border bg-secondary p-2">{profiles.isLoading ? <p className="px-2 py-3 text-xs text-muted-foreground">Читаем профили…</p> : profiles.data?.items.length === 0 ? <p className="px-2 py-3 text-xs text-muted-foreground">Сначала создайте профиль.</p> : profiles.data?.items.map((profile) => <ProfileCheckbox key={profile.profile_id ?? profile.name} profile={profile} checked={selectedProfiles.includes(profile.profile_id ?? profile.name)} onChange={() => toggleProfile(profile)} />)}</div></div><div className="flex justify-end gap-2"><Button variant="ghost" onClick={closeAliasDialog}>Отмена</Button><Button variant="primary" disabled={!aliasName || Boolean(aliasError) || selectedProfiles.length === 0 || createAlias.isPending || updateAlias.isPending || updateRoute.isPending} onClick={submitAlias}>{editingRoute ? <Pencil className="size-3.5" /> : <Plus className="size-3.5" />}{editingRoute ? 'Сохранить' : createAlias.isPending ? 'Сохраняем…' : 'Добавить алиас'}</Button></div></div></Dialog>
      </>}
      </CardContent>
    </Card>
    {toast && <NotificationToast tone={toast.tone} title={toast.tone === 'success' ? 'Готово' : 'Не удалось сохранить'} message={toast.message} />}
  </div>
}

function RouteCard({ route, dragSource, onDragSource, dragTarget, onDragTarget, onDrop, onDelete, onEdit, onRemove, updating, deleting }: { route: ProxyRoute; dragSource: { route: string; index: number } | null; onDragSource: (source: { route: string; index: number } | null) => void; dragTarget: { route: string; index: number } | null; onDragTarget: (target: { route: string; index: number } | null) => void; onDrop: (route: ProxyRoute, event: DragEvent<HTMLDivElement>, to: number) => void; onDelete: () => void; onEdit: () => void; onRemove: (profileId: string) => void; updating: boolean; deleting: boolean }) {
  function insertionIndex(index: number) {
    if (dragSource?.route !== route.name || updating) return null
    if (index === dragSource.index) return null
    return index > dragSource.index ? index + 1 : index
  }

  return <div className="rounded-md border border-border bg-secondary/40" onDragLeave={(event) => {
    if (!(event.relatedTarget instanceof Node) || !event.currentTarget.contains(event.relatedTarget)) onDragTarget(null)
  }}>
    <div className="flex flex-wrap items-center gap-2 border-b border-border px-3 py-2.5"><h3 className="font-mono text-xs text-foreground">{route.name}</h3><Badge>{route.kind === 'auto' ? 'автоматический' : 'ручной'}</Badge><Badge className={route.available ? 'border-[#596c4b] text-[#b2c995]' : 'border-[#72544a] text-[#d0a390]'}>{route.diagnostics.includes('route_name_collision') ? 'конфликт имени' : route.available ? 'доступен' : route.kind === 'alias' ? 'ожидает профиль' : 'скрыт из /v1/models'}</Badge>{route.kind === 'alias' && <div className="ml-auto flex gap-1"><Button variant="ghost" size="icon-sm" iconOnly aria-label={`Изменить алиас ${route.name}`} title="Изменить алиас" onClick={onEdit}><Pencil className="size-3.5" /></Button><Button variant="danger" size="icon-sm" iconOnly aria-label={`Удалить алиас ${route.name}`} title="Удалить алиас" onClick={onDelete} disabled={deleting}><Trash2 className="size-3.5" /></Button></div>}</div>
    {route.diagnostics.map((diagnostic) => <p key={diagnostic} className="flex items-center gap-1.5 border-b border-border px-3 py-2 text-[11px] text-[#d09b87]"><AlertTriangle className="size-3.5" />{diagnosticText(diagnostic)}</p>)}
    <div className="divide-y divide-border">{route.candidates.length === 0 ? <p className="px-3 py-3 text-xs text-muted-foreground">Кандидаты не настроены.</p> : <>{route.candidates.map((candidate, index) => {
      const isDropTarget = dragTarget?.route === route.name && dragTarget.index === index
      const isBottomTarget = index === route.candidates.length - 1 && dragTarget?.route === route.name && dragTarget.index === route.candidates.length
      return <div key={candidate.profile_id} data-index={index} onDragOver={(event) => {
        const to = insertionIndex(index)
        event.preventDefault()
        event.dataTransfer.dropEffect = to === null ? 'none' : 'move'
        onDragTarget(to === null ? null : { route: route.name, index: to })
      }} onDrop={(event) => {
        const to = insertionIndex(index)
        event.preventDefault()
        if (to !== null) onDrop(route, event, to)
        onDragTarget(null)
      }} className="relative">
        {isDropTarget && <div data-drop-indicator="before" className="pointer-events-none absolute -top-px left-3 right-3 z-10 h-0.5 rounded-full bg-copper shadow-[0_0_8px_rgba(197,128,85,0.85)]" />}
        {isBottomTarget && <div data-drop-indicator="after" className="pointer-events-none absolute bottom-0 left-3 right-3 z-10 h-0.5 rounded-full bg-copper shadow-[0_0_8px_rgba(197,128,85,0.85)]" />}
        <div draggable={route.candidates.length > 1 && !updating} onDragStart={(event) => { event.dataTransfer.effectAllowed = 'move'; event.dataTransfer.setData('text/plain', candidate.profile_id); onDragSource({ route: route.name, index }); onDragTarget(null) }} onDragEnd={() => { onDragTarget(null); onDragSource(null) }} className="flex items-center gap-2 px-3 py-2.5 transition-colors">
          <GripVertical className="size-4 shrink-0 cursor-grab text-muted-foreground" aria-label="Перетащить кандидата" />
          <div className="min-w-0 flex-1"><div className="flex flex-wrap items-center gap-x-2 gap-y-1"><span className={`size-2 shrink-0 rounded-full ${candidate.available ? 'bg-[#9ab77b]' : 'bg-[#8d665c]'}`} title={candidate.available ? 'Кандидат доступен' : 'Кандидат недоступен'} /><span className="text-xs text-foreground">{candidate.profile_name ?? candidate.profile_id}</span>{candidate.upstream_model && <span className="font-mono text-[10px] text-muted-foreground">→ {candidate.upstream_model}</span>}</div><p className="mt-0.5 text-[10px] text-muted-foreground">{candidate.engine_id ?? '—'} · порт {candidate.port ?? '—'}</p>{!candidate.available && candidate.reason !== 'profile_not_running' && <p className="mt-1 text-[11px] text-[#d09b87]">{reasonText(candidate.reason)}</p>}</div>
          {route.kind === 'alias' && <Button variant="ghost" size="icon-sm" iconOnly aria-label={`Удалить ${candidate.profile_name ?? candidate.profile_id} из алиаса`} title={route.candidates.length > 1 ? 'Убрать из алиаса' : 'В алиасе должен остаться профиль'} onClick={() => onRemove(candidate.profile_id)} disabled={route.candidates.length <= 1 || updating}><X className="size-3.5" /></Button>}
        </div>
      </div>
    })}</>}</div>
  </div>
}

function ProfileCheckbox({ profile, checked, onChange }: { profile: ProfileData; checked: boolean; onChange: () => void }) {
  return <label className="flex cursor-pointer items-start gap-2 rounded-sm px-2 py-2 text-xs hover:bg-accent"><input type="checkbox" checked={checked} onChange={onChange} className="mt-0.5 size-4 accent-[#c58055]" /><span className="min-w-0"><span className="block truncate text-foreground">{profile.name}</span><span className="block truncate font-mono text-[10px] text-muted-foreground">{profile.model_name}</span></span></label>
}

function diagnosticText(value: string): string { return value === 'route_name_collision' ? 'Имя маршрута конфликтует с auto-route или моделью.' : value }
function reasonText(value: string | null): string { return ({ profile_missing: 'Профиль не найден.', identity_unverified: 'Идентичность процесса не подтверждена.', port_unconfirmed: 'Порт ещё не подтверждён.', engine_not_ready: 'Движок ещё не готов.', model_metadata_unavailable: 'Метаданные модели ещё не получены.', multiple_upstream_models: 'Движок сообщил несколько моделей без точного совпадения.' } as Record<string, string>)[value ?? ''] ?? 'Кандидат недоступен.' }
