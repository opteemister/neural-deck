import { FormEvent, useEffect, useState, type ReactNode } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { CircleAlert, Command, Pencil, Plus, RefreshCw, Save, TerminalSquare, Trash2, Wrench, X } from 'lucide-react'
import { useSearchParams } from 'react-router-dom'
import { useLogsDrawer } from '@/components/logs-drawer'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Dialog } from '@/components/ui/dialog'
import { ExpandableCard } from '@/components/ui/expandable-card'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Select } from '@/components/ui/select'
import { Textarea } from '@/components/ui/textarea'
import { NotificationToast } from '@/components/ui/notification-toast'
import { PageHeader } from '@/components/page-header'
import { QueryEmpty, QueryError, QueryLoading } from '@/components/query-state'
import { StatusBadge } from '@/components/status-badge'
import { api, queryKeys } from '@/lib/api'
import type { ApiProtocol, EngineData, EngineDefinition, EngineId, EngineType, Installation } from '@/types/api'

type EngineView = 'engines' | 'templates'
type OperationNotification = { id: string; tone: 'success' | 'error'; title: string; message: string }

export function EnginesPage() {
  const openLogs = useLogsDrawer()
  const queryClient = useQueryClient()
  const [params, setParams] = useSearchParams()
  const engines = useQuery({ queryKey: queryKeys.engines, queryFn: api.getEngines })
  const definitions = useQuery({ queryKey: queryKeys.engineDefinitions, queryFn: api.getEngineDefinitions })
  const view: EngineView = params.get('view') === 'templates' ? 'templates' : 'engines'
  const [expanded, setExpanded] = useState<string | null>(params.get('engine'))
  const [installingEngine, setInstallingEngine] = useState<EngineId | null>(null)
  const [targetPath, setTargetPath] = useState('')
  const [executablePath, setExecutablePath] = useState('')
  const [installCommand, setInstallCommand] = useState('')
  const [runInstall, setRunInstall] = useState(true)
  const [editingDefinition, setEditingDefinition] = useState<EngineDefinition | null>(null)
  const [operationId, setOperationId] = useState<string | null>(null)
  const [operationLabel, setOperationLabel] = useState('')
  const [operationNotification, setOperationNotification] = useState<OperationNotification | null>(null)
  const [maintenanceFor, setMaintenanceFor] = useState<string | null>(null)
  const [maintenanceCommand, setMaintenanceCommand] = useState('')
  const operation = useQuery({ queryKey: queryKeys.operation(operationId ?? ''), queryFn: () => api.getOperation(operationId!), enabled: Boolean(operationId), refetchInterval: (query) => ['queued', 'running'].includes(query.state.data?.state ?? '') ? 1_500 : false })
  useEffect(() => {
    if (!operation.data || ['queued', 'running'].includes(operation.data.state)) return
    void queryClient.invalidateQueries({ queryKey: queryKeys.engines })
    void queryClient.invalidateQueries({ queryKey: queryKeys.installations })
    setOperationNotification({
      id: operation.data.id,
      tone: operation.data.state === 'succeeded' ? 'success' : 'error',
      title: operationLabel || 'Операция',
      message: operation.data.state === 'succeeded' ? 'Операция успешно завершена.' : operation.data.error ?? 'Проверьте журнал операции.',
    })
    setOperationId(null)
  }, [operation.data, operationLabel, queryClient])
  const installingEngineData = engines.data?.items.find((item) => item.id === installingEngine)
  const installingDefinition = definitions.data?.items.find((item) => item.id === installingEngine)
  useEffect(() => { setInstallCommand(installingDefinition?.install_script ?? '') }, [installingEngine, installingDefinition?.install_script])
  const engineParam = params.get('engine')
  useEffect(() => {
    const available = engines.data?.items ?? []
    if (available.length === 0) return
    const requested = available.find((engine) => engine.id === engineParam)?.id
    if (requested) setExpanded(requested)
  }, [engineParam, engines.data])
  const scan = useMutation({ mutationFn: api.scanEngines, onSuccess: (data) => queryClient.setQueryData(queryKeys.engines, data) })
  const create = useMutation({ mutationFn: () => api.createInstallation(installingEngine!, { target_path: resolveInstallationTarget(targetPath, installingEngineData?.installation_root ?? ''), run_install: runInstall, ...(!runInstall ? { executable_path: executablePath.trim() } : {}), ...(runInstall && installCommand.trim() ? { command: installCommand } : {}) }), onSuccess: (data) => { setOperationNotification(null); setOperationId(data.id); setOperationLabel(runInstall ? 'Установка движка' : 'Регистрация установки'); setInstallingEngine(null); openLogs(undefined, 'operations'); void queryClient.invalidateQueries({ queryKey: queryKeys.engines }) } })
  const update = useMutation({ mutationFn: (id: string) => api.updateInstallation(id), onSuccess: (data) => { setOperationNotification(null); setOperationId(data.id); setOperationLabel('Обновление установки'); openLogs(undefined, 'operations'); void queryClient.invalidateQueries({ queryKey: queryKeys.engines }) } })
  const maintenance = useMutation({ mutationFn: ({ id, command }: { id: string; command: string }) => api.maintenance(id, command), onSuccess: (data) => { setOperationNotification(null); setOperationId(data.id); setOperationLabel('Обслуживание установки'); openLogs(undefined, 'operations'); setMaintenanceFor(null); setMaintenanceCommand('') } })
  const saveDefinition = useMutation({ mutationFn: ({ definition, existingId }: { definition: EngineDefinition; existingId?: string }) => existingId ? api.updateEngineDefinition(existingId, definition) : api.createEngineDefinition(definition), onSuccess: () => { setEditingDefinition(null); void queryClient.invalidateQueries({ queryKey: queryKeys.engineDefinitions }); void queryClient.invalidateQueries({ queryKey: queryKeys.engines }) } })
  const deleteDefinition = useMutation({ mutationFn: api.deleteEngineDefinition, onSuccess: () => { void queryClient.invalidateQueries({ queryKey: queryKeys.engineDefinitions }); void queryClient.invalidateQueries({ queryKey: queryKeys.engines }) } })

  function setView(next: EngineView) { setParams((current) => { if (next === 'templates') current.set('view', 'templates'); else current.delete('view'); return current }, { replace: true }); setEditingDefinition(null); setInstallingEngine(null) }
  function toggleEngine(id: EngineId) { const next = expanded === id ? null : id; setExpanded(next); setParams((current) => { if (next) current.set('engine', id); else current.delete('engine'); return current }, { replace: true }) }
  function openInstaller(id: EngineId) { setExpanded(id); setInstallingEngine(id); setTargetPath(''); setExecutablePath(''); setRunInstall(true); create.reset(); setParams((current) => { current.delete('view'); current.set('engine', id); return current }, { replace: true }) }
  function closeInstaller() { setInstallingEngine(null); create.reset() }
  function submitCreate(event: FormEvent<HTMLFormElement>) { event.preventDefault(); if (installingEngine && targetPath.trim() && (runInstall || executablePath.trim())) void create.mutate() }
  function requestDeleteDefinition(id: string) { if (window.confirm(`Удалить пользовательское определение «${id}»? Если оно переопределяет встроенный движок, будет восстановлена встроенная версия. Существующие установки и профили не изменятся.`)) void deleteDefinition.mutate(id) }

  if (engines.isLoading) return <><PageHeader eyebrow="Neural Deck / каталог" title="Движки" /><QueryLoading label="Ищем установки движков" /></>
  if (engines.isError) return <><PageHeader eyebrow="Neural Deck / каталог" title="Движки" /><QueryError error={engines.error} retry={() => void engines.refetch()} label="Список движков недоступен" /></>
  const data = engines.data
  if (!data) return <QueryLoading label="Ожидаем список движков" />
  const engineGroups = groupEngines(data.items)
  const renderEngineCard = (engine: EngineData) => <EngineCard key={engine.id} engine={engine} definition={definitions.data?.items.find((item) => item.id === engine.id)} expanded={expanded === engine.id} installing={installingEngine === engine.id} onToggle={() => toggleEngine(engine.id)} onInstall={() => openInstaller(engine.id)} onCloseInstall={closeInstaller} targetPath={targetPath} setTargetPath={setTargetPath} executablePath={executablePath} setExecutablePath={setExecutablePath} installCommand={installCommand} setInstallCommand={setInstallCommand} runInstall={runInstall} setRunInstall={setRunInstall} onSubmitInstall={submitCreate} creating={create.isPending} createError={create.error} onUpdate={(id) => void update.mutate(id)} updating={update.isPending} maintenanceFor={maintenanceFor} setMaintenanceFor={setMaintenanceFor} maintenanceCommand={maintenanceCommand} setMaintenanceCommand={setMaintenanceCommand} onMaintenance={(id, command) => void maintenance.mutate({ id, command })} maintenancePending={maintenance.isPending} />
  return <div>
    <PageHeader eyebrow="Neural Deck / каталог" title="Движки" description={view === 'engines' ? 'Установленные движки и доступные способы запуска.' : 'Шаблоны установки и запуска движков.'} action={view === 'engines' ? <div className="flex gap-2"><Button variant="secondary" size="sm" onClick={() => openLogs(undefined, 'operations')}><TerminalSquare className="size-3.5" />Логи</Button><Button variant="secondary" size="sm" onClick={() => void scan.mutate()} disabled={scan.isPending}><RefreshCw className={`size-3.5 ${scan.isPending ? 'animate-spin' : ''}`} />{scan.isPending ? 'Сканируем…' : 'Повторить поиск'}</Button></div> : undefined} />
    <div className="mb-5 flex flex-wrap gap-1 border-b border-border"><Tab active={view === 'engines'} onClick={() => setView('engines')}>Движки</Tab><Tab active={view === 'templates'} onClick={() => setView('templates')}>Шаблоны</Tab></div>
    {view === 'engines' && scan.isError && <QueryError error={scan.error} retry={() => void scan.mutate()} label="Повторный поиск не выполнен" />}
    {view === 'engines' && update.isError && <QueryError error={update.error} label="Обновление установки не запущено" />}
    {view === 'engines' && maintenance.isError && <QueryError error={maintenance.error} label="Команда обслуживания не запущена" />}
    {operationId && operation.isError && <QueryError error={operation.error} retry={() => void operation.refetch()} label="Состояние операции недоступно" />}
    {operationNotification && <NotificationToast key={operationNotification.id} tone={operationNotification.tone} title={operationNotification.title} message={operationNotification.message} />}
    {view === 'engines' ? engineGroups.length === 0 ? <QueryEmpty title="Движки не найдены" description="Добавьте шаблон движка или повторите поиск установок." /> : <div className="space-y-7">{engineGroups.map((group) => <section key={group.type} aria-labelledby={`engine-group-${group.type}`}><div className="mb-3 flex items-center gap-2 border-b border-border pb-2"><h2 id={`engine-group-${group.type}`} className="text-sm font-medium">{ENGINE_TYPE_LABELS[group.type]}</h2><span className="rounded-sm border border-border px-1.5 py-0.5 text-[10px] text-muted-foreground">{group.items.length}</span></div><div className="grid gap-3">{group.items.map(renderEngineCard)}</div></section>)}</div> : <TemplatesView definitions={definitions.data?.items ?? []} loading={definitions.isLoading} loadError={definitions.error} reload={() => void definitions.refetch()} issues={definitions.data?.issues ?? []} editingDefinition={editingDefinition} onNew={() => setEditingDefinition(emptyEngineDefinition())} onEdit={setEditingDefinition} onCancel={() => { setEditingDefinition(null); saveDefinition.reset() }} onSave={(definition) => saveDefinition.mutate({ definition, existingId: definitions.data?.items.some((item) => item.id === editingDefinition?.id) ? editingDefinition!.id : undefined })} saving={saveDefinition.isPending} saveError={saveDefinition.error} deleting={deleteDefinition.isPending} deleteError={deleteDefinition.error} onDelete={requestDeleteDefinition} />}
  </div>
}

function Tab({ active, onClick, children }: { active: boolean; onClick: () => void; children: string }) { return <button type="button" className={`min-h-10 border-b-2 px-3 text-xs ${active ? 'border-copper text-foreground' : 'border-transparent text-muted-foreground hover:text-foreground'}`} onClick={onClick}>{children}</button> }

const ENGINE_TYPE_LABELS: Record<EngineType, string> = { text: 'Текст', tts: 'TTS', asr: 'ASR', video: 'Видео', image: 'Изображения', embeddings: 'Embeddings', decision: 'Решения' }
const ENGINE_GROUP_ORDER: EngineType[] = ['text', 'embeddings', 'image', 'video', 'tts', 'asr', 'decision']

function groupEngines(engines: EngineData[]): Array<{ type: EngineType; items: EngineData[] }> {
  const groups = new Map<EngineType, EngineData[]>()
  for (const engine of engines) {
    const primaryType = engine.types[0] ?? 'text'
    groups.set(primaryType, [...(groups.get(primaryType) ?? []), engine])
  }
  return ENGINE_GROUP_ORDER.flatMap((type) => {
    const items = groups.get(type)
    if (!items) return []
    return [{
      type,
      items: [...items].sort((left, right) => engineAvailabilityRank(left) - engineAvailabilityRank(right)
        || left.display_name.localeCompare(right.display_name)),
    }]
  })
}

function engineAvailabilityRank(engine: EngineData): number {
  if (engine.installations.some((installation) => installation.status === 'ready')) return 0
  if (engine.installations.length > 0) return 1
  return 2
}

function EngineCard({ engine, definition, expanded, installing, onToggle, onInstall, onCloseInstall, targetPath, setTargetPath, executablePath, setExecutablePath, installCommand, setInstallCommand, runInstall, setRunInstall, onSubmitInstall, creating, createError, onUpdate, updating, maintenanceFor, setMaintenanceFor, maintenanceCommand, setMaintenanceCommand, onMaintenance, maintenancePending }: { engine: EngineData; definition?: EngineDefinition; expanded: boolean; installing: boolean; onToggle: () => void; onInstall: () => void; onCloseInstall: () => void; targetPath: string; setTargetPath: (value: string) => void; executablePath: string; setExecutablePath: (value: string) => void; installCommand: string; setInstallCommand: (value: string) => void; runInstall: boolean; setRunInstall: (value: boolean) => void; onSubmitInstall: (event: FormEvent<HTMLFormElement>) => void; creating: boolean; createError: unknown; onUpdate: (id: string) => void; updating: boolean; maintenanceFor: string | null; setMaintenanceFor: (id: string | null) => void; maintenanceCommand: string; setMaintenanceCommand: (value: string) => void; onMaintenance: (id: string, command: string) => void; maintenancePending: boolean }) {
  const readyCount = engine.installations.filter((item) => item.status === 'ready').length
  const summary = <div><div className="flex flex-wrap items-center gap-2"><CardTitle>{engine.display_name}</CardTitle>{engine.types.map((type) => <span key={type} className="rounded-sm border border-border px-1.5 py-0.5 text-[10px] text-muted-foreground">{ENGINE_TYPE_LABELS[type]}</span>)}</div><CardDescription className="mt-1"><span className="font-mono">{engine.id}</span> · {engine.installations.length === 0 ? 'не установлен' : `${engine.installations.length} установок · ${readyCount} готово`}</CardDescription></div>
  return <ExpandableCard expanded={expanded} onToggle={onToggle} summary={summary} actions={<Button size="sm" variant="secondary" onClick={onInstall}><Plus className="size-3.5" />Установить</Button>} contentClassName="space-y-3">{engine.installations.length === 0 && !installing ? <QueryEmpty title="Движок ещё не установлен" description="Установите его из готового шаблона или зарегистрируйте существующий executable." /> : engine.installations.length > 0 && <div className="space-y-2">{engine.installations.map((installation) => <InstallationRow key={installation.id} installation={installation} updateSupported={installation.kind === 'python_venv' || (installation.kind === 'custom' && Boolean(definition?.update_script))} onUpdate={onUpdate} updating={updating} maintenanceFor={maintenanceFor} setMaintenanceFor={setMaintenanceFor} maintenanceCommand={maintenanceCommand} setMaintenanceCommand={setMaintenanceCommand} onMaintenance={onMaintenance} maintenancePending={maintenancePending} />)}</div>}{installing && <InstallationForm engine={engine} definition={definition} targetPath={targetPath} setTargetPath={setTargetPath} executablePath={executablePath} setExecutablePath={setExecutablePath} installCommand={installCommand} setInstallCommand={setInstallCommand} runInstall={runInstall} setRunInstall={setRunInstall} onSubmit={onSubmitInstall} onClose={onCloseInstall} creating={creating} error={createError} />}<p className="border-t border-border pt-3 text-[11px] text-muted-foreground">Для профилей доступны только подтверждённые установки со статусом «Готово».</p></ExpandableCard>
}

function InstallationForm({ engine, definition, targetPath, setTargetPath, executablePath, setExecutablePath, installCommand, setInstallCommand, runInstall, setRunInstall, onSubmit, onClose, creating, error }: { engine: EngineData; definition?: EngineDefinition; targetPath: string; setTargetPath: (value: string) => void; executablePath: string; setExecutablePath: (value: string) => void; installCommand: string; setInstallCommand: (value: string) => void; runInstall: boolean; setRunInstall: (value: boolean) => void; onSubmit: (event: FormEvent<HTMLFormElement>) => void; onClose: () => void; creating: boolean; error: unknown }) {
  const hasScript = Boolean(installCommand.trim() || definition?.install_script?.trim())
  return <form className="space-y-4 rounded-md border border-copper/50 bg-background p-4" onSubmit={onSubmit}><div className="flex items-start justify-between gap-3"><div><p className="text-sm font-medium">Новая установка {engine.display_name}</p><p className="mt-1 text-[11px] text-muted-foreground">Проверьте каталог и скрипт перед запуском.</p></div><Button type="button" size="icon" variant="ghost" aria-label="Закрыть форму установки" title="Закрыть" onClick={onClose}><X className="size-4" /></Button></div><div><Label htmlFor={`installation-target-${engine.id}`}>Имя установки или абсолютный путь</Label><Input id={`installation-target-${engine.id}`} className="mt-1.5 font-mono" value={targetPath} onChange={(event) => setTargetPath(event.target.value)} placeholder={`${engine.id}-latest`} required /></div><label className="flex min-h-9 items-center gap-2 text-xs"><input type="checkbox" checked={runInstall} onChange={(event) => setRunInstall(event.target.checked)} className="size-4 accent-[#c58055]" />Установить автоматически по скрипту шаблона</label>{runInstall ? <div><Label htmlFor={`installation-command-${engine.id}`}>Скрипт установки</Label><Textarea id={`installation-command-${engine.id}`} className="mt-1.5 font-mono" rows={9} value={installCommand} onChange={(event) => setInstallCommand(event.target.value)} spellCheck={false} placeholder={'#!/bin/sh\nset -eu\n...'} /><p className="mt-1.5 text-[11px] text-muted-foreground">Можно изменить только для этого запуска. Шаблон движка останется прежним.</p></div> : <div><Label htmlFor={`existing-executable-${engine.id}`}>Путь к уже установленному executable</Label><Input id={`existing-executable-${engine.id}`} className="mt-1.5 font-mono" value={executablePath} onChange={(event) => setExecutablePath(event.target.value)} placeholder="/opt/engines/my-engine/bin/server" required /></div>}{runInstall && !hasScript && <p className="text-[11px] text-[#d0a390]">У шаблона нет скрипта установки. Введите его выше или зарегистрируйте существующий executable.</p>}<div className="flex flex-wrap items-center gap-3"><Button type="submit" variant="primary" disabled={creating || !targetPath.trim() || (runInstall && !hasScript) || (!runInstall && !executablePath.trim())}><TerminalSquare className="size-3.5" />{creating ? 'Запускаем…' : runInstall ? 'Установить' : 'Зарегистрировать'}</Button><Button type="button" variant="ghost" onClick={onClose}>Отмена</Button>{Boolean(error) && <QueryError error={error} label="Операция не создана" />}</div></form>
}

function resolveInstallationTarget(value: string, installationRoot: string): string {
  const target = value.trim()
  if (!target || target.startsWith('/')) return target
  return `${installationRoot.replace(/\/$/, '')}/${target}`
}

function TemplatesView({ definitions, loading, loadError, reload, issues, editingDefinition, onNew, onEdit, onCancel, onSave, saving, saveError, deleting, deleteError, onDelete }: { definitions: EngineDefinition[]; loading: boolean; loadError: unknown; reload: () => void; issues: Array<{ file: string; code: string; message: string }>; editingDefinition: EngineDefinition | null; onNew: () => void; onEdit: (definition: EngineDefinition) => void; onCancel: () => void; onSave: (definition: EngineDefinition) => void; saving: boolean; saveError: unknown; deleting: boolean; deleteError: unknown; onDelete: (id: string) => void }) {
  const editingExisting = Boolean(editingDefinition && definitions.some((item) => item.id === editingDefinition.id))
  return <div className="space-y-4"><div className="flex flex-wrap items-center justify-between gap-3"><div><h2 className="text-base font-medium">Шаблоны движков</h2><p className="mt-1 text-xs text-muted-foreground">Редактируйте встроенные определения или добавляйте новые движки.</p></div><Button variant="primary" size="sm" onClick={onNew}><Plus className="size-3.5" />Новый шаблон</Button></div><Dialog open={editingDefinition !== null} title={editingExisting ? `Изменить шаблон ${editingDefinition?.name ?? ''}` : 'Новый шаблон движка'} size="wide" onOpenChange={(open) => { if (!open) onCancel() }}>{editingDefinition && <EngineDefinitionEditor key={editingDefinition.id} initial={editingDefinition} idLocked={editingExisting} saving={saving} error={saveError} onCancel={onCancel} onSave={onSave} />}</Dialog>{loading ? <QueryLoading label="Загружаем шаблоны движков" /> : loadError ? <QueryError error={loadError} retry={reload} label="Шаблоны недоступны" /> : definitions.length === 0 ? <QueryEmpty title="Шаблоны не найдены" description="Добавьте первый шаблон движка." /> : <Card><CardContent className="space-y-2">{definitions.map((definition) => <div key={definition.id} className="flex flex-wrap items-center justify-between gap-3 border-b border-border py-3 last:border-b-0"><div className="min-w-0"><div className="flex flex-wrap items-center gap-2"><p className="text-sm font-medium">{definition.name}</p>{definition.types.map((type) => <span key={type} className="rounded-sm border border-border px-1.5 py-0.5 text-[10px] text-muted-foreground">{ENGINE_TYPE_LABELS[type]}</span>)}</div><p className="mt-1 font-mono text-[11px] text-muted-foreground">{definition.id}{definition.install_script ? ' · есть скрипт установки' : ' · без скрипта установки'}</p></div><div className="flex gap-2"><Button size="sm" variant="secondary" onClick={() => onEdit(definition)}><Pencil className="size-3.5" />Изменить</Button><Button size="sm" variant="danger" disabled={deleting} onClick={() => onDelete(definition.id)}><Trash2 className="size-3.5" />Удалить / сбросить</Button></div></div>)}</CardContent></Card>}{issues.map((issue) => <p key={`${issue.file}-${issue.code}`} className="text-xs text-[#d0a390]">{issue.file}: {issue.message}</p>)}{Boolean(deleteError) && <QueryError error={deleteError} label="Шаблон не удалён" />}</div>
}

function InstallationRow({ installation, updateSupported, onUpdate, updating, maintenanceFor, setMaintenanceFor, maintenanceCommand, setMaintenanceCommand, onMaintenance, maintenancePending }: { installation: Installation; updateSupported: boolean; onUpdate: (id: string) => void; updating: boolean; maintenanceFor: string | null; setMaintenanceFor: (id: string | null) => void; maintenanceCommand: string; setMaintenanceCommand: (value: string) => void; onMaintenance: (id: string, command: string) => void; maintenancePending: boolean }) {
  const queryClient = useQueryClient()
  const forget = useMutation({ mutationFn: api.forgetInstallation, onSuccess: () => { void queryClient.invalidateQueries({ queryKey: queryKeys.engines }); void queryClient.invalidateQueries({ queryKey: queryKeys.profiles }) } })
  const canUpdate = updateSupported && installation.status === 'ready'
  return <div className="rounded-md border border-border bg-secondary px-3 py-3"><div className="flex flex-wrap items-start justify-between gap-2"><div className="min-w-0"><p className="break-all font-mono text-xs text-foreground">{String(installation.executable_path)}</p><p className="mt-1 text-[11px] text-muted-foreground">{installation.kind === 'python_venv' ? 'Python venv' : installation.kind === 'binary' ? 'Бинарный файл' : 'Пользовательская установка'} · {installation.version ?? 'версия не определена'}</p></div><div className="flex items-center gap-2"><StatusBadge state={installation.status} context="installation" />{installation.registered && <Button size="sm" variant="danger" disabled={forget.isPending} title="Удалить запись из конфигурации; файлы останутся на диске" onClick={() => forget.mutate(installation.id)}><Trash2 className="size-3.5" />Удалить запись</Button>}</div></div>{forget.isError && <QueryError error={forget.error} label="Запись установки не удалена" />}{installation.diagnostic && <p className="mt-2 flex gap-1.5 text-[11px] text-[#d0a390]"><CircleAlert className="mt-0.5 size-3 shrink-0" />{installation.diagnostic}</p>}<p className="mt-2 break-all text-[10px] text-muted-foreground">{installation.verified ? 'Проверено адаптером движка' : 'Проверка не подтверждена'} · id <code>{installation.id}</code></p><div className="mt-3 flex flex-wrap gap-2 border-t border-border pt-3"><Button size="sm" variant="secondary" disabled={!canUpdate || updating} title={!updateSupported ? 'В шаблоне движка нет поддерживаемого сценария обновления' : installation.status !== 'ready' ? 'Сначала восстановите установку' : undefined} onClick={() => onUpdate(installation.id)}><RefreshCw className="size-3.5" />Обновить до актуальной версии</Button><Button size="sm" variant="ghost" onClick={() => setMaintenanceFor(maintenanceFor === installation.id ? null : installation.id)}><Wrench className="size-3.5" />Обслуживание</Button></div>{maintenanceFor === installation.id && <div className="mt-3 space-y-2 border-t border-border pt-3"><Label htmlFor={`maintenance-${installation.id}`}>Команда обслуживания</Label><Textarea id={`maintenance-${installation.id}`} rows={2} value={maintenanceCommand} onChange={(event) => setMaintenanceCommand(event.target.value)} placeholder="python -m pip check" spellCheck={false} /><div className="flex items-center gap-2"><Command className="size-3.5 text-copper" /><span className="text-[11px] text-muted-foreground">Backend выполнит ровно эту подтверждённую команду.</span><Button className="ml-auto" size="sm" variant="primary" disabled={maintenancePending || !maintenanceCommand.trim()} onClick={() => onMaintenance(installation.id, maintenanceCommand.trim())}>Выполнить</Button></div></div>}</div>
}

const ENGINE_TYPES: EngineType[] = ['text', 'tts', 'asr', 'video', 'image', 'embeddings', 'decision']

function emptyEngineDefinition(): EngineDefinition {
  return {
    version: 1, id: '', name: '', types: ['text'], install_script: '', update_script: null,
    executable: '', working_directory: null, environment: {}, command: '', healthcheck: null,
    api: null, ui_url: null, version_command: null,
  }
}

function EngineDefinitionEditor({ initial, idLocked, saving, error, onCancel, onSave }: {
  initial: EngineDefinition
  idLocked: boolean
  saving: boolean
  error: unknown
  onCancel: () => void
  onSave: (definition: EngineDefinition) => void
}) {
  const [draft, setDraft] = useState(initial)
  const [environment, setEnvironment] = useState(Object.entries(initial.environment).map(([key, value]) => `${key}=${value}`).join('\n'))
  const [versionCommand, setVersionCommand] = useState(initial.version_command?.join(' ') ?? '')
  const [apiProtocol, setApiProtocol] = useState<ApiProtocol | ''>(initial.api?.protocol ?? '')
  const [apiBaseUrl, setApiBaseUrl] = useState(initial.api?.base_url ?? '')
  const [healthPath, setHealthPath] = useState(initial.healthcheck?.path ?? '')
  const [healthTimeout, setHealthTimeout] = useState(String(initial.healthcheck?.timeout_seconds ?? 3))
  const set = <K extends keyof EngineDefinition>(key: K, value: EngineDefinition[K]) => setDraft((current) => ({ ...current, [key]: value }))

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const api = apiProtocol ? { protocol: apiProtocol, base_url: apiProtocol === 'none' ? null : apiBaseUrl.trim() || null } : null
    const healthcheck = healthPath.trim() && apiBaseUrl.trim() && apiProtocol && apiProtocol !== 'none'
      ? { path: healthPath.trim(), timeout_seconds: Number(healthTimeout) || 3 }
      : null
    const environmentMap = Object.fromEntries(environment.split('\n').map((line) => line.trim()).filter(Boolean).map((line) => {
      const separator = line.indexOf('=')
      return separator > 0 ? [line.slice(0, separator).trim(), line.slice(separator + 1)] : [line, '']
    }))
    onSave({
      ...draft,
      id: draft.id.trim(), name: draft.name.trim(),
      types: ENGINE_TYPES.filter((type) => draft.types.includes(type)),
      install_script: draft.install_script?.trim() ? draft.install_script : null,
      update_script: draft.update_script?.trim() ? draft.update_script : null,
      executable: draft.executable.trim(),
      working_directory: draft.working_directory?.trim() || null,
      environment: environmentMap,
      command: draft.command.trim(),
      healthcheck,
      api,
      ui_url: draft.ui_url?.trim() || null,
      version_command: versionCommand.trim() ? versionCommand.trim().split(/\s+/) : null,
    })
  }

  return <form className="space-y-4 p-4" onSubmit={submit}>
    <div className="space-y-5">
      <FormSection title="Основное"><div className="grid gap-3 md:grid-cols-2"><Field id="definition-id" label="ID" required><Input id="definition-id" value={draft.id} onChange={(event) => set('id', event.target.value)} disabled={idLocked} required /></Field><Field id="definition-name" label="Название" required><Input id="definition-name" value={draft.name} onChange={(event) => set('name', event.target.value)} required /></Field><div className="md:col-span-2"><Label>Типы движка <span className="text-copper" aria-hidden="true">*</span></Label><div className="mt-2 flex flex-wrap gap-x-4 gap-y-2">{ENGINE_TYPES.map((type) => <label key={type} className="flex items-center gap-1.5 text-xs"><input type="checkbox" checked={draft.types.includes(type)} onChange={(event) => set('types', event.target.checked ? [...draft.types, type] : draft.types.filter((item) => item !== type))} className="size-4 accent-[#c58055]" />{type}</label>)}</div></div></div></FormSection>
      <FormSection title="Установка"><div className="grid gap-3 md:grid-cols-2"><Field id="definition-executable" label="Executable относительно установки или абсолютный путь" required><Input id="definition-executable" className="font-mono" value={draft.executable} onChange={(event) => set('executable', event.target.value)} required /></Field><Field id="definition-working-directory" label="Рабочий каталог"><Input id="definition-working-directory" className="font-mono" value={draft.working_directory ?? ''} onChange={(event) => set('working_directory', event.target.value || null)} /></Field><Field id="definition-install-script" label="Скрипт установки"><Textarea id="definition-install-script" className="font-mono" rows={3} value={draft.install_script ?? ''} onChange={(event) => set('install_script', event.target.value || null)} spellCheck={false} placeholder={'#!/bin/sh\nset -eu\n...'} /></Field><Field id="definition-update-script" label="Скрипт обновления"><Textarea id="definition-update-script" className="font-mono" rows={3} value={draft.update_script ?? ''} onChange={(event) => set('update_script', event.target.value || null)} spellCheck={false} /></Field></div></FormSection>
      <FormSection title="Запуск"><div className="grid gap-3 md:grid-cols-2"><Field id="definition-command" label="Шаблон команды запуска" required><Textarea id="definition-command" className="font-mono" rows={2} value={draft.command} onChange={(event) => set('command', event.target.value)} required spellCheck={false} /></Field><Field id="definition-environment" label="Переменные окружения"><Textarea id="definition-environment" className="font-mono" rows={2} value={environment} onChange={(event) => setEnvironment(event.target.value)} placeholder="KEY=value" spellCheck={false} /></Field></div></FormSection>
      <FormSection title="API и интерфейс"><div className="grid gap-3 md:grid-cols-3"><Field id="definition-api-protocol" label="Протокол API"><Select id="definition-api-protocol" value={apiProtocol} onChange={(event) => setApiProtocol(event.target.value as ApiProtocol | '')}><option value="">Не подключать</option><option value="none">none</option><option value="openai">openai</option><option value="comfyui">comfyui</option><option value="jev">jev</option></Select></Field><Field id="definition-api-url" label="API base URL" required={Boolean(apiProtocol && apiProtocol !== 'none')}><Input id="definition-api-url" type="url" className="font-mono" value={apiBaseUrl} onChange={(event) => setApiBaseUrl(event.target.value)} placeholder="http://127.0.0.1:8000/v1" required={Boolean(apiProtocol && apiProtocol !== 'none')} /></Field><Field id="definition-ui-url" label="UI URL"><Input id="definition-ui-url" type="url" className="font-mono" value={draft.ui_url ?? ''} onChange={(event) => set('ui_url', event.target.value || null)} /></Field></div></FormSection>
      <FormSection title="Проверки"><div className="grid gap-3 md:grid-cols-3"><Field id="definition-health-path" label="Healthcheck path"><Input id="definition-health-path" className="font-mono" value={healthPath} onChange={(event) => setHealthPath(event.target.value)} placeholder="/health" /></Field><Field id="definition-health-timeout" label="Таймаут healthcheck, секунд"><Input id="definition-health-timeout" type="number" min="0.1" step="0.1" value={healthTimeout} onChange={(event) => setHealthTimeout(event.target.value)} /></Field><Field id="definition-version-command" label="Команда определения версии"><Input id="definition-version-command" className="font-mono" value={versionCommand} onChange={(event) => setVersionCommand(event.target.value)} placeholder="{{engine}} --version" spellCheck={false} /></Field></div></FormSection>
    </div>
    {healthPath.trim() && (!apiBaseUrl.trim() || !apiProtocol || apiProtocol === 'none') && <p className="text-xs text-[#d0a390]">Healthcheck требует настроенный API protocol и base URL.</p>}
    {Boolean(error) && <QueryError error={error} label="Определение не сохранено" />}
    <div className="flex justify-end gap-2"><Button type="button" variant="ghost" onClick={onCancel}>Отмена</Button><Button type="submit" variant="primary" disabled={saving || !draft.id.trim() || !draft.name.trim() || !draft.executable.trim() || !draft.command.trim() || draft.types.length === 0 || Boolean(healthPath.trim() && (!apiBaseUrl.trim() || !apiProtocol || apiProtocol === 'none'))}><Save className="size-3.5" />{saving ? 'Сохраняем…' : 'Сохранить определение'}</Button></div>
  </form>
}

function FormSection({ title, children }: { title: string; children: ReactNode }) {
  return <fieldset className="space-y-3 border-t border-border pt-3"><legend className="pr-2 text-xs font-medium text-muted-foreground">{title}</legend>{children}</fieldset>
}

function Field({ id, label, required = false, children }: { id: string; label: string; required?: boolean; children: ReactNode }) {
  return <div><Label htmlFor={id}>{label}{required && <span className="text-copper" aria-hidden="true"> *</span>}</Label><div className="mt-1.5">{children}</div></div>
}
