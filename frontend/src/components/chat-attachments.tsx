import { useRef } from 'react'
import { FileText, Plus, X } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { formatBytes } from '@/lib/utils'
import { isImageAttachment, type ChatAttachment } from '@/lib/chat-media'

export function ChatAttachButton({ disabled, onAdd }: { disabled?: boolean; onAdd: (files: File[]) => void }) {
  const input = useRef<HTMLInputElement>(null)
  return <>
    <input ref={input} type="file" hidden multiple onChange={(event) => {
      const files = Array.from(event.target.files ?? [])
      if (files.length) onAdd(files)
      event.currentTarget.value = ''
    }} disabled={disabled} />
    <Button variant="ghost" size="icon" shape="pill" className="!h-11 !w-11 !min-h-11" aria-label="Прикрепить файлы" title="Прикрепить файлы" disabled={disabled} onClick={() => input.current?.click()}><Plus className="size-5" strokeWidth={1.7} /></Button>
  </>
}

export function ChatAttachments({ attachments, imageRoles = false, disabled, onRemove }: {
  attachments: ChatAttachment[]; imageRoles?: boolean; disabled?: boolean; onRemove: (id: string) => void
}) {
  if (!attachments.length) return null
  return <div className="flex flex-wrap gap-2 px-2 pb-2 pt-1" aria-label="Вложения">
    {attachments.map((attachment, index) => <div key={attachment.id} className="group relative flex max-w-full items-center gap-3 rounded-2xl bg-background/60 py-2 pl-2 pr-10 text-xs" title={attachment.file.name}>
      {isImageAttachment(attachment) ? <img src={attachment.previewUrl} alt={attachment.file.name} className="size-12 rounded-xl object-cover" /> : <span className="flex size-12 shrink-0 items-center justify-center rounded-xl bg-card text-muted-foreground"><FileText className="size-5" strokeWidth={1.5} /></span>}
      <span className="min-w-0 max-w-40"><span className="block truncate text-foreground">{attachment.file.name}</span><span className="mt-1 block text-[11px] text-muted-foreground">{imageRoles ? index === 0 ? 'Изображение' : 'Маска' : formatBytes(attachment.file.size)}</span></span>
      <Button type="button" size="icon-sm" shape="pill" variant="ghost" className="absolute right-1 top-1" aria-label={`Удалить ${attachment.file.name}`} title="Удалить вложение" disabled={disabled} onClick={() => onRemove(attachment.id)}><X className="size-3.5" /></Button>
    </div>)}
  </div>
}
