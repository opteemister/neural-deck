import { expect, it } from 'vitest'
import { chartScaleMaximum } from './chart-scale'

it('rounds chart maxima with larger steps as values grow', () => {
  expect(chartScaleMaximum(3)).toBe(5)
  expect(chartScaleMaximum(13)).toBe(15)
  expect(chartScaleMaximum(180)).toBe(200)
  expect(chartScaleMaximum(1267)).toBe(1300)
  expect(chartScaleMaximum(1_267_500)).toBe(1_300_000)
})

it('keeps an empty or zero-valued chart on a usable baseline', () => {
  expect(chartScaleMaximum(0)).toBe(1)
  expect(chartScaleMaximum(Number.NaN)).toBe(1)
})
