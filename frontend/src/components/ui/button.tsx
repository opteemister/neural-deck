import type { ButtonHTMLAttributes } from 'react'
import { cn } from '@/lib/utils'

type ButtonVariant = 'primary' | 'secondary' | 'ghost' | 'danger'

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant
  size?: 'sm' | 'md' | 'icon'
}

export function Button({ className, variant = 'secondary', size = 'md', type = 'button', ...props }: ButtonProps) {
  return (
    <button
      type={type}
      className={cn(
        'inline-flex min-h-10 items-center justify-center gap-2 rounded-md border px-3 text-xs font-medium transition-colors',
        'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-copper focus-visible:ring-offset-2 focus-visible:ring-offset-background',
        'disabled:pointer-events-none disabled:opacity-45',
        variant === 'primary' && 'border-copper bg-primary text-primary-foreground hover:bg-[#b86d3d]',
        variant === 'secondary' && 'border-border bg-secondary text-secondary-foreground hover:border-[#6c625a] hover:bg-accent',
        variant === 'ghost' && 'border-transparent bg-transparent text-muted-foreground hover:bg-accent hover:text-foreground',
        variant === 'danger' && 'border-[#81554c] bg-transparent text-[#d09b87] hover:bg-[#382725]',
        size === 'sm' && 'min-h-8 px-2.5',
        size === 'icon' && 'h-10 w-10 px-0',
        className,
      )}
      {...props}
    />
  )
}
