import type { AnchorHTMLAttributes, ButtonHTMLAttributes } from 'react'
import { cn } from '@/lib/utils'

type ButtonVariant = 'primary' | 'secondary' | 'ghost' | 'danger'
type ButtonSize = 'sm' | 'md' | 'icon' | 'icon-sm'
type ButtonShape = 'default' | 'pill'

interface ButtonStyleProps {
  variant?: ButtonVariant
  size?: ButtonSize
  iconOnly?: boolean
  shape?: ButtonShape
}

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement>, ButtonStyleProps {}

export interface ButtonLinkProps extends AnchorHTMLAttributes<HTMLAnchorElement>, ButtonStyleProps {}

function buttonClassName({ className, variant = 'secondary', size = 'md', iconOnly = false, shape = 'default' }: ButtonStyleProps & { className?: string }) {
  const sizeClass = size === 'sm'
    ? iconOnly ? 'size-8 gap-0 p-0' : 'h-8 min-h-8 gap-2 px-2.5'
    : size === 'md'
      ? iconOnly ? 'size-10 gap-0 p-0' : 'h-10 min-h-10 gap-2 px-3'
      : size === 'icon-sm'
        ? 'size-8 gap-0 p-0'
        : 'size-10 gap-0 p-0'

  return cn(
        'inline-flex items-center justify-center border text-xs font-medium transition-colors [&>svg]:shrink-0',
        shape === 'pill' ? 'rounded-full' : 'rounded-md',
        sizeClass,
        'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-copper focus-visible:ring-offset-2 focus-visible:ring-offset-background',
        'disabled:pointer-events-none disabled:opacity-45',
        variant === 'primary' && 'border-copper bg-primary text-primary-foreground hover:bg-[#b86d3d]',
        variant === 'secondary' && 'border-border bg-secondary text-secondary-foreground hover:border-[#6c625a] hover:bg-accent',
        variant === 'ghost' && 'border-transparent bg-transparent text-muted-foreground hover:bg-accent hover:text-foreground',
        variant === 'danger' && 'border-[#81554c] bg-transparent text-[#d09b87] hover:bg-[#382725]',
        iconOnly && 'shrink-0',
        className,
      )
}

export function Button({ className, variant = 'secondary', size = 'md', iconOnly = false, shape = 'default', type = 'button', ...props }: ButtonProps) {
  return <button type={type} className={buttonClassName({ className, variant, size, iconOnly, shape })} {...props} />
}

export function ButtonLink({ className, variant = 'secondary', size = 'md', iconOnly = false, shape = 'default', ...props }: ButtonLinkProps) {
  return <a className={buttonClassName({ className, variant, size, iconOnly, shape })} {...props} />
}
