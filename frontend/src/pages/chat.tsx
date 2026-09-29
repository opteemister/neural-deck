import { FormEvent, useEffect, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Bot, MessageSquare, Send, Trash2, UserRound } from 'lucide-react'
import { useSearchParams } from 'react-router-dom'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Select } from '@/components/ui/select'
import { Textarea } from '@/components/ui/textarea'
import { PageHeader } from '@/components/page-header'
import { QueryEmpty, QueryError, QueryLoading } from '@/components/query-state'
import { api, queryKeys, StudioApiError } from '@/lib/api'
import type { ChatMessage } from '@/types/api'

export function ChatPage() {
  const [params, setParams] = useSearchParams()
  const models = useQuery({ queryKey: queryKeys.openAiModels, queryFn: api.getOpenAiModels, refetchInterval: 15_000 })
  const [draft, setDraft] = useState('')
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<unknown>(null)
  const availableModels = models.data?.items ?? []
  const requestedModel = params.get('model') ?? ''
  const activeModel = availableModels.some((model) => model.id === requestedModel) ? requestedModel : availableModels[0]?.id ?? ''
  useEffect(() => { if (activeModel && activeModel !== requestedModel) setParams((current) => { current.set('model', activeModel); return current }, { replace: true }) }, [activeModel, requestedModel, setParams])

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const content = draft.trim()
    if (!content || !activeModel || busy) return
    const next: ChatMessage[] = [...messages, { role: 'user', content }, { role: 'assistant', content: '' }]
    setMessages(next); setDraft(''); setError(null); setBusy(true)
    try {
      await api.streamChat({ model: activeModel, messages: next.slice(0, -1), stream: true }, (chunk) => { if (chunk.content) setMessages((current) => current.map((message, index) => index === current.length - 1 ? { ...message, content: message.content + chunk.content } : message)) })
} catch (caught) { setError(caught) }
    finally { setBusy(false) }
  }

  return <div><PageHeader eyebrow="Студия / доступ" title="Чат" description="Запросы идут в общий OpenAI-совместимый API и используют только реально зарегистрированные модели." />{models.isLoading ? <QueryLoading label="Получаем доступные модели" /> : models.isError ? <QueryError error={models.error} retry={() => void models.refetch()} label="Список моделей общего API недоступен" /> : availableModels.length === 0 ? <QueryEmpty title="Нет доступных моделей" description="Запущенный экземпляр должен зарегистрировать модель в общем API." /> : <div className="mx-auto max-w-4xl"><Card><CardHeader><div className="flex items-center justify-between gap-3"><div className="flex items-center gap-3"><MessageSquare className="size-4 text-copper" /><CardTitle>Новый диалог</CardTitle></div><Button size="sm" variant="ghost" onClick={() => { setMessages([]); setError(null) }} disabled={busy}><Trash2 className="size-3.5" />Очистить</Button></div></CardHeader><CardContent><div className="mb-5 flex flex-wrap items-center gap-3"><label htmlFor="chat-model" className="text-xs text-muted-foreground">Модель</label><Select id="chat-model" className="max-w-md" value={activeModel} onChange={(event) => setParams((current) => { current.set('model', event.target.value); return current }, { replace: true })}>{availableModels.map((model) => <option key={model.id} value={model.id}>{model.id}</option>)}</Select></div><div className="min-h-72 space-y-3 rounded-md border border-border bg-secondary p-4">{messages.length === 0 ? <div className="flex min-h-60 flex-col items-center justify-center gap-2 text-center text-muted-foreground"><Bot className="size-5 text-copper" /><p className="text-xs">Напишите сообщение, чтобы проверить доступность модели.</p></div> : messages.map((message, index) => <div key={`${message.role}-${index}`} className={`flex gap-3 ${message.role === 'user' ? 'justify-end' : 'justify-start'}`}><div className={`flex max-w-[85%] gap-2 rounded-md border px-3 py-2 text-xs leading-5 ${message.role === 'user' ? 'border-[#75513e] bg-[#302a26]' : 'border-border bg-card'}`}>{message.role === 'user' ? <UserRound className="mt-0.5 size-3.5 shrink-0 text-copper" /> : <Bot className="mt-0.5 size-3.5 shrink-0 text-copper" />}<p className="whitespace-pre-wrap">{message.content || (busy && index === messages.length - 1 ? '…' : '')}</p></div></div>)}{busy && <div className="flex items-center gap-2 text-xs text-muted-foreground"><Bot className="size-3.5 text-copper" />Модель отвечает…</div>}</div>{Boolean(error) && <div className="mt-3 text-xs text-[#d0a390]">{error instanceof StudioApiError ? error.message : 'Ответ модели не получен. Проверьте состояние экземпляра и повторите запрос.'}</div>}<form className="mt-4 flex flex-col gap-2 sm:flex-row" onSubmit={submit}><Textarea className="min-h-20 flex-1" value={draft} onChange={(event) => setDraft(event.target.value)} placeholder="Введите сообщение…" aria-label="Сообщение" /><Button variant="primary" type="submit" disabled={!draft.trim() || !activeModel || busy}><Send className="size-3.5" />Отправить</Button></form></CardContent></Card></div>}</div>
}
