import { useEffect, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { NavLink, Outlet, useLocation } from 'react-router-dom'
import { Activity, Boxes, Check, Clock, Copy, Cpu, FlaskConical, LayoutDashboard, Menu, MessageSquare, Settings2, Terminal, X } from 'lucide-react'
import { LogsDrawerProvider, useLogsDrawer } from '@/components/logs-drawer'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'
import { api, getOpenAiEndpoint, queryKeys } from '@/lib/api'

const navGroups = [
  { label: 'Студия', items: [
    { to: '/', label: 'Обзор', icon: LayoutDashboard, end: true },
    { to: '/chat', label: 'Чат', icon: MessageSquare },
    { to: '/profiles', label: 'Профили', icon: Terminal },
    { to: '/models', label: 'Модели', icon: Boxes },
    { to: '/engines', label: 'Движки', icon: Cpu },
  ] },
  { label: 'Наблюдение', items: [
    { to: '/status', label: 'Статус', icon: Activity },
    { to: '/benchmarks', label: 'Benchmarks', icon: FlaskConical },
  ] },
]

function Navigation({ onNavigate }: { onNavigate?: () => void }) {
  return <nav className="flex flex-1 flex-col gap-6" aria-label="Основная навигация">{navGroups.map((group) => <div key={group.label}><p className="mb-2 px-3 text-[11px] text-muted-foreground">{group.label}</p><div className="space-y-0.5">{group.items.map(({ to, label, icon: Icon, end }) => <NavLink key={to} to={to} end={end} onClick={onNavigate} className={({ isActive }) => cn('flex min-h-10 items-center gap-3 rounded-md px-3 text-xs text-muted-foreground transition-colors hover:bg-accent hover:text-foreground', isActive && 'bg-card text-foreground')}><Icon className="size-4 shrink-0" aria-hidden="true" />{label}{label === 'Benchmarks' && <span className="ml-auto text-[10px] text-muted-foreground">позже</span>}</NavLink>)}</div></div>)}</nav>
}

function ApiEndpoint() {
  const [copied, setCopied] = useState(false)
  const health = useQuery({ queryKey: queryKeys.health, queryFn: api.getHealth, refetchInterval: 15_000 })
  const endpointQuery = useQuery({ queryKey: ['openai-endpoint'], queryFn: getOpenAiEndpoint })
  const endpoint = endpointQuery.data ?? ''

  async function copyEndpoint() {
    await navigator.clipboard.writeText(endpoint)
    setCopied(true)
    window.setTimeout(() => setCopied(false), 1500)
  }

  const dotClass = health.isLoading ? 'bg-muted-foreground' : health.isError || health.data?.status !== 'ok' ? 'bg-[#d09b87]' : 'bg-[#9ab77b]'
  return <button type="button" className="hidden min-w-0 items-center gap-2 rounded-md px-2 py-1 font-mono text-[11px] leading-none text-muted-foreground transition-colors hover:bg-accent hover:text-foreground sm:flex" onClick={() => void copyEndpoint()} title="Скопировать адрес OpenAI API"><span className="flex size-3.5 shrink-0 items-center justify-center" aria-label={health.data?.status === 'ok' ? 'OpenAI API доступен' : 'OpenAI API недоступен'}><span className={`size-1.5 rounded-full ${dotClass}`} /></span><span className="truncate">{endpoint}</span><span className="flex size-3.5 shrink-0 items-center justify-center">{copied ? <Check className="size-3.5 text-copper" /> : <Copy className="size-3.5" />}</span></button>
}

function ServiceUptime() {
  const health = useQuery({ queryKey: queryKeys.health, queryFn: api.getHealth, refetchInterval: 15_000, refetchIntervalInBackground: false })
  const [sample, setSample] = useState<{ seconds: number; at: number } | null>(null)
  const [now, setNow] = useState(() => performance.now())
  useEffect(() => {
    const seconds = health.data?.uptime_seconds
    if (seconds == null) { setSample(null); return }
    const at = performance.now()
    setSample({ seconds, at })
    setNow(at)
  }, [health.dataUpdatedAt, health.data?.uptime_seconds])
  useEffect(() => {
    const timer = window.setInterval(() => { if (!document.hidden) setNow(performance.now()) }, 1000)
    return () => window.clearInterval(timer)
  }, [])
  const seconds = sample ? Math.floor(sample.seconds + Math.max(0, now - sample.at) / 1000) : null
  const elapsed = seconds === null ? '—' : `${Math.floor(seconds / 86400) ? `${Math.floor(seconds / 86400)}д ` : ''}${String(Math.floor(seconds / 3600) % 24).padStart(2, '0')}:${String(Math.floor(seconds / 60) % 60).padStart(2, '0')}:${String(seconds % 60).padStart(2, '0')}`
  return <span className="flex shrink-0 items-center gap-1.5 text-[11px] text-muted-foreground" title="Время работы сервиса LLM Studio"><Clock className="size-3.5" /><span className="hidden md:inline">Uptime</span><span className="font-mono tabular-nums">{elapsed}</span></span>
}

export function AppShell() {
  return <LogsDrawerProvider><ShellContent /></LogsDrawerProvider>
}

function ShellContent() {
  const openLogs = useLogsDrawer()
  const [mobileOpen, setMobileOpen] = useState(false)
  const [collapsed, setCollapsed] = useState(() => {
    try { return window.localStorage.getItem('llm-studio.navigation-collapsed') === 'true' }
    catch { return false }
  })
  useEffect(() => {
    try { window.localStorage.setItem('llm-studio.navigation-collapsed', String(collapsed)) }
    catch { /* Navigation still works when browser storage is disabled. */ }
  }, [collapsed])
  const isChat = useLocation().pathname === '/chat'
  return <div className="min-h-screen bg-background text-foreground">
    <header className="sticky top-0 z-40 flex h-16 items-center gap-4 border-b border-border bg-secondary px-4 sm:px-6"><Button variant="ghost" size="icon" className="hidden lg:inline-flex" aria-label={collapsed ? 'Развернуть меню' : 'Свернуть меню'} title={collapsed ? 'Развернуть меню' : 'Свернуть меню'} aria-expanded={!collapsed} onClick={() => setCollapsed((current) => !current)}><Menu className="size-5" /></Button><Button variant="ghost" size="icon" className="lg:hidden" aria-label="Открыть навигацию" aria-expanded={mobileOpen} onClick={() => setMobileOpen(true)}><Menu className="size-5" /></Button><NavLink to="/" className="flex shrink-0 items-center gap-2.5 text-lg font-semibold tracking-tight"><img src="/llm-studio.svg" alt="" width={32} height={32} className="size-8 shrink-0" /><span>llm<span className="mx-0.5 text-copper">/</span>studio</span></NavLink><div className="ml-auto flex items-center gap-2"><ApiEndpoint /><ServiceUptime /><button type="button" onClick={() => openLogs()} className="rounded-md px-3 py-2 text-xs text-muted-foreground transition-colors hover:bg-accent hover:text-foreground">Логи</button></div></header>
    <div className="flex min-h-[calc(100vh-4rem)]">
      {!collapsed && <aside className="hidden w-56 shrink-0 flex-col border-r border-border bg-secondary px-3 py-6 lg:flex">
        <Navigation />
        <div className="mt-auto border-t border-border px-3 pt-4 text-[11px] text-muted-foreground"><NavLink to="/settings" className="flex items-center gap-3 hover:text-foreground"><Settings2 className="size-4 shrink-0" />Настройки</NavLink></div>
      </aside>}
      {mobileOpen && <div className="fixed inset-0 z-50 lg:hidden"><button className="absolute inset-0 bg-black/70" aria-label="Закрыть навигацию" onClick={() => setMobileOpen(false)} /><aside className="relative flex h-full w-72 flex-col border-r border-border bg-secondary px-3 py-5"><div className="mb-6 flex items-center justify-between px-3"><span className="text-lg font-semibold">Навигация</span><Button variant="ghost" size="icon" aria-label="Закрыть навигацию" onClick={() => setMobileOpen(false)}><X className="size-4" /></Button></div><Navigation onNavigate={() => setMobileOpen(false)} /><div className="mt-auto border-t border-border px-3 pt-4 text-[11px] text-muted-foreground"><NavLink to="/settings" onClick={() => setMobileOpen(false)} className="flex items-center gap-3 hover:text-foreground"><Settings2 className="size-4" />Настройки</NavLink></div></aside></div>}
      <main className="min-w-0 flex-1"><div className={isChat ? 'w-full' : 'mx-auto w-full max-w-[1440px] px-4 py-6 sm:px-6 lg:px-8'}><Outlet /></div></main>
    </div>
  </div>
}
