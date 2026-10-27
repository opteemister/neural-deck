import { Select } from '@/components/ui/select'
import type { ChatOperation } from '@/lib/chat-media'
import type { OpenAiModel } from '@/types/api'

const operationOptions: Array<{ value: ChatOperation; label: string }> = [
  { value: 'text', label: 'Текст' },
  { value: 'vision', label: 'Vision' },
  { value: 'image-generation', label: 'Генерация изображения' },
  { value: 'image-edit', label: 'Edit изображения' },
  { value: 'image-variations', label: 'Вариации изображения' },
  { value: 'video', label: 'Видео' },
  { value: 'ocr', label: 'OCR' },
]

export function ChatOperationPicker({ operation, model, models, showModel = false, disabled, onOperation, onModel }: {
  operation: ChatOperation
  model: string
  models: OpenAiModel[]
  showModel?: boolean
  disabled?: boolean
  onOperation: (operation: ChatOperation) => void
  onModel: (model: string) => void
}) {
  const available = models.filter((item) => {
    if (operation === 'image-generation' || operation === 'image-edit' || operation === 'image-variations') return item.types?.includes('image') ?? true
    if (operation === 'video') return item.types?.includes('video') ?? true
    if (operation === 'vision') return item.types?.includes('text') ?? true
    return true
  })
  return <div className="flex min-w-0 flex-wrap items-center gap-2">
    <div className="w-44 max-w-full"><Select aria-label="Операция чата" value={operation} disabled={disabled} onChange={(event) => onOperation(event.target.value as ChatOperation)} placement="top">
      {operationOptions.map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}
    </Select></div>
    {showModel && <div className="w-52 max-w-full"><Select aria-label="Модель операции" value={model} disabled={disabled || available.length === 0} onChange={(event) => onModel(event.target.value)} placement="top">
      {available.map((item) => <option key={item.id} value={item.id}>{item.id}</option>)}
      {!available.length && <option value="">Нет подходящих моделей</option>}
    </Select></div>}
  </div>
}
