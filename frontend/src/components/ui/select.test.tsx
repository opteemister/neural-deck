import { useState } from 'react'
import { fireEvent, render, screen, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { Dialog } from '@/components/ui/dialog'
import { Select } from '@/components/ui/select'

afterEach(() => vi.restoreAllMocks())

function Example() {
  const [open, setOpen] = useState(true)
  const [value, setValue] = useState('one')
  return <Dialog open={open} title="Настройки" onOpenChange={setOpen}><Select aria-label="Workflow" value={value} onChange={(event) => setValue(event.target.value)}><option value="one">Первый</option><option value="disabled" disabled>Недоступный</option><option value="two">Второй</option></Select></Dialog>
}

describe('Select overlay', () => {
  it('renders outside the scrolling dialog and still selects an option without closing the dialog', () => {
    render(<Example />)
    const dialog = screen.getByRole('dialog')
    const trigger = within(dialog).getByRole('button', { name: 'Workflow' })
    fireEvent.click(trigger)
    const menu = screen.getByRole('listbox')
    expect(dialog.contains(menu)).toBe(true)
    expect(within(dialog).getByRole('button', { name: 'Workflow' }).parentElement?.contains(menu)).toBe(false)
    expect(dialog.children[1].contains(menu)).toBe(false)
    const option = within(menu).getByRole('option', { name: 'Второй' })
    fireEvent.mouseDown(option)
    fireEvent.click(option)
    expect(trigger).toHaveTextContent('Второй')
    expect(screen.getByRole('dialog')).toBe(dialog)
    expect(screen.queryByRole('listbox')).not.toBeInTheDocument()
    expect(trigger).toHaveFocus()
  })

  it('supports keyboard navigation and lets Escape close the menu before the dialog', () => {
    render(<Example />)
    const trigger = screen.getByRole('button', { name: 'Workflow' })
    fireEvent.keyDown(trigger, { key: 'ArrowDown' })
    expect(screen.getByRole('option', { name: 'Первый' })).toHaveFocus()
    fireEvent.keyDown(document.activeElement!, { key: 'ArrowDown' })
    expect(screen.getByRole('option', { name: 'Второй' })).toHaveFocus()
    fireEvent.keyDown(document.activeElement!, { key: 'Escape' })
    expect(screen.queryByRole('listbox')).not.toBeInTheDocument()
    expect(screen.getByRole('dialog')).toBeInTheDocument()
    expect(trigger).toHaveFocus()
    fireEvent.keyDown(trigger, { key: 'Escape' })
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })

  it('opens above a trigger near the viewport edge and follows it when its container scrolls', () => {
    render(<Example />)
    const trigger = screen.getByRole('button', { name: 'Workflow' })
    const height = window.innerHeight
    const rect = vi.spyOn(trigger, 'getBoundingClientRect').mockReturnValue({ left: 40, top: height - 50, bottom: height - 10, width: 300 } as DOMRect)
    fireEvent.click(trigger)
    const menu = screen.getByRole('listbox')
    expect(menu.style.position).toBe('fixed')
    expect(Number.parseFloat(menu.style.bottom)).toBeGreaterThan(50)
    expect(menu.style.top).toBe('')
    rect.mockReturnValue({ left: 40, top: 80, bottom: 120, width: 300 } as DOMRect)
    fireEvent.scroll(screen.getByRole('dialog').firstElementChild!)
    expect(Number.parseFloat(menu.style.top)).toBeGreaterThanOrEqual(120)
    expect(menu.style.bottom).toBe('')
    fireEvent.mouseDown(document.body)
    expect(screen.queryByRole('listbox')).not.toBeInTheDocument()
  })
})
