import { FormEvent, useEffect, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Check, FolderCog, Save } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { PageHeader } from '@/components/page-header'
import { QueryError, QueryLoading } from '@/components/query-state'
import { api, queryKeys, StudioApiError } from '@/lib/api'

export function SettingsPage() {
  const queryClient = useQueryClient()
  const settings = useQuery({ queryKey: queryKeys.settings, queryFn: api.getSettings })
  const [modelsRoot, setModelsRoot] = useState('')
  const [saved, setSaved] = useState(false)
  useEffect(() => { if (settings.data) setModelsRoot(settings.data.models_root) }, [settings.data])
  const update = useMutation({ mutationFn: () => api.updateSettings({ models_root: modelsRoot.trim() }), onSuccess: (data) => { queryClient.setQueryData(queryKeys.settings, data); setModelsRoot(data.models_root); setSaved(true); setTimeout(() => setSaved(false), 2500) } })
  const settingsData = settings.data
  function submit(event: FormEvent<HTMLFormElement>) { event.preventDefault(); if (modelsRoot.trim()) void update.mutate() }
return <div><PageHeader eyebrow="Настройки / окружение" title="Настройки" description="Эффективные пути и сетевые параметры. Изменение моделей root запускает последующее сканирование backend." />{settings.isLoading ? <QueryLoading label="Получаем настройки" /> : settings.isError ? <QueryError error={settings.error} retry={() => void settings.refetch()} label="Настройки недоступны" /> : !settingsData ? <QueryLoading label="Ожидаем настройки" /> : <div className="grid gap-5 xl:grid-cols-[minmax(0,1fr)_minmax(280px,0.55fr)]"><Card><CardHeader><CardTitle className="flex items-center gap-2"><FolderCog className="size-4 text-copper" />Хранилища</CardTitle><CardDescription>Пути действуют для текущего сервиса и не содержат секретов.</CardDescription></CardHeader><CardContent><form className="space-y-4" onSubmit={submit}><div><Label htmlFor="models-root">Корень моделей</Label><Input id="models-root" className="mt-1.5 font-mono" value={modelsRoot} onChange={(event) => setModelsRoot(event.target.value)} required /><p className="mt-1.5 text-[11px] text-muted-foreground">Сохранение этого пути выполняется через management API.</p></div><div className="flex items-center gap-3"><Button type="submit" variant="primary" disabled={!modelsRoot.trim() || update.isPending}><Save className="size-3.5" />{update.isPending ? 'Сохраняем…' : 'Сохранить'}</Button>{saved && <span className="flex items-center gap-1.5 text-xs text-[#cf9a78]"><Check className="size-3.5" />Сохранено</span>}</div>{update.isError && <p className="text-xs text-[#d0a390]">{update.error instanceof StudioApiError ? update.error.message : 'Настройки не сохранены.'}</p>}</form></CardContent></Card><Card><CardHeader><CardTitle>Параметры сервиса</CardTitle></CardHeader><CardContent className="space-y-3 text-xs"><SettingRow label="Data root" value={settingsData.data_root} mono /><SettingRow label="Log root" value={settingsData.log_root} mono /><SettingRow label="Host" value={settingsData.host} mono /><SettingRow label="Порт приложения" value={String(settingsData.port)} mono /><SettingRow label="Порт API моделей" value={String(settingsData.openai_port)} mono /><div><p className="text-[11px] text-muted-foreground">Дополнительные корни venv</p>{settingsData.extra_venv_roots.length ? <ul className="mt-1 space-y-1 font-mono text-[11px]">{settingsData.extra_venv_roots.map((root) => <li key={root}>{root}</li>)}</ul> : <p className="mt-1 text-[11px] text-muted-foreground">не настроены</p>}</div></CardContent></Card></div>}</div>
}

function SettingRow({ label, value, mono }: { label: string; value: string; mono?: boolean }) { return <div className="flex flex-wrap justify-between gap-3 border-b border-border pb-2"><span className="text-[11px] text-muted-foreground">{label}</span><span className={mono ? 'max-w-full break-all font-mono text-[11px] text-foreground' : 'text-xs'}>{value}</span></div> }
