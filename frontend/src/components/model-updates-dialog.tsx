import { useEffect, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Check, LoaderCircle, RefreshCw } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Dialog } from '@/components/ui/dialog'
import { Progress } from '@/components/ui/progress'
import { QueryEmpty, QueryError, QueryLoading } from '@/components/query-state'
import { api, queryKeys } from '@/lib/api'
import { formatBytes } from '@/lib/utils'
import type { LocalModelArtifact, ModelFileUpdates, ModelFileVersion, Operation } from '@/types/api'

const updatesKey = (id: string) => ['model-file-updates', id] as const
const running = (operation?: Operation | null) => !!operation && ['queued', 'running'].includes(operation.state)
function date(value: string | null) { return value ? new Date(value).toLocaleString('ru-RU', { dateStyle: 'medium' }) : '—' }
const fileColumns = 'grid min-w-[680px] grid-cols-[minmax(12rem,1fr)_10.5rem_10.5rem_7rem] items-center gap-x-4'
function Version({ value }: { value: ModelFileVersion }) {
  return <span className="whitespace-nowrap text-[11px]">{date(value.file_date)}{value.size !== null && <span className="text-muted-foreground"> ({formatBytes(value.size)})</span>}</span>
}

type Props = {
  model: LocalModelArtifact
  operations: Operation[]
  onClose: () => void
  onStarted: (operation: Operation) => void
  onError: (message: string) => void
}

export function ModelUpdatesDialog({ model, operations, onClose, onStarted, onError }: Props) {
  const client = useQueryClient()
  const [started, setStarted] = useState<Operation | null>(null)
  const finished = useRef('')
  const [snapshot, setSnapshot] = useState<ModelFileUpdates | null>(null)
  const [selectedPaths, setSelectedPaths] = useState<string[] | null>(null)
  const details = useQuery({ queryKey: updatesKey(model.id), queryFn: () => api.getModelFileUpdates(model.id), gcTime: 0 })
  useEffect(() => { if (details.data) setSnapshot((current) => current ?? details.data!) }, [details.data])
  const existing = operations.find((item) => item.target_id === `hf-update:${model.id}` && running(item))
  useEffect(() => { if (existing && !started) setStarted(existing) }, [existing, started])
  const operationId = started?.id ?? existing?.id
  const status = useQuery({ queryKey: queryKeys.operation(operationId ?? ''), queryFn: () => api.getOperation(operationId!), enabled: !!operationId, refetchInterval: (query) => running(query.state.data) ? 1000 : false })
  const operation = status.data ?? started ?? existing
  const busy = running(operation)
  const update = useMutation({
    mutationFn: (files: string[] | null) => api.updateModelFiles(model.id, files),
    onMutate: (files) => {
      const remaining = details.data?.items.filter((item) => item.status !== 'removed').map((item) => item.path) ?? []
      setSelectedPaths(files ?? remaining)
    },
    onSuccess: (value) => {
      client.setQueryData(queryKeys.operation(value.id), value)
      setStarted(value)
      onStarted(value)
    },
    onError: (error) => onError(error instanceof Error ? error.message : 'Не удалось запустить обновление'),
  })
  useEffect(() => {
    if (!operation || running(operation)) return
    const key = `${operation.id}:${operation.created_at}:${operation.state}`
    if (finished.current === key) return
    finished.current = key
    void client.invalidateQueries({ queryKey: updatesKey(model.id) })
    void client.invalidateQueries({ queryKey: queryKeys.localModels })
    void client.invalidateQueries({ queryKey: queryKeys.downloads })
    if (operation.state === 'failed') onError(operation.error ?? 'Не удалось обновить файлы')
  }, [client, model.id, operation, onError])
  const displayed = snapshot ?? details.data
  const rows = displayed?.items ?? []
  const remaining = new Set(details.data?.items.map((item) => item.path))
  const completed = (path: string) => details.isSuccess && !remaining.has(path)
  const selectable = rows.filter((item) => item.status !== 'removed' && !completed(item.path))
  const active = busy || update.isPending || (operation?.state === 'succeeded' && details.isFetching)
  const showProgress = active || !!operation || update.isError
  const progress = !update.isPending ? operation?.progress : null
  const selected = selectedPaths ?? selectable.map((item) => item.path)
  const selectedRows = rows.filter((item) => selected.includes(item.path))
  const totalFiles = progress?.files_total || selected.length
  const totalBytes = progress?.bytes_total || (selectedRows.every((item) => item.remote.size !== null) ? selectedRows.reduce((sum, item) => sum + (item.remote.size ?? 0), 0) : null)
  const bytesCompleted = progress?.bytes_completed ?? 0
  const percent = totalBytes ? bytesCompleted / totalBytes * 100 : 0
  const failed = operation?.state === 'failed' || update.isError
  return <Dialog open title="Обновления модели" size="wide" onOpenChange={(open) => { if (!open) onClose() }}>
    <div className="space-y-4 p-5">
      <div className="flex flex-wrap items-center justify-between gap-3"><div className="min-w-0"><p className="truncate text-sm font-medium">{model.hf_match.repo_id}</p>{displayed?.checked_at && <p className="mt-1 text-[11px] text-muted-foreground">Сверено {date(displayed.checked_at)}</p>}</div><Button size="sm" variant="primary" disabled={!selectable.length || active} onClick={() => update.mutate(null)}><RefreshCw className={`size-3.5 ${active ? 'animate-spin' : ''}`} />Обновить все изменённые</Button></div>
      {showProgress && <div className="h-14 space-y-2" role="status" aria-label="Обновление файлов"><div className="flex justify-between gap-3 text-xs text-muted-foreground"><span>{active ? 'Обновляем файлы…' : failed ? 'Обновление не завершено' : 'Обновление завершено'}</span><span className="shrink-0 tabular-nums">{progress?.files_completed ?? 0} / {totalFiles}</span></div><Progress value={percent} /><p className="whitespace-nowrap text-[11px] tabular-nums text-muted-foreground">{formatBytes(bytesCompleted)} / {totalBytes === null ? '—' : formatBytes(totalBytes)}</p></div>}
      {status.isError && <QueryError error={status.error} retry={() => void status.refetch()} label="Не удалось получить ход обновления" />}
      {!displayed && details.isPending ? <QueryLoading label="Загружаем список файлов" /> : !displayed && details.isError ? <QueryError error={details.error} retry={() => void details.refetch()} label="Список обновлений недоступен" /> : rows.length === 0 ? <QueryEmpty title="Все файлы обновлены" description="Локальные файлы совпадают с сохранённой версией Hugging Face." /> : <div className="overflow-x-auto">
        <div className={`${fileColumns} pb-2 text-[10px] text-muted-foreground`}><span>Файл</span><span>Локально</span><span>Hugging Face</span><span /></div>
        <div className="min-w-[680px] divide-y divide-border">{rows.map((file) => {
          const done = completed(file.path)
          const updating = active && !done && selected.includes(file.path)
          return <div key={file.path} className={`${fileColumns} py-2`} data-testid="model-file-update">
            <span className="truncate font-mono text-xs" title={file.path}>{file.path}</span>
            <Version value={file.local} />
            <Version value={file.remote} />
            {file.status === 'removed' ? <span className="justify-self-end text-[10px] text-muted-foreground">Удалён на HF</span> : <Button className="w-full justify-self-end" size="sm" variant="secondary" disabled={done || active} aria-label={done ? `Обновлено ${file.path}` : `Обновить ${file.path}`} onClick={() => update.mutate([file.path])}>{done ? <Check className="size-3.5" /> : updating ? <LoaderCircle className="size-3.5 animate-spin" /> : <RefreshCw className="size-3.5" />}{done ? 'Готово' : updating ? 'Обновляем…' : 'Обновить'}</Button>}
          </div>
        })}</div>
      </div>}
    </div>
  </Dialog>
}
