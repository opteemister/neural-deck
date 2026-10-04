import { fireEvent, render, screen } from '@testing-library/react'
import { expect, it } from 'vitest'
import { GpuSparkline } from './gpu-sparkline'

it('renders a compact line without chart labels or axes and shows the hovered point', () => {
  render(<GpuSparkline label="Загрузка GPU" values={[10, null, 30, 20]} formatValue={(value) => `${value}%`} />)
  const chart = screen.getByRole('img', { name: 'Загрузка GPU' })
  expect(chart).toHaveAttribute('data-sparkline', 'Загрузка GPU')
  expect(chart.querySelector('path')).toHaveAttribute('d', 'M1.5,13.4 L34.0,2.6 L66.5,8.0')
  expect(chart.querySelectorAll('text')).toHaveLength(0)
  expect(chart.querySelectorAll('line')).toHaveLength(0)
  fireEvent.pointerEnter(chart.querySelectorAll('circle')[1])
  expect(screen.getByRole('tooltip')).toHaveTextContent('30%')
  fireEvent.pointerLeave(chart.parentElement!)
  expect(screen.queryByRole('tooltip')).not.toBeInTheDocument()
})
