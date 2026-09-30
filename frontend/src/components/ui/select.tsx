import { Children, isValidElement, useEffect, useRef, useState, type ChangeEvent, type ReactElement, type ReactNode, type SelectHTMLAttributes } from 'react'
import { ChevronDown } from 'lucide-react'
import { cn } from '@/lib/utils'

type OptionProps = { value?: string; disabled?: boolean; children?: ReactNode }

export function Select({ className, placement = 'bottom', variant = 'default', ...props }: SelectHTMLAttributes<HTMLSelectElement> & { placement?: 'top' | 'bottom'; variant?: 'default' | 'inline' }) {
  const { children, value, defaultValue, onChange, disabled, id, ...selectProps } = props
  const options = Children.toArray(children).filter((child): child is ReactElement<OptionProps> => isValidElement(child) && child.type === 'option')
  const controlled = value !== undefined
  const initialValue = Array.isArray(defaultValue) ? String(defaultValue[0] ?? '') : String(defaultValue ?? '')
  const [internalValue, setInternalValue] = useState(initialValue)
  const [open, setOpen] = useState(false)
  const rootRef = useRef<HTMLDivElement>(null)
  const nativeRef = useRef<HTMLSelectElement>(null)
  const selectedValue = controlled ? (Array.isArray(value) ? String(value[0] ?? '') : String(value ?? '')) : internalValue
  const selectedOption = options.find((option) => String(option.props.value ?? '') === selectedValue)
  const listboxId = id ? `${id}-options` : undefined

  useEffect(() => {
    function close(event: MouseEvent) {
      if (!rootRef.current?.contains(event.target as Node)) setOpen(false)
    }
    function closeOnEscape(event: KeyboardEvent) {
      if (event.key === 'Escape') setOpen(false)
    }
    document.addEventListener('mousedown', close)
    document.addEventListener('keydown', closeOnEscape)
    return () => {
      document.removeEventListener('mousedown', close)
      document.removeEventListener('keydown', closeOnEscape)
    }
  }, [])

  function selectOption(nextValue: string, optionDisabled?: boolean) {
    if (optionDisabled) return
    if (!controlled) setInternalValue(nextValue)
    setOpen(false)
    if (nativeRef.current) nativeRef.current.value = nextValue
    onChange?.({ target: nativeRef.current, currentTarget: nativeRef.current } as unknown as ChangeEvent<HTMLSelectElement>)
  }

  return <div ref={rootRef} className={cn('relative block', className)}>
    <select ref={nativeRef} id={id} {...selectProps} value={selectedValue} onChange={onChange} disabled={disabled} tabIndex={-1} aria-hidden="true" className="sr-only">{children}</select>
    <button type="button" disabled={disabled} aria-label={selectProps['aria-label']} aria-labelledby={selectProps['aria-labelledby']} aria-haspopup="listbox" aria-expanded={open} aria-controls={listboxId} className={cn('flex w-full items-center justify-between gap-3 rounded-md text-left text-xs text-foreground outline-none focus-visible:ring-1 focus-visible:ring-copper disabled:opacity-50', variant === 'inline' ? 'h-8 border border-transparent bg-transparent px-2 hover:bg-accent' : 'h-10 border border-input bg-muted px-3 focus-visible:border-copper')} onClick={() => setOpen((current) => !current)}>
      <span className="min-w-0 truncate">{selectedOption?.props.children ?? 'Выберите значение'}</span>
      <ChevronDown className={cn('size-4 shrink-0 text-muted-foreground transition-transform', open && 'rotate-180')} aria-hidden="true" />
    </button>
    {open && <div id={listboxId} role="listbox" className={cn('absolute left-0 right-0 z-50 max-h-60 overflow-auto rounded-md border border-border bg-card p-1 shadow-lg', placement === 'top' ? 'bottom-full mb-1' : 'top-full mt-1')}>
      {options.map((option, index) => {
        const optionValue = String(option.props.value ?? '')
        const selected = optionValue === selectedValue
        return <button key={`${optionValue}-${index}`} type="button" role="option" aria-selected={selected} disabled={option.props.disabled} className={cn('flex min-h-9 w-full items-center rounded-sm px-3 text-left text-xs text-foreground hover:bg-accent disabled:cursor-not-allowed disabled:opacity-50', selected && 'bg-accent')} onClick={() => selectOption(optionValue, option.props.disabled)}>{option.props.children}</button>
      })}
    </div>}
  </div>
}
