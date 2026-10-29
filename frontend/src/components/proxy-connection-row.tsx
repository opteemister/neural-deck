import { Pencil, RefreshCw, Trash2 } from 'lucide-react'
import { Button } from '@/components/ui/button'

export function ProxyConnectionRow({ name, url, pending, available, disabled, detail, onEdit, onDelete, onRefresh, refreshing = false }: {
  name: string; url: string; pending: boolean; available: boolean; disabled: boolean; detail?: string
  onEdit?: () => void; onDelete?: () => void; onRefresh?: () => void; refreshing?: boolean
}) {
  return <div className="flex min-w-0 flex-wrap items-center gap-2 py-2.5">
    <span className={`size-2 shrink-0 rounded-full ${available ? 'bg-[#6f9a72]' : 'bg-[#c9974f]'}`} />
    <span className="text-xs font-medium">{name}</span><span title={detail} className="text-[11px] text-muted-foreground">{pending ? 'Проверяем…' : available ? 'Подключено' : 'Не подключено'}</span>
    <span className="min-w-0 truncate font-mono text-[10px] text-muted-foreground" title={url}>{url}</span>
    {(onRefresh || onEdit || onDelete) && <div className="ml-auto flex gap-1">{onRefresh && <Button variant="ghost" size="icon-sm" iconOnly disabled={disabled || refreshing} aria-label={`Обновить модели ${name}`} title="Обновить список моделей" onClick={onRefresh}><RefreshCw className={`size-3.5 ${refreshing ? 'animate-spin' : ''}`} /></Button>}{onEdit && <Button variant="ghost" size="icon-sm" iconOnly disabled={disabled} aria-label={`Изменить подключение ${name}`} title="Изменить подключение" onClick={onEdit}><Pencil className="size-3.5" /></Button>}{onDelete && <Button variant="ghost" size="icon-sm" iconOnly disabled={disabled} aria-label={`Удалить подключение ${name}`} title="Удалить подключение" onClick={onDelete}><Trash2 className="size-3.5" /></Button>}</div>}
  </div>
}
