import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { ProfileGpuPicker } from './profile-gpu-picker'
import type { GpuData } from '@/types/api'

const gpus: GpuData[] = [0, 1].map((index) => ({ uuid: `GPU-${index}`, index, name: `Card ${index}`, memory_used_bytes: 0, memory_total_bytes: 100, usage_percent: 0, temperature_celsius: 30 }))
afterEach(cleanup)

it('adds PCI bus ordering when selecting a second GPU and preserves other variables', () => {
  const onChange = vi.fn()
  render(<ProfileGpuPicker gpus={gpus} environment={'HF_HOME=/models/cache\nCUDA_VISIBLE_DEVICES=GPU-0'} onChange={onChange} />)
  fireEvent.click(screen.getAllByRole('checkbox', { hidden: true })[1])
  expect(onChange).toHaveBeenCalledWith('HF_HOME=/models/cache\nCUDA_VISIBLE_DEVICES=GPU-0,GPU-1\nCUDA_DEVICE_ORDER=PCI_BUS_ID')
})

it('normalizes ordering without duplicates when selecting all GPUs', () => {
  const onChange = vi.fn()
  render(<ProfileGpuPicker gpus={gpus} environment={'CUDA_VISIBLE_DEVICES=GPU-0\nCUDA_DEVICE_ORDER=FASTEST_FIRST\nCUDA_DEVICE_ORDER=PCI_BUS_ID'} onChange={onChange} />)
  fireEvent.click(screen.getByRole('button', { name: 'Все GPU', hidden: true }))
  expect(onChange).toHaveBeenCalledWith('CUDA_DEVICE_ORDER=PCI_BUS_ID')
})

it('does not add ordering for a single GPU or remove an existing manual value', () => {
  const onChange = vi.fn()
  const view = render(<ProfileGpuPicker gpus={gpus} environment="CUDA_VISIBLE_DEVICES=-1" onChange={onChange} />)
  fireEvent.click(screen.getAllByRole('checkbox', { hidden: true })[0])
  expect(onChange).toHaveBeenLastCalledWith('CUDA_VISIBLE_DEVICES=GPU-0')
  view.rerender(<ProfileGpuPicker gpus={gpus} environment={'CUDA_VISIBLE_DEVICES=GPU-0,GPU-1\nCUDA_DEVICE_ORDER=PCI_BUS_ID'} onChange={onChange} />)
  fireEvent.click(screen.getAllByRole('checkbox', { hidden: true })[1])
  expect(onChange).toHaveBeenLastCalledWith('CUDA_DEVICE_ORDER=PCI_BUS_ID\nCUDA_VISIBLE_DEVICES=GPU-0')
})
