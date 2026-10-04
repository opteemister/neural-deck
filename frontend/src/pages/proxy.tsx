import { useMemo, useState, type DragEvent } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { AlertTriangle, ChevronDown, ChevronUp, GripVertical, Plus, RefreshCw, Trash2 } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Badge } from '@/components/ui/badge'
import { PageHeader } from '@/components/page-header'
import { QueryEmpty, QueryError, QueryLoading } from '@/components/query-state'
import { api, queryKeys } from '@/lib/api'
import type { ProfileData, ProxyRoute } from '@/types/api'

const aliasPattern = /^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/

export function ProxyPage() {
  const queryClient = useQueryClient()
  const routes = useQuery({ queryKey: queryKeys.proxyRoutes, queryFn: api.getProxyRoutes, refetchInterval: 10_000 })
  const profiles = useQuery({ queryKey: queryKeys.profiles, queryFn: api.getProfiles })
  const [aliasName, setAliasName] = useState('')
  const [selectedProfiles, setSelectedProfiles] = useState<string[]>([])

  const invalidateRoutes = () => {
    void queryClient.invalidateQueries({ queryKey: queryKeys.proxyRoutes })
    void queryClient.invalidateQueries({ queryKey: queryKeys.openAiModels })
  }
  const createAlias = useMutation({
    mutationFn: api.createProxyAlias,
    onSuccess: () => { setAliasName(''); setSelectedProfiles([]); invalidateRoutes() },
  })
  const updateRoute = useMutation({
    mutationFn: ({ name, profileIds }: { name: string; profileIds: string[] }) => api.updateProxyRoute(name, profileIds),
    onSuccess: invalidateRoutes,
  })
  const deleteAlias = useMutation({
    mutationFn: api.deleteProxyAlias,
    onSuccess: invalidateRoutes,
  })

  const routeNames = useMemo(() => new Set(routes.data?.items.map((route) => route.name) ?? []), [routes.data?.items])
  const aliasError = aliasName.length === 0
    ? null
    : !aliasPattern.test(aliasName)
      ? 'Используйте 1–128 ASCII-символов: буквы, цифры, точку, дефис или подчёркивание.'
      : routeNames.has(aliasName) || (profiles.data?.items ?? []).some((profile) => profile.model_name === aliasName)
        ? 'Это имя уже занято маршрутом или публичным именем модели.'
        : null
  const mutationError = createAlias.error ?? updateRoute.error ?? deleteAlias.error

  function toggleProfile(name: string) {
    setSelectedProfiles((current) => current.includes(name) ? current.filter((item) => item !== name) : [...current, name])
  }

  function reorder(route: ProxyRoute, from: number, to: number) {
    if (to < 0 || to >= route.candidate_profile_ids.length || from === to) return
    const profileIds = [...route.candidate_profile_ids]
    const [item] = profileIds.splice(from, 1)
    profileIds.splice(to, 0, item)
    updateRoute.mutate({ name: route.name, profileIds })
  }

  function drop(route: ProxyRoute, event: DragEvent<HTMLDivElement>) {
    event.preventDefault()
    const profileId = event.dataTransfer.getData('text/plain')
    const from = route.candidate_profile_ids.indexOf(profileId)
    if (from >= 0) reorder(route, from, Number(event.currentTarget.dataset.index))
  }

  return <div>
    <PageHeader title="Прокси" action={<Button variant="secondary" size="sm" onClick={() => { void routes.refetch(); void profiles.refetch() }}><RefreshCw className="size-3.5" />Обновить</Button>} />
    <div className="mb-6 grid gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(18rem,24rem)]">
      <Card>
        <CardContent className="space-y-3">
          {routes.isLoading ? <QueryLoading label="Читаем маршруты" /> : routes.isError ? <QueryError error={routes.error} retry={() => void routes.refetch()} label="Маршруты недоступны" /> : routes.data?.items.length === 0 ? <QueryEmpty title="Маршрутов пока нет" description="Создайте профиль — auto-route появится автоматически." /> : routes.data?.items.map((route) => <RouteCard key={`${route.kind}:${route.name}`} route={route} onMove={reorder} onDrop={drop} onDelete={() => deleteAlias.mutate(route.name)} deleting={deleteAlias.isPending && deleteAlias.variables === route.name} />)}
        </CardContent>
      </Card>

      <Card className="h-fit">
        <CardHeader><CardTitle>Новый алиас</CardTitle></CardHeader>
        <CardContent>
          <div className="space-y-4">
            <div><label htmlFor="proxy-alias-name" className="text-xs text-foreground">Имя алиаса</label><Input id="proxy-alias-name" className="mt-1.5" value={aliasName} onChange={(event) => setAliasName(event.target.value)} placeholder="my-chat-model" aria-invalid={Boolean(aliasError)} />{aliasError && <p className="mt-1.5 text-[11px] text-[#d09b87]">{aliasError}</p>}</div>
            <div><p className="text-xs text-foreground">Профили-кандидаты</p><div className="mt-1.5 max-h-64 space-y-1 overflow-auto rounded-md border border-border bg-secondary p-2">{profiles.isLoading ? <p className="px-2 py-3 text-xs text-muted-foreground">Читаем профили…</p> : profiles.data?.items.length === 0 ? <p className="px-2 py-3 text-xs text-muted-foreground">Сначала создайте профиль.</p> : profiles.data?.items.map((profile) => <ProfileCheckbox key={profile.name} profile={profile} checked={selectedProfiles.includes(profile.name)} onChange={() => toggleProfile(profile.name)} />)}</div></div>
            <Button variant="primary" className="w-full" disabled={!aliasName || Boolean(aliasError) || selectedProfiles.length === 0 || createAlias.isPending} onClick={() => createAlias.mutate({ name: aliasName, profile_ids: selectedProfiles })}><Plus className="size-3.5" />{createAlias.isPending ? 'Сохраняем…' : 'Добавить алиас'}</Button>
          </div>
        </CardContent>
      </Card>
    </div>
    {mutationError && <QueryError error={mutationError} label="Изменение маршрута не сохранено" />}
  </div>
}

function RouteCard({ route, onMove, onDrop, onDelete, deleting }: { route: ProxyRoute; onMove: (route: ProxyRoute, from: number, to: number) => void; onDrop: (route: ProxyRoute, event: DragEvent<HTMLDivElement>) => void; onDelete: () => void; deleting: boolean }) {
  return <div className="rounded-md border border-border bg-secondary/40">
    <div className="flex flex-wrap items-center gap-2 border-b border-border px-3 py-2.5"><h3 className="font-mono text-xs text-foreground">{route.name}</h3><Badge>{route.kind === 'auto' ? 'auto-route' : 'алиас'}</Badge><Badge className={route.available ? 'border-[#596c4b] text-[#b2c995]' : 'border-[#72544a] text-[#d0a390]'}>{route.available ? 'доступен' : 'скрыт из /v1/models'}</Badge>{route.kind === 'alias' && <Button variant="danger" size="icon-sm" iconOnly className="ml-auto" aria-label={`Удалить алиас ${route.name}`} title="Удалить алиас" onClick={onDelete} disabled={deleting}><Trash2 className="size-3.5" /></Button>}</div>
    {route.diagnostics.map((diagnostic) => <p key={diagnostic} className="flex items-center gap-1.5 border-b border-border px-3 py-2 text-[11px] text-[#d09b87]"><AlertTriangle className="size-3.5" />{diagnosticText(diagnostic)}</p>)}
    <div className="divide-y divide-border">{route.candidates.length === 0 ? <p className="px-3 py-3 text-xs text-muted-foreground">Кандидаты не настроены.</p> : route.candidates.map((candidate, index) => <div key={`${candidate.profile_id}-${index}`} data-index={index} draggable={route.candidates.length > 1} onDragStart={(event) => { event.dataTransfer.effectAllowed = 'move'; event.dataTransfer.setData('text/plain', candidate.profile_id); (event.currentTarget as HTMLDivElement).dataset.dragging = 'true' }} onDragEnd={(event) => { delete (event.currentTarget as HTMLDivElement).dataset.dragging }} onDragOver={(event) => event.preventDefault()} onDrop={(event) => onDrop(route, event)} className="flex items-center gap-2 px-3 py-2.5 transition-colors data-[dragging=true]:opacity-50"><GripVertical className="size-4 shrink-0 cursor-grab text-muted-foreground" aria-label="Перетащить кандидата" /><div className="min-w-0 flex-1"><div className="flex flex-wrap items-center gap-x-2 gap-y-1"><span className="text-xs text-foreground">{candidate.profile_name ?? candidate.profile_id}</span>{candidate.upstream_model && <span className="font-mono text-[10px] text-muted-foreground">→ {candidate.upstream_model}</span>}</div><p className="mt-0.5 text-[10px] text-muted-foreground">{candidate.engine_id ?? '—'} · порт {candidate.port ?? '—'} · {candidate.state}</p>{!candidate.available && <p className="mt-1 text-[11px] text-[#d09b87]">{reasonText(candidate.reason)}</p>}</div><span className={`size-2 shrink-0 rounded-full ${candidate.available ? 'bg-[#9ab77b]' : 'bg-[#8d665c]'}`} title={candidate.available ? 'Кандидат доступен' : 'Кандидат недоступен'} /><div className="flex shrink-0 gap-1"><Button variant="ghost" size="icon-sm" iconOnly aria-label="Поднять кандидата" title="Поднять" onClick={() => onMove(route, index, index - 1)} disabled={index === 0}><ChevronUp className="size-3.5" /></Button><Button variant="ghost" size="icon-sm" iconOnly aria-label="Опустить кандидата" title="Опустить" onClick={() => onMove(route, index, index + 1)} disabled={index === route.candidates.length - 1}><ChevronDown className="size-3.5" /></Button></div></div>)}</div>
  </div>
}

function ProfileCheckbox({ profile, checked, onChange }: { profile: ProfileData; checked: boolean; onChange: () => void }) {
  return <label className="flex cursor-pointer items-start gap-2 rounded-sm px-2 py-2 text-xs hover:bg-accent"><input type="checkbox" checked={checked} onChange={onChange} className="mt-0.5 size-4 accent-[#c58055]" /><span className="min-w-0"><span className="block truncate text-foreground">{profile.name}</span><span className="block truncate font-mono text-[10px] text-muted-foreground">{profile.model_name}</span></span></label>
}

function diagnosticText(value: string): string { return value === 'route_name_collision' ? 'Имя маршрута конфликтует с auto-route или моделью.' : value }
function reasonText(value: string | null): string { return ({ profile_missing: 'Профиль не найден.', profile_not_running: 'Профиль не запущен.', identity_unverified: 'Идентичность процесса не подтверждена.', port_unconfirmed: 'Порт ещё не подтверждён.', engine_not_ready: 'Движок ещё не готов.', model_metadata_unavailable: 'Метаданные модели ещё не получены.', multiple_upstream_models: 'Движок сообщил несколько моделей без точного совпадения.' } as Record<string, string>)[value ?? ''] ?? 'Кандидат недоступен.' }
