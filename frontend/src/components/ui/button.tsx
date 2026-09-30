import type { ButtonHTMLAttributes } from 'react'
import { cn } from '@/lib/utils'

type ButtonVariant = 'primary' | 'secondary' | 'ghost' | 'danger'

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant
  size?: 'sm' | 'md' | 'icon' | 'icon-sm'
  iconOnly?: boolean
  shape?: 'default' | 'pill'
}

export function Button({ className, variant = 'secondary', size = 'md', iconOnly = false, shape = 'default', type = 'button', ...props }: ButtonProps) {
  return (
    <button
      type={type}
      className={cn(
        'inline-flex items-center justify-center border text-xs font-medium transition-colors [&>svg]:shrink-0',
        shape === 'pill' ? 'rounded-full' : 'rounded-md',
        !iconOnly && 'min-h-10 gap-2 px-3',
        'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-copper focus-visible:ring-offset-2 focus-visible:ring-offset-background',
        'disabled:pointer-events-none disabled:opacity-45',
        variant === 'primary' && 'border-copper bg-primary text-primary-foreground hover:bg-[#b86d3d]',
        variant === 'secondary' && 'border-border bg-secondary text-secondary-foreground hover:border-[#6c625a] hover:bg-accent',
        variant === 'ghost' && 'border-transparent bg-transparent text-muted-foreground hover:bg-accent hover:text-foreground',
        variant === 'danger' && 'border-[#81554c] bg-transparent text-[#d09b87] hover:bg-[#382725]',
        !iconOnly && size === 'sm' && 'min-h-8 px-2.5',
        !iconOnly && size === 'icon' && 'h-10 w-10 px-0',
        !iconOnly && size === 'icon-sm' && 'h-7 min-h-0 w-7 gap-0 px-0',
        iconOnly && 'shrink-0 gap-0 p-0',
        iconOnly && (size === 'sm' || size === 'icon-sm' ? 'size-8' : 'size-10'),
        className,
      )}
      {...props}
    />
  )
}
