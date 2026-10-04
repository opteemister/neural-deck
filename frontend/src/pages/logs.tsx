import { useEffect, useMemo, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useSearchParams } from 'react-router-dom'
import { ArrowDown, Check, Copy, Download, Filter, RefreshCw, Trash2 } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Select } from '@/components/ui/select'
import { QueryEmpty, QueryError, QueryLoading } from '@/components/query-state'
import { api, queryKeys, StudioApiError } from '@/lib/api'
import type { LogLine, LogSource } from '@/types/api'

const LOG_PAGE_SIZE = 500

function mergeLines(current: LogLine[], incoming: LogLine[]): LogLine[] {
  const byNumber = new Map(current.map((line) => [line.number, line]))
  incoming.forEach((line) => byNumber.set(line.number, line))
  return [...byNumber.values()].sort((left, right) => left.number - right.number)
}

function sourceLabel(source: LogSource): string {
  if (source.kind === 'application') return 'Application'
  if (source.kind === 'operations') return 'Operations'
  return `Профиль: ${source.profile_name ?? source.label}`
}

export function LogsPage() {
  const [params] = useSearchParams()
  return <LogsViewer initialProfile={params.get('profile') ?? undefined} initialSource={params.get('source') ?? ''} />
}

export function LogsViewer({ initialProfile, initialSource = '', drawer = false }: { initialProfile?: string; initialSource?: string; drawer?: boolean }) {
  const queryClient = useQueryClient()
  const sources = useQuery({ queryKey: queryKeys.logs, queryFn: api.getLogSources, refetchInterval: 10_000, refetchOnMount: 'always' })
  const [selectedId, setSourceId] = useState(initialSource)
  const [profileName, setProfileName] = useState<string | null>(initialProfile ?? null)
  function setSelectedId(id: string) {
    setProfileName(null)
    setSourceId(id)
  }
  const [filter, setFilter] = useState('')
  const [autoRefresh, setAutoRefresh] = useState(true)
  const [autoScroll, setAutoScroll] = useState(true)
  const [lines, setLines] = useState<LogLine[]>([])
  const [loadingOlder, setLoadingOlder] = useState(false)
  const [manualRefreshing, setManualRefreshing] = useState(false)
  const [downloadError, setDownloadError] = useState<string | null>(null)
  const [atBottom, setAtBottom] = useState(true)
  const [copying, setCopying] = useState(false)
  const [copied, setCopied] = useState(false)
  const [copyError, setCopyError] = useState<string | null>(null)
  const scrollRef = useRef<HTMLDivElement>(null)
  const atBottomRef = useRef(true)
  const loadedSourceRef = useRef<string | null>(null)

  const sourceItems = sources.data?.items ?? []
  const activeSelectedId = profileName !== null
    ? sourceItems.find((source) => source.kind === 'profile' && source.profile_name === profileName)?.id ?? ''
    : selectedId && sourceItems.some((source) => source.id === selectedId) ? selectedId : sourceItems[0]?.id ?? ''
  const selectedSource = sourceItems.find((source) => source.id === activeSelectedId) ?? null

  const log = useQuery({
    queryKey: queryKeys.log(activeSelectedId),
    queryFn: () => api.getLog(activeSelectedId, undefined, LOG_PAGE_SIZE),
    enabled: Boolean(activeSelectedId),
    refetchInterval: autoRefresh ? 3_000 : false,
    refetchOnMount: 'always',
  })

  useEffect(() => {
    setLines([])
    loadedSourceRef.current = null
    atBottomRef.current = true
    setAtBottom(true)
    setCopied(false)
    setCopyError(null)
  }, [activeSelectedId])

  useEffect(() => {
    if (!log.data || !activeSelectedId) return
    setLines((current) => {
      const currentEnd = current.length > 0 ? current[current.length - 1].number + 1 : 0
      if (loadedSourceRef.current !== activeSelectedId || log.data.total_lines < currentEnd) {
        loadedSourceRef.current = activeSelectedId
        return log.data.lines
      }
      return mergeLines(current, log.data.lines)
    })
  }, [activeSelectedId, log.data])

  const visibleLines = useMemo(() => {
    const needle = filter.trim().toLocaleLowerCase()
    return needle ? lines.filter((line) => line.text.toLocaleLowerCase().includes(needle)) : lines
  }, [filter, lines])
  function updateScrollPosition() {
    const element = scrollRef.current
    if (!element) return
    const bottom = element.scrollHeight - element.scrollTop - element.clientHeight < 16
    atBottomRef.current = bottom
    setAtBottom(bottom)
  }

  function scrollToBottom() {
    if (!scrollRef.current) return
    scrollRef.current.scrollTop = scrollRef.current.scrollHeight
    updateScrollPosition()
  }

  useEffect(() => {
    const element = scrollRef.current
    if (!element) return
    if (autoScroll && atBottomRef.current) element.scrollTop = element.scrollHeight
    const bottom = element.scrollHeight - element.scrollTop - element.clientHeight < 16
    atBottomRef.current = bottom
    setAtBottom(bottom)
  }, [autoScroll, visibleLines])

  const lineNumberDigits = String((visibleLines.at(-1)?.number ?? 0) + 1).length
  const Container = drawer ? 'div' : Card
  const Header = drawer ? 'div' : CardHeader
  const Content = drawer ? 'div' : CardContent
  const hasOlder = (lines[0]?.number ?? 0) > 0

  const remove = useMutation({
    mutationFn: () => api.deleteLog(activeSelectedId),
    onSuccess: async () => {
      setLines([])
      await queryClient.invalidateQueries({ queryKey: queryKeys.logs })
      await queryClient.invalidateQueries({ queryKey: queryKeys.log(activeSelectedId) })
    },
  })

  async function refresh() {
    if (manualRefreshing) return
    setManualRefreshing(true)
    try {
      const refreshedSources = await sources.refetch()
      const refreshedItems = refreshedSources.data?.items ?? []
      const refreshedId = profileName !== null
        ? refreshedItems.find((source) => source.kind === 'profile' && source.profile_name === profileName)?.id ?? ''
        : refreshedItems.some((source) => source.id === activeSelectedId) ? activeSelectedId : refreshedItems[0]?.id ?? ''
      if (profileName === null && refreshedId !== selectedId) setSelectedId(refreshedId)
      if (refreshedId) {
        await queryClient.fetchQuery({
          queryKey: queryKeys.log(refreshedId),
          queryFn: () => api.getLog(refreshedId, undefined, LOG_PAGE_SIZE),
          staleTime: 0,
        })
      }
    } finally {
      setManualRefreshing(false)
    }
  }

  async function loadOlder() {
    const firstLine = lines[0]?.number
    if (firstLine === undefined || firstLine <= 0 || loadingOlder || !activeSelectedId || !scrollRef.current) return
    const element = scrollRef.current
    const previousHeight = element.scrollHeight
    const previousTop = element.scrollTop
    setLoadingOlder(true)
    try {
      const older = await api.getLog(activeSelectedId, firstLine, LOG_PAGE_SIZE)
      setLines((current) => mergeLines(current, older.lines))
      window.setTimeout(() => {
        if (scrollRef.current) scrollRef.current.scrollTop = scrollRef.current.scrollHeight - previousHeight + previousTop
      }, 0)
    } finally {
      setLoadingOlder(false)
    }
  }

  async function download() {
    if (!activeSelectedId) return
    setDownloadError(null)
    try {
      const blob = await api.downloadLog(activeSelectedId)
      const url = URL.createObjectURL(blob)
      const anchor = document.createElement('a')
      anchor.href = url
      anchor.download = `${selectedSource?.label ?? 'log'}.log`
      document.body.appendChild(anchor)
      anchor.click()
      anchor.remove()
      URL.revokeObjectURL(url)
    } catch (error) {
      setDownloadError(error instanceof StudioApiError ? error.message : 'Лог не удалось скачать.')
    }
  }

  async function copyLatest() {
    if (!activeSelectedId || copying) return
    setCopying(true)
    setCopied(false)
    setCopyError(null)
    try {
      const file = await api.downloadLog(activeSelectedId, true)
      await navigator.clipboard.writeText(await file.text())
      setCopied(true)
    } catch (error) {
      setCopyError(error instanceof StudioApiError ? error.message : 'Не удалось скопировать последний файл лога.')
    } finally {
      setCopying(false)
    }
  }

  function onScroll() {
    const element = scrollRef.current
    if (!element) return
    updateScrollPosition()
    if (element.scrollTop < 16 && hasOlder) void loadOlder()
  }

  function deleteSelected() {
    if (!selectedSource || remove.isPending) return
    if (window.confirm(`Удалить лог «${sourceLabel(selectedSource)}»?`)) void remove.mutateAsync()
  }

  if (sources.isLoading) return <QueryLoading label="Получаем список логов" />
  if (sources.isError) return <QueryError error={sources.error} retry={() => void sources.refetch()} label="Список логов недоступен" />

  return <div className={drawer ? 'flex h-full min-h-0 flex-col' : undefined}>
    {sourceItems.length === 0 ? <QueryEmpty title="Логи пока не появились" description="Они появятся после запуска приложения или первой операции." /> : <Container className={drawer ? 'flex min-h-0 flex-1 flex-col' : undefined}>
      <Header className={drawer ? 'shrink-0 border-b border-border p-3' : undefined}>
        <div className="flex flex-wrap items-center gap-2">
          <div className="min-w-0 flex-1"><Select aria-label="Выберите лог" value={activeSelectedId} onChange={(event) => setSelectedId(event.target.value)}>
            {!activeSelectedId && <option value="">Логи профиля пока не появились</option>}
            {sourceItems.map((source) => <option key={source.id} value={source.id}>{sourceLabel(source)} · {source.file_count} файлов</option>)}
          </Select></div>
          <div className="ml-auto flex shrink-0 flex-wrap gap-2">
            <Button size="sm" variant="secondary" onClick={() => void refresh()} disabled={manualRefreshing}><RefreshCw className={`size-3.5 ${manualRefreshing ? 'animate-spin' : ''}`} />Обновить</Button>
            <Button size="sm" variant="secondary" onClick={() => void download()} disabled={!activeSelectedId || !selectedSource}><Download className="size-3.5" />Скачать</Button>
            <Button size="sm" variant="danger" onClick={deleteSelected} disabled={!activeSelectedId || !selectedSource || remove.isPending}><Trash2 className="size-3.5" />Удалить</Button>
          </div>
        </div>
      </Header>
      <Content className={drawer ? 'flex min-h-0 flex-1 flex-col' : undefined}>
        <div className={`flex shrink-0 flex-wrap items-center gap-4 border-b border-border ${drawer ? 'p-3' : 'mb-3 pb-3'}`}>
          <div className="relative min-w-56 flex-1"><Filter className="pointer-events-none absolute left-3 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground" /><Input className="pl-8 pr-20" value={filter} onChange={(event) => setFilter(event.target.value)} placeholder="Фильтр по тексту" aria-label="Фильтр по тексту" /><span className="pointer-events-none absolute right-2 top-1/2 -translate-y-1/2 rounded bg-muted px-1.5 py-0.5 font-mono text-[10px] text-muted-foreground">{visibleLines.length}/{log.data?.total_lines ?? lines.length}</span></div>
          <Button size="sm" variant="ghost" iconOnly onClick={() => void copyLatest()} disabled={!activeSelectedId || !selectedSource?.file_count || copying} aria-label={copied ? 'Последний файл скопирован' : 'Копировать последний файл лога'} title={copied ? 'Скопировано' : 'Копировать последний файл лога'}>{copied ? <Check className="size-3.5" /> : <Copy className="size-3.5" />}</Button>
          <div className="flex items-center gap-4">
            <label className="flex items-center gap-2 text-xs text-muted-foreground"><input type="checkbox" checked={autoRefresh} onChange={(event) => setAutoRefresh(event.target.checked)} />Авторефреш</label>
            <label className="flex items-center gap-2 text-xs text-muted-foreground"><input type="checkbox" checked={autoScroll} onChange={(event) => setAutoScroll(event.target.checked)} />Автоскролл</label>
          </div>
        </div>
        {copyError && <p role="alert" className="mb-3 text-xs text-[#d0a390]">{copyError}</p>}
        {downloadError && <p className="mb-3 text-xs text-[#d0a390]">{downloadError}</p>}
        {!activeSelectedId ? <QueryEmpty title="Логи профиля пока не появились" description={`Профиль: ${profileName ?? ''}`} /> : log.isError ? <QueryError error={log.error} retry={() => void log.refetch()} label="Лог недоступен" /> : log.isPending && !log.data ? <QueryLoading label="Загружаем выбранный лог" /> : <div className={drawer ? 'relative flex min-h-0 flex-1 flex-col' : 'relative'}><div ref={scrollRef} onScroll={onScroll} className={`${drawer ? 'min-h-0 flex-1 p-2' : 'h-[min(65vh,42rem)] rounded-md border border-border p-3'} overflow-auto overscroll-contain bg-background font-mono text-[11px] leading-5`} aria-label="Содержимое лога">{loadingOlder && <p className="mb-2 text-center text-muted-foreground">Загружаем более ранние строки…</p>}{visibleLines.length === 0 ? <p className="text-muted-foreground">В выбранном логе пока нет строк.</p> : visibleLines.map((line) => <div key={line.number} className="flex min-w-max whitespace-pre"><span style={{ width: `calc(${lineNumberDigits}ch + 0.5rem)` }} className="mr-2 inline-block shrink-0 select-none border-r border-border pr-2 text-right text-muted-foreground">{line.number + 1}</span><span>{line.text || ' '}</span></div>)}</div>{!atBottom && <Button type="button" size="md" variant="secondary" className="absolute bottom-4 right-4 z-10 min-w-24 rounded-full px-4 shadow-md" aria-label="Прокрутить лог до конца" title="Прокрутить лог до конца" onClick={scrollToBottom}><ArrowDown className="size-4" />Вниз</Button>}</div>}
      </Content>
    </Container>}
  </div>
}
