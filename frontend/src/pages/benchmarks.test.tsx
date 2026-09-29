// spec: version-zero
import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it } from 'vitest'
import { BenchmarksPage } from '@/pages/benchmarks'

describe('BenchmarksPage', () => {
  it('keeps the Version Zero placeholder available from navigation', () => {
    render(<MemoryRouter initialEntries={['/benchmarks']}><BenchmarksPage /></MemoryRouter>)
    expect(screen.getByRole('heading', { name: 'Benchmarks' })).toBeInTheDocument()
    expect(screen.getByText('Бенчмарки появятся позже')).toBeInTheDocument()
  })
})
