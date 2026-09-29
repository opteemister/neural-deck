import type { HTMLAttributes, ThHTMLAttributes } from 'react'
import { cn } from '@/lib/utils'

export function Table({ className, ...props }: HTMLAttributes<HTMLTableElement>) { return <div className="w-full overflow-x-auto"><table className={cn('w-full caption-bottom text-xs', className)} {...props} /></div> }
export function TableHeader({ className, ...props }: HTMLAttributes<HTMLTableSectionElement>) { return <thead className={cn('[&_tr]:border-b [&_tr]:border-border', className)} {...props} /> }
export function TableBody({ className, ...props }: HTMLAttributes<HTMLTableSectionElement>) { return <tbody className={cn('[&_tr:last-child]:border-0', className)} {...props} /> }
export function TableRow({ className, ...props }: HTMLAttributes<HTMLTableRowElement>) { return <tr className={cn('border-b border-border transition-colors hover:bg-accent/50', className)} {...props} /> }
export function TableHead({ className, ...props }: ThHTMLAttributes<HTMLTableCellElement>) { return <th className={cn('h-10 px-3 text-left align-middle text-[11px] font-normal text-muted-foreground', className)} {...props} /> }
export function TableCell({ className, ...props }: HTMLAttributes<HTMLTableCellElement>) { return <td className={cn('px-3 py-3 align-middle', className)} {...props} /> }
