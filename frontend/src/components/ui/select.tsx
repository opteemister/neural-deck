import { Children, isValidElement, useEffect, useId, useLayoutEffect, useRef, useState, type ChangeEvent, type CSSProperties, type KeyboardEvent as ReactKeyboardEvent, type ReactElement, type ReactNode, type SelectHTMLAttributes } from 'react'
import { createPortal } from 'react-dom'
import { Check, ChevronDown } from 'lucide-react'
import { cn } from '@/lib/utils'

type OptionProps = { value?: string; disabled?: boolean; children?: ReactNode }

export function Select({ className, placement = 'bottom', variant = 'default', ...props }: SelectHTMLAttributes<HTMLSelectElement> & { placement?: 'top' | 'bottom'; variant?: 'default' | 'inline' }) {
  const { children, value, defaultValue, onChange, disabled, id, ...selectProps } = props
  const options = Children.toArray(children).filter((child): child is ReactElement<OptionProps> => isValidElement(child) && child.type === 'option')
  const controlled = value !== undefined
  const initialValue = Array.isArray(defaultValue) ? String(defaultValue[0] ?? '') : String(defaultValue ?? '')
  const [internalValue, setInternalValue] = useState(initialValue)
  const [open, setOpen] = useState(false)
  const [menuStyle, setMenuStyle] = useState<CSSProperties>({ position: 'fixed', visibility: 'hidden' })
  const rootRef = useRef<HTMLDivElement>(null)
  const triggerRef = useRef<HTMLButtonElement>(null)
  const menuRef = useRef<HTMLDivElement>(null)
  const nativeRef = useRef<HTMLSelectElement>(null)
  const generatedId = useId()
  const selectedValue = controlled ? (Array.isArray(value) ? String(value[0] ?? '') : String(value ?? '')) : internalValue
  const selectedOption = options.find((option) => String(option.props.value ?? '') === selectedValue)
  const listboxId = `${id ?? generatedId}-options`

  useLayoutEffect(() => {
    if (!open) return
    function position() {
      const rect = triggerRef.current?.getBoundingClientRect()
      if (!rect) return
      const margin = 8
      const gap = 4
      const width = Math.min(rect.width, Math.max(0, window.innerWidth - margin * 2))
      const above = Math.max(0, rect.top - margin - gap)
      const below = Math.max(0, window.innerHeight - rect.bottom - margin - gap)
      const wanted = Math.min(menuRef.current?.scrollHeight || 240, 240)
      const top = placement === 'top' ? above >= wanted || above >= below : below < wanted && above > below
      setMenuStyle({
        position: 'fixed',
        width,
        left: Math.max(margin, Math.min(rect.left, window.innerWidth - width - margin)),
        maxHeight: Math.min(240, top ? above : below),
        ...(top ? { bottom: window.innerHeight - rect.top + gap } : { top: rect.bottom + gap }),
      })
    }
    position()
    const selected = menuRef.current?.querySelector<HTMLButtonElement>('[aria-selected="true"]:not(:disabled)')
    const first = menuRef.current?.querySelector<HTMLButtonElement>('[role="option"]:not(:disabled)')
    const focusTarget = selected ?? first
    focusTarget?.focus({ preventScroll: true })
    window.addEventListener('resize', position)
    document.addEventListener('scroll', position, true)
    return () => { window.removeEventListener('resize', position); document.removeEventListener('scroll', position, true) }
  }, [open, placement, options.length])

  useEffect(() => {
    if (!open) return
    function close(event: MouseEvent) {
      if (!rootRef.current?.contains(event.target as Node) && !menuRef.current?.contains(event.target as Node)) setOpen(false)
    }
    function closeOnEscape(event: KeyboardEvent) {
      if (event.key === 'Escape') {
        event.preventDefault()
        event.stopImmediatePropagation()
        setOpen(false)
        triggerRef.current?.focus()
      }
    }
    document.addEventListener('mousedown', close)
    document.addEventListener('keydown', closeOnEscape, true)
    return () => {
      document.removeEventListener('mousedown', close)
      document.removeEventListener('keydown', closeOnEscape, true)
    }
  }, [open])

  function navigate(event: ReactKeyboardEvent<HTMLDivElement>) {
    if (event.key === 'Tab') { setOpen(false); triggerRef.current?.focus(); return }
    if (!['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) return
    event.preventDefault()
    const buttons = Array.from(menuRef.current?.querySelectorAll<HTMLButtonElement>('[role="option"]:not(:disabled)') ?? [])
    const current = buttons.indexOf(document.activeElement as HTMLButtonElement)
    const next = event.key === 'Home' ? 0 : event.key === 'End' ? buttons.length - 1 : (current + (event.key === 'ArrowUp' ? -1 : 1) + buttons.length) % buttons.length
    buttons[next]?.focus()
  }

  function selectOption(nextValue: string, optionDisabled?: boolean) {
    if (optionDisabled) return
    if (!controlled) setInternalValue(nextValue)
    setOpen(false)
    triggerRef.current?.focus()
    if (nativeRef.current) nativeRef.current.value = nextValue
    onChange?.({ target: nativeRef.current, currentTarget: nativeRef.current } as unknown as ChangeEvent<HTMLSelectElement>)
  }

  return <div ref={rootRef} className={cn('relative block', className)}>
    <select ref={nativeRef} id={id} {...selectProps} value={selectedValue} onChange={onChange} disabled={disabled} tabIndex={-1} aria-hidden="true" className="sr-only">{children}</select>
    <button ref={triggerRef} type="button" disabled={disabled} aria-label={selectProps['aria-label']} aria-labelledby={selectProps['aria-labelledby']} aria-haspopup="listbox" aria-expanded={open} aria-controls={open ? listboxId : undefined} className={cn('flex w-full items-center justify-between gap-3 rounded-md text-left text-xs text-foreground outline-none focus-visible:ring-1 focus-visible:ring-copper disabled:opacity-50', variant === 'inline' ? 'h-8 border border-transparent bg-transparent px-2 hover:bg-accent' : 'h-10 border border-input bg-muted px-3 focus-visible:border-copper')} onClick={() => setOpen((current) => !current)} onKeyDown={(event) => { if (event.key === 'ArrowDown' || event.key === 'ArrowUp') { event.preventDefault(); setOpen(true) } }}>
      <span className="min-w-0 truncate">{selectedOption?.props.children ?? 'Выберите значение'}</span>
      <ChevronDown className={cn('size-4 shrink-0 text-muted-foreground transition-transform', open && 'rotate-180')} aria-hidden="true" />
    </button>
    {open && createPortal(<div ref={menuRef} id={listboxId} role="listbox" style={menuStyle} onKeyDown={navigate} className="z-[100] overflow-auto rounded-md border border-border bg-card p-1 shadow-lg">
      {options.map((option, index) => {
        const optionValue = String(option.props.value ?? '')
        const selected = optionValue === selectedValue
        return <button key={`${optionValue}-${index}`} type="button" role="option" aria-selected={selected} disabled={option.props.disabled} className={cn('flex min-h-9 w-full items-center gap-2 rounded-sm px-3 text-left text-xs text-foreground hover:bg-accent disabled:cursor-not-allowed disabled:opacity-50', selected && 'bg-accent')} onClick={() => selectOption(optionValue, option.props.disabled)}><Check className={cn('size-3.5 shrink-0 text-[#78b488]', !selected && 'invisible')} aria-hidden="true" /><span className="min-w-0 flex-1">{option.props.children}</span></button>
      })}
    </div>, rootRef.current?.closest('[role="dialog"]') ?? document.body)}
  </div>
}
