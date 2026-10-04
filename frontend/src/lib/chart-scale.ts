export function concurrencyScaleMaximum(value: number): number {
  const magnitude = 10 ** Math.floor(Math.log10(Math.max(1, value)))
  const target = value * 1.2
  return [1, 1.5, 2, 2.5, 3, 4, 5, 6, 8, 10, 15]
    .map((step) => step * magnitude)
    .filter((limit) => limit > value)
    .reduce((best, limit) => Math.abs(limit - target) < Math.abs(best - target) ? limit : best)
}

/** Round a chart's upper bound to a readable step while keeping the data below it. */
export function chartScaleMaximum(value: number): number {
  if (!Number.isFinite(value) || value <= 0) return 1
  const step = value < 1 ? 1 : value < 100 ? 5 : value < 1_000 ? 50 : 10 ** (Math.floor(Math.log10(value)) - 1)
  const rounded = Math.ceil(value / step) * step
  return rounded > value ? rounded : rounded + step
}
