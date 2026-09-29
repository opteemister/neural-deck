import { FormEvent, useEffect, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { CheckCircle2, ChevronDown, CircleAlert, Command, FolderPlus, RefreshCw, TerminalSquare, Wrench } from 'lucide-react'
import { useSearchParams } from 'react-router-dom'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Select } from '@/components/ui/select'
import { Textarea } from '@/components/ui/textarea'
import { PageHeader } from '@/components/page-header'
import { QueryEmpty, QueryError, QueryLoading } from '@/components/query-state'
import { StatusBadge } from '@/components/status-badge'
import { api, queryKeys, StudioApiError } from '@/lib/api'
import type { EngineId, Installation, Operation } from '@/types/api'

export function EnginesPage() {
  const queryClient = useQueryClient()
  const [params, setParams] = useSearchParams()
  const engines = useQuery({ queryKey: queryKeys.engines, queryFn: api.getEngines })
  const [expanded, setExpanded] = useState<string | null>(params.get('engine'))
  const [selectedEngine, setSelectedEngine] = useState<EngineId>((params.get('engine') as EngineId) || 'vllm')
  const [kind, setKind] = useState<'python_venv' | 'binary'>('python_venv')
  const [venvName, setVenvName] = useState('')
  const [binaryPath, setBinaryPath] = useState('')
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
    const fallback = available.find((engine) => engine.id === engineParam)?.id ?? available[0].id
    if (selectedEngine !== fallback) setSelectedEngine(fallback)
    if (expanded !== fallback) setExpanded(fallback)
    const kinds = available.find((engine) => engine.id === fallback)?.installation_kinds ?? []
    if (kinds.length > 0 && !kinds.includes(kind)) setKind(kinds[0])
  }, [engineParam, engines.data, expanded, kind, selectedEngine])
  const scan = useMutation({ mutationFn: api.scanEngines, onSuccess: (data) => queryClient.setQueryData(queryKeys.engines, data) })
  const create = useMutation({ mutationFn: () => api.createInstallation(selectedEngine, kind === 'python_venv' ? { kind, venv_name: venvName.trim() } : { kind, path: binaryPath.trim() }), onSuccess: (data) => { setOperationId(data.id); setOperationLabel('Установка движка'); setOperationCommand(null); setVenvName(''); setBinaryPath(''); void queryClient.invalidateQueries({ queryKey: queryKeys.engines }) } })
  const update = useMutation({ mutationFn: (id: string) => api.updateInstallation(id), onSuccess: (data) => { setOperationId(data.id); setOperationLabel('Обновление установки'); setOperationCommand(null); void queryClient.invalidateQueries({ queryKey: queryKeys.engines }) } })
  const maintenance = useMutation({ mutationFn: ({ id, command }: { id: string; command: string }) => api.maintenance(id, command), onSuccess: (data, variables) => { setOperationId(data.id); setOperationLabel('Обслуживание установки'); setOperationCommand(variables.command); setMaintenanceFor(null); setMaintenanceCommand('') } })

  function selectEngine(id: EngineId) { setSelectedEngine(id); setExpanded(id); setParams((current) => { current.set('engine', id); return current }, { replace: true }) }
  function submitCreate(event: FormEvent<HTMLFormElement>) { event.preventDefault(); if ((kind === 'python_venv' ? venvName : binaryPath).trim()) void create.mutate() }

  if (engines.isLoading) return <><PageHeader eyebrow="Студия / каталог" title="Движки" /><QueryLoading label="Ищем установки движков" /></>
  if (engines.isError) return <><PageHeader eyebrow="Студия / каталог" title="Движки" /><QueryError error={engines.error} retry={() => void engines.refetch()} label="Список движков недоступен" /></>
  const data = engines.data
  if (!data) return <QueryLoading label="Ожидаем список движков" />
  return <div>
    <PageHeader eyebrow="Студия / каталог" title="Движки" description="Пять поддерживаемых движков, подтверждённые установки и результаты операций." action={<Button variant="secondary" size="sm" onClick={() => void scan.mutate()} disabled={scan.isPending}><RefreshCw className={`size-3.5 ${scan.isPending ? 'animate-spin' : ''}`} />{scan.isPending ? 'Сканируем…' : 'Повторить поиск'}</Button>} />
    {scan.isError && <div className="mb-4"><QueryError error={scan.error} retry={() => void scan.mutate()} label="Повторный поиск не выполнен" /></div>}
    {update.isError && <div className="mb-4"><QueryError error={update.error} label="Обновление установки не запущено" /></div>}
    {maintenance.isError && <div className="mb-4"><QueryError error={maintenance.error} label="Команда обслуживания не запущена" /></div>}
    {operationId && operation.data && <OperationNotice label={operationLabel} command={operationCommand} operation={operation.data} />}
    {operationId && operation.isLoading && <div className="mb-4"><QueryLoading label={`${operationLabel || 'Операция'}: получаем состояние`} /></div>}
    <div className="grid gap-4 xl:grid-cols-2">{data.items.map((engine) => <EngineCard key={engine.id} engine={engine} expanded={expanded === engine.id} onToggle={() => selectEngine(engine.id)} onUpdate={(id) => void update.mutate(id)} updating={update.isPending} maintenanceFor={maintenanceFor} setMaintenanceFor={setMaintenanceFor} maintenanceCommand={maintenanceCommand} setMaintenanceCommand={setMaintenanceCommand} onMaintenance={(id, command) => void maintenance.mutate({ id, command })} maintenancePending={maintenance.isPending} />)}</div>
    <Card className="mt-6"><CardHeader><CardTitle className="flex items-center gap-2"><FolderPlus className="size-4 text-copper" />Добавить установку</CardTitle><CardDescription>Создайте новое Python окружение или зарегистрируйте проверяемый бинарный файл.</CardDescription></CardHeader><CardContent><form className="space-y-4" onSubmit={submitCreate}><div className="grid gap-4 md:grid-cols-3 md:items-end"><div><Label htmlFor="engine-id">Движок</Label><Select id="engine-id" className="mt-1.5" value={selectedEngine} onChange={(event) => selectEngine(event.target.value as EngineId)}>{data.items.map((engine) => <option key={engine.id} value={engine.id}>{engine.display_name}</option>)}</Select></div><div><Label htmlFor="installation-kind">Способ установки</Label><Select id="installation-kind" className="mt-1.5" value={kind} onChange={(event) => setKind(event.target.value as typeof kind)}>{(selectedEngineData?.installation_kinds ?? ['python_venv', 'binary']).map((option) => <option key={option} value={option}>{option === 'python_venv' ? 'Python venv' : 'Бинарный файл'}</option>)}</Select></div><div>{kind === 'python_venv' ? <><Label htmlFor="venv-name">Имя venv</Label><Input id="venv-name" className="mt-1.5 font-mono" value={venvName} onChange={(event) => setVenvName(event.target.value)} placeholder="vllm-main" /></> : <><Label htmlFor="binary-path">Абсолютный путь</Label><Input id="binary-path" className="mt-1.5 font-mono" value={binaryPath} onChange={(event) => setBinaryPath(event.target.value)} placeholder="/opt/llama/bin/llama-server" /></>}</div></div><div className="flex flex-wrap items-center gap-3"><Button type="submit" variant="primary" disabled={create.isPending || !(kind === 'python_venv' ? venvName.trim() : binaryPath.trim())}><TerminalSquare className="size-3.5" />{create.isPending ? 'Запускаем…' : kind === 'python_venv' ? 'Создать окружение' : 'Проверить путь'}</Button>{create.isError && <span className="text-xs text-[#d0a390]">{create.error instanceof StudioApiError ? create.error.message : 'Операция не создана.'}</span>}</div></form></CardContent></Card>
  </div>
}

function EngineCard({ engine, expanded, onToggle, onUpdate, updating, maintenanceFor, setMaintenanceFor, maintenanceCommand, setMaintenanceCommand, onMaintenance, maintenancePending }: { engine: { id: EngineId; display_name: string; installation_kinds: Installation['kind'][]; installations: Installation[] }; expanded: boolean; onToggle: () => void; onUpdate: (id: string) => void; updating: boolean; maintenanceFor: string | null; setMaintenanceFor: (id: string | null) => void; maintenanceCommand: string; setMaintenanceCommand: (value: string) => void; onMaintenance: (id: string, command: string) => void; maintenancePending: boolean }) {
  return <Card><CardHeader><button type="button" className="flex w-full items-center justify-between gap-3 text-left" onClick={onToggle}><div><CardTitle>{engine.display_name}</CardTitle><CardDescription className="mt-1 font-mono">{engine.id} · {engine.installations.length} установок</CardDescription></div><ChevronDown className={`size-4 text-muted-foreground transition-transform ${expanded ? 'rotate-180' : ''}`} /></button></CardHeader><CardContent>{engine.installations.length === 0 ? <QueryEmpty title="Установки не найдены" description="Добавьте venv или бинарный путь ниже." /> : <div className="space-y-2">{engine.installations.map((installation) => <InstallationRow key={installation.id} installation={installation} expanded={expanded} onUpdate={onUpdate} updating={updating} maintenanceFor={maintenanceFor} setMaintenanceFor={setMaintenanceFor} maintenanceCommand={maintenanceCommand} setMaintenanceCommand={setMaintenanceCommand} onMaintenance={onMaintenance} maintenancePending={maintenancePending} />)}</div>}{expanded && <p className="mt-4 border-t border-border pt-3 text-[11px] text-muted-foreground">Способы установки: {engine.installation_kinds.join(', ') || 'не указаны'}. Для профиля доступны только подтверждённые установки со статусом «Готово».</p>}</CardContent></Card>
}

function InstallationRow({ installation, expanded, onUpdate, updating, maintenanceFor, setMaintenanceFor, maintenanceCommand, setMaintenanceCommand, onMaintenance, maintenancePending }: { installation: Installation; expanded: boolean; onUpdate: (id: string) => void; updating: boolean; maintenanceFor: string | null; setMaintenanceFor: (id: string | null) => void; maintenanceCommand: string; setMaintenanceCommand: (value: string) => void; onMaintenance: (id: string, command: string) => void; maintenancePending: boolean }) {
  const canUpdate = installation.kind === 'python_venv' && installation.status === 'ready'
  return <div className="rounded-md border border-border bg-secondary px-3 py-3"><div className="flex flex-wrap items-start justify-between gap-2"><div className="min-w-0"><p className="break-all font-mono text-xs text-foreground">{String(installation.executable_path)}</p><p className="mt-1 text-[11px] text-muted-foreground">{installation.kind === 'python_venv' ? 'Python venv' : 'Бинарный файл'} · {installation.version ?? 'версия не определена'}</p></div><StatusBadge state={installation.status} /></div>{installation.diagnostic && <p className="mt-2 flex gap-1.5 text-[11px] text-[#d0a390]"><CircleAlert className="mt-0.5 size-3 shrink-0" />{installation.diagnostic}</p>}<p className="mt-2 break-all text-[10px] text-muted-foreground">{installation.verified ? 'Проверено адаптером движка' : 'Проверка не подтверждена'} · id <code>{installation.id}</code></p>{expanded && <div className="mt-3 flex flex-wrap gap-2 border-t border-border pt-3"><Button size="sm" variant="secondary" disabled={!canUpdate || updating} onClick={() => onUpdate(installation.id)}><RefreshCw className="size-3.5" />Обновить до актуальной версии</Button><Button size="sm" variant="ghost" onClick={() => setMaintenanceFor(maintenanceFor === installation.id ? null : installation.id)}><Wrench className="size-3.5" />Обслуживание</Button></div>}{maintenanceFor === installation.id && <div className="mt-3 space-y-2 border-t border-border pt-3"><Label htmlFor={`maintenance-${installation.id}`}>Команда обслуживания</Label><Textarea id={`maintenance-${installation.id}`} rows={2} value={maintenanceCommand} onChange={(event) => setMaintenanceCommand(event.target.value)} placeholder="python -m pip check" spellCheck={false} /><div className="flex items-center gap-2"><Command className="size-3.5 text-copper" /><span className="text-[11px] text-muted-foreground">Backend выполнит ровно эту подтверждённую команду.</span><Button className="ml-auto" size="sm" variant="primary" disabled={maintenancePending || !maintenanceCommand.trim()} onClick={() => onMaintenance(installation.id, maintenanceCommand.trim())}>Выполнить</Button></div></div>}</div>
}

function OperationNotice({ label, command, operation }: { label: string; command: string | null; operation: Operation }) {
  const finished = !['queued', 'running'].includes(operation.state)
  const succeeded = operation.state === 'succeeded'
  return <div className="mb-4 rounded-md border border-border bg-secondary px-4 py-3 text-xs"><div className="flex items-start gap-3"><div className="mt-0.5">{succeeded ? <CheckCircle2 className="size-4 text-copper" /> : <CircleAlert className="size-4 text-copper" />}</div><div className="min-w-0"><p className="font-medium">{label || 'Операция'} · <StatusBadge state={operation.state} /></p><p className="mt-1 break-all text-muted-foreground">operation <code>{operation.id}</code> · target <code>{operation.target_id}</code></p>{command && <p className="mt-2 break-all text-[11px] text-foreground">Команда: <code>{command}</code></p>}{operation.error && <p className="mt-2 text-[#d0a390]">{operation.error}</p>}{finished && operation.stdout && <pre className="mt-2 max-h-32 overflow-auto whitespace-pre-wrap rounded bg-background p-2 font-mono text-[11px]">{operation.stdout}</pre>}{finished && operation.stderr && <pre className="mt-2 max-h-32 overflow-auto whitespace-pre-wrap rounded border border-[#72544a] bg-background p-2 font-mono text-[11px] text-[#d0a390]">{operation.stderr}</pre>}{finished && operation.resolved_packages && <p className="mt-2 text-[11px] text-muted-foreground">Пакеты: {Object.entries(operation.resolved_packages).map(([name, version]) => `${name} ${version}`).join(', ')}</p>}</div></div></div>
}
