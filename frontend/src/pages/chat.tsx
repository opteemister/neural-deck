import { type FormEvent, useEffect, useRef, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { ArrowDown, ArrowUp, Check, Copy, LoaderCircle, Square } from 'lucide-react'
import { useSearchParams } from 'react-router-dom'
import { Button } from '@/components/ui/button'
import { ChatOptions } from '@/components/chat-options'
import { ChatAttachButton, ChatAttachments } from '@/components/chat-attachments'
import { ChatMediaResult } from '@/components/chat-media-result'
import { NotificationToast } from '@/components/ui/notification-toast'
import { reasoningChoices, reasoningRequest, type Reasoning } from '@/lib/chat-reasoning'
import { ChatMarkdown } from '@/components/chat-markdown'
import { api, queryKeys } from '@/lib/api'
import {
  createImageGeneration,
  createImageMultipart,
  createOcr,
  createVideo,
  fileToDataUrl,
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
}

type ToastState = { id: number; tone: 'success' | 'error' | 'info'; title: string; message?: string } | null

const number = (value?: number) => value === undefined ? '—' : value.toLocaleString('ru-RU')
const seconds = (value?: number) => value === undefined ? '—' : (value / 1000).toLocaleString('ru-RU', { maximumFractionDigits: 2 }) + ' с'

function CopyAnswer({ content }: { content: string }) {
  const [copied, setCopied] = useState(false)
  const [failed, setFailed] = useState(false)
  useEffect(() => { if (copied) { const timer = window.setTimeout(() => setCopied(false), 1800); return () => window.clearTimeout(timer) } }, [copied])
  return <Button variant="ghost" shape="pill" size="sm" iconOnly aria-label={copied ? 'Скопировано' : 'Копировать ответ'} title={failed ? 'Не удалось скопировать' : copied ? 'Скопировано' : 'Копировать ответ'} onClick={() => {
    void navigator.clipboard.writeText(content).then(() => { setCopied(true); setFailed(false) }).catch(() => setFailed(true))
  }}>{copied ? <Check className="size-3.5" /> : <Copy className="size-3.5" />}</Button>
}

function ResponseStats({ turn }: { turn: Turn }) {
  const input = turn.usage?.prompt_tokens ?? turn.timings?.prompt_n
  const output = turn.usage?.completion_tokens ?? turn.timings?.predicted_n
  const measuredSpeed = turn.timings?.predicted_per_second
  const generationTime = turn.elapsed !== undefined && turn.firstToken !== undefined ? turn.elapsed - turn.firstToken : undefined
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
  const [params, setParams] = useSearchParams()
  const models = useQuery({ queryKey: queryKeys.openAiModels, queryFn: () => api.getOpenAiModels(true), refetchInterval: 15_000 })
  const [draft, setDraft] = useState('')
  const [turns, setTurns] = useState<Turn[]>([])
  const [reasoning, setReasoning] = useState<Reasoning>('auto')
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
  const nextToastId = useRef(0)
  const objectUrlsRef = useRef<string[]>([])
  const allModels = (models.data?.items ?? []).filter((model) => chatModelType(model) !== null)
  const requestedModel = params.get('model') ?? ''
  const activeModel = requestedModel || allModels.find((model) => chatModelType(model) === 'text')?.id || allModels[0]?.id || ''
  const selectedModel = allModels.find((model) => model.id === activeModel)
  const modelType = chatModelType(selectedModel)
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
    if (followRef.current && scrollRef.current) scrollRef.current.scrollTop = scrollRef.current.scrollHeight
  }, [turns])

  function notifyError(title: string, message?: string) {
    setToast({ id: nextToastId.current++, tone: 'error', title, message })
  }
  function changeModel(model: string) {
    setReasoning('auto')
    setParams((current) => { current.set('model', model); return current }, { replace: true })
    inputRef.current?.focus()
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
    const started = performance.now()
    let firstToken: number | undefined
    let id: number | undefined
    const update = (patch: Partial<Turn>) => setTurns((current) => current.map((turn) => turn.id === id ? { ...turn, ...patch } : turn))

    try {
      const operation = resolveChatOperation(selectedModel, attachments, content)
      if (operation === 'video' && (!content || attachments.length > 1)) throw new Error('Опишите видео и при необходимости прикрепите одно исходное изображение.')
      if ((operation === 'image-edit' || operation === 'image-variations') && attachments.length > 2) throw new Error('Прикрепите одно изображение и, при необходимости, маску.')
      if (operation === 'ocr' && (attachments.length !== 1 || !['image', 'document'].includes(classifyAttachment(attachments[0])))) throw new Error('Прикрепите один документ или скан для распознавания.')
      const userContent = operation === 'text' || operation === 'vision' ? await prepareUserContent(content, attachments) : undefined
      if (controller.signal.aborted) return

      const history: Array<{ role: 'user' | 'assistant'; content: string | VisionContentPart[] }> = turns
        .filter((turn) => turn.state !== 'error' && (turn.operation === 'text' || turn.operation === 'vision' || turn.result?.kind === 'ocr'))
        .flatMap((turn) => [
          { role: 'user' as const, content: turn.userContent ?? turn.prompt },
          ...((turn.answer || turn.result?.kind === 'ocr') ? [{ role: 'assistant' as const, content: turn.answer || (turn.result?.kind === 'ocr' ? turn.result.markdown : '') }] : []),
        ])
      const prompt = content || (operation === 'ocr' ? 'Распознать документ' : operation === 'image-variations' ? 'Вариация изображения' : '')
      const turnAttachments = attachments.map((attachment) => ({ name: attachment.file.name, previewUrl: attachment.previewUrl, type: isImageAttachment(attachment) ? 'image/*' : attachment.file.type }))
      const validAttachment = attachments[0]
      id = nextId.current++
      setTurns((current) => [...current, { id: id!, prompt, answer: '', reasoning: '', model: activeModel, operation, userContent, attachments: turnAttachments, state: 'streaming' }])
      setDraft(''); setAttachments([]); setActiveTurn(id); followRef.current = true

      if (operation === 'text' || operation === 'vision') {
        const request = { model: activeModel, messages: [...history, { role: 'user' as const, content: userContent ?? prompt }], stream: true, ...reasoningRequest(activeReasoning, selectedModel) }
        await api.streamChat(request, (chunk) => {
          if (firstToken === undefined && (chunk.content || chunk.reasoning)) firstToken = performance.now() - started
          setTurns((current) => current.map((turn) => turn.id === id ? {
            ...turn, answer: turn.answer + chunk.content, reasoning: turn.reasoning + (chunk.reasoning ?? ''),
            usage: chunk.usage ?? turn.usage, timings: chunk.timings ?? turn.timings,
            firstToken, finishReason: chunk.finishReason ?? turn.finishReason,
          } : turn))
        }, controller.signal)
      } else if (operation === 'image-generation') {
        update({ result: await createImageGeneration({ model: activeModel, prompt }, controller.signal) })
      } else if (operation === 'image-edit' || operation === 'image-variations') {
        update({ result: await createImageMultipart(operation === 'image-edit' ? '/images/edits' : '/images/variations', { model: activeModel, prompt: operation === 'image-edit' ? prompt : undefined, image: validAttachment.file, mask: attachments[1]?.file }, controller.signal) })
      } else if (operation === 'video') {
        const created = await createVideo({ model: activeModel, prompt, inputReference: validAttachment?.file }, controller.signal)
        const status = created.id ? await waitForVideo(created.id, controller.signal) : created
        const result = await resolveVideoResult(status, controller.signal)
        if (result.url.startsWith('blob:')) objectUrlsRef.current.push(result.url)
        update({ result })
      } else if (operation === 'ocr') {
        const result = await createOcr({ model: activeModel, documentUrl: await fileToDataUrl(validAttachment.file) }, controller.signal)
        update({ result })
      }
      update({ state: 'complete', elapsed: performance.now() - started, firstToken })
    } catch (caught) {
      const cancelled = controller.signal.aborted
      const error = caught instanceof Error ? caught.message : 'Не удалось выполнить запрос.'
      if (id !== undefined) update({ state: cancelled ? 'cancelled' : 'error', elapsed: performance.now() - started, firstToken, error: cancelled ? undefined : error })
      if (!cancelled) notifyError('Не удалось отправить запрос', error)
    } finally {
      controllerRef.current = null
      setBusy(false)
      inputRef.current?.focus()
    }
  }

  const canSubmit = !busy && !!selectedModel && (!!draft.trim() || !!attachments.length)
  const placeholder = activeModel && !selectedModel && !models.isFetching ? 'Модель недоступна — выберите другую' : modelType === 'image' ? attachments.length ? 'Что изменить на изображении?' : 'Какую картинку создадим?'
    : modelType === 'video' ? 'Опишите видео, которое хотите создать…'
    : modelType === 'ocr' ? 'Прикрепите документ для распознавания…' : 'Напишите сообщение…'
  const hasTurns = turns.length > 0

  return <div className="relative flex h-full min-h-0 flex-col">
    {toast && <NotificationToast key={toast.id} tone={toast.tone} title={toast.title} message={toast.message} />}
    {hasTurns && <nav aria-label="Запросы диалога" className="absolute left-1 top-1/2 z-10 flex max-h-[45vh] -translate-y-1/2 flex-col gap-1 overflow-y-auto sm:left-3">
      {turns.map((turn, index) => <button key={turn.id} type="button" aria-label={'Перейти к запросу ' + (index + 1)} aria-current={activeTurn === turn.id ? 'step' : undefined} title={'Запрос ' + (index + 1) + ': ' + turn.prompt.slice(0, 80)} onClick={() => jump(turn.id)} className="group flex h-3 w-5 items-center justify-center rounded-full focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-copper"><span className={'h-0.5 rounded-full transition-all group-hover:w-4 group-hover:bg-foreground ' + (activeTurn === turn.id ? 'w-4 bg-copper' : 'w-2 bg-muted-foreground/50')} /></button>)}
    </nav>}
    <div ref={scrollRef} onScroll={onScroll} className={hasTurns ? 'min-h-0 flex-1 overflow-y-auto overscroll-contain px-7 sm:px-12' : 'hidden'} aria-label="Диалог">
      <div className="mx-auto w-full max-w-3xl pb-10 pt-8 sm:pt-12">
        {turns.map((turn, index) => <section key={turn.id} data-turn={turn.id} className="mb-12 scroll-mt-8" aria-label={'Запрос ' + (index + 1)}>
          <div className="mb-7 flex justify-end"><div className="max-w-[90%] rounded-3xl bg-card px-5 py-3 text-sm leading-6"><div className="whitespace-pre-wrap break-words">{turn.prompt}</div>{turn.attachments && turn.attachments.length > 0 && <div className="mt-3 flex flex-wrap gap-2">{turn.attachments.map((attachment) => attachment.previewUrl && attachment.type.startsWith('image/') ? <img key={attachment.name + attachment.previewUrl} src={attachment.previewUrl} alt={attachment.name} className="max-h-32 max-w-44 rounded-2xl object-cover" /> : <span key={attachment.name} className="rounded-full bg-background/60 px-3 py-1 text-xs text-muted-foreground">{attachment.name}</span>)}</div>}</div></div>
          {turn.reasoning && <details className="mb-5 text-muted-foreground"><summary className="cursor-pointer text-xs hover:text-foreground">Рассуждение{turn.state === 'streaming' && !turn.answer ? '…' : ''}</summary><div className="mt-3 border-l border-border pl-4"><ChatMarkdown source={turn.reasoning} /></div></details>}
          <div className="min-h-6">{turn.answer ? <ChatMarkdown source={turn.answer} /> : turn.result ? <ChatMediaResult result={turn.result} onError={(error) => notifyError('Не удалось скачать файл', error instanceof Error ? error.message : undefined)} /> : turn.state === 'streaming' ? <span className="inline-flex items-center gap-2 text-xs text-muted-foreground"><LoaderCircle className="size-3.5 animate-spin" />{turn.operation === 'video' ? 'Создаём видео…' : turn.operation === 'ocr' ? 'Читаем документ…' : turn.operation === 'image-generation' ? 'Создаём изображение…' : turn.operation === 'image-edit' || turn.operation === 'image-variations' ? 'Изменяем изображение…' : turn.reasoning ? 'Думаю…' : 'Готовлю ответ…'}</span> : null}</div>
          {turn.error && <p role="alert" className="mt-3 text-xs text-[#d0a390]">{turn.error}</p>}
          {turn.state !== 'streaming' && <div className="mt-3 flex flex-wrap items-center gap-3">{turn.answer && <CopyAnswer content={turn.answer} />}{(turn.operation === 'text' || turn.operation === 'vision') && <details className="text-muted-foreground"><summary className="cursor-pointer text-[11px] hover:text-foreground">Статистика</summary><div className="pt-2"><ResponseStats turn={turn} /></div></details>}</div>}
        </section>)}
      </div>
    </div>
    <div className={hasTurns ? 'relative shrink-0 px-4 pb-5 pt-2 sm:px-12 sm:pb-6' : 'flex min-h-0 flex-1 flex-col justify-center px-4 pb-24 sm:px-12'}>
      {hasTurns && !atBottom && <div className="absolute -top-12 left-1/2 -translate-x-1/2"><Button variant="secondary" shape="pill" size="icon" aria-label="К последнему ответу" title="К последнему ответу" onClick={() => { followRef.current = true; scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: 'smooth' }) }}><ArrowDown className="size-4" /></Button></div>}
      <div className="mx-auto w-full max-w-3xl">
        {!hasTurns && <h1 className="mb-7 text-center text-2xl font-medium tracking-tight text-foreground sm:text-[28px]">Чем могу помочь?</h1>}
        <form onSubmit={(event) => void submit(event)} onDragOver={(event) => { if (event.dataTransfer.types.includes('Files')) { event.preventDefault(); setDragging(true) } }} onDragLeave={(event) => { if (!event.currentTarget.contains(event.relatedTarget as Node | null)) setDragging(false) }} onDrop={(event) => { event.preventDefault(); setDragging(false); addAttachments(Array.from(event.dataTransfer.files)) }} className={`relative rounded-[28px] border bg-card px-3 pb-2.5 pt-3 shadow-[0_8px_32px_rgba(0,0,0,0.08)] transition-colors sm:px-4 ${dragging ? 'border-copper' : 'border-border/60 focus-within:border-muted-foreground/50'}`}>
          <ChatAttachments attachments={attachments} disabled={busy} imageRoles={modelType === 'image'} onRemove={removeAttachment} />
          <textarea ref={inputRef} value={draft} onChange={(event) => setDraft(event.target.value)} onPaste={(event) => { const files = Array.from(event.clipboardData.files); if (files.length) { event.preventDefault(); addAttachments(files) } }} aria-label="Сообщение" placeholder={dragging ? 'Отпустите файлы здесь' : placeholder} rows={2} className="block max-h-[200px] min-h-[54px] w-full resize-none border-0 bg-transparent px-2 py-2 text-[15px] leading-6 text-foreground outline-none placeholder:text-muted-foreground/80" onKeyDown={(event) => { if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) { event.preventDefault(); event.currentTarget.form?.requestSubmit() } }} />
          <div className="mt-2 flex min-w-0 items-center justify-between gap-2">
            <div className="flex min-w-0 items-center gap-1">
              <ChatAttachButton disabled={busy} onAdd={addAttachments} />
              <ChatOptions models={allModels} model={activeModel} reasoning={activeReasoning} disabled={busy} loading={models.isFetching} error={models.isError} onOpen={() => void models.refetch()} onModel={changeModel} onReasoning={setReasoning} />
            </div>
            <div className="flex shrink-0 items-center gap-3">
              {modelType === 'text' && usedContext !== undefined && <span className="hidden items-center gap-2 text-[10px] tabular-nums text-muted-foreground sm:inline-flex" aria-label="Контекст диалога" title="Использованный контекст последнего ответа">
                {contextPercent !== undefined && <span className="h-1 w-8 overflow-hidden rounded-full bg-border"><span className="block h-full bg-copper" style={{ width: contextPercent + '%' }} /></span>}
                Контекст {number(usedContext)}{capacity ? ' / ' + number(capacity) : ' токенов'}
              </span>}
              {busy ? <Button size="icon" shape="pill" variant="ghost" className="!h-11 !w-11 !min-h-11 !bg-foreground !text-background" aria-label="Остановить генерацию" title="Остановить генерацию" onClick={() => controllerRef.current?.abort()}><Square className="size-4 fill-current" /></Button> : <Button type="submit" size="icon" shape="pill" variant="ghost" className="!h-11 !w-11 !min-h-11 !bg-foreground !text-background disabled:!bg-muted-foreground/15 disabled:!text-muted-foreground/60" aria-label="Отправить" title="Отправить · Enter" disabled={!canSubmit}><ArrowUp className="size-5" strokeWidth={2} /></Button>}
            </div>
          </div>
        </form>
      </div>
    </div>
  </div>
}
