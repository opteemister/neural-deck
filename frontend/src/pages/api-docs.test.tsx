import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ApiDocsPage } from '@/pages/api-docs'

const schema = {
  info: { title: 'Neural Deck OpenAI API', version: '0.1.0' },
  paths: {
    '/v1/images/edits': {
      post: {
        tags: ['media'], summary: 'Edit image', requestBody: { required: true, content: {
          'multipart/form-data': { schema: {
            type: 'object', required: ['model', 'prompt', 'image'], properties: {
              model: { type: 'string' }, prompt: { type: 'string' },
              image: { type: 'string', format: 'binary' }, mask: { type: 'string', format: 'binary' },
            },
          } },
        } }, responses: { '200': { description: 'ok' } },
      },
    },
    '/v2/embed': {
      post: {
        tags: ['cohere'], summary: 'Embed', requestBody: { content: {
          'application/json': { schema: { $ref: '#/components/schemas/CohereEmbedRequest' } },
        } }, responses: { '200': { description: 'ok' } },
      },
    },
  },
  components: { schemas: {
    CohereEmbedRequest: { type: 'object', required: ['model', 'texts'], properties: {
      model: { type: 'string' }, texts: { type: 'array', items: { type: 'string' } }, input_type: { type: 'string' },
    } },
  } },
}

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={client}><ApiDocsPage /></QueryClientProvider>)
}

afterEach(() => vi.unstubAllGlobals())

describe('API docs provider schema', () => {
  it('renders multipart fields and binary file example from OpenAPI', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify(schema), { status: 200 })))
    renderPage()

    const summary = await screen.findByText('/v1/images/edits')
    fireEvent.click(summary)
    expect(screen.getByText('image', { selector: 'td' })).toBeInTheDocument()
    expect(screen.getByText('mask', { selector: 'td' })).toBeInTheDocument()
    expect(screen.getByText("-F 'image=@file'", { exact: false })).toBeInTheDocument()
  })

  it('resolves referenced schemas and searches provider routes', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify(schema), { status: 200 })))
    renderPage()

    fireEvent.change(await screen.findByLabelText('Поиск API'), { target: { value: '/v2/embed' } })
    expect(await screen.findByText('/v2/embed')).toBeInTheDocument()
    expect(screen.queryByText('/v1/images/edits')).not.toBeInTheDocument()
    fireEvent.click(screen.getByText('/v2/embed'))
    expect(screen.getByText('model', { selector: 'td' })).toBeInTheDocument()
    expect(screen.getByText('texts', { selector: 'td' })).toBeInTheDocument()
  })
})
