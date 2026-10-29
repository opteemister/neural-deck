import { useEffect, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { History, LoaderCircle, MessageSquare, PanelRightClose, Plus, RefreshCw, Trash2 } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Dialog } from '@/components/ui/dialog'
import { api } from '@/lib/api'
import type { ChatSummary } from '@/lib/chat-harness'

export const chatHistoryKey = ['chat-history'] as const

type Props = {
  open: boolean
  docked: boolean
  currentId: string | null
  onOpenChange: (open: boolean) => void
  onSelect: (chat: ChatSummary) => void
  onNew: () => void
  onDeleted: (id: string) => void
  onError: (title: string, detail?: string) => void
}

function dateLabel(value: number | null): string {
  return value === null ? '' : new Date(value * 1000).toLocaleString('ru-RU', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' })
}

export function ChatHistory({ open, docked, currentId, onOpenChange, onSelect, onNew, onDeleted, onError }: Props) {
  const client = useQueryClient()
  const [deleting, setDeleting] = useState<ChatSummary | null>(null)
  const toggleRef = useRef<HTMLButtonElement>(null)
  const wasOpen = useRef(false)
  const history = useQuery({ queryKey: chatHistoryKey, queryFn: ({ signal }) => api.listChats(signal), enabled: open, refetchInterval: open ? 5000 : false })
  const remove = useMutation({
    mutationFn: (id: string) => api.deleteChat(id),
    onSuccess: (_, id) => {
      setDeleting(null)
      void client.invalidateQueries({ queryKey: chatHistoryKey })
      onDeleted(id)
    },
    onError: (error) => onError('Не удалось удалить чат', error instanceof Error ? error.message : undefined),
  })

  useEffect(() => {
    if (!open && wasOpen.current) toggleRef.current?.focus()
    wasOpen.current = open
  }, [open])
  useEffect(() => {
    if (!open || deleting) return
    const escape = (event: KeyboardEvent) => { if (event.key === 'Escape') onOpenChange(false) }
    document.addEventListener('keydown', escape)
    return () => document.removeEventListener('keydown', escape)
  }, [open, deleting, onOpenChange])

  return <>
    {!open && <Button ref={toggleRef} variant="ghost" shape="pill" size="sm" className="absolute right-4 top-3 z-20 bg-background/90" aria-label="Открыть историю чатов" aria-expanded={false} aria-controls="chat-history" onClick={() => onOpenChange(true)}><History className="size-4" />Чаты</Button>}
    {open && <>
      {!docked && <button className="absolute inset-0 z-20 bg-black/35" aria-label="Закрыть историю чатов" tabIndex={-1} onClick={() => onOpenChange(false)} />}
      <aside id="chat-history" aria-label="История чатов" className="absolute bottom-3 right-3 top-3 z-30 flex max-w-[calc(100%_-_1.5rem)] w-80 flex-col overflow-hidden rounded-[28px] border border-border/60 bg-card shadow-2xl">
        <header className="flex shrink-0 items-center gap-2 px-4 pb-3 pt-4">
          <h2 className="mr-auto text-sm font-medium">Чаты</h2>
          <Button variant="ghost" shape="pill" size="icon-sm" iconOnly aria-label="Новый чат" title="Новый чат" onClick={() => { onNew(); if (!docked) onOpenChange(false) }}><Plus className="size-4" /></Button>
          <Button variant="ghost" shape="pill" size="icon-sm" iconOnly aria-label="Свернуть историю чатов" title="Свернуть" autoFocus onClick={() => onOpenChange(false)}><PanelRightClose className="size-4" /></Button>
        </header>
        <div className="min-h-0 flex-1 overflow-y-auto overscroll-contain px-2 pb-3">
          {history.isPending && <div className="flex justify-center p-8" role="status" aria-label="Загрузка чатов"><LoaderCircle className="size-4 animate-spin text-muted-foreground" /></div>}
          {history.isError && <div className="space-y-3 px-4 py-6 text-center text-xs text-muted-foreground"><p>Не удалось загрузить чаты</p><Button shape="pill" size="sm" variant="ghost" onClick={() => void history.refetch()}><RefreshCw className="size-3.5" />Повторить</Button></div>}
          {history.isSuccess && history.data.items.length === 0 && <div className="flex flex-col items-center gap-3 px-4 py-12 text-sm text-muted-foreground"><MessageSquare className="size-5 opacity-60" /><span>Пока нет чатов</span></div>}
          {history.data?.items.map((chat) => <div key={chat.id} className={`group mb-1 flex items-center gap-1 rounded-2xl pl-3 pr-1 transition-colors ${chat.id === currentId ? 'bg-accent' : 'hover:bg-background/60'}`}>
            <button type="button" className="min-w-0 flex-1 py-3 text-left outline-none focus-visible:text-copper" aria-current={chat.id === currentId ? 'page' : undefined} onClick={() => { onSelect(chat); if (!docked) onOpenChange(false) }}>
              <span className="block truncate text-[13px] font-medium" title={chat.title}>{chat.title}</span>
              <span className="mt-1 flex min-w-0 items-center gap-1.5 text-[10px] text-muted-foreground">
                {chat.busy && <LoaderCircle className="size-3 shrink-0 animate-spin" aria-label="Выполняется запрос" />}
                {chat.model && <span className="truncate">{chat.model}</span>}
                {chat.updated_at !== null && <time className="ml-auto shrink-0" dateTime={new Date(chat.updated_at * 1000).toISOString()}>{dateLabel(chat.updated_at)}</time>}
              </span>
            </button>
            <Button variant="ghost" shape="pill" size="icon-sm" iconOnly className="text-muted-foreground/60 hover:text-destructive-foreground" aria-label={`Удалить чат «${chat.title}»`} title="Удалить чат" onClick={() => setDeleting(chat)}><Trash2 className="size-3.5" /></Button>
          </div>)}
        </div>
      </aside>
    </>}
    <Dialog open={deleting !== null} title="Удалить чат?" dismissible={!remove.isPending} onOpenChange={(value) => { if (!value) setDeleting(null) }}>
      <div className="space-y-5 p-5">
        <div className="space-y-2"><p className="truncate text-sm font-medium">{deleting?.title}</p><p className="text-sm text-muted-foreground">История и загруженные файлы этого чата будут удалены.</p></div>
        <div className="flex justify-end gap-2"><Button shape="pill" variant="ghost" disabled={remove.isPending} onClick={() => setDeleting(null)}>Отмена</Button><Button shape="pill" variant="danger" disabled={remove.isPending} onClick={() => { if (deleting) remove.mutate(deleting.id) }}>{remove.isPending ? 'Удаляем…' : 'Удалить чат'}</Button></div>
      </div>
    </Dialog>
  </>
}
