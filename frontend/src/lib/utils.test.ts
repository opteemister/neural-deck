// spec: version-zero
import { describe, expect, it } from 'vitest'
import { formatBytes, formatPercent, stateLabel } from '@/lib/utils'

describe('display formatting', () => {
  it('formats bytes without turning missing values into zero', () => {
    expect(formatBytes(1024 * 1024 * 2)).toBe('2.0 МБ')
    expect(formatBytes(null)).toBe('недоступно')
  })

  it('formats percentage and known states', () => {
    expect(formatPercent(14)).toBe('14%')
    expect(formatPercent(undefined)).toBe('недоступно')
    expect(stateLabel('starting')).toBe('Запускается')
  })
})
