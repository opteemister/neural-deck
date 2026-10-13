import { useEffect, useState, type ReactNode } from 'react'
import { AlertTriangle, CircleCheck, Info, X } from 'lucide-react'

type NotificationTone = 'success' | 'error' | 'info'

const toneStyles: Record<NotificationTone, string> = {
  success: 'border-[#6e5b4e]',
  error: 'border-[#72544a]',
  info: 'border-border',
}

const toneIcons = {
  success: CircleCheck,
  error: AlertTriangle,
  info: Info,
}

export function NotificationToast({ tone = 'info', title, message, action, durationMs = 6_000 }: {
  tone?: NotificationTone
  title: string
  message?: string
  action?: ReactNode
  durationMs?: number | null
}) {
  const [visible, setVisible] = useState(true)
  const Icon = toneIcons[tone]

  useEffect(() => {
    setVisible(true)
    if (durationMs === null) return
    const timeout = window.setTimeout(() => setVisible(false), durationMs)
    return () => window.clearTimeout(timeout)
  }, [durationMs, message, title, tone])

  if (!visible) return null
  return <div role={tone === 'error' ? 'alert' : 'status'} aria-live={tone === 'error' ? 'assertive' : 'polite'} className={`fixed right-4 top-20 z-50 w-[min(30rem,calc(100vw-2rem))] rounded-md border bg-secondary px-3 py-3 text-xs shadow-xl ${toneStyles[tone]}`}><div className="flex items-start gap-3"><Icon className="mt-0.5 size-4 shrink-0 text-copper" /><div className="min-w-0 flex-1"><div className="flex items-start justify-between gap-3"><p className="font-medium text-foreground">{title}</p><button type="button" className="shrink-0 text-muted-foreground transition-colors hover:text-foreground" aria-label="Закрыть уведомление" onClick={() => setVisible(false)}><X className="size-3.5" /></button></div>{message && <p className="mt-1 break-words text-muted-foreground">{message}</p>}{action}</div></div></div>
}
