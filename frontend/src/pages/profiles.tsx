import { stateLabel } from '@/lib/utils'
import { FormEvent, ReactNode, useEffect, useMemo, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useLogsDrawer } from '@/components/logs-drawer'
import { ExternalLink, FileWarning, FolderOpen, Pencil, Play, Plus, RefreshCw, Save, Square, Trash2, TriangleAlert, WandSparkles } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Dialog } from '@/components/ui/dialog'
import { ExpandableCard } from '@/components/ui/expandable-card'
import { InfoHint } from '@/components/ui/info-hint'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Select } from '@/components/ui/select'
import { Textarea } from '@/components/ui/textarea'
import { NotificationToast } from '@/components/ui/notification-toast'
import { PageHeader } from '@/components/page-header'
import { QueryEmpty, QueryError, QueryLoading } from '@/components/query-state'
import { api, queryKeys, NeuralDeckApiError } from '@/lib/api'
import { profileModelOptions } from '@/lib/profile-model-options'
import { ProfileGpuPicker } from '@/components/profile-gpu-picker'
import { previewProfileEnvironment } from '@/lib/profile-preview'
import type { ApiProtocol, EngineApi, EngineType, Instance, LocalModelArtifact, ProfileData, ProfileSummary, SystemData } from '@/types/api'

type FormState = { name: string; modelName: string; installationId: string; modelPath: string; draftPath: string; command: string; environment: string; autostart: boolean; enableMetrics: boolean; inputPrice: string; cachedInputPrice: string; outputPrice: string; overrideTypes: boolean; types: EngineType[]; apiProtocol: ApiProtocol | ''; apiBaseUrl: string; uiUrl: string }
type EditorState = { kind: 'new' } | { kind: 'edit'; name: string }
export type RuntimeStatus = { label: string; tone: 'active' | 'starting' | 'stopping' | 'error' | 'idle' | 'stopped'; processPresent: boolean; instance?: Instance }

const emptyForm: FormState = { name: '', modelName: '', installationId: '', modelPath: '', draftPath: '', command: '', environment: '', autostart: false, enableMetrics: true, inputPrice: '', cachedInputPrice: '', outputPrice: '', overrideTypes: false, types: [], apiProtocol: '', apiBaseUrl: '', uiUrl: '' }

export function ProfilesPage() {
  const openLogs = useLogsDrawer()
  const queryClient = useQueryClient()
  const [editor, setEditor] = useState<EditorState | null>(null)
  const isNew = editor?.kind === 'new'
  const selectedName = editor?.kind === 'edit' ? editor.name : null
  const settings = useQuery({ queryKey: queryKeys.settings, queryFn: api.getSettings })
  const profiles = useQuery({ queryKey: queryKeys.profiles, queryFn: api.getProfiles })
  const summaries = useQuery({ queryKey: queryKeys.profileSummary, queryFn: api.getProfileSummary, refetchInterval: 5_000 })
  const engines = useQuery({ queryKey: queryKeys.engines, queryFn: api.getEngines })
  const localModels = useQuery({ queryKey: queryKeys.localModels, queryFn: api.getLocalModels })
  const system = useQuery({ queryKey: queryKeys.system, queryFn: api.getSystem, refetchInterval: 5_000 })
  const detail = useQuery({ queryKey: ['profile', selectedName], queryFn: () => api.getProfile(selectedName!), enabled: Boolean(selectedName) })
  const [form, setForm] = useState<FormState>(emptyForm)
  const [saved, setSaved] = useState(false)
  const installations = useMemo(() => engines.data?.items.flatMap((engine) => engine.installations.map((installation) => ({ ...installation, displayName: engine.display_name, commandTemplate: engine.command_template, types: engine.types, api: engine.api, ui_url: engine.ui_url }))) ?? [], [engines.data])
  const create = useMutation({ mutationFn: (body: ProfileData) => api.createProfile(body), onSuccess: () => { setSaved(true); setEditor(null); void queryClient.invalidateQueries({ queryKey: queryKeys.profiles }); void queryClient.invalidateQueries({ queryKey: queryKeys.profileSummary }); setTimeout(() => setSaved(false), 2000) } })
  const update = useMutation({ mutationFn: ({ name, body }: { name: string; body: ProfileData }) => api.updateProfile(name, body), onSuccess: () => { setSaved(true); setEditor(null); void queryClient.invalidateQueries({ queryKey: queryKeys.profiles }); void queryClient.invalidateQueries({ queryKey: queryKeys.profileSummary }); setTimeout(() => setSaved(false), 2000) } })
  const remove = useMutation({ mutationFn: api.deleteProfile, onSuccess: (_, name) => { if (selectedName === name) setEditor(null); void queryClient.invalidateQueries({ queryKey: queryKeys.profiles }); void queryClient.invalidateQueries({ queryKey: queryKeys.profileSummary }); void queryClient.invalidateQueries({ queryKey: queryKeys.system }) } })
  const format = useMutation({ mutationFn: () => api.formatScript(form.command), onSuccess: (data) => setForm((current) => ({ ...current, command: data.script })) })
  const start = useMutation({ mutationFn: ({ name, configuration }: { name: string; configuration?: ProfileData }) => configuration ? api.startProfileConfiguration(configuration) : api.startProfile(name), onSuccess: (instance, variables) => { if (variables.configuration) { void queryClient.invalidateQueries({ queryKey: queryKeys.logs }); openLogs(variables.name) } queryClient.setQueryData<SystemData>(queryKeys.system, (current) => current ? { ...current, instances: [...current.instances.filter((item) => item.id !== instance.id), instance] } : current); void queryClient.invalidateQueries({ queryKey: queryKeys.system }); void queryClient.invalidateQueries({ queryKey: queryKeys.instances }); void queryClient.invalidateQueries({ queryKey: queryKeys.models }); void queryClient.invalidateQueries({ queryKey: queryKeys.profileSummary }) }, onError: (error) => { if (error instanceof NeuralDeckApiError && error.code === 'profile_already_running') { const instance = conflictInstance(error); if (instance) queryClient.setQueryData<SystemData>(queryKeys.system, (current) => current ? { ...current, instances: [...current.instances.filter((item) => item.id !== instance.id && item.profile_name !== instance.profile_name), instance] } : current); void queryClient.invalidateQueries({ queryKey: queryKeys.system }); void queryClient.invalidateQueries({ queryKey: queryKeys.instances }) } } })
  const stop = useMutation({ mutationFn: api.stopInstance, onSuccess: () => { void queryClient.invalidateQueries({ queryKey: queryKeys.system }); void queryClient.invalidateQueries({ queryKey: queryKeys.instances }); void queryClient.invalidateQueries({ queryKey: queryKeys.models }); void queryClient.invalidateQueries({ queryKey: queryKeys.profileSummary }) } })
  const runtimeInstances = useMemo(() => system.data?.instances ?? [], [system.data?.instances])
  const orderedProfiles = useMemo(() => [...(profiles.data?.items ?? [])].sort((left, right) => Number(profileRuntimeStatus(right, runtimeInstances).processPresent) - Number(profileRuntimeStatus(left, runtimeInstances).processPresent)), [profiles.data?.items, runtimeInstances])

  useEffect(() => { if (isNew) setForm(emptyForm) }, [isNew])
  useEffect(() => { if (detail.data && selectedName) setForm(profileToForm(detail.data)) }, [detail.data, selectedName])

  function openNew() { setSaved(false); create.reset(); update.reset(); setEditor({ kind: 'new' }) }
  function openEdit(name: string) { setSaved(false); create.reset(); update.reset(); setEditor({ kind: 'edit', name }) }
  function closeEditor() { setEditor(null) }
  function updateForm<K extends keyof FormState>(key: K, value: FormState[K]) {
    setForm((current) => {
      if (key === 'installationId') {
        const previous = installations.find((item) => item.id === current.installationId)
        const next = installations.find((item) => item.id === value)
        const generated = !current.command.trim() || current.command === previous?.commandTemplate
        return { ...current, [key]: value, ...(previous && next && previous.engine_id !== next.engine_id ? { modelPath: '', draftPath: '' } : {}), command: (generated || previous?.engine_id !== next?.engine_id) && next ? next.commandTemplate : current.command }
      }
      return { ...current, [key]: value }
    })
  }
  function formConfiguration(): ProfileData | null {
    const installation = installations.find((item) => item.id === form.installationId)
    if (!installation || installation.status !== 'ready' || !form.name.trim() || !form.command.trim()) return null
    const modelPath = form.modelPath.trim()
    const draftPath = form.draftPath.trim()
    if ((form.command.includes('{{model}}') && !modelPath) || (form.command.includes('{{model_draft}}') && !draftPath) || (draftPath && !modelPath)) return null
    const api = form.apiProtocol || form.apiBaseUrl.trim() ? { ...(form.apiProtocol ? { protocol: form.apiProtocol } : {}), ...(form.apiBaseUrl.trim() ? { base_url: form.apiBaseUrl.trim() } : {}) } : undefined
    return { version: 1, profile_id: selectedName ? detail.data?.profile_id ?? null : crypto.randomUUID(), name: form.name.trim(), model_name: form.modelName.trim() || form.name.trim(), engine: { id: installation.engine_id, installation_id: installation.id }, ...(modelPath ? { model: { path: modelPath, ...(draftPath ? { draft_path: draftPath } : {}) } } : {}), command: form.command, ...(form.overrideTypes ? { types: form.types } : {}), ...(api ? { api } : {}), ...(form.uiUrl.trim() ? { ui_url: form.uiUrl.trim() } : {}), environment: parseEnvironment(form.environment), autostart: form.autostart, enable_metrics: form.enableMetrics, input_price_usd_per_million_tokens: parsePrice(form.inputPrice), cached_input_price_usd_per_million_tokens: parsePrice(form.cachedInputPrice), output_price_usd_per_million_tokens: parsePrice(form.outputPrice) }
  }
  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const body = formConfiguration()
    if (!body) return
    if (selectedName) update.mutate({ name: selectedName, body })
    else create.mutate(body)
  }
  function requestStart(name: string, editing: boolean) {
    const profile = profiles.data?.items.find((item) => item.name === name)
    if (start.isPending || (profile && profileRuntimeStatus(profile, runtimeInstances).processPresent)) return
    if (!editing) { start.mutate({ name }); return }
    if (!document.querySelector<HTMLFormElement>('#profile-editor-form')?.reportValidity()) return
    const configuration = formConfiguration()
    if (configuration) start.mutate({ name, configuration })
  }
  function requestDelete(name: string) { if (window.confirm(`Удалить профиль «${name}»? Файл конфигурации будет удалён. Запущенный процесс остановлен не будет.`)) void remove.mutate(name) }

  return <div><PageHeader eyebrow="Neural Deck / управление" title="Профили" description="Редактируемый YAML-профиль: модель, конкретная установка, команда, окружение и автозапуск." action={<div className="flex gap-2"><Button variant="secondary" size="sm" onClick={() => { void profiles.refetch(); void summaries.refetch(); void engines.refetch(); void localModels.refetch(); void system.refetch() }}><RefreshCw className="size-3.5" />Обновить</Button><Button variant="primary" size="sm" onClick={openNew}><Plus className="size-3.5" />Новый профиль</Button></div>} />
    <Dialog open={editor !== null} title={isNew ? 'Новый профиль' : `Изменить профиль ${selectedName ?? ''}`} size="wide" dismissible={false} onOpenChange={(open) => { if (!open) closeEditor() }}>
      {editor && (selectedName && (detail.isLoading || !detail.data) ? <div className="p-4">{detail.isError ? <QueryError error={detail.error} retry={() => void detail.refetch()} label="Профиль недоступен" /> : <QueryLoading label="Получаем профиль" />}</div> : <ProfileEditor form={form} updateForm={updateForm} localModels={localModels.data?.items ?? []} gpus={system.data?.gpus ?? []} installations={installations} nameLocked={false} onSubmit={submit} saving={create.isPending || update.isPending} error={create.error ?? update.error} format={() => void format.mutate()} formatting={format.isPending} formatted={format.data?.changed} formatReason={format.data?.reason} onStart={selectedName ? () => requestStart(selectedName, true) : undefined} starting={start.isPending && start.variables?.name === selectedName} startDisabled={!selectedName || start.isPending || !formConfiguration() || Boolean(selectedName && detail.data && profileRuntimeStatus(detail.data, runtimeInstances).processPresent)} onLogs={selectedName ? () => openLogs(selectedName) : undefined} />)}
    </Dialog>
    {saved && <NotificationToast tone="success" title="Профиль сохранён" />}
    {start.isError && <QueryError error={start.error} label="Запуск профиля не выполнен" />}
    {stop.isError && <QueryError error={stop.error} label="Остановка профиля не выполнена" />}
    {remove.isError && <QueryError error={remove.error} label="Профиль не удалось удалить" />}
    {profiles.isLoading ? <QueryLoading label="Читаем YAML-профили" /> : profiles.isError ? <QueryError error={profiles.error} retry={() => void profiles.refetch()} label="Список профилей недоступен" /> : !profiles.data ? <QueryLoading label="Ожидаем профили" /> : <div className="space-y-2">{profiles.data.issues.length > 0 && <Issues issues={profiles.data.issues} />}{orderedProfiles.length === 0 ? <QueryEmpty title="Профилей нет" description="Создайте первый профиль, чтобы запускать локальные модели." /> : orderedProfiles.map((profile) => <ProfileCard key={profile.profile_id ?? profile.name} profile={profile} summary={summaries.data?.items.find((item) => item.profile_id === profile.profile_id || (item.profile_id == null && item.profile_name === profile.name))} summaryLoading={summaries.isLoading} modelsRoot={settings.data?.models_root} installation={installations.find((item) => item.id === profile.engine.installation_id)} runtime={system.isLoading ? { label: 'Проверяем…', tone: 'idle', processPresent: false } : profileRuntimeStatus(profile, runtimeInstances)} edit={() => openEdit(profile.name)} start={() => requestStart(profile.name, false)} starting={start.isPending && start.variables?.name === profile.name} startDisabled={start.isPending} stop={(id) => stop.mutate(id)} stoppingId={stop.isPending ? stop.variables : undefined} remove={() => requestDelete(profile.name)} removing={remove.isPending && remove.variables === profile.name} />)}</div>}
  </div>
}

function ProfileEditor({ form, updateForm, localModels, gpus, installations, nameLocked, onSubmit, saving, error, format, formatting, formatted, formatReason, onStart, starting = false, startDisabled = false, onLogs }: { form: FormState; updateForm: <K extends keyof FormState>(key: K, value: FormState[K]) => void; localModels: LocalModelArtifact[]; gpus: SystemData['gpus']; installations: Array<{ id: string; engine_id: ProfileData['engine']['id']; displayName: string; version: string | null; executable_path: string; commandTemplate: string; status: string; types: EngineType[]; api: EngineApi | null; ui_url: string | null }>; nameLocked: boolean; onSubmit: (event: FormEvent<HTMLFormElement>) => void; saving: boolean; error: unknown; format: () => void; formatting: boolean; formatted?: boolean; formatReason?: string | null; onStart?: () => void; starting?: boolean; startDisabled?: boolean; onLogs?: () => void }) {
  const selectedInstallation = installations.find((item) => item.id === form.installationId && item.status === 'ready')
  const modelOptions = profileModelOptions(localModels, selectedInstallation?.types)
  const currentModelOption = modelOptions.find((model) => sameModelLocation(model.path, form.modelPath))
  const selectedModelPath = currentModelOption?.path ?? form.modelPath
  const currentModelMissing = Boolean(form.modelPath) && !currentModelOption
  const modelRequired = form.command.includes('{{model}}')

  useEffect(() => {
    if (currentModelOption && currentModelOption.path !== form.modelPath) updateForm('modelPath', currentModelOption.path)
  }, [currentModelOption, form.modelPath, updateForm])

  return <CardContent><form id="profile-editor-form" className="space-y-5" onSubmit={onSubmit}>
    <div className="grid gap-4 md:grid-cols-2">
      <Field id="profile-name" label="Имя профиля"><Input id="profile-name" value={form.name} onChange={(event) => updateForm('name', event.target.value)} readOnly={nameLocked} required placeholder="qwen-local" /></Field>
      <Field id="profile-model-name" label="Публичное имя модели"><Input id="profile-model-name" value={form.modelName} onChange={(event) => updateForm('modelName', event.target.value)} placeholder="Пусто — использовать имя профиля" /></Field>
      <Field id="profile-installation" label="Движок"><Select id="profile-installation" value={form.installationId} onChange={(event) => updateForm('installationId', event.target.value)} required><option value="">Выберите движок</option>{installations.map((installation) => <option key={installation.id} value={installation.id} disabled={installation.status !== 'ready'}>{installation.displayName} · {installation.version ?? 'версия неизвестна'} · {installation.executable_path}{installation.status !== 'ready' ? ' · недоступна' : ''}</option>)}</Select></Field>
      <Field id="profile-model-path" label="Локальная модель"><Select id="profile-model-path" value={selectedModelPath} onChange={(event) => updateForm('modelPath', event.target.value)} required={modelRequired} disabled={modelOptions.length === 0 && !currentModelMissing}><option value="">{modelOptions.length === 0 ? modelRequired ? 'Локальные модели не найдены' : 'Без модели' : modelRequired ? 'Выберите модель' : 'Без модели'}</option>{currentModelMissing && <option value={form.modelPath}>{form.modelPath}</option>}{modelOptions.map((model) => <option key={model.path} value={model.path} disabled={model.disabled}>{model.label}</option>)}</Select></Field>
    </div>
    <div><div className="flex items-center gap-2"><p className="text-xs font-medium">Цены, USD за 1 млн токенов</p><InfoHint label="Информация о расчёте стоимости">Кэшированные input-токены входят в input и вычитаются из обычной ставки перед расчётом.</InfoHint></div><div className="mt-3 grid gap-4 md:grid-cols-3"><Field id="profile-input-price" label="Input"><Input id="profile-input-price" type="number" min="0" step="any" value={form.inputPrice} onChange={(event) => updateForm('inputPrice', event.target.value)} placeholder="например, 0.40" /></Field><Field id="profile-cached-input-price" label="Cached input"><Input id="profile-cached-input-price" type="number" min="0" step="any" value={form.cachedInputPrice} onChange={(event) => updateForm('cachedInputPrice', event.target.value)} placeholder="например, 0.10" /></Field><Field id="profile-output-price" label="Output"><Input id="profile-output-price" type="number" min="0" step="any" value={form.outputPrice} onChange={(event) => updateForm('outputPrice', event.target.value)} placeholder="например, 1.60" /></Field></div></div>
    <ProfileGpuPicker gpus={gpus} environment={form.environment} onChange={(value) => updateForm('environment', value)} />
    <div><div className="flex items-center gap-2"><Label htmlFor="profile-environment">Переменные окружения</Label><InfoHint label="Формат переменных окружения">Одна переменная на строку: <code>NAME=value</code>. Пустые строки игнорируются.</InfoHint></div><Textarea id="profile-environment" className="mt-1.5" rows={4} value={form.environment} onChange={(event) => updateForm('environment', event.target.value)} placeholder={'CUDA_VISIBLE_DEVICES=0\nHF_HOME=/models/.cache'} spellCheck={false} /></div>
    <div><div className="flex flex-wrap items-center justify-between gap-2"><div className="flex items-center gap-2"><Label htmlFor="profile-command">Команда запуска</Label><InfoHint label="Информация о шаблоне команды запуска"><code>{'{{engine}}'}</code> — движок, <code>{'{{model}}'}</code> — путь модели, <code>{'{{model_name}}'}</code> — публичное имя. Дополнительные заполнители зависят от выбранного шаблона. Значения подставляются как безопасные shell-аргументы.</InfoHint></div><div className="flex items-center gap-2"><Button type="button" size="sm" variant="ghost" disabled={!selectedInstallation} onClick={() => selectedInstallation && updateForm('command', selectedInstallation.commandTemplate)}>Шаблон движка</Button><Button type="button" size="sm" variant="ghost" onClick={format} disabled={formatting || !form.command.trim()}><WandSparkles className="size-3.5" />{formatting ? 'Форматируем…' : 'Форматировать строку'}</Button></div></div><Textarea id="profile-command" className="mt-1.5" rows={6} value={form.command} onChange={(event) => updateForm('command', event.target.value)} required spellCheck={false} />{formatted === true && <NotificationToast tone="success" title="Команда отформатирована" />}{formatted === false && formatReason && <NotificationToast tone="info" title="Команда оставлена без изменений" message={formatReason} />}</div>
    <div className="space-y-2"><div className="flex flex-wrap items-center justify-end gap-x-4 gap-y-1"><label className="flex min-h-8 items-center gap-2 text-xs"><input type="checkbox" checked={form.autostart} onChange={(event) => updateForm('autostart', event.target.checked)} className="size-4 accent-[#c58055]" />Автозапуск при старте Neural Deck</label><div className="flex items-center gap-2"><label className="flex min-h-8 items-center gap-2 text-xs"><input type="checkbox" checked={form.enableMetrics} onChange={(event) => updateForm('enableMetrics', event.target.checked)} className="size-4 accent-[#c58055]" />Включить метрики</label><InfoHint id="profile-metrics-info" label="Информация о включении метрик" align="right">Метрики включаются по умолчанию. Флаги конкретного движка управляются backend; явно заданные параметры сохраняются. Снятая галочка не удаляет уже указанные ключи.</InfoHint></div></div><div className="flex flex-wrap items-center gap-2 border-t border-border pt-3">{onLogs && <Button type="button" size="sm" variant="ghost" onClick={onLogs}>Логи</Button>}<div className="ml-auto flex shrink-0 items-center gap-2">{onStart && <Button type="button" size="md" variant="secondary" onClick={onStart} disabled={starting || startDisabled}><Play className="size-3.5" />{starting ? 'Запускаем…' : 'Запустить без сохранения'}</Button>}<Button type="submit" size="md" variant="primary" disabled={saving || !selectedInstallation || !formConfigurationReady(form)}><Save className="size-3.5" />{saving ? 'Сохраняем…' : 'Сохранить профиль'}</Button></div></div></div>
    {Boolean(error) && <QueryError error={error} label="Профиль не сохранён" />}
  </form></CardContent>
}


function Field({ id, label, children }: { id: string; label: string; children: ReactNode }) { return <div><Label htmlFor={id}>{label}</Label><div className="mt-1.5">{children}</div></div> }
function sameModelLocation(left: string, right: string): boolean {
  if (!left || !right) return false
  const identity = (path: string) => {
    const normalized = path.replaceAll('\\', '/').replace(/\/$/, '')
    const modelsMarker = '/models/'
    const modelsIndex = normalized.lastIndexOf(modelsMarker)
    return modelsIndex >= 0 ? normalized.slice(modelsIndex + modelsMarker.length) : normalized
  }
  return identity(left) === identity(right)
}
function profileToForm(profile: ProfileData): FormState { return { name: profile.name, modelName: profile.model_name, installationId: profile.engine.installation_id, modelPath: profile.model?.path ?? '', draftPath: profile.model?.draft_path ?? '', command: profile.command.replaceAll('{{port}}', String(profile.port ?? 8188)), environment: Object.entries(profile.environment).map(([key, value]) => `${key}=${value}`).join('\n'), autostart: profile.autostart, enableMetrics: profile.enable_metrics ?? true, inputPrice: profile.input_price_usd_per_million_tokens?.toString() ?? '', cachedInputPrice: profile.cached_input_price_usd_per_million_tokens?.toString() ?? '', outputPrice: profile.output_price_usd_per_million_tokens?.toString() ?? '', overrideTypes: profile.types != null, types: profile.types ?? [], apiProtocol: profile.api?.protocol ?? '', apiBaseUrl: profile.api?.base_url ?? '', uiUrl: profile.ui_url ?? '' } }
function formConfigurationReady(form: FormState): boolean { const modelPath = form.modelPath.trim(); const draftPath = form.draftPath.trim(); return Boolean(form.name.trim() && form.command.trim() && (!form.command.includes('{{model}}') || modelPath) && (!form.command.includes('{{model_draft}}') || draftPath) && (!draftPath || modelPath)) }
function parseEnvironment(value: string): Record<string, string> { return Object.fromEntries(value.split('\n').map((line) => line.trim()).filter(Boolean).map((line) => { const separator = line.indexOf('='); return separator > 0 ? [line.slice(0, separator).trim(), line.slice(separator + 1)] : [line, ''] })) }
function parsePrice(value: string): number | null { const parsed = Number(value); return value.trim() === '' ? null : Number.isFinite(parsed) && parsed >= 0 ? parsed : null }
function Issues({ issues }: { issues: Array<{ file: string; code: string; message: string }> }) { return <Card><CardHeader><CardTitle className="flex items-center gap-2"><FileWarning className="size-4 text-copper" />Ошибки файлов профилей</CardTitle><CardDescription>Исправьте YAML и обновите список. Остальные профили остаются доступными.</CardDescription></CardHeader><CardContent className="space-y-2">{issues.map((issue) => <div key={`${issue.file}-${issue.code}`} className="rounded-md border border-[#72544a] bg-secondary px-3 py-2 text-xs"><p className="font-mono text-[#d0a390]">{issue.file}</p><p className="mt-1 text-muted-foreground">{issue.message}</p><p className="mt-1 text-[10px] text-muted-foreground">{issue.code}</p></div>)}</CardContent></Card> }

function ProfileCard({ profile, summary, summaryLoading, modelsRoot, installation, runtime, edit, start, starting, startDisabled, stop, stoppingId, remove, removing }: { profile: ProfileData; summary?: ProfileSummary; summaryLoading: boolean; modelsRoot?: string; installation?: { executable_path: string; displayName: string; version: string | null; ui_url: string | null }; runtime: RuntimeStatus; edit: () => void; start: () => void; starting: boolean; startDisabled: boolean; stop: (id: string) => void; stoppingId?: string; remove: () => void; removing: boolean }) {
  const [expanded, setExpanded] = useState(false)
  const commandPreview = useQuery({
    queryKey: ['profile-command-preview', profile],
    queryFn: () => api.previewProfile(profile),
    enabled: expanded,
    retry: false,
  })
  const preview = commandPreview.data ? { ok: true as const, command: commandPreview.data.command } : { ok: false as const, error: commandPreview.error instanceof NeuralDeckApiError ? commandPreview.error.message : commandPreview.isError ? 'Не удалось получить фактическую команду.' : 'Расчёт фактической команды…' }
  const environmentPreview = previewProfileEnvironment(profile.environment)
  const modelPath = profile.model?.path
  const modelLabel = modelPath ? relativeModelPath(modelPath, modelsRoot) : null
  const uiUrl = runtime.instance?.ui_url ?? profile.ui_url ?? installation?.ui_url
  const engineName = installation?.displayName ?? profile.engine.id

  const cardSummary = <div className="profile-card-summary-grid">
    <div className="profile-card-identity min-w-0"><div className="flex flex-wrap items-center gap-x-2 gap-y-1"><h2 className="text-sm font-medium">{profile.name}</h2><span className="text-muted-foreground">—</span><span className="text-sm text-foreground">{profile.model_name}</span>{uiUrl && <a href={uiUrl} target="_blank" rel="noopener noreferrer" title="Открыть UI движка" className="inline-flex items-center gap-1 text-[11px] font-medium text-copper underline-offset-2 hover:underline"><ExternalLink className="size-3" />Открыть UI</a>}</div>{modelPath && modelLabel ? <a href={`/models?local_q=${encodeURIComponent(modelPath)}`} title={modelPath} className="mt-1.5 flex min-w-0 overflow-hidden items-center gap-1.5 whitespace-nowrap font-mono text-[11px] text-copper underline-offset-2 hover:underline"><FolderOpen className="size-3.5 shrink-0" /><MiddleTruncatedModelLabel value={modelLabel} /></a> : <p className="mt-1.5 text-[11px] text-muted-foreground">Модель не задана</p>}</div>
    <div className="profile-card-metrics" data-testid="profile-summary-metrics">
      <SummaryCell label="Статус" value={<span className="inline-flex min-w-0 items-center gap-1.5"><RuntimeStatus status={runtime} profileName={profile.name} />{runtime.instance?.model_paths_restart_required && <span className="rounded border border-[#806241] px-1 py-0.5 text-[9px] leading-none text-[#d4a85b]" title="Пути моделей ComfyUI изменились после запуска. Перезапустите экземпляр, чтобы применить их.">Перезапуск ComfyUI</span>}</span>} title={runtime.instance?.model_paths_restart_required ? 'Пути моделей ComfyUI изменились после запуска; требуется перезапуск.' : `Логи профиля «${profile.name}»`} />
      <SummaryCell label="Порт" value={summaryLoading ? '…' : formatPort(summary?.endpoint)} warning={Boolean(summary?.endpoint.conflict)} title={summary?.endpoint.conflict ? `Порт совпадает с: ${summary.endpoint.conflict_with.join(', ')}` : 'Effective port из command или default engine'} />
      <SummaryCell label="Engine" value={<span className="block truncate">{engineName}</span>} title={engineName} />
      <ProfileSummaryCells summary={summary} loading={summaryLoading} />
      <SummaryCell label="А.Запуск" value={<span aria-label={profile.autostart ? 'Автозапуск включён' : 'Автозапуск выключен'}>{profile.autostart ? 'Вкл' : 'Откл'}</span>} title={profile.autostart ? 'Автозапуск включён' : 'Автозапуск выключен'} />
    </div>
  </div>
  const actions = <><Button variant="secondary" size="sm" iconOnly aria-label="Изменить" title="Изменить" onClick={edit}><Pencil className="size-3.5" /></Button><Button variant="primary" size="sm" iconOnly aria-label={runtime.processPresent ? 'Остановить' : 'Запустить'} title={runtime.processPresent ? 'Остановить' : 'Запустить'} onClick={() => runtime.processPresent && runtime.instance ? stop(runtime.instance.id) : start()} disabled={starting || (!runtime.processPresent && startDisabled) || (runtime.processPresent && (!runtime.instance?.can_stop || !runtime.instance.identity_verified || runtime.tone === 'stopping' || stoppingId === runtime.instance.id))}>{runtime.processPresent ? <Square className="size-3.5" /> : <Play className="size-3.5" />}</Button></>
  return <ExpandableCard className="profile-card" expanded={expanded} onToggle={() => setExpanded((value) => !value)} summary={cardSummary} actions={actions} contentClassName="py-3"><div className="grid gap-3 lg:grid-cols-2"><CommandPanel title="Шаблон команды" value={profile.command} /><div className="flex items-end gap-3"><div className="min-w-0 flex-1"><p className="text-[11px] text-muted-foreground">Фактическая команда</p><pre className={`mt-1 max-h-40 min-h-[4rem] overflow-auto whitespace-pre-wrap rounded-md border bg-background p-3 font-mono text-[11px] leading-5 ${preview.ok ? 'border-copper/60 text-foreground' : 'border-border text-[#d0a390]'}`}>{preview.ok ? <>{environmentPreview && <><span className="text-muted-foreground">{environmentPreview}</span>{'\n\n'}</>}{preview.command}</> : preview.error}</pre></div><Button variant="danger" size="sm" iconOnly aria-label="Удалить" title="Удалить" onClick={remove} disabled={removing}><Trash2 className="size-3.5" /></Button></div></div></ExpandableCard>
}

function ProfileSummaryCells({ summary, loading }: { summary?: ProfileSummary; loading: boolean }) {
  const vram = summary && [summary.memory.vram_current_bytes, summary.memory.vram_last_bytes].find((item) => item.state === 'ok' || item.state === 'stale')
  return <>
    <SummaryCell label="Prefill" value={loading ? '…' : formatRate(summary?.performance.prefill_tps)} title="Средняя скорость prefill за UTC-сутки" />
    <SummaryCell label="Decode" value={loading ? '…' : formatRate(summary?.performance.decode_tps)} title="Средняя скорость decode за UTC-сутки" />
    <SummaryCell label="VRAM" value={loading ? '…' : formatBytes(vram)} title={vram === summary?.memory.vram_last_bytes ? 'Последнее сохранённое измерение VRAM процесса' : 'Текущее измерение VRAM процесса'} />
    <SummaryCell label="Max req." value={loading ? '…' : formatMaxRequests(summary?.concurrency)} title="Максимум одновременно выполнявшихся запросов за всю сохранённую историю профиля" />
  </>
}

function SummaryCell({ label, value, title, warning = false }: { label: string; value: ReactNode; title: string; warning?: boolean }) {
  return <div className="min-w-0 overflow-hidden" title={title}><p className="h-3 whitespace-nowrap text-[10px] uppercase leading-3 tracking-[0.16em] text-muted-foreground">{label}</p><div className="mt-1 flex min-h-4 items-center gap-1 overflow-hidden whitespace-nowrap text-xs leading-4 tabular-nums text-foreground">{warning && <TriangleAlert className="size-3.5 shrink-0 text-[#d4a85b]" aria-label="Совпадение порта" />}{value}</div></div>
}

function formatRate(metric?: ProfileSummary['performance']['prefill_tps']): string {
  return metric?.state === 'ok' && metric.value !== null ? `${formatNumber(metric.value)} tok/s` : '—'
}
function formatBytes(metric?: ProfileSummary['memory']['disk_bytes']): string {
  if (!metric || !['ok', 'stale'].includes(metric.state) || metric.value === null) return '—'
  const units = ['B', 'KiB', 'MiB', 'GiB', 'TiB']
  let value = metric.value
  let index = 0
  while (value >= 1024 && index < units.length - 1) { value /= 1024; index += 1 }
  return `${formatNumber(value)} ${units[index]}`
}
function formatMaxRequests(concurrency?: ProfileSummary['concurrency']): string {
  if (!concurrency) return '—'
  return concurrency.limit.state === 'ok' && concurrency.limit.value !== null ? String(Math.round(concurrency.limit.value)) : '—'
}
function formatPort(endpoint?: ProfileSummary['endpoint']): string { return endpoint?.state === 'ok' && endpoint.port !== null ? String(endpoint.port) : '—' }
function formatNumber(value: number): string { return new Intl.NumberFormat('ru-RU', { maximumFractionDigits: value >= 100 ? 0 : 1 }).format(value) }

function CommandPanel({ title, value }: { title: string; value: string }) { return <div><p className="text-[11px] text-muted-foreground">{title}</p><pre className="mt-1 max-h-40 overflow-auto whitespace-pre-wrap rounded-md bg-secondary p-3 font-mono text-[11px] leading-5 text-muted-foreground">{value}</pre></div> }
function RuntimeStatus({ status, profileName }: { status: RuntimeStatus; profileName: string }) {
  const openLogs = useLogsDrawer()
  const dot = status.tone === 'active' ? 'bg-[#6f9a72]' : ['starting', 'stopping'].includes(status.tone) ? 'bg-[#c9974f]' : status.tone === 'error' ? 'bg-[#d09b87]' : 'bg-muted-foreground'
  const content = <><span className={`size-2 rounded-full ${dot}`} aria-hidden="true" />{status.label}</>
  const className = 'inline-flex items-center gap-1.5 text-inherit'
  return <button type="button" onClick={() => openLogs(profileName)} className={`${className} rounded-sm underline underline-offset-2 hover:text-foreground focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-copper`} aria-label={`${status.label}: открыть логи профиля «${profileName}»`} title={`Логи профиля «${profileName}»`}>{content}</button>
}
export function profileRuntimeStatus(profile: ProfileData | string, instances: Instance[]): RuntimeStatus {
  const profileId = typeof profile === 'string' ? undefined : profile.profile_id
  const profileName = typeof profile === 'string' ? profile : profile.name
  const instance = instances.filter((item) => profileId ? item.profile_id === profileId || (item.profile_id == null && item.profile_name === profileName) : item.profile_name === profileName).sort((left, right) => {
    const active = (item: Instance) => ['starting', 'ready', 'running', 'stopping'].includes(item.state)
    return Number(active(right)) - Number(active(left)) || right.observed_at.localeCompare(left.observed_at) || runtimePriority(right.state) - runtimePriority(left.state)
  })[0]
  if (!instance) return { label: 'Не запущен', tone: 'idle', processPresent: false }
  if (instance.state === 'ready' || instance.state === 'running') return { label: stateLabel(instance.state), tone: 'active', processPresent: true, instance }
  if (instance.state === 'starting') return { label: stateLabel(instance.state), tone: 'starting', processPresent: true, instance }
  if (instance.state === 'stopping') return { label: stateLabel(instance.state), tone: 'stopping', processPresent: true, instance }
  if (instance.state === 'error') return { label: stateLabel(instance.state), tone: 'error', processPresent: true, instance }
  if (instance.state === 'exited') return { label: stateLabel(instance.state), tone: 'stopped', processPresent: false, instance }
  return { label: stateLabel(instance.state), tone: 'error', processPresent: true, instance }
}
function runtimePriority(state: Instance['state']): number { return { starting: 5, ready: 4, running: 4, stopping: 3, error: 2, unknown: 1, exited: 0 }[state] }
function conflictInstance(error: NeuralDeckApiError): Instance | null {
  const value = error.details.instance
  if (!value || typeof value !== 'object') return null
  const instance = value as Partial<Instance>
  return typeof instance.id === 'string' && typeof instance.profile_name === 'string' && typeof instance.state === 'string' ? value as Instance : null
}

function MiddleTruncatedModelLabel({ value }: { value: string }) {
  const labelRef = useRef<HTMLSpanElement>(null)
  const [displayValue, setDisplayValue] = useState(value)

  useEffect(() => {
    const element = labelRef.current
    if (!element) return

    function update() {
      const label = labelRef.current
      if (!label) return
      const availableWidth = label.clientWidth
      if (!availableWidth) {
        setDisplayValue(value)
        return
      }

      const computedStyle = getComputedStyle(label)
      const ruler = document.createElement('span')
      ruler.style.position = 'fixed'
      ruler.style.left = '-10000px'
      ruler.style.top = '0'
      ruler.style.visibility = 'hidden'
      ruler.style.whiteSpace = 'pre'
      ruler.style.font = computedStyle.font
      ruler.style.letterSpacing = computedStyle.letterSpacing
      ruler.style.wordSpacing = computedStyle.wordSpacing
      document.body.append(ruler)

      const measure = (text: string) => {
        ruler.textContent = text
        return ruler.getBoundingClientRect().width
      }

      if (measure(value) <= availableWidth) {
        setDisplayValue(value)
      } else {
        const prefix = value.slice(0, 20)
        let minSuffixLength = 0
        let maxSuffixLength = value.length - prefix.length
        while (minSuffixLength < maxSuffixLength) {
          const suffixLength = Math.ceil((minSuffixLength + maxSuffixLength) / 2)
          const candidate = `${prefix}...${value.slice(-suffixLength)}`
          if (measure(candidate) <= availableWidth) minSuffixLength = suffixLength
          else maxSuffixLength = suffixLength - 1
        }
        setDisplayValue(`${prefix}...${minSuffixLength ? value.slice(-minSuffixLength) : ''}`)
      }

      ruler.remove()
    }

    update()
    if (typeof ResizeObserver === 'undefined') {
      window.addEventListener('resize', update)
      return () => window.removeEventListener('resize', update)
    }

    const observer = new ResizeObserver(update)
    observer.observe(element)
    return () => observer.disconnect()
  }, [value])

  return <span ref={labelRef} className="block min-w-0 flex-1 overflow-hidden whitespace-nowrap">{displayValue}</span>
}

function relativeModelPath(path: string, modelsRoot?: string): string {
  if (modelsRoot) {
    const prefix = `${modelsRoot.replace(/\/$/, '')}/`
    return path.startsWith(prefix) ? path.slice(prefix.length) : path
  }
  const marker = '/models/'
  const index = path.indexOf(marker)
  return index >= 0 ? path.slice(index + marker.length) : path
}
