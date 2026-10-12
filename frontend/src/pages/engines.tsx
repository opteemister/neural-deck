import { FormEvent, useEffect, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ChevronDown, CircleAlert, Command, FolderPlus, RefreshCw, TerminalSquare, Trash2, Wrench } from 'lucide-react'
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
import type { EngineId, Installation, Operation } from '@/types/api'

const ENGINE_ORDER: EngineId[] = ['vllm', 'llama.cpp', 'sglang', 'ninfer', 'freetoken', 'image']

export function EnginesPage() {
  const openLogs = useLogsDrawer()
  const queryClient = useQueryClient()
  const [params, setParams] = useSearchParams()
  const engines = useQuery({ queryKey: queryKeys.engines, queryFn: api.getEngines })
  const [expanded, setExpanded] = useState<string | null>(params.get('engine'))
  const [selectedEngine, setSelectedEngine] = useState<EngineId>((params.get('engine') as EngineId) || 'vllm')
  const [kind, setKind] = useState<'python_venv' | 'binary'>('python_venv')
  const [venvName, setVenvName] = useState('')
  const [binaryPath, setBinaryPath] = useState('')
  const [installCommand, setInstallCommand] = useState('')
  const [templateCommand, setTemplateCommand] = useState('')
  const selectedTemplate = engines.data?.items.find((item) => item.id === selectedEngine)
  const templateText = selectedTemplate?.installation_template
  const installationRoot = selectedTemplate?.installation_root
  useEffect(() => {
    const destination = (kind === 'python_venv' ? venvName : binaryPath).trim()
    if (!destination || !templateText) { setInstallCommand(''); setTemplateCommand(''); return }
    const target = kind === 'python_venv' ? `${installationRoot}/${destination}` : destination
    const targetDir = target.slice(0, target.lastIndexOf('/')) || '/'
    const command = templateText.replace(/\{\{(target|target_dir)\}\}/g, (_, key: string) => quoteInstallPath(key === 'target' ? target : targetDir))
    setInstallCommand(command)
    setTemplateCommand(command)
  }, [selectedEngine, kind, venvName, binaryPath, templateText, installationRoot])
  const [operationId, setOperationId] = useState<string | null>(null)
  const [operationLabel, setOperationLabel] = useState('')
  const [operationCommand, setOperationCommand] = useState<string | null>(null)
  const [maintenanceFor, setMaintenanceFor] = useState<string | null>(null)
  const [maintenanceCommand, setMaintenanceCommand] = useState('')
  const operation = useQuery({ queryKey: queryKeys.operation(operationId ?? ''), queryFn: () => api.getOperation(operationId!), enabled: Boolean(operationId), refetchInterval: (query) => ['queued', 'running'].includes(query.state.data?.state ?? '') ? 1_500 : false })
  useEffect(() => { if (operation.data && !['queued', 'running'].includes(operation.data.state)) { void queryClient.invalidateQueries({ queryKey: queryKeys.engines }); void queryClient.invalidateQueries({ queryKey: queryKeys.installations }) } }, [operation.data, queryClient])
  const selectedEngineData = engines.data?.items.find((item) => item.id === selectedEngine)
  const engineParam = params.get('engine')
  useEffect(() => {
    const available = engines.data?.items ?? []
    if (available.length === 0) return
    const requested = available.find((engine) => engine.id === engineParam)?.id
    const fallback = requested ?? available.find((engine) => engine.id === 'vllm')?.id ?? available[0].id
    const effective = requested ?? (available.some((engine) => engine.id === selectedEngine) ? selectedEngine : fallback)
    if (selectedEngine !== effective) setSelectedEngine(effective)
    if (requested) setExpanded(requested)
    const kinds = available.find((engine) => engine.id === effective)?.installation_kinds ?? []
    if (kinds.length > 0 && !kinds.includes(kind)) setKind(kinds[0])
  }, [engineParam, engines.data, kind, selectedEngine])
  const scan = useMutation({ mutationFn: api.scanEngines, onSuccess: (data) => queryClient.setQueryData(queryKeys.engines, data) })
  const create = useMutation({ mutationFn: () => api.createInstallation(selectedEngine, kind === 'python_venv' ? { kind, venv_name: venvName.trim(), command: installCommand } : { kind, path: binaryPath.trim(), command: installCommand }), onSuccess: (data) => { setOperationId(data.id); setOperationLabel('Установка движка'); setOperationCommand(installCommand); openLogs(undefined, 'operations'); void queryClient.invalidateQueries({ queryKey: queryKeys.engines }) } })
  const update = useMutation({ mutationFn: (id: string) => api.updateInstallation(id), onSuccess: (data) => { setOperationId(data.id); setOperationLabel('Обновление установки'); setOperationCommand(null); openLogs(undefined, 'operations'); void queryClient.invalidateQueries({ queryKey: queryKeys.engines }) } })
  const maintenance = useMutation({ mutationFn: ({ id, command }: { id: string; command: string }) => api.maintenance(id, command), onSuccess: (data, variables) => { setOperationId(data.id); setOperationLabel('Обслуживание установки'); setOperationCommand(variables.command); openLogs(undefined, 'operations'); setMaintenanceFor(null); setMaintenanceCommand('') } })

  function selectEngine(id: EngineId) { setSelectedEngine(id) }
  function toggleEngine(id: EngineId) { const next = expanded === id ? null : id; setSelectedEngine(id); setExpanded(next); setParams((current) => { if (next) current.set('engine', id); else current.delete('engine'); return current }, { replace: true }) }
  function submitCreate(event: FormEvent<HTMLFormElement>) { event.preventDefault(); if (installCommand.trim() && (kind === 'python_venv' ? venvName : binaryPath).trim()) void create.mutate() }

  if (engines.isLoading) return <><PageHeader eyebrow="Neural Deck / каталог" title="Движки" /><QueryLoading label="Ищем установки движков" /></>
  if (engines.isError) return <><PageHeader eyebrow="Neural Deck / каталог" title="Движки" /><QueryError error={engines.error} retry={() => void engines.refetch()} label="Список движков недоступен" /></>
  const data = engines.data
  if (!data) return <QueryLoading label="Ожидаем список движков" />
  const orderedEngines = [...data.items].sort((left, right) => ENGINE_ORDER.indexOf(left.id) - ENGINE_ORDER.indexOf(right.id))
  return <div>
    <PageHeader eyebrow="Neural Deck / каталог" title="Движки" description="Поддерживаемые движки, подтверждённые установки и результаты операций." action={<div className="flex gap-2"><Button variant="secondary" size="sm" onClick={() => openLogs(undefined, 'operations')}><TerminalSquare className="size-3.5" />Логи</Button><Button variant="secondary" size="sm" onClick={() => void scan.mutate()} disabled={scan.isPending}><RefreshCw className={`size-3.5 ${scan.isPending ? 'animate-spin' : ''}`} />{scan.isPending ? 'Сканируем…' : 'Повторить поиск'}</Button></div>} />
    {scan.isError && <div className="mb-4"><QueryError error={scan.error} retry={() => void scan.mutate()} label="Повторный поиск не выполнен" /></div>}
    {update.isError && <div className="mb-4"><QueryError error={update.error} label="Обновление установки не запущено" /></div>}
    {maintenance.isError && <div className="mb-4"><QueryError error={maintenance.error} label="Команда обслуживания не запущена" /></div>}
    {operationId && operation.data && <OperationNotice label={operationLabel} command={operationCommand} operation={operation.data} />}
    {operationId && operation.isLoading && <div className="mb-4"><QueryLoading label={`${operationLabel || 'Операция'}: получаем состояние`} /></div>}
    <div className="grid gap-3">{orderedEngines.map((engine) => <EngineCard key={engine.id} engine={engine} expanded={expanded === engine.id} onToggle={() => toggleEngine(engine.id)} onUpdate={(id) => void update.mutate(id)} updating={update.isPending} maintenanceFor={maintenanceFor} setMaintenanceFor={setMaintenanceFor} maintenanceCommand={maintenanceCommand} setMaintenanceCommand={setMaintenanceCommand} onMaintenance={(id, command) => void maintenance.mutate({ id, command })} maintenancePending={maintenance.isPending} />)}</div>
    <Card className="mt-6"><CardHeader><CardTitle className="flex items-center gap-2"><FolderPlus className="size-4 text-copper" />Добавить установку</CardTitle><CardDescription>Выберите движок и путь, затем отредактируйте команду установки при необходимости.</CardDescription></CardHeader><CardContent><form className="space-y-4" onSubmit={submitCreate}><div className="grid gap-4 md:grid-cols-3 md:items-end"><div><Label htmlFor="engine-id">Движок</Label><Select id="engine-id" className="mt-1.5" value={selectedEngine} onChange={(event) => selectEngine(event.target.value as EngineId)}>{data.items.map((engine) => <option key={engine.id} value={engine.id}>{engine.display_name}</option>)}</Select></div><div><Label htmlFor="installation-kind">Способ установки</Label><Select id="installation-kind" className="mt-1.5" value={kind} onChange={(event) => setKind(event.target.value as typeof kind)}>{(selectedEngineData?.installation_kinds ?? ['python_venv', 'binary']).map((option) => <option key={option} value={option}>{option === 'python_venv' ? 'Python venv' : 'Бинарный файл'}</option>)}</Select></div><div>{kind === 'python_venv' ? <><Label htmlFor="venv-name">Имя venv</Label><Input id="venv-name" className="mt-1.5 font-mono" value={venvName} onChange={(event) => setVenvName(event.target.value)} placeholder="vllm-main" /></> : <><Label htmlFor="binary-path">Абсолютный путь</Label><Input id="binary-path" className="mt-1.5 font-mono" value={binaryPath} onChange={(event) => setBinaryPath(event.target.value)} placeholder="/opt/llama/bin/llama-server" /></>}</div></div><div><div className="flex items-center justify-between gap-2"><Label htmlFor="engine-install-command">Команда установки</Label><Button type="button" variant="ghost" size="sm" disabled={!templateCommand} onClick={() => setInstallCommand(templateCommand)}>Шаблон движка</Button></div><Textarea id="engine-install-command" className="mt-1.5 font-mono" rows={10} value={installCommand} onChange={(event) => setInstallCommand(event.target.value)} required spellCheck={false} placeholder="Укажите путь установки" /></div><div className="flex flex-wrap items-center gap-3"><Button type="submit" variant="primary" disabled={create.isPending || !installCommand.trim() || !(kind === 'python_venv' ? venvName.trim() : binaryPath.trim())}><TerminalSquare className="size-3.5" />{create.isPending ? 'Запускаем…' : 'Установить'}</Button>{create.isError && <span className="text-xs text-[#d0a390]">{create.error instanceof NeuralDeckApiError ? create.error.message : 'Операция не создана.'}</span>}</div></form></CardContent></Card>
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

function quoteInstallPath(value: string): string {
  return /^[A-Za-z0-9_@%+=:,./-]+$/.test(value) ? value : `'${value.replace(/'/g, `'"'"'`)}'`
}
