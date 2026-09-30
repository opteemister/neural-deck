import type { ReactNode } from 'react'
import { Button, type ButtonProps } from '@/components/ui/button'

export interface IconButtonProps extends Omit<ButtonProps, 'children' | 'size'> {
  label: string
  children: ReactNode
}

export function IconButton({ label, title, children, ...props }: IconButtonProps) {
  return (
    <Button
      {...props}
      size="icon-sm"
      aria-label={label}
      title={title ?? label}
    >
      {children}
    </Button>
  )
}
