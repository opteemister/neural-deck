// spec: version-zero
import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { StatusBadge } from '@/components/status-badge'

describe('StatusBadge', () => {
  it('uses text together with an icon for a state', () => {
    render(<StatusBadge state="unavailable" />)
    expect(screen.getByText('Недоступно')).toBeInTheDocument()
    expect(screen.getByText('Недоступно').querySelector('svg')).toBeTruthy()
  })
})
