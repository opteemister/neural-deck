import { FileText, Image as ImageIcon, Paperclip, X } from 'lucide-react'
import { Button } from '@/components/ui/button'
import type { ChatAttachment } from '@/lib/chat-media'

export function ChatAttachments({ attachments, accept, disabled, imageRoles = false, onAdd, onRemove }: {
  attachments: ChatAttachment[]
  accept: string
  disabled?: boolean
  imageRoles?: boolean
  onAdd: (files: File[]) => void
  onRemove: (id: string) => void
}) {
  return <div className="mt-2 flex flex-wrap items-center gap-2">
    <label className="inline-flex">
      <input type="file" className="sr-only" accept={accept} multiple onChange={(event) => {
        const files = Array.from(event.target.files ?? [])
        if (files.length) onAdd(files)
        event.currentTarget.value = ''
      }} disabled={disabled} />
      <span className="inline-flex h-8 cursor-pointer items-center gap-2 rounded-md border border-border bg-secondary px-2.5 text-xs text-muted-foreground transition-colors hover:border-[#6c625a] hover:bg-accent has-[:focus-visible]:ring-2 has-[:focus-visible]:ring-copper has-[:focus-visible]:ring-offset-2 has-[:focus-visible]:ring-offset-background">
        <Paperclip className="size-3.5" />
        Прикрепить
      </span>
    </label>
    {attachments.map((attachment, index) => {
      const isImage = attachment.file.type.startsWith('image/')
      const role = imageRoles ? (index === 0 ? 'Источник' : 'Маска') : undefined
      return <div key={attachment.id} className="group flex max-w-full items-center gap-2 rounded-md border border-border bg-muted px-2 py-1.5 text-xs" title={attachment.file.name}>
        {isImage ? <img src={attachment.previewUrl} alt="" className="size-8 rounded object-cover" /> : <span className="flex size-8 items-center justify-center rounded bg-secondary text-muted-foreground">{attachment.file.type === 'application/pdf' ? <FileText className="size-4" /> : <ImageIcon className="size-4" />}</span>}
        <span className="min-w-0 max-w-44"><span className="block truncate text-foreground">{attachment.file.name}</span>{role && <span className="block text-[10px] text-muted-foreground">{role}</span>}</span>
        <Button type="button" size="icon-sm" iconOnly variant="ghost" aria-label={`Удалить ${attachment.file.name}`} title="Удалить вложение" onClick={() => onRemove(attachment.id)}><X className="size-3.5" /></Button>
      </div>
    })}
  </div>
}
