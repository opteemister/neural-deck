import { useState, type FormEvent } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Plus } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Dialog } from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { ProxyConnectionRow } from '@/components/proxy-connection-row'
import { api } from '@/lib/api'
import type { OpenAIConnection } from '@/types/api'

export const openAIConnectionsKey = ['openai-connections'] as const
export const connectionModelsKey = (id: string) => ['openai-connection-models', id] as const

function Connection({ connection, disabled, onEdit, onDelete }: { connection: OpenAIConnection; disabled: boolean; onEdit: () => void; onDelete: () => void }) {
  const models = useQuery({ queryKey: connectionModelsKey(connection.id), queryFn: () => api.getConnectionModels(connection.id, true), retry: false, staleTime: 30_000 })
  return <ProxyConnectionRow name={connection.name} url={connection.base_url} pending={models.isFetching} available={models.isSuccess} detail={models.error?.message} disabled={disabled} onRefresh={() => { void models.refetch({ cancelRefetch: false }) }} refreshing={models.isFetching} onEdit={onEdit} onDelete={onDelete} />
}

export function ProxyOpenAIConnections({ connections, onChanged, notify }: { connections: OpenAIConnection[]; onChanged: () => void; notify: (tone: 'success' | 'error', message: string) => void }) {
  const [draft, setDraft] = useState<{ id?: string; name: string; url: string; key: string; hasKey: boolean; clearKey: boolean } | null>(null)
  const [saving, setSaving] = useState(false)
  async function save(event: FormEvent) {
    event.preventDefault()
    if (!draft || saving) return
    setSaving(true)
    try {
      const body = { name: draft.name.trim(), base_url: draft.url.trim(), ...(draft.clearKey ? { api_key: '' } : draft.key ? { api_key: draft.key } : {}) }
      if (draft.id) await api.updateOpenAIConnection(draft.id, body)
      else await api.createOpenAIConnection(body)
      setDraft(null); onChanged(); notify('success', 'Подключение сохранено.')
    } catch (error) { notify('error', error instanceof Error ? error.message : 'Не удалось сохранить подключение.') }
    finally { setSaving(false) }
  }
  async function remove(id: string) {
    setSaving(true)
    try { await api.deleteOpenAIConnection(id); onChanged(); notify('success', 'Подключение удалено.') }
    catch (error) { notify('error', error instanceof Error ? error.message : 'Не удалось удалить подключение.') }
    finally { setSaving(false) }
  }
  return <section className="mb-6 border-b border-border pb-5">
    <div className="mb-3 flex items-center justify-between gap-3"><h2 className="text-sm font-medium">OpenAI-подключения</h2><Button variant="ghost" size="sm" disabled={saving} onClick={() => setDraft({ name: '', url: '', key: '', hasKey: false, clearKey: false })}><Plus className="size-3.5" />Подключение</Button></div>
    <div className="divide-y divide-border">{connections.map((connection) => <Connection key={connection.id} connection={connection} disabled={saving} onEdit={() => setDraft({ id: connection.id, name: connection.name, url: connection.base_url, key: '', hasKey: connection.has_api_key, clearKey: false })} onDelete={() => void remove(connection.id)} />)}</div>
    <Dialog open={draft !== null} title={draft?.id ? 'Изменить OpenAI-подключение' : 'Новое OpenAI-подключение'} dismissible={!saving} onOpenChange={(open) => { if (!open && !saving) setDraft(null) }}>
      {draft && <form className="space-y-5 p-4" onSubmit={(event) => void save(event)}>
        <div className="grid gap-4 [grid-template-columns:repeat(auto-fit,minmax(min(100%,12rem),1fr))]">
          <div><Label htmlFor="openai-connection-name" className="mb-1.5 block">Имя</Label><Input id="openai-connection-name" autoFocus required maxLength={128} value={draft.name} disabled={saving} onChange={(event) => setDraft({ ...draft, name: event.target.value })} placeholder="GPU-сервер" /></div>
          <div><Label htmlFor="openai-connection-url" className="mb-1.5 block">URL</Label><Input id="openai-connection-url" type="url" required value={draft.url} disabled={saving} onChange={(event) => setDraft({ ...draft, url: event.target.value })} placeholder="http://server:8000/v1" /></div>
        </div>
        <div><Label htmlFor="openai-connection-key" className="mb-1.5 block">API-ключ</Label><Input id="openai-connection-key" type="password" autoComplete="new-password" value={draft.key} disabled={saving || draft.clearKey} onChange={(event) => setDraft({ ...draft, key: event.target.value })} placeholder={draft.hasKey ? 'Ключ сохранён' : 'Необязательно'} />
          {draft.hasKey && <label className="mt-2 flex items-center gap-2 text-xs text-muted-foreground"><input type="checkbox" className="accent-[#c58055]" checked={draft.clearKey} disabled={saving} onChange={(event) => setDraft({ ...draft, clearKey: event.target.checked })} />Удалить сохранённый ключ</label>}
        </div>
        <div className="flex justify-end gap-2 border-t border-border pt-4"><Button variant="ghost" disabled={saving} onClick={() => setDraft(null)}>Отмена</Button><Button type="submit" variant="primary" disabled={saving}>{saving ? 'Сохраняем…' : 'Сохранить'}</Button></div>
      </form>}
    </Dialog>
  </section>
}
