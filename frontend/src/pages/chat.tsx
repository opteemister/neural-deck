import { type FormEvent, useEffect, useRef, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { ArrowDown, ArrowUp, Check, Copy, LoaderCircle, Square } from 'lucide-react'
import { useSearchParams } from 'react-router-dom'
import { Button } from '@/components/ui/button'
import { Progress } from '@/components/ui/progress'
import { ChatOptions } from '@/components/chat-options'
import { ChatAttachButton, ChatAttachments } from '@/components/chat-attachments'
import { ChatMediaResult } from '@/components/chat-media-result'
import { NotificationToast } from '@/components/ui/notification-toast'
import { reasoningChoices, reasoningRequest, type Reasoning } from '@/lib/chat-reasoning'
import { DEFAULT_IMAGE_SETTINGS, imageRequestSize, maxImageMegapixels, readImageSettings, writeImageSettings, type ImageSettings } from '@/lib/chat-image-settings'
import { DEFAULT_VIDEO_SETTINGS, normalizeVideoSettings, videoRequestSize, readVideoSettings, writeVideoSettings, type VideoSettings } from '@/lib/chat-video-settings'
import { readPendingVideo, savePendingVideo, clearPendingVideo, type PendingVideo } from '@/lib/chat-video-job'
import { watchConversation, type ChatConversation, type HarnessRequest } from '@/lib/chat-harness'
import { ChatHistory, chatHistoryKey } from '@/components/chat-history'
import { ChatMarkdown } from '@/components/chat-markdown'
import { api, queryKeys } from '@/lib/api'
import {
  chatModelType,
  classifyAttachment,
  isImageAttachment,
  prepareUserContent,
  resolveChatOperation,
  resolveVideoResult,
  waitForVideo,
  type ChatAttachment,
  type ChatOperation,
  type MediaResult,
  type VisionContentPart,
} from '@/lib/chat-media'
import type { ChatTimings, ChatUsage } from '@/types/api'

type TurnAttachment = { name: string; previewUrl?: string; type: string }
type Turn = {
  id: number
  prompt: string
  answer: string
  reasoning: string
  model: string
  operation: ChatOperation
  userContent?: string | VisionContentPart[]
  attachments?: TurnAttachment[]
  result?: MediaResult
  state: 'streaming' | 'complete' | 'cancelled' | 'error'
  usage?: ChatUsage
  timings?: ChatTimings
  elapsed?: number
  firstToken?: number
  finishReason?: string | null
  error?: string
  videoId?: string
  videoState?: 'waiting' | 'reconnecting' | 'downloading'
  serverId?: string
  results?: MediaResult[]
  phase?: string | null
}

type ToastState = { id: number; tone: 'success' | 'error' | 'info'; title: string; message?: string } | null

const number = (value?: number) => value === undefined ? '—' : value.toLocaleString('ru-RU')
const seconds = (value?: number) => value === undefined ? '—' : (value / 1000).toLocaleString('ru-RU', { maximumFractionDigits: 2 }) + ' с'
const toolPhases: Record<string, string> = {
  preparing_pdf: 'Подготавливаю PDF…', reading_document: 'Распознаю документ…',
  get_image: 'Рассматриваю изображение…',
  generate_image: 'Создаю изображение…', edit_image: 'Изменяю изображение…',
  generate_video: 'Создаю видео…', reconnecting: 'Восстанавливаем связь…',
}

function CopyAnswer({ content }: { content: string }) {
  const [copied, setCopied] = useState(false)
  const [failed, setFailed] = useState(false)
  useEffect(() => { if (copied) { const timer = window.setTimeout(() => setCopied(false), 1800); return () => window.clearTimeout(timer) } }, [copied])
  return <Button variant="ghost" shape="pill" size="sm" iconOnly aria-label={copied ? 'Скопировано' : 'Копировать ответ'} title={failed ? 'Не удалось скопировать' : copied ? 'Скопировано' : 'Копировать ответ'} onClick={() => {
    void navigator.clipboard.writeText(content).then(() => { setCopied(true); setFailed(false) }).catch(() => setFailed(true))
  }}>{copied ? <Check className="size-3.5" /> : <Copy className="size-3.5" />}</Button>
}

function ResponseStats({ turn }: { turn: Turn }) {
  if (turn.operation !== 'text' && turn.operation !== 'vision') return <div className="text-[11px] tabular-nums text-muted-foreground" aria-label="Статистика ответа">Всего {seconds(turn.elapsed)}{turn.state === 'cancelled' ? ' · Генерация прервана' : ''}</div>
  const input = turn.usage?.prompt_tokens ?? turn.timings?.prompt_n
  const output = turn.usage?.completion_tokens ?? turn.timings?.predicted_n
  const measuredSpeed = turn.timings?.predicted_per_second
  const generationTime = !turn.results?.length && turn.elapsed !== undefined && turn.firstToken !== undefined ? turn.elapsed - turn.firstToken : undefined
  const speed = measuredSpeed ?? (output !== undefined && output > 1 && generationTime && generationTime > 0 ? (output - 1) / (generationTime / 1000) : undefined)
  return <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] tabular-nums text-muted-foreground" aria-label="Статистика ответа">
    <span title="Токены входного контекста">Вход {number(input)}</span>
    <span title="Сгенерированные токены, включая reasoning, если движок учитывает его в usage">Выход {number(output)}</span>
    <span title={measuredSpeed === undefined ? 'Приблизительная скорость по времени получения потока в браузере' : 'Скорость, сообщённая движком'}>{speed === undefined ? '—' : (measuredSpeed === undefined ? '≈ ' : '') + speed.toLocaleString('ru-RU', { maximumFractionDigits: 1 })} ток/с</span>
    <span title="Время до первого токена ответа или reasoning">Первый токен {seconds(turn.firstToken)}</span>
    <span title="Полное время запроса">Всего {seconds(turn.elapsed)}</span>
    {turn.usage?.completion_tokens_details?.reasoning_tokens !== undefined && <span>Reasoning {number(turn.usage.completion_tokens_details.reasoning_tokens)}</span>}
    {turn.usage?.prompt_tokens_details?.cached_tokens !== undefined && <span>Кэш {number(turn.usage.prompt_tokens_details.cached_tokens)}</span>}
    {turn.finishReason === 'length' && <span className="text-copper">Достигнут лимит токенов</span>}
    {turn.state === 'cancelled' && <span>Генерация прервана</span>}
  </div>
}

export function ChatPage() {
  const queryClient = useQueryClient()
  const workspaceRef = useRef<HTMLDivElement>(null)
  const [historyOpen, setHistoryOpen] = useState(false)
  const [historyDocked, setHistoryDocked] = useState(false)
  useEffect(() => {
    const element = workspaceRef.current
    if (!element || typeof ResizeObserver === 'undefined') return
    const observer = new ResizeObserver(([entry]) => setHistoryDocked(entry.contentRect.width >= 1024))
    observer.observe(element)
    return () => observer.disconnect()
  }, [])
  const [params, setParams] = useSearchParams()
  const [resumableVideo] = useState<PendingVideo | null>(() => params.has('chat') ? null : readPendingVideo() ?? (params.get('video') ? { id: params.get('video')!, model: params.get('model') ?? '', prompt: 'Генерация видео', createdAt: 0 } : null))
  const models = useQuery({ queryKey: queryKeys.openAiModels, queryFn: () => api.getOpenAiModels(true), refetchInterval: 15_000 })
  const [draft, setDraft] = useState('')
  const [turns, setTurns] = useState<Turn[]>([])
  const [reasoning, setReasoning] = useState<Reasoning>('auto')
  const [imageSettingsByModel, setImageSettingsByModel] = useState(readImageSettings)
  const [videoSettingsByModel, setVideoSettingsByModel] = useState(readVideoSettings)
  const [dragging, setDragging] = useState(false)
  const [attachments, setAttachments] = useState<ChatAttachment[]>([])
  const [busy, setBusy] = useState(false)
  const [activeTurn, setActiveTurn] = useState<number | null>(null)
  const [atBottom, setAtBottom] = useState(true)
  const [toast, setToast] = useState<ToastState>(null)
  const scrollRef = useRef<HTMLDivElement>(null)
  const inputRef = useRef<HTMLTextAreaElement>(null)
  const controllerRef = useRef<AbortController | null>(null)
  const followRef = useRef(true)
  const nextId = useRef(0)
  const restoredTurnId = useRef<number | null>(null)
  const activeVideoRef = useRef<string | null>(null)
  const nextToastId = useRef(0)
  const objectUrlsRef = useRef<string[]>([])
  const harnessActiveRef = useRef<string | null>(null)
  const viewedChatRef = useRef(params.get('chat'))
  const conversationId = params.get('chat')
  const allModels = (models.data?.items ?? []).filter((model) => chatModelType(model) !== null)
  const requestedModel = params.get('model') ?? ''
  const activeModel = requestedModel || allModels.find((model) => chatModelType(model) === 'text')?.id || allModels[0]?.id || ''
  const selectedModel = allModels.find((model) => model.id === activeModel)
  const modelType = chatModelType(selectedModel)
  const imageSettings = Object.prototype.hasOwnProperty.call(imageSettingsByModel, activeModel) ? imageSettingsByModel[activeModel] : DEFAULT_IMAGE_SETTINGS
  const videoSettings = normalizeVideoSettings(Object.prototype.hasOwnProperty.call(videoSettingsByModel, activeModel) ? videoSettingsByModel[activeModel] : DEFAULT_VIDEO_SETTINGS, selectedModel)
  const activeReasoning = reasoningChoices(selectedModel).all.some((option) => option.value === reasoning) ? reasoning : 'auto'
  let usedContext: number | undefined
  for (let index = turns.length - 1; index >= 0; index--) {
    const turn = turns[index]
    if (turn.model !== activeModel) continue
    const usage = turn.usage
    const tokens = usage?.total_tokens ?? (usage?.prompt_tokens !== undefined && usage?.completion_tokens !== undefined ? usage.prompt_tokens + usage.completion_tokens : undefined)
    if (tokens !== undefined) { usedContext = tokens; break }
  }
  const capacity = selectedModel?.context_length
  const contextPercent = usedContext !== undefined && capacity ? Math.min(100, usedContext / capacity * 100) : undefined

  function changeImageSettings(settings: ImageSettings) {
    const next = { ...imageSettingsByModel, [activeModel]: settings }
    setImageSettingsByModel(next)
    if (!writeImageSettings(next)) notifyError('Браузер не сохранил настройки изображения', 'Выбранные параметры действуют до перезагрузки страницы.')
  }

  function changeVideoSettings(settings: VideoSettings) {
    const next = { ...videoSettingsByModel, [activeModel]: settings }
    setVideoSettingsByModel(next)
    if (!writeVideoSettings(next)) notifyError('Браузер не сохранил настройки видео', 'Выбранные параметры действуют до перезагрузки страницы.')
  }

  function applyConversation(conversation: ChatConversation) {
    if (viewedChatRef.current !== conversation.id) return
    if (!conversation.busy) void queryClient.invalidateQueries({ queryKey: chatHistoryKey })
    setTurns((current) => {
      const received = conversation.turns.map((turn): Turn => {
        const previous = current.find((item) => item.serverId === turn.id)
        return { ...turn, id: previous?.id ?? nextId.current++, serverId: turn.id, operation: turn.operation ?? (turn.attachments.length ? 'vision' : 'text') }
      })
      const byId = new Map(received.map((turn) => [turn.serverId, turn]))
      const existing = new Set(current.map((turn) => turn.serverId))
      return [...current.map((turn) => turn.serverId ? byId.get(turn.serverId) ?? turn : turn),
        ...received.filter((turn) => !existing.has(turn.serverId))]
    })
  }

  function openConversation(id: string | null, model?: string | null) {
    if (id === conversationId) return
    controllerRef.current?.abort()
    controllerRef.current = null
    harnessActiveRef.current = null
    viewedChatRef.current = null
    setBusy(false)
    setTurns([])
    setDraft('')
    setAttachments([])
    setReasoning('auto')
    followRef.current = true
    setParams((current) => {
      if (id) current.set('chat', id); else current.delete('chat')
      current.delete('video')
      if (model) current.set('model', model)
      return current
    })
  }

  function reconnectConversation() {
    setTurns((current) => current.map((turn) => turn.serverId && turn.state === 'streaming' ? { ...turn, phase: 'reconnecting' } : turn))
  }

  useEffect(() => {
    if (viewedChatRef.current !== conversationId) {
      controllerRef.current?.abort()
      setTurns([])
      viewedChatRef.current = conversationId
    }
    if (!conversationId || harnessActiveRef.current === conversationId) return
    const controller = new AbortController()
    controllerRef.current = controller
    harnessActiveRef.current = conversationId
    setBusy(true)
    void watchConversation(conversationId, applyConversation, controller.signal, reconnectConversation)
      .catch((error: unknown) => { if (!controller.signal.aborted) notifyError('Не удалось открыть диалог', error instanceof Error ? error.message : undefined) })
      .finally(() => {
        if (controllerRef.current === controller) { controllerRef.current = null; harnessActiveRef.current = null; setBusy(false) }
      })
    return () => {
      controller.abort()
      if (controllerRef.current === controller) { controllerRef.current = null; harnessActiveRef.current = null }
    }
    // The active request owns polling when it adds its newly created ID to the URL.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [conversationId])

  useEffect(() => { if (activeModel && !requestedModel) setParams((current) => { current.set('model', activeModel); return current }, { replace: true }) }, [activeModel, requestedModel, setParams])
  useEffect(() => { if (params.has('operation')) setParams((current) => { current.delete('operation'); return current }, { replace: true }) }, [params, setParams])
  useEffect(() => () => {
    controllerRef.current?.abort()
    objectUrlsRef.current.forEach((url) => URL.revokeObjectURL(url))
  }, [])
  useEffect(() => {
    if (inputRef.current) { inputRef.current.style.height = 'auto'; inputRef.current.style.height = Math.min(200, inputRef.current.scrollHeight) + 'px' }
  }, [draft])
  useEffect(() => {
    if (!resumableVideo) return
    let disposed = false
    let startedAt = resumableVideo.createdAt || Date.now()
    const controller = new AbortController()
    const id = restoredTurnId.current ?? nextId.current++
    restoredTurnId.current = id
    activeVideoRef.current = resumableVideo.id
    controllerRef.current = controller
    setBusy(true)
    setActiveTurn(id)
    setTurns((current) => current.some((turn) => turn.id === id) ? current : [...current, { id, prompt: resumableVideo.prompt, model: resumableVideo.model, operation: 'video', answer: '', reasoning: '', state: 'streaming', videoId: resumableVideo.id, videoState: 'waiting' }])
    const update = (change: Partial<Turn>) => { if (!disposed) setTurns((current) => current.map((turn) => turn.id === id ? { ...turn, ...change } : turn)) }
    void (async () => {
      try {
        const status = await waitForVideo(resumableVideo.id, controller.signal, (value) => {
          if (disposed) return
          if (value.created_at) startedAt = value.created_at * 1000
          const model = value.model ?? resumableVideo.model
          update({ model })
          savePendingVideo({ ...resumableVideo, model, createdAt: startedAt })
        }, (reconnecting) => update({ videoState: reconnecting ? 'reconnecting' : 'waiting' }))
        update({ videoState: 'downloading' })
        const result = await resolveVideoResult(status, controller.signal, (reconnecting) => update({ videoState: reconnecting ? 'reconnecting' : 'downloading' }))
        if (disposed) { if (result.url.startsWith('blob:')) URL.revokeObjectURL(result.url); return }
        if (result.url.startsWith('blob:')) objectUrlsRef.current.push(result.url)
        clearPendingVideo(resumableVideo.id)
        update({ result, state: 'complete', videoState: undefined, elapsed: Math.max(0, Date.now() - startedAt) })
      } catch (error) {
        if (disposed) return
        if (!controller.signal.aborted) clearPendingVideo(resumableVideo.id)
        update({ state: controller.signal.aborted ? 'cancelled' : 'error', error: controller.signal.aborted ? undefined : error instanceof Error ? error.message : 'Не удалось получить видео', elapsed: Math.max(0, Date.now() - startedAt) })
      } finally {
        if (!disposed && controllerRef.current === controller) { controllerRef.current = null; activeVideoRef.current = null; setBusy(false) }
      }
    })()
    return () => { disposed = true; controller.abort() }
  }, [resumableVideo])
  useEffect(() => {
    if (followRef.current && scrollRef.current) scrollRef.current.scrollTop = scrollRef.current.scrollHeight
  }, [turns])

  function notifyError(title: string, message?: string) {
    setToast({ id: nextToastId.current++, tone: 'error', title, message })
  }
  function changeModel(model: string) {
    setReasoning('auto')
    setParams((current) => { current.set('model', model); current.delete('video'); return current }, { replace: true })
    inputRef.current?.focus()
  }
  function stopGeneration() {
    if (harnessActiveRef.current) {
      const id = harnessActiveRef.current
      void api.stopChat(id).then(applyConversation).catch((error: unknown) => notifyError('Не удалось остановить запрос', error instanceof Error ? error.message : undefined))
      return
    }
    if (activeVideoRef.current) clearPendingVideo(activeVideoRef.current)
    controllerRef.current?.abort()
  }
  function addAttachments(files: File[]) {
    if (busy) return
    const next = files.map((file) => {
      const previewUrl = URL.createObjectURL(file)
      objectUrlsRef.current.push(previewUrl)
      return { id: `${file.name}-${file.size}-${file.lastModified}-${Math.random()}`, file, previewUrl }
    })
    if (next.length) setAttachments((current) => [...current, ...next])
    inputRef.current?.focus()
  }
  function removeAttachment(id: string) {
    setAttachments((current) => {
      const index = current.findIndex((attachment) => attachment.id === id)
      // Keep the mask from silently becoming the source image after source removal.
      if (modelType === 'image' && index === 0) return []
      return current.filter((attachment) => attachment.id !== id)
    })
  }
  function onScroll() {
    const element = scrollRef.current
    if (!element) return
    const bottom = element.scrollHeight - element.scrollTop - element.clientHeight < 64
    followRef.current = bottom
    setAtBottom(bottom)
    const top = element.getBoundingClientRect().top
    const sections = Array.from(element.querySelectorAll<HTMLElement>('[data-turn]'))
    const current = sections.find((section) => section.getBoundingClientRect().bottom > top + 100)
    if (current) setActiveTurn(Number(current.dataset.turn))
  }
  function jump(id: number) {
    followRef.current = false
    scrollRef.current?.querySelector('[data-turn="' + id + '"]')?.scrollIntoView({ behavior: 'smooth', block: 'start' })
    setActiveTurn(id)
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const content = draft.trim()
    if (busy || !selectedModel || (!content && !attachments.length)) return
    const controller = new AbortController()
    controllerRef.current = controller
    setBusy(true)
    let id: number | undefined
    const update = (patch: Partial<Turn>) => setTurns((current) => current.map((turn) => turn.id === id ? { ...turn, ...patch } : turn))

    try {
      const operation = resolveChatOperation(selectedModel, attachments, content)
      if (operation === 'video' && (!content || attachments.length > 1)) throw new Error('Опишите видео и при необходимости прикрепите одно исходное изображение.')
      if ((operation === 'image-edit' || operation === 'image-variations') && attachments.length > 2) throw new Error('Прикрепите одно изображение и, при необходимости, маску.')
      if (operation === 'ocr' && (attachments.length !== 1 || !['image', 'document'].includes(classifyAttachment(attachments[0])))) throw new Error('Прикрепите один документ или скан для распознавания.')
      const userContent = await prepareUserContent(content, attachments)
      if (controller.signal.aborted) return

      const prompt = content || (operation === 'ocr' ? 'Распознать документ' : operation === 'image-variations' ? 'Вариация изображения' : '')
      const turnAttachments = attachments.map((attachment) => ({ name: attachment.file.name, previewUrl: attachment.previewUrl, type: isImageAttachment(attachment) ? 'image/*' : attachment.file.type }))
      id = nextId.current++
      setTurns((current) => [...current, { id: id!, prompt, answer: '', reasoning: '', model: activeModel, operation, userContent, attachments: turnAttachments, state: 'streaming' }])
      setDraft(''); setAttachments([]); setActiveTurn(id); followRef.current = true

      const chatId = conversationId || crypto.randomUUID()
      const turnId = crypto.randomUUID()
      const defaults: HarnessRequest['media_defaults'] = {}
      for (const model of allModels) {
        if (chatModelType(model) === 'image') {
          const settings = imageSettingsByModel[model.id] ?? DEFAULT_IMAGE_SETTINGS
          defaults[model.id] = { size: imageRequestSize(settings, maxImageMegapixels(model)), background: settings.transparent ? 'transparent' : 'opaque' }
        } else if (chatModelType(model) === 'video') {
          const settings = normalizeVideoSettings(videoSettingsByModel[model.id] ?? DEFAULT_VIDEO_SETTINGS, model)
          defaults[model.id] = { size: videoRequestSize(settings), seconds: settings.seconds }
        }
      }
      viewedChatRef.current = chatId
      harnessActiveRef.current = chatId
      update({ serverId: turnId, phase: 'thinking' })
      setParams((current) => { current.set('chat', chatId); return current }, { replace: true })
      const snapshot = await api.sendChat(chatId, { id: turnId, model: activeModel, operation: operation === 'ocr' ? 'ocr' : 'chat', prompt, content: userContent ?? prompt,
        image_names: attachments.filter(isImageAttachment).map((item) => item.file.name),
        reasoning: reasoningRequest(activeReasoning, selectedModel), media_defaults: defaults }, controller.signal)
      void queryClient.invalidateQueries({ queryKey: chatHistoryKey })
      applyConversation(snapshot)
      await watchConversation(chatId, applyConversation, controller.signal, reconnectConversation)
    } catch (caught) {
      const cancelled = controller.signal.aborted
      if (!cancelled && activeVideoRef.current) clearPendingVideo(activeVideoRef.current)
      const error = caught instanceof Error ? caught.message : 'Не удалось выполнить запрос.'
      if (id !== undefined) update({ state: cancelled ? 'cancelled' : 'error', error: cancelled ? undefined : error })
      if (!cancelled) notifyError('Не удалось отправить запрос', error)
    } finally {
      if (controllerRef.current === controller) {
        controllerRef.current = null
        activeVideoRef.current = null
        harnessActiveRef.current = null
        setBusy(false)
        inputRef.current?.focus()
      }
    }
  }

  const canSubmit = !busy && !!selectedModel && (!!draft.trim() || !!attachments.length)
  const placeholder = activeModel && !selectedModel && !models.isFetching ? 'Модель недоступна — выберите другую' : modelType === 'image' ? attachments.length ? 'Что изменить на изображении?' : 'Какую картинку создадим?'
    : modelType === 'video' ? 'Опишите видео, которое хотите создать…'
    : modelType === 'ocr' ? 'Прикрепите документ для распознавания…' : 'Напишите сообщение…'
  const hasTurns = turns.length > 0

  return <div ref={workspaceRef} className="relative flex h-full min-h-0 min-w-0">
    <div className="relative flex h-full min-h-0 min-w-0 flex-1 flex-col transition-[padding] duration-200 motion-reduce:transition-none" style={{ paddingRight: historyOpen && historyDocked ? 336 : 0 }}>
    {toast && <NotificationToast key={toast.id} tone={toast.tone} title={toast.title} message={toast.message} />}
    {hasTurns && <nav aria-label="Запросы диалога" className="absolute left-1 top-1/2 z-10 flex max-h-[45vh] -translate-y-1/2 flex-col gap-1 overflow-y-auto sm:left-3">
      {turns.map((turn, index) => <button key={turn.id} type="button" aria-label={'Перейти к запросу ' + (index + 1)} aria-current={activeTurn === turn.id ? 'step' : undefined} title={'Запрос ' + (index + 1) + ': ' + turn.prompt.slice(0, 80)} onClick={() => jump(turn.id)} className="group flex h-3 w-5 items-center justify-center rounded-full focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-copper"><span className={'h-0.5 rounded-full transition-all group-hover:w-4 group-hover:bg-foreground ' + (activeTurn === turn.id ? 'w-4 bg-copper' : 'w-2 bg-muted-foreground/50')} /></button>)}
    </nav>}
    <div ref={scrollRef} onScroll={onScroll} className="min-h-0 flex-1 overflow-y-auto overscroll-contain px-7 sm:px-12" aria-label="Диалог">
      <div className="mx-auto w-full max-w-3xl pb-10 pt-8 sm:pt-12">
        {!hasTurns && <div className="flex min-h-[45vh] items-center justify-center text-sm text-muted-foreground">С чего начнём?</div>}
        {turns.map((turn, index) => <section key={turn.id} data-turn={turn.id} className="mb-12 scroll-mt-8" aria-label={'Запрос ' + (index + 1)}>
          <div className="mb-7 flex justify-end"><div className="max-w-[90%] rounded-3xl bg-card px-5 py-3 text-sm leading-6"><div className="whitespace-pre-wrap break-words">{turn.prompt}</div>{turn.attachments && turn.attachments.length > 0 && <div className="mt-3 flex flex-wrap gap-2">{turn.attachments.map((attachment) => attachment.previewUrl && attachment.type.startsWith('image/') ? <img key={attachment.name + attachment.previewUrl} src={attachment.previewUrl} alt={attachment.name} className="max-h-32 max-w-44 rounded-2xl object-cover" /> : <span key={attachment.name} className="rounded-full bg-background/60 px-3 py-1 text-xs text-muted-foreground">{attachment.name}</span>)}</div>}</div></div>
          {turn.reasoning && <details className="mb-5 text-muted-foreground"><summary className="cursor-pointer text-xs hover:text-foreground">Рассуждение{turn.state === 'streaming' && !turn.answer ? '…' : ''}</summary><div className="mt-3 border-l border-border pl-4"><ChatMarkdown source={turn.reasoning} /></div></details>}
          <div className="min-h-6 space-y-4">
            {turn.answer && <ChatMarkdown source={turn.answer} />}
            {[...(turn.result ? [turn.result] : []), ...(turn.results ?? [])].map((result, resultIndex) => <ChatMediaResult key={resultIndex} result={result} onError={(error) => notifyError('Не удалось скачать файл', error instanceof Error ? error.message : undefined)} />)}
            {turn.state === 'streaming' && <span className="inline-flex items-center gap-2 text-xs text-muted-foreground"><LoaderCircle className="size-3.5 animate-spin" />{turn.phase ? toolPhases[turn.phase] ?? (turn.reasoning ? 'Думаю…' : 'Готовлю ответ…') : turn.operation === 'video' ? turn.videoState === 'reconnecting' ? 'Восстанавливаем связь…' : turn.videoState === 'downloading' ? 'Получаем видео…' : 'Создаём видео…' : turn.operation === 'ocr' ? 'Читаем документ…' : turn.operation === 'image-generation' ? 'Создаём изображение…' : turn.operation === 'image-edit' || turn.operation === 'image-variations' ? 'Изменяем изображение…' : turn.reasoning ? 'Думаю…' : 'Готовлю ответ…'}</span>}
          </div>
          {turn.error && <p role="alert" className="mt-3 text-xs text-[#d0a390]">{turn.error}</p>}
          {turn.state !== 'streaming' && <div className="mt-4 flex flex-wrap items-center gap-x-3 gap-y-1">{turn.answer && <CopyAnswer content={turn.answer} />}<ResponseStats turn={turn} /></div>}
        </section>)}
      </div>
    </div>
    <div className="relative shrink-0 px-4 pb-3 pt-2 sm:px-12 sm:pb-4">
      {hasTurns && !atBottom && <div className="absolute -top-12 left-1/2 -translate-x-1/2"><Button variant="secondary" shape="pill" size="icon" aria-label="К последнему ответу" title="К последнему ответу" onClick={() => { followRef.current = true; scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: 'smooth' }) }}><ArrowDown className="size-4" /></Button></div>}
      <div className="mx-auto w-full max-w-3xl">
        <form onSubmit={(event) => void submit(event)} onDragOver={(event) => { if (event.dataTransfer.types.includes('Files')) { event.preventDefault(); setDragging(true) } }} onDragLeave={(event) => { if (!event.currentTarget.contains(event.relatedTarget as Node | null)) setDragging(false) }} onDrop={(event) => { event.preventDefault(); setDragging(false); addAttachments(Array.from(event.dataTransfer.files)) }} className={`relative rounded-[28px] border bg-card px-3 pb-2.5 pt-3 shadow-[0_8px_32px_rgba(0,0,0,0.08)] transition-colors sm:px-4 ${dragging ? 'border-copper' : 'border-border/60 focus-within:border-muted-foreground/50'}`}>
          <ChatAttachments attachments={attachments} disabled={busy} imageRoles={modelType === 'image'} onRemove={removeAttachment} />
          <textarea ref={inputRef} value={draft} onChange={(event) => setDraft(event.target.value)} onPaste={(event) => { const files = Array.from(event.clipboardData.files); if (files.length) { event.preventDefault(); addAttachments(files) } }} aria-label="Сообщение" placeholder={dragging ? 'Отпустите файлы здесь' : placeholder} rows={2} className="block max-h-[200px] min-h-[54px] w-full resize-none border-0 bg-transparent px-2 py-2 text-[15px] leading-6 text-foreground outline-none placeholder:text-muted-foreground/80" onKeyDown={(event) => { if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) { event.preventDefault(); event.currentTarget.form?.requestSubmit() } }} />
          <div className="mt-2 flex min-w-0 items-center justify-between gap-2">
            <ChatAttachButton disabled={busy} onAdd={addAttachments} />
            <div className="flex min-w-0 items-center gap-2">
              {usedContext !== undefined && usedContext > 0 && <Progress shape="circle" value={contextPercent ?? null} label="Контекст диалога" title={capacity ? `Использовано ${number(usedContext)} из ${number(capacity)} токенов` : `Использовано ${number(usedContext)} токенов · лимит неизвестен`} />}
              <ChatOptions models={allModels} model={activeModel} reasoning={activeReasoning} imageSettings={imageSettings} onImageSettings={changeImageSettings} videoSettings={videoSettings} onVideoSettings={changeVideoSettings} disabled={false} loading={models.isFetching} error={models.isError} onOpen={() => void models.refetch()} onModel={changeModel} onReasoning={setReasoning} />
              {busy ? <Button size="icon" shape="pill" variant="ghost" className="!h-11 !w-11 !min-h-11 !bg-foreground !text-background" aria-label="Остановить генерацию" title="Остановить генерацию" onClick={stopGeneration}><Square className="size-4 fill-current" /></Button> : <Button type="submit" size="icon" shape="pill" variant="ghost" className="!h-11 !w-11 !min-h-11 !bg-foreground !text-background disabled:!bg-muted-foreground/15 disabled:!text-muted-foreground/60" aria-label="Отправить" title="Отправить · Enter" disabled={!canSubmit}><ArrowUp className="size-5" strokeWidth={2} /></Button>}
            </div>
          </div>
        </form>
      </div>
    </div>
    </div>
    <ChatHistory open={historyOpen} docked={historyDocked} currentId={conversationId} onOpenChange={setHistoryOpen} onSelect={(chat) => openConversation(chat.id, chat.model)} onNew={() => openConversation(null)} onDeleted={(id) => { if (id === conversationId) openConversation(null) }} onError={notifyError} />
  </div>
}
