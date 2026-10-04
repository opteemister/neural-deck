import { type FormEvent, useEffect, useRef, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { ArrowDown, ArrowUp, Check, Copy, LoaderCircle, Square } from 'lucide-react'
import { useSearchParams } from 'react-router-dom'
import { Button } from '@/components/ui/button'
import { ChatOptions } from '@/components/chat-options'
import { reasoningChoices, reasoningRequest, type Reasoning } from '@/lib/chat-reasoning'
import { ChatMarkdown } from '@/components/chat-markdown'
import { api, queryKeys, StudioApiError } from '@/lib/api'
import type { ChatMessage, ChatTimings, ChatUsage } from '@/types/api'

type Turn = {
  id: number; prompt: string; answer: string; reasoning: string; model: string
  state: 'streaming' | 'complete' | 'cancelled' | 'error'
  usage?: ChatUsage; timings?: ChatTimings; elapsed?: number; firstToken?: number
  finishReason?: string | null; error?: string
}
const number = (value?: number) => value === undefined ? '—' : value.toLocaleString('ru-RU')
const seconds = (value?: number) => value === undefined ? '—' : (value / 1000).toLocaleString('ru-RU', { maximumFractionDigits: 2 }) + ' с'

function CopyAnswer({ content }: { content: string }) {
  const [copied, setCopied] = useState(false)
  const [failed, setFailed] = useState(false)
  useEffect(() => { if (copied) { const timer = window.setTimeout(() => setCopied(false), 1800); return () => window.clearTimeout(timer) } }, [copied])
  return <Button variant="ghost" size="sm" iconOnly aria-label={copied ? 'Скопировано' : 'Копировать ответ'} title={failed ? 'Не удалось скопировать' : copied ? 'Скопировано' : 'Копировать ответ'} onClick={() => {
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
  const models = useQuery({ queryKey: queryKeys.openAiModels, queryFn: api.getOpenAiModels, refetchInterval: 15_000 })
  const [draft, setDraft] = useState('')
  const [turns, setTurns] = useState<Turn[]>([])
  const [reasoning, setReasoning] = useState<Reasoning>('auto')
  const [busy, setBusy] = useState(false)
  const [activeTurn, setActiveTurn] = useState<number | null>(null)
  const [atBottom, setAtBottom] = useState(true)
  const scrollRef = useRef<HTMLDivElement>(null)
  const inputRef = useRef<HTMLTextAreaElement>(null)
  const controllerRef = useRef<AbortController | null>(null)
  const followRef = useRef(true)
  const nextId = useRef(0)
  const availableModels = models.data?.items ?? []
  const requestedModel = params.get('model') ?? ''
  const activeModel = availableModels.some((model) => model.id === requestedModel) ? requestedModel : availableModels[0]?.id ?? ''
  const selectedModel = availableModels.find((model) => model.id === activeModel)
  const activeReasoning = reasoningChoices(selectedModel).all.some((option) => option.value === reasoning) ? reasoning : 'auto'
  let usedContext: number | undefined
  for (let index = turns.length - 1; index >= 0; index--) {
    const turn = turns[index]
    if (turn.model !== activeModel) continue
    const usage = turn.usage
    const tokens = usage?.total_tokens ?? (usage?.prompt_tokens !== undefined && usage.completion_tokens !== undefined ? usage.prompt_tokens + usage.completion_tokens : undefined)
    if (tokens !== undefined) { usedContext = tokens; break }
  }
  const capacity = selectedModel?.context_length
  const contextPercent = usedContext !== undefined && capacity ? Math.min(100, usedContext / capacity * 100) : undefined

  useEffect(() => { if (activeModel && activeModel !== requestedModel) setParams((current) => { current.set('model', activeModel); return current }, { replace: true }) }, [activeModel, requestedModel, setParams])
  useEffect(() => () => controllerRef.current?.abort(), [])
  useEffect(() => {
    if (inputRef.current) { inputRef.current.style.height = 'auto'; inputRef.current.style.height = Math.min(200, inputRef.current.scrollHeight) + 'px' }
  }, [draft])
  useEffect(() => {
    if (followRef.current && scrollRef.current) scrollRef.current.scrollTop = scrollRef.current.scrollHeight
  }, [turns])

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
    if (!content || !activeModel || busy) return
    const id = nextId.current++
    const started = performance.now()
    let firstToken: number | undefined
    const controller = new AbortController()
    controllerRef.current = controller
    const history: ChatMessage[] = turns.flatMap((turn) => [
      { role: 'user' as const, content: turn.prompt },
      ...(turn.answer ? [{ role: 'assistant' as const, content: turn.answer }] : []),
    ])
    const update = (patch: Partial<Turn>) => setTurns((current) => current.map((turn) => turn.id === id ? { ...turn, ...patch } : turn))
    setTurns((current) => [...current, { id, prompt: content, answer: '', reasoning: '', model: activeModel, state: 'streaming' }])
    setDraft(''); setBusy(true); setActiveTurn(id); followRef.current = true
    try {
      await api.streamChat({
        model: activeModel, messages: [...history, { role: 'user', content }], stream: true,
        ...reasoningRequest(activeReasoning, selectedModel),
      }, (chunk) => {
        if (firstToken === undefined && (chunk.content || chunk.reasoning)) firstToken = performance.now() - started
        setTurns((current) => current.map((turn) => turn.id === id ? {
          ...turn, answer: turn.answer + chunk.content, reasoning: turn.reasoning + (chunk.reasoning ?? ''),
          usage: chunk.usage ?? turn.usage, timings: chunk.timings ?? turn.timings,
          firstToken, finishReason: chunk.finishReason ?? turn.finishReason,
        } : turn))
      }, controller.signal)
      update({ state: 'complete', elapsed: performance.now() - started, firstToken })
    } catch (caught) {
      update({ state: controller.signal.aborted ? 'cancelled' : 'error', elapsed: performance.now() - started, firstToken,
        error: controller.signal.aborted ? undefined : caught instanceof StudioApiError ? caught.message : 'Не удалось получить ответ модели.' })
    } finally {
      controllerRef.current = null
      setBusy(false)
      inputRef.current?.focus()
    }
  }

  return <div className="relative flex h-[calc(100dvh-4rem)] min-h-0 flex-col">
    {turns.length > 0 && <nav aria-label="Запросы диалога" className="absolute left-1 top-1/2 z-10 flex max-h-[45vh] -translate-y-1/2 flex-col gap-1 overflow-y-auto sm:left-3">
      {turns.map((turn, index) => <button key={turn.id} type="button" aria-label={'Перейти к запросу ' + (index + 1)} aria-current={activeTurn === turn.id ? 'step' : undefined} title={'Запрос ' + (index + 1) + ': ' + turn.prompt.slice(0, 80)} onClick={() => jump(turn.id)} className="group flex h-3 w-5 items-center justify-center rounded focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-copper"><span className={'h-0.5 rounded transition-all group-hover:w-4 group-hover:bg-foreground ' + (activeTurn === turn.id ? 'w-4 bg-copper' : 'w-2 bg-muted-foreground/50')} /></button>)}
    </nav>}
    <div ref={scrollRef} onScroll={onScroll} className="min-h-0 flex-1 overflow-y-auto overscroll-contain px-8 sm:px-12" aria-label="Диалог">
      <div className="mx-auto w-full max-w-3xl pb-10 pt-8 sm:pt-12">
        {turns.length === 0 ? <div className="flex min-h-[45vh] items-center justify-center text-sm text-muted-foreground">С чего начнём?</div> : turns.map((turn, index) => <section key={turn.id} data-turn={turn.id} className="mb-12 scroll-mt-8" aria-label={'Запрос ' + (index + 1)}>
          <div className="mb-7 flex justify-end"><div className="max-w-[90%] whitespace-pre-wrap break-words rounded-xl bg-accent px-4 py-3 text-sm leading-6">{turn.prompt}</div></div>
          {turn.reasoning && <details className="mb-5 text-muted-foreground"><summary className="cursor-pointer text-xs hover:text-foreground">Рассуждение{turn.state === 'streaming' && !turn.answer ? '…' : ''}</summary><div className="mt-3 border-l border-border pl-4"><ChatMarkdown source={turn.reasoning} /></div></details>}
          <div className="min-h-6">{turn.answer ? <ChatMarkdown source={turn.answer} /> : turn.state === 'streaming' ? <span className="inline-flex items-center gap-2 text-xs text-muted-foreground"><LoaderCircle className="size-3.5 animate-spin" />{turn.reasoning ? 'Модель рассуждает…' : 'Модель отвечает…'}</span> : null}</div>
          {turn.error && <p role="alert" className="mt-3 text-xs text-[#d0a390]">{turn.error}</p>}
          {turn.state !== 'streaming' && <div className="mt-4 flex flex-wrap items-center gap-x-3 gap-y-1">{turn.answer && <CopyAnswer content={turn.answer} />}<ResponseStats turn={turn} /></div>}
        </section>)}
      </div>
    </div>
    <div className="relative shrink-0 px-8 pb-3 pt-2 sm:px-12 sm:pb-4">
      {!atBottom && <div className="absolute -top-10 left-1/2 -translate-x-1/2"><Button variant="secondary" size="sm" iconOnly aria-label="К последнему ответу" title="К последнему ответу" onClick={() => { followRef.current = true; scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: 'smooth' }) }}><ArrowDown className="size-3.5" /></Button></div>}
      <div className="mx-auto max-w-3xl">
        <form onSubmit={(event) => void submit(event)} className="rounded-xl border border-border bg-card p-3 focus-within:border-[#6c625a]">
          <textarea ref={inputRef} value={draft} onChange={(event) => setDraft(event.target.value)} aria-label="Сообщение" placeholder="Напишите сообщение…" rows={2} className="block max-h-[200px] min-h-[56px] w-full resize-none border-0 bg-transparent px-1 py-1 text-sm leading-6 text-foreground outline-none placeholder:text-muted-foreground" onKeyDown={(event) => { if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) { event.preventDefault(); event.currentTarget.form?.requestSubmit() } }} />
          <div className="mt-2 flex items-center justify-end gap-2">
            <ChatOptions models={availableModels} model={activeModel} reasoning={activeReasoning} disabled={busy} loading={models.isFetching} error={models.isError} onOpen={() => void models.refetch()}
              onModel={(model) => { setReasoning('auto'); setParams((current) => { current.set('model', model); return current }, { replace: true }) }} onReasoning={setReasoning} />
            {busy ? <Button size="sm" iconOnly variant="primary" aria-label="Остановить генерацию" title="Остановить генерацию" onClick={() => controllerRef.current?.abort()}><Square className="size-3.5" /></Button> : <Button type="submit" size="sm" iconOnly variant="primary" aria-label="Отправить" title="Отправить · Enter" disabled={!draft.trim() || !activeModel}><ArrowUp className="size-3.5" /></Button>}
          </div>
        </form>
        <div className="mt-2 flex flex-wrap items-center justify-between gap-2 px-1 text-[11px] text-muted-foreground">
          <span>Enter — отправить · Shift+Enter — новая строка</span>
          <span className="inline-flex items-center gap-2 tabular-nums" aria-label="Контекст диалога" title="Последний известный контекст с учётом ответа. Во время генерации сохраняется последнее измерение; число обновляется из usage движка. Прочерк означает, что измерения ещё нет.">
            {contextPercent !== undefined && <span className="h-1 w-12 overflow-hidden rounded bg-border"><span className="block h-full bg-copper" style={{ width: contextPercent + '%' }} /></span>}
            Контекст {number(usedContext)}{capacity ? ' / ' + number(capacity) : ' токенов'}
          </span>
        </div>
      </div>
    </div>
  </div>
}
