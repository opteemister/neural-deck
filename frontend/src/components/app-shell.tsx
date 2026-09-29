import { useState } from 'react'
import { NavLink, Outlet, useLocation } from 'react-router-dom'
import { Activity, Boxes, Cpu, FlaskConical, LayoutDashboard, Menu, MessageSquare, Settings2, SlidersHorizontal, Terminal, X } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'

const navGroups = [
  { label: 'Студия', items: [
    { to: '/', label: 'Обзор', icon: LayoutDashboard, end: true },
    { to: '/profiles', label: 'Профили', icon: Terminal },
    { to: '/models', label: 'Модели', icon: Boxes },
    { to: '/engines', label: 'Движки', icon: Cpu },
    { to: '/chat', label: 'Чат', icon: MessageSquare },
  ] },
  { label: 'Наблюдение', items: [
    { to: '/status', label: 'Статус', icon: Activity },
    { to: '/benchmarks', label: 'Benchmarks', icon: FlaskConical },
  ] },
]

function Navigation({ onNavigate }: { onNavigate?: () => void }) {
  return <nav className="flex flex-1 flex-col gap-6" aria-label="Основная навигация">{navGroups.map((group) => <div key={group.label}><p className="mb-2 px-3 text-[11px] text-muted-foreground">{group.label}</p><div className="space-y-0.5">{group.items.map(({ to, label, icon: Icon, end }) => <NavLink key={to} to={to} end={end} onClick={onNavigate} className={({ isActive }) => cn('flex min-h-10 items-center gap-3 rounded-md px-3 text-xs text-muted-foreground transition-colors hover:bg-accent hover:text-foreground', isActive && 'bg-card text-foreground')}><Icon className="size-4" aria-hidden="true" />{label}{label === 'Benchmarks' && <span className="ml-auto text-[10px] text-muted-foreground">позже</span>}</NavLink>)}</div></div>)}</nav>
}

export function AppShell() {
  const [mobileOpen, setMobileOpen] = useState(false)
  const location = useLocation()
  return <div className="min-h-screen bg-background text-foreground">
    <header className="flex h-16 items-center gap-6 border-b border-border bg-secondary px-4 sm:px-6"><Button variant="ghost" size="icon" className="lg:hidden" aria-label="Открыть навигацию" onClick={() => setMobileOpen(true)}><Menu className="size-5" /></Button><NavLink to="/" className="text-lg font-semibold tracking-tight">llm<span className="mx-0.5 text-copper">/</span>studio</NavLink><div className="hidden items-center gap-2 text-xs text-muted-foreground sm:flex"><span className="size-1.5 rounded-full bg-copper" />локальное окружение</div><div className="ml-auto hidden items-center gap-2 text-[11px] text-muted-foreground md:flex"><SlidersHorizontal className="size-3.5" />{location.pathname === '/' ? 'Система' : 'Управление'}</div></header>
    <div className="flex min-h-[calc(100vh-4rem)]">
      <aside className="hidden w-56 shrink-0 flex-col border-r border-border bg-secondary px-3 py-6 lg:flex"><Navigation /><div className="mt-auto border-t border-border px-3 pt-4 text-[11px] text-muted-foreground"><NavLink to="/settings" className="mb-3 flex items-center gap-3 hover:text-foreground"><Settings2 className="size-4" />Настройки</NavLink><div className="flex items-center justify-between"><span>OpenAI API</span><code className="text-foreground">/v1</code></div></div></aside>
      {mobileOpen && <div className="fixed inset-0 z-50 lg:hidden"><button className="absolute inset-0 bg-black/70" aria-label="Закрыть навигацию" onClick={() => setMobileOpen(false)} /><aside className="relative flex h-full w-72 flex-col border-r border-border bg-secondary px-3 py-5"><div className="mb-6 flex items-center justify-between px-3"><span className="text-lg font-semibold">Навигация</span><Button variant="ghost" size="icon" aria-label="Закрыть навигацию" onClick={() => setMobileOpen(false)}><X className="size-4" /></Button></div><Navigation onNavigate={() => setMobileOpen(false)} /><div className="mt-auto border-t border-border px-3 pt-4 text-[11px] text-muted-foreground"><NavLink to="/settings" onClick={() => setMobileOpen(false)} className="flex items-center gap-3 hover:text-foreground"><Settings2 className="size-4" />Настройки</NavLink></div></aside></div>}
      <main className="min-w-0 flex-1"><div className="mx-auto w-full max-w-[1440px] px-4 py-6 sm:px-6 lg:px-8"><Outlet /></div></main>
    </div>
  </div>
}
