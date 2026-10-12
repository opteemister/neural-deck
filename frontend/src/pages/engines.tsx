import { FormEvent, useEffect, useState, type ReactNode } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ChevronDown, CircleAlert, Command, FolderPlus, Pencil, Plus, RefreshCw, Save, TerminalSquare, Trash2, Wrench } from 'lucide-react'
import { useSearchParams } from 'react-router-dom'
import { useLogsDrawer } from '@/components/logs-drawer'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Select } from '@/components/ui/select'
import { Textarea } from '@/components/ui/textarea'
import { PageHeader } from '@/components/page-header'
import { QueryEmpty, QueryError, QueryLoading } from '@/components/query-state'
import { StatusBadge } from '@/components/status-badge'
import { api, queryKeys, NeuralDeckApiError } from '@/lib/api'
import type { ApiProtocol, EngineDefinition, EngineId, EngineType, Installation, Operation } from '@/types/api'

export function EnginesPage() {
  const openLogs = useLogsDrawer()
  const queryClient = useQueryClient()
  const [params, setParams] = useSearchParams()
  const engines = useQuery({ queryKey: queryKeys.engines, queryFn: api.getEngines })
  const definitions = useQuery({ queryKey: queryKeys.engineDefinitions, queryFn: api.getEngineDefinitions })
  const [expanded, setExpanded] = useState<string | null>(params.get('engine'))
  const [selectedEngine, setSelectedEngine] = useState<EngineId>(params.get('engine') ?? '')
  const [targetPath, setTargetPath] = useState('')
  const [executablePath, setExecutablePath] = useState('')
  const [installCommand, setInstallCommand] = useState('')
  const [runInstall, setRunInstall] = useState(true)
  const [editingDefinition, setEditingDefinition] = useState<EngineDefinition | null>(null)
  const [operationId, setOperationId] = useState<string | null>(null)
  const [operationLabel, setOperationLabel] = useState('')
  const [operationCommand, setOperationCommand] = useState<string | null>(null)
  const [maintenanceFor, setMaintenanceFor] = useState<string | null>(null)
  const [maintenanceCommand, setMaintenanceCommand] = useState('')
  const operation = useQuery({ queryKey: queryKeys.operation(operationId ?? ''), queryFn: () => api.getOperation(operationId!), enabled: Boolean(operationId), refetchInterval: (query) => ['queued', 'running'].includes(query.state.data?.state ?? '') ? 1_500 : false })
  useEffect(() => { if (operation.data && !['queued', 'running'].includes(operation.data.state)) { void queryClient.invalidateQueries({ queryKey: queryKeys.engines }); void queryClient.invalidateQueries({ queryKey: queryKeys.installations }) } }, [operation.data, queryClient])
  const selectedEngineData = engines.data?.items.find((item) => item.id === selectedEngine)
  const selectedDefinition = definitions.data?.items.find((item) => item.id === selectedEngine)
  useEffect(() => { setInstallCommand(selectedDefinition?.install_script ?? '') }, [selectedEngine, selectedDefinition?.install_script])
  const engineParam = params.get('engine')
  useEffect(() => {
    const available = engines.data?.items ?? []
    if (available.length === 0) return
    const requested = available.find((engine) => engine.id === engineParam)?.id
    const effective = requested ?? (available.some((engine) => engine.id === selectedEngine) ? selectedEngine : available[0].id)
    if (selectedEngine !== effective) setSelectedEngine(effective)
    if (requested) setExpanded(requested)
  }, [engineParam, engines.data, selectedEngine])
  const scan = useMutation({ mutationFn: api.scanEngines, onSuccess: (data) => queryClient.setQueryData(queryKeys.engines, data) })
  const create = useMutation({ mutationFn: () => api.createInstallation(selectedEngine, { target_path: targetPath.trim(), run_install: runInstall, ...(!runInstall ? { executable_path: executablePath.trim() } : {}), ...(runInstall && installCommand.trim() ? { command: installCommand } : {}) }), onSuccess: (data) => { setOperationId(data.id); setOperationLabel(runInstall ? 'Установка движка' : 'Регистрация установки'); setOperationCommand(null); openLogs(undefined, 'operations'); void queryClient.invalidateQueries({ queryKey: queryKeys.engines }) } })
  const update = useMutation({ mutationFn: (id: string) => api.updateInstallation(id), onSuccess: (data) => { setOperationId(data.id); setOperationLabel('Обновление установки'); setOperationCommand(null); openLogs(undefined, 'operations'); void queryClient.invalidateQueries({ queryKey: queryKeys.engines }) } })
  const maintenance = useMutation({ mutationFn: ({ id, command }: { id: string; command: string }) => api.maintenance(id, command), onSuccess: (data, variables) => { setOperationId(data.id); setOperationLabel('Обслуживание установки'); setOperationCommand(variables.command); openLogs(undefined, 'operations'); setMaintenanceFor(null); setMaintenanceCommand('') } })
  const saveDefinition = useMutation({ mutationFn: ({ definition, existingId }: { definition: EngineDefinition; existingId?: string }) => existingId ? api.updateEngineDefinition(existingId, definition) : api.createEngineDefinition(definition), onSuccess: () => { setEditingDefinition(null); void queryClient.invalidateQueries({ queryKey: queryKeys.engineDefinitions }); void queryClient.invalidateQueries({ queryKey: queryKeys.engines }) } })
  const deleteDefinition = useMutation({ mutationFn: api.deleteEngineDefinition, onSuccess: () => { void queryClient.invalidateQueries({ queryKey: queryKeys.engineDefinitions }); void queryClient.invalidateQueries({ queryKey: queryKeys.engines }) } })

  function selectEngine(id: EngineId) { setSelectedEngine(id) }
  function toggleEngine(id: EngineId) { const next = expanded === id ? null : id; setSelectedEngine(id); setExpanded(next); setParams((current) => { if (next) current.set('engine', id); else current.delete('engine'); return current }, { replace: true }) }
  function submitCreate(event: FormEvent<HTMLFormElement>) { event.preventDefault(); if (selectedEngine && targetPath.trim() && (runInstall || executablePath.trim())) void create.mutate() }
  function requestDeleteDefinition(id: string) { if (window.confirm(`Удалить пользовательское определение «${id}»? Если оно переопределяет встроенный движок, будет восстановлена встроенная версия. Существующие установки и профили не изменятся.`)) void deleteDefinition.mutate(id) }

  if (engines.isLoading) return <><PageHeader eyebrow="Neural Deck / каталог" title="Движки" /><QueryLoading label="Ищем установки движков" /></>
  if (engines.isError) return <><PageHeader eyebrow="Neural Deck / каталог" title="Движки" /><QueryError error={engines.error} retry={() => void engines.refetch()} label="Список движков недоступен" /></>
  const data = engines.data
  if (!data) return <QueryLoading label="Ожидаем список движков" />
  const orderedEngines = [...data.items].sort((left, right) => left.display_name.localeCompare(right.display_name))
  return <div>
    <PageHeader eyebrow="Neural Deck / каталог" title="Движки" description="Поддерживаемые движки, подтверждённые установки и результаты операций." action={<div className="flex gap-2"><Button variant="secondary" size="sm" onClick={() => openLogs(undefined, 'operations')}><TerminalSquare className="size-3.5" />Логи</Button><Button variant="secondary" size="sm" onClick={() => void scan.mutate()} disabled={scan.isPending}><RefreshCw className={`size-3.5 ${scan.isPending ? 'animate-spin' : ''}`} />{scan.isPending ? 'Сканируем…' : 'Повторить поиск'}</Button></div>} />
    {scan.isError && <div className="mb-4"><QueryError error={scan.error} retry={() => void scan.mutate()} label="Повторный поиск не выполнен" /></div>}
    {update.isError && <div className="mb-4"><QueryError error={update.error} label="Обновление установки не запущено" /></div>}
    {maintenance.isError && <div className="mb-4"><QueryError error={maintenance.error} label="Команда обслуживания не запущена" /></div>}
    {operationId && operation.data && <OperationNotice label={operationLabel} command={operationCommand} operation={operation.data} />}
    {operationId && operation.isLoading && <div className="mb-4"><QueryLoading label={`${operationLabel || 'Операция'}: получаем состояние`} /></div>}
    <Card className="mb-6"><CardHeader><div className="flex flex-wrap items-center justify-between gap-3"><div><CardTitle>Определения движков</CardTitle><CardDescription className="mt-1">Встроенные и пользовательские определения, доступные для создания установок.</CardDescription></div><Button variant="primary" size="sm" onClick={() => setEditingDefinition(emptyEngineDefinition())}><Plus className="size-3.5" />Новое определение</Button></div></CardHeader><CardContent className="space-y-3">
      {definitions.isLoading ? <QueryLoading label="Загружаем определения движков" /> : definitions.isError ? <QueryError error={definitions.error} retry={() => void definitions.refetch()} label="Определения недоступны" /> : <div className="space-y-2">{(definitions.data?.items ?? []).map((definition) => <div key={definition.id} className="flex flex-wrap items-center justify-between gap-3 rounded-md border border-border bg-secondary px-3 py-2"><div className="min-w-0"><p className="text-sm font-medium">{definition.name} <span className="ml-1 font-mono text-[11px] text-muted-foreground">{definition.id}</span></p><p className="mt-1 text-[11px] text-muted-foreground">{definition.types.join(' · ')}{definition.install_script ? ' · установочный скрипт' : ''}</p></div><div className="flex gap-2"><Button size="sm" variant="secondary" onClick={() => setEditingDefinition(definition)}><Pencil className="size-3.5" />Изменить</Button><Button size="sm" variant="danger" disabled={deleteDefinition.isPending} onClick={() => requestDeleteDefinition(definition.id)}><Trash2 className="size-3.5" />Удалить / сбросить</Button></div></div>)}</div>}
      {definitions.data?.issues?.map((issue) => <p key={`${issue.file}-${issue.code}`} className="text-xs text-[#d0a390]">{issue.file}: {issue.message}</p>)}
      {deleteDefinition.isError && <QueryError error={deleteDefinition.error} label="Определение не удалено" />}
      {editingDefinition && <EngineDefinitionEditor key={editingDefinition.id} initial={editingDefinition} idLocked={Boolean(definitions.data?.items.some((item) => item.id === editingDefinition.id))} saving={saveDefinition.isPending} error={saveDefinition.error} onCancel={() => { setEditingDefinition(null); saveDefinition.reset() }} onSave={(definition) => saveDefinition.mutate({ definition, existingId: definitions.data?.items.some((item) => item.id === editingDefinition.id) ? editingDefinition.id : undefined })} />}
    </CardContent></Card>
    <Card className="mb-6"><CardHeader><CardTitle className="flex items-center gap-2"><FolderPlus className="size-4 text-copper" />Добавить установку</CardTitle><CardDescription>Установите движок в целевой каталог или зарегистрируйте уже существующий executable.</CardDescription></CardHeader><CardContent><form className="space-y-4" onSubmit={submitCreate}><div className="grid gap-4 md:grid-cols-2 md:items-end"><div><Label htmlFor="engine-id">Определение движка</Label><Select id="engine-id" className="mt-1.5" value={selectedEngine} onChange={(event) => selectEngine(event.target.value)} required><option value="">Выберите движок</option>{data.items.map((engine) => <option key={engine.id} value={engine.id}>{engine.display_name}</option>)}</Select></div><div><Label htmlFor="installation-target">Целевой каталог установки</Label><Input id="installation-target" className="mt-1.5 font-mono" value={targetPath} onChange={(event) => setTargetPath(event.target.value)} placeholder={selectedEngineData?.installation_root || "/opt/engines/my-engine"} required /></div></div><div className="flex flex-wrap items-center gap-2 text-xs"><label className="flex min-h-9 items-center gap-2"><input type="checkbox" checked={runInstall} onChange={(event) => setRunInstall(event.target.checked)} className="size-4 accent-[#c58055]" />Выполнить install_script определения в целевом каталоге</label>{selectedDefinition && <Button type="button" size="sm" variant="ghost" onClick={() => setEditingDefinition(selectedDefinition)}><Pencil className="size-3.5" />Изменить скрипт</Button>}</div>{runInstall && <div><Label htmlFor="installation-command">Команда / скрипт установки</Label><Textarea id="installation-command" className="mt-1.5 font-mono" rows={7} value={installCommand} onChange={(event) => setInstallCommand(event.target.value)} spellCheck={false} placeholder={'#!/bin/sh\nset -eu\n...'} /><p className="mt-1.5 text-[11px] text-muted-foreground">Текст отправляется как необязательное поле command и переопределяет скрипт для этой установки. Пустое поле использует install_script определения.</p></div>}{runInstall && !selectedDefinition?.install_script && !installCommand.trim() && <p className="text-[11px] text-[#d0a390]">У определения нет install_script: введите скрипт выше или зарегистрируйте существующий executable.</p>}{!runInstall && <div><Label htmlFor="existing-executable">Путь к уже установленному executable</Label><Input id="existing-executable" className="mt-1.5 font-mono" value={executablePath} onChange={(event) => setExecutablePath(event.target.value)} placeholder="/opt/engines/my-engine/bin/server" required /></div>}<div className="flex flex-wrap items-center gap-3"><Button type="submit" variant="primary" disabled={create.isPending || !selectedEngine || !targetPath.trim() || (runInstall && !(installCommand.trim() || selectedDefinition?.install_script?.trim())) || (!runInstall && !executablePath.trim())}><TerminalSquare className="size-3.5" />{create.isPending ? 'Запускаем…' : runInstall ? 'Установить' : 'Зарегистрировать'}</Button>{create.isError && <span className="text-xs text-[#d0a390]">{create.error instanceof NeuralDeckApiError ? create.error.message : 'Операция не создана.'}</span>}</div></form></CardContent></Card>
    <div className="grid gap-3">{orderedEngines.map((engine) => <EngineCard key={engine.id} engine={engine} expanded={expanded === engine.id} onToggle={() => toggleEngine(engine.id)} onUpdate={(id) => void update.mutate(id)} updating={update.isPending} maintenanceFor={maintenanceFor} setMaintenanceFor={setMaintenanceFor} maintenanceCommand={maintenanceCommand} setMaintenanceCommand={setMaintenanceCommand} onMaintenance={(id, command) => void maintenance.mutate({ id, command })} maintenancePending={maintenance.isPending} />)}</div>
  </div>
}

function EngineCard({ engine, expanded, onToggle, onUpdate, updating, maintenanceFor, setMaintenanceFor, maintenanceCommand, setMaintenanceCommand, onMaintenance, maintenancePending }: { engine: { id: EngineId; display_name: string; installation_kinds: Installation['kind'][]; installations: Installation[] }; expanded: boolean; onToggle: () => void; onUpdate: (id: string) => void; updating: boolean; maintenanceFor: string | null; setMaintenanceFor: (id: string | null) => void; maintenanceCommand: string; setMaintenanceCommand: (value: string) => void; onMaintenance: (id: string, command: string) => void; maintenancePending: boolean }) {
  return <Card><CardHeader><button type="button" className="flex w-full items-center justify-between gap-3 text-left" onClick={onToggle}><div><CardTitle>{engine.display_name}</CardTitle><CardDescription className="mt-1 font-mono">{engine.id} · {engine.installations.length} установок</CardDescription></div><ChevronDown className={`size-4 text-muted-foreground transition-transform ${expanded ? 'rotate-180' : ''}`} /></button></CardHeader>{expanded && <CardContent>{engine.installations.length === 0 ? <QueryEmpty title="Установки не найдены" description="Добавьте venv или бинарный путь ниже." /> : <div className="space-y-2">{engine.installations.map((installation) => <InstallationRow key={installation.id} installation={installation} expanded={expanded} onUpdate={onUpdate} updating={updating} maintenanceFor={maintenanceFor} setMaintenanceFor={setMaintenanceFor} maintenanceCommand={maintenanceCommand} setMaintenanceCommand={setMaintenanceCommand} onMaintenance={onMaintenance} maintenancePending={maintenancePending} />)}</div>}{expanded && <p className="mt-4 border-t border-border pt-3 text-[11px] text-muted-foreground">Способы установки: {engine.installation_kinds.join(', ') || 'не указаны'}. Для профиля доступны только подтверждённые установки со статусом «Готово».</p>}</CardContent>}</Card>
}

function InstallationRow({ installation, expanded, onUpdate, updating, maintenanceFor, setMaintenanceFor, maintenanceCommand, setMaintenanceCommand, onMaintenance, maintenancePending }: { installation: Installation; expanded: boolean; onUpdate: (id: string) => void; updating: boolean; maintenanceFor: string | null; setMaintenanceFor: (id: string | null) => void; maintenanceCommand: string; setMaintenanceCommand: (value: string) => void; onMaintenance: (id: string, command: string) => void; maintenancePending: boolean }) {
  const queryClient = useQueryClient()
  const forget = useMutation({ mutationFn: api.forgetInstallation, onSuccess: () => { void queryClient.invalidateQueries({ queryKey: queryKeys.engines }); void queryClient.invalidateQueries({ queryKey: queryKeys.profiles }) } })
  const canUpdate = installation.kind === 'python_venv' && installation.status === 'ready'
  return <div className="rounded-md border border-border bg-secondary px-3 py-3"><div className="flex flex-wrap items-start justify-between gap-2"><div className="min-w-0"><p className="break-all font-mono text-xs text-foreground">{String(installation.executable_path)}</p><p className="mt-1 text-[11px] text-muted-foreground">{installation.kind === 'python_venv' ? 'Python venv' : 'Бинарный файл'} · {installation.version ?? 'версия не определена'}</p></div><div className="flex items-center gap-2"><StatusBadge state={installation.status} />{installation.registered && <Button size="sm" variant="danger" disabled={forget.isPending} title="Удалить запись из конфигурации; файлы останутся на диске" onClick={() => forget.mutate(installation.id)}><Trash2 className="size-3.5" />Удалить запись</Button>}</div></div>{forget.isError && <QueryError error={forget.error} label="Запись установки не удалена" />}{installation.diagnostic && <p className="mt-2 flex gap-1.5 text-[11px] text-[#d0a390]"><CircleAlert className="mt-0.5 size-3 shrink-0" />{installation.diagnostic}</p>}<p className="mt-2 break-all text-[10px] text-muted-foreground">{installation.verified ? 'Проверено адаптером движка' : 'Проверка не подтверждена'} · id <code>{installation.id}</code></p>{expanded && <div className="mt-3 flex flex-wrap gap-2 border-t border-border pt-3"><Button size="sm" variant="secondary" disabled={!canUpdate || updating} onClick={() => onUpdate(installation.id)}><RefreshCw className="size-3.5" />Обновить до актуальной версии</Button><Button size="sm" variant="ghost" onClick={() => setMaintenanceFor(maintenanceFor === installation.id ? null : installation.id)}><Wrench className="size-3.5" />Обслуживание</Button></div>}{maintenanceFor === installation.id && <div className="mt-3 space-y-2 border-t border-border pt-3"><Label htmlFor={`maintenance-${installation.id}`}>Команда обслуживания</Label><Textarea id={`maintenance-${installation.id}`} rows={2} value={maintenanceCommand} onChange={(event) => setMaintenanceCommand(event.target.value)} placeholder="python -m pip check" spellCheck={false} /><div className="flex items-center gap-2"><Command className="size-3.5 text-copper" /><span className="text-[11px] text-muted-foreground">Backend выполнит ровно эту подтверждённую команду.</span><Button className="ml-auto" size="sm" variant="primary" disabled={maintenancePending || !maintenanceCommand.trim()} onClick={() => onMaintenance(installation.id, maintenanceCommand.trim())}>Выполнить</Button></div></div>}</div>
}

function OperationNotice({ label, operation }: { label: string; command: string | null; operation: Operation }) {
  return <div className="mb-4 flex flex-wrap items-center gap-2 text-xs"><span>{label}</span><StatusBadge state={operation.state} />{operation.error && <span className="text-[#d0a390]">{operation.error}</span>}</div>
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
  const [versionCommand, setVersionCommand] = useState(initial.version_command?.join('\n') ?? '')
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
      version_command: versionCommand.split('\n').map((part) => part.trim()).filter(Boolean).length
        ? versionCommand.split('\n').map((part) => part.trim()).filter(Boolean)
        : null,
    })
  }

  return <form className="space-y-4 rounded-md border border-border bg-background p-3" onSubmit={submit}>
    <div className="flex items-center justify-between gap-3"><div><p className="text-sm font-medium">{idLocked ? 'Изменить определение / создать override' : 'Новое определение движка'}</p><p className="text-[11px] text-muted-foreground">Сохранение встроенного ID создаёт YAML-переопределение, не меняя исходник приложения.</p></div><Button type="button" size="sm" variant="ghost" onClick={onCancel}>Закрыть</Button></div>
    <div className="grid gap-3 md:grid-cols-2">
      <Field id="definition-id" label="ID"><Input id="definition-id" value={draft.id} onChange={(event) => set('id', event.target.value)} disabled={idLocked} required /></Field>
      <Field id="definition-name" label="Название"><Input id="definition-name" value={draft.name} onChange={(event) => set('name', event.target.value)} required /></Field>
      <Field id="definition-executable" label="Executable относительно установки или абсолютный путь"><Input id="definition-executable" className="font-mono" value={draft.executable} onChange={(event) => set('executable', event.target.value)} required /></Field>
      <Field id="definition-working-directory" label="Рабочий каталог (необязательно)"><Input id="definition-working-directory" className="font-mono" value={draft.working_directory ?? ''} onChange={(event) => set('working_directory', event.target.value || null)} /></Field>
      <div className="md:col-span-2"><Label>Типы движка</Label><div className="mt-2 flex flex-wrap gap-x-4 gap-y-2">{ENGINE_TYPES.map((type) => <label key={type} className="flex items-center gap-1.5 text-xs"><input type="checkbox" checked={draft.types.includes(type)} onChange={(event) => set('types', event.target.checked ? [...draft.types, type] : draft.types.filter((item) => item !== type))} className="size-4 accent-[#c58055]" />{type}</label>)}</div></div>
      <div className="md:col-span-2"><Label htmlFor="definition-install-script">Скрипт установки (редактируемый)</Label><Textarea id="definition-install-script" className="mt-1.5 font-mono" rows={5} value={draft.install_script ?? ''} onChange={(event) => set('install_script', event.target.value || null)} spellCheck={false} placeholder={'#!/bin/sh\nset -eu\n...'} /></div>
      <div className="md:col-span-2"><Label htmlFor="definition-update-script">Скрипт обновления</Label><Textarea id="definition-update-script" className="mt-1.5 font-mono" rows={3} value={draft.update_script ?? ''} onChange={(event) => set('update_script', event.target.value || null)} spellCheck={false} /></div>
      <div className="md:col-span-2"><Label htmlFor="definition-command">Команда запуска</Label><Textarea id="definition-command" className="mt-1.5 font-mono" rows={3} value={draft.command} onChange={(event) => set('command', event.target.value)} required spellCheck={false} /></div>
      <div className="md:col-span-2"><Label htmlFor="definition-environment">Переменные окружения определения</Label><Textarea id="definition-environment" className="mt-1.5 font-mono" rows={3} value={environment} onChange={(event) => setEnvironment(event.target.value)} placeholder="KEY=value" spellCheck={false} /></div>
      <Field id="definition-api-protocol" label="Протокол API"><Select id="definition-api-protocol" value={apiProtocol} onChange={(event) => setApiProtocol(event.target.value as ApiProtocol | '')}><option value="">Не подключать</option><option value="none">none</option><option value="openai">openai</option><option value="comfyui">comfyui</option><option value="jev">jev</option></Select></Field>
      <Field id="definition-api-url" label="API base URL"><Input id="definition-api-url" type="url" className="font-mono" value={apiBaseUrl} onChange={(event) => setApiBaseUrl(event.target.value)} placeholder="http://127.0.0.1:8000/v1" /></Field>
      <Field id="definition-ui-url" label="UI URL (необязательно)"><Input id="definition-ui-url" type="url" className="font-mono" value={draft.ui_url ?? ''} onChange={(event) => set('ui_url', event.target.value || null)} /></Field>
      <Field id="definition-health-path" label="Healthcheck path"><Input id="definition-health-path" className="font-mono" value={healthPath} onChange={(event) => setHealthPath(event.target.value)} placeholder="/health" /></Field>
      <Field id="definition-health-timeout" label="Таймаут healthcheck, секунд"><Input id="definition-health-timeout" type="number" min="0.1" step="0.1" value={healthTimeout} onChange={(event) => setHealthTimeout(event.target.value)} /></Field>
      <div className="md:col-span-2"><Label htmlFor="definition-version-command">Команда версии — аргументы по одному на строку</Label><Textarea id="definition-version-command" className="mt-1.5 font-mono" rows={2} value={versionCommand} onChange={(event) => setVersionCommand(event.target.value)} placeholder="--version" spellCheck={false} /></div>
    </div>
    {healthPath.trim() && (!apiBaseUrl.trim() || !apiProtocol || apiProtocol === 'none') && <p className="text-xs text-[#d0a390]">Healthcheck требует настроенный API protocol и base URL.</p>}
    {Boolean(error) && <p className="text-xs text-[#d0a390]">{error instanceof NeuralDeckApiError ? error.message : 'Определение не сохранено.'}</p>}
    <div className="flex justify-end gap-2"><Button type="button" variant="ghost" onClick={onCancel}>Отмена</Button><Button type="submit" variant="primary" disabled={saving || !draft.id.trim() || !draft.name.trim() || !draft.executable.trim() || !draft.command.trim() || draft.types.length === 0 || Boolean(healthPath.trim() && (!apiBaseUrl.trim() || !apiProtocol || apiProtocol === 'none'))}><Save className="size-3.5" />{saving ? 'Сохраняем…' : 'Сохранить определение'}</Button></div>
  </form>
}

function Field({ id, label, children }: { id: string; label: string; children: ReactNode }) {
  return <div><Label htmlFor={id}>{label}</Label><div className="mt-1.5">{children}</div></div>
}
