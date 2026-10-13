// spec: version-zero
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { LogsDrawerProvider } from '@/components/logs-drawer'
import { EnginesPage } from '@/pages/engines'

const engine = { id: 'vllm', display_name: 'vLLM', command_template: '{{engine}} serve', installation_kinds: ['python_venv'], installation_root: '/opt/engines/vllm', installation_template: 'python -m venv {{target}}', installations: [], types: ['text'], api: null, ui_url: null }
const definition = { version: 1, id: 'vllm', name: 'vLLM', types: ['text'], install_script: 'python -m venv {{target}}', update_script: null, executable: '{{target}}/bin/vllm', working_directory: null, environment: {}, command: '{{engine}} serve', healthcheck: null, api: null, ui_url: null, version_command: ['--version'] }
const envelope = (data: unknown) => JSON.stringify({ data, request_id: 'req_test' })

function renderPage(initialEntry = '/engines') {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={client}><MemoryRouter initialEntries={[initialEntry]}><LogsDrawerProvider><EnginesPage /></LogsDrawerProvider></MemoryRouter></QueryClientProvider>)
}

afterEach(() => vi.unstubAllGlobals())

describe('Engines workflow', () => {
  it('opens template creation and editing in a reusable dialog', async () => {
    vi.stubGlobal('fetch', vi.fn().mockImplementation((input: RequestInfo | URL) => {
      const url = String(input)
      if (url.endsWith('/api/v1/engines/definitions')) return Promise.resolve(new Response(envelope({ items: [definition], issues: [] }), { status: 200 }))
      return Promise.resolve(new Response(envelope({ items: [engine] }), { status: 200 }))
    }))

    renderPage('/engines?view=templates')

    fireEvent.click(await screen.findByRole('button', { name: 'Изменить' }))
    const editDialog = screen.getByRole('dialog', { name: 'Изменить шаблон vLLM' })
    expect(within(editDialog).getByLabelText(/Название/)).toHaveValue('vLLM')
    expect(within(editDialog).getByLabelText(/Рабочий каталог/)).not.toBeRequired()
    expect(within(editDialog).getAllByText('· необязательно').length).toBeGreaterThan(0)
    fireEvent.click(within(editDialog).getByRole('button', { name: 'Закрыть' }))
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'Новый шаблон' }))
    expect(screen.getByRole('dialog', { name: 'Новый шаблон движка' })).toBeInTheDocument()
  })

  it('groups engines by their primary type and shows ready installations first', async () => {
    const readyInstallation = { id: 'inst_llama', engine_id: 'llama.cpp', kind: 'binary', root_path: '/opt/llama', executable_path: '/opt/llama/llama-server', version: '1.0', status: 'ready', verified: true, diagnostic: null, registered: false }
    const items = [
      { ...engine, id: 'comfyui', display_name: 'ComfyUI', types: ['image', 'video'], installations: [] },
      { ...engine, id: 'vllm', display_name: 'vLLM', types: ['text'], installations: [] },
      { ...engine, id: 'llama.cpp', display_name: 'llama.cpp', types: ['text'], installations: [readyInstallation] },
    ]
    vi.stubGlobal('fetch', vi.fn().mockImplementation((input: RequestInfo | URL) => {
      const url = String(input)
      if (url.endsWith('/api/v1/engines/definitions')) return Promise.resolve(new Response(envelope({ items: [], issues: [] }), { status: 200 }))
      return Promise.resolve(new Response(envelope({ items }), { status: 200 }))
    }))

    renderPage()

    const textHeading = await screen.findByRole('heading', { name: 'Текст' })
    const imageHeading = screen.getByRole('heading', { name: 'Изображения' })
    expect(textHeading.compareDocumentPosition(imageHeading) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    const textContent = textHeading.closest('section')?.textContent ?? ''
    expect(textContent.indexOf('llama.cpp')).toBeLessThan(textContent.indexOf('vLLM'))
    expect(imageHeading.closest('section')).toHaveTextContent('ComfyUI')
  })

  it('scans the engine catalogue and reports a created operation', async () => {
    const fetchMock = vi.fn().mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      if (url.endsWith('/api/v1/logs')) return Promise.resolve(new Response(envelope({ items: [] }), { status: 200 }))
      if (url.endsWith('/api/v1/engines/definitions')) return Promise.resolve(new Response(envelope({ items: [definition], issues: [] }), { status: 200 }))
      if (url.endsWith('/api/v1/engines/scan')) return Promise.resolve(new Response(envelope({ items: [engine] }), { status: 200 }))
      if (url.endsWith('/api/v1/engines/vllm/installations') && init?.method === 'POST') return Promise.resolve(new Response(envelope({ id: 'op_install', kind: 'install', state: 'queued', target_id: 'vllm', created_at: '2026-09-29T00:00:00Z', started_at: null, finished_at: null, stdout: '', stderr: '', exit_code: null, error: null }), { status: 202 }))
      if (url.includes('/api/v1/operations/op_install')) return Promise.resolve(new Response(envelope({ id: 'op_install', kind: 'install', state: 'succeeded', target_id: 'vllm', created_at: '2026-09-29T00:00:00Z', started_at: null, finished_at: '2026-09-29T00:00:01Z', stdout: 'done', stderr: '', exit_code: 0, error: null }), { status: 200 }))
      return Promise.resolve(new Response(envelope({ items: [engine] }), { status: 200 }))
    })
    vi.stubGlobal('fetch', fetchMock)
    renderPage()
    await screen.findByRole('heading', { name: 'vLLM' })
    fireEvent.click(screen.getByRole('button', { name: 'Повторить поиск' }))
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith('/api/v1/engines/scan', expect.objectContaining({ method: 'POST' })))
    fireEvent.click(screen.getByRole('button', { name: 'Установить' }))
    const installationTarget = screen.getByLabelText('Имя установки или абсолютный путь')
    expect(installationTarget).toHaveAttribute('placeholder', 'vllm-latest')
    fireEvent.change(installationTarget, { target: { value: 'vllm-test' } })
    fireEvent.click(screen.getAllByRole('button', { name: 'Установить' }).at(-1)!)
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith('/api/v1/engines/vllm/installations', expect.objectContaining({ method: 'POST' })))
    const notification = await screen.findByRole('status')
    expect(notification).toHaveTextContent('Установка движка')
    expect(notification).toHaveTextContent('Операция успешно завершена.')
    expect(screen.queryByText('Завершено')).not.toBeInTheDocument()
    const installationRequest = fetchMock.mock.calls.find(([input, init]) => String(input).endsWith('/api/v1/engines/vllm/installations') && init?.method === 'POST')!
    expect(JSON.parse(String(installationRequest[1].body))).toEqual({ target_path: '/opt/engines/vllm/vllm-test', run_install: true, command: 'python -m venv {{target}}' })
  })
})
