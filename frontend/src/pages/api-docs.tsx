import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Copy, Download } from 'lucide-react'
import { PageHeader } from '@/components/page-header'
import { QueryError, QueryLoading } from '@/components/query-state'
import { Button, ButtonLink } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { NotificationToast } from '@/components/ui/notification-toast'
import { Select } from '@/components/ui/select'

interface Schema {
  $ref?: string
  type?: string
  format?: string
  description?: string
  properties?: Record<string, Schema>
  required?: string[]
  items?: Schema
  anyOf?: Schema[]
  oneOf?: Schema[]
  allOf?: Schema[]
  enum?: unknown[]
  example?: unknown
  examples?: unknown[]
  default?: unknown
}
interface Media { schema?: Schema; example?: unknown; examples?: Record<string, { value?: unknown }> }
interface Operation {
  summary?: string
  description?: string
  tags?: string[]
  parameters?: Array<{ name: string; in: string; required?: boolean; description?: string; schema?: Schema }>
  requestBody?: { required?: boolean; content?: Record<string, Media> }
  responses?: Record<string, { description?: string; content?: Record<string, Media> }>
}
interface ProviderSchema {
  info: { title: string; version: string }
  paths: Record<string, Record<string, Operation>>
  components?: { schemas?: Record<string, Schema> }
}
const schemaUrl = '/api/v1/proxy/openapi.json'
const methods = new Set(['get', 'post', 'put', 'patch', 'delete'])

function resolve(schema: Schema | undefined, doc: ProviderSchema): Schema {
  if (!schema) return {}
  if (schema.$ref?.startsWith('#/components/schemas/')) {
    return doc.components?.schemas?.[schema.$ref.split('/').at(-1)!] ?? schema
  }
  return schema
}

function typeName(input: Schema | undefined, doc: ProviderSchema): string {
  const schema = resolve(input, doc)
  const variants = schema.anyOf ?? schema.oneOf
  if (variants) return variants.map((item) => resolve(item, doc).type ?? item.$ref?.split('/').at(-1) ?? 'object').join(' | ')
  if (schema.type === 'array') return `${resolve(schema.items, doc).type ?? 'object'}[]`
  return schema.format ? `${schema.type ?? 'string'} (${schema.format})` : schema.type ?? (schema.properties ? 'object' : '—')
}

function sample(input: Schema | undefined, doc: ProviderSchema, depth = 0): unknown {
  if (depth > 5) return null
  const schema = resolve(input, doc)
  if (schema.example !== undefined) return schema.example
  if (schema.examples?.length) return schema.examples[0]
  if (schema.default !== undefined) return schema.default
  if (schema.enum?.length) return schema.enum[0]
  const variant = (schema.anyOf ?? schema.oneOf)?.find((item) => item.type !== 'null')
  if (variant) return sample(variant, doc, depth + 1)
  if (schema.type === 'array') return [sample(schema.items, doc, depth + 1)]
  if (schema.properties) return Object.fromEntries(Object.entries(schema.properties)
    .filter(([name, field]) => schema.required?.includes(name) || field.example !== undefined || field.examples?.length || field.default !== null && field.default !== undefined)
    .map(([name, field]) => [name, field.format === 'binary' ? '<file>' : sample(field, doc, depth + 1)]))
  if (schema.type === 'integer' || schema.type === 'number') return 1
  if (schema.type === 'boolean') return false
  return schema.type === 'object' ? {} : 'string'
}

function mediaSample(media: Media, doc: ProviderSchema): string {
  const explicit = media.example ?? Object.values(media.examples ?? {}).find((value) => value.value !== undefined)?.value
  return JSON.stringify(explicit ?? sample(media.schema, doc), null, 2)
}

function SchemaFields({ schema, doc }: { schema?: Schema; doc: ProviderSchema }) {
  const value = resolve(schema, doc)
  if (!value.properties) return <p className="text-xs text-muted-foreground">{typeName(schema, doc)}{value.description ? ` · ${value.description}` : ''}</p>
  return <div className="overflow-x-auto"><table className="w-full text-left text-xs"><thead className="text-muted-foreground"><tr className="border-b border-border"><th className="py-2 pr-4 font-normal">Поле</th><th className="py-2 pr-4 font-normal">Тип</th><th className="py-2 font-normal">Описание</th></tr></thead><tbody>{Object.entries(value.properties).map(([name, field]) => <tr key={name} className="border-b border-border/50"><td className="py-2 pr-4 align-top font-mono">{name}{value.required?.includes(name) && <span className="ml-2 font-sans text-copper">обязательно</span>}</td><td className="py-2 pr-4 align-top text-muted-foreground">{typeName(field, doc)}</td><td className="py-2 align-top text-muted-foreground">{resolve(field, doc).description}{field.enum && <span className="block font-mono">{field.enum.map(String).join(' | ')}</span>}</td></tr>)}</tbody></table></div>
}

export function ApiDocsPage() {
  const [search, setSearch] = useState('')
  const [group, setGroup] = useState('all')
  const [notice, setNotice] = useState<{ title: string; tone: 'success' | 'error' } | null>(null)
  const query = useQuery({ queryKey: ['provider-openapi'], queryFn: async () => {
    const response = await fetch(schemaUrl)
    if (!response.ok) throw new Error(`OpenAPI: HTTP ${response.status}`)
    return await response.json() as ProviderSchema
  }, staleTime: 60_000 })
  const doc = query.data
  const operations = doc ? Object.entries(doc.paths).flatMap(([path, item]) => Object.entries(item).filter(([method]) => methods.has(method)).map(([method, operation]) => ({ path, method, operation }))) : []
  const groups = [...new Set(operations.flatMap(({ operation }) => operation.tags ?? ['API']))].sort()
  const filtered = operations.filter(({ path, method, operation }) => (group === 'all' || (operation.tags ?? ['API']).includes(group)) && `${method} ${path} ${operation.summary ?? ''} ${operation.description ?? ''}`.toLowerCase().includes(search.toLowerCase()))

  async function copy(value: string) {
    try { await navigator.clipboard.writeText(value); setNotice({ title: 'Пример скопирован', tone: 'success' }) }
    catch { setNotice({ title: 'Не удалось скопировать', tone: 'error' }) }
  }

  return <div>
    <PageHeader title="API" action={<ButtonLink href={schemaUrl} download="neural-deck-openapi.json"><Download className="size-4" />OpenAPI JSON</ButtonLink>} />
    <div className="mb-6 flex flex-wrap gap-3"><Input className="min-w-52 flex-1" aria-label="Поиск API" placeholder="Метод, путь или название" value={search} onChange={(event) => setSearch(event.target.value)} /><Select className="min-w-52" aria-label="Группа API" value={group} onChange={(event) => setGroup(event.target.value)}><option value="all">Все группы</option>{groups.map((tag) => <option key={tag} value={tag}>{tag}</option>)}</Select></div>
    {query.isLoading ? <QueryLoading label="Читаем OpenAPI" /> : query.isError ? <QueryError error={query.error} retry={() => void query.refetch()} label="Схема API недоступна" /> : doc && <div className="space-y-3">{filtered.length === 0 && <p className="text-xs text-muted-foreground">Маршруты не найдены.</p>}{filtered.map(({ path, method, operation }) => <details key={`${method}:${path}`} className="rounded-md border border-border bg-card"><summary className="flex cursor-pointer flex-wrap items-center gap-3 px-4 py-3 text-xs"><span className="w-14 font-mono uppercase text-copper">{method}</span><span className="break-all font-mono">{path}</span><span className="text-muted-foreground">{operation.summary}</span></summary><div className="space-y-5 border-t border-border px-4 py-4">
      {operation.description && <p className="whitespace-pre-wrap text-xs text-muted-foreground">{operation.description}</p>}
      {!!operation.parameters?.length && <section><h3 className="mb-2 text-sm">Параметры</h3>{operation.parameters.map((parameter) => <p key={`${parameter.in}:${parameter.name}`} className="py-1 text-xs"><code>{parameter.name}</code> · {parameter.in} · {typeName(parameter.schema, doc)}{parameter.required && ' · обязательно'}{parameter.description && ` · ${parameter.description}`}</p>)}</section>}
      {Object.entries(operation.requestBody?.content ?? {}).map(([contentType, media]) => {
        const example = mediaSample(media, doc)
        const bodySchema = resolve(media.schema, doc)
        const fields = Object.entries(bodySchema.properties ?? {}).filter(([name, field]) => bodySchema.required?.includes(name) || field.example !== undefined || field.examples?.length)
        const multipart = contentType.includes('multipart')
        const requestExample = multipart ? [`curl -X ${method.toUpperCase()} "$NEURAL_DECK_API${path}"`, ...fields.map(([name, field]) => `  -F '${name}=${field.format === 'binary' ? '@file' : String(sample(field, doc))}'`)].join(' \\\n') : example
        return <section key={contentType}><h3 className="mb-2 text-sm">Запрос <span className="ml-2 font-mono text-xs text-muted-foreground">{contentType}</span></h3><SchemaFields schema={media.schema} doc={doc} /><div className="mt-3 flex items-center justify-between"><span className="text-xs text-muted-foreground">Пример структуры</span><Button size="icon-sm" variant="ghost" aria-label={`Скопировать пример ${method} ${path}`} onClick={() => void copy(requestExample)}><Copy className="size-3.5" /></Button></div><pre className="overflow-x-auto rounded-md bg-muted p-3 text-xs"><code>{requestExample}</code></pre></section>
      })}
      <section><h3 className="mb-2 text-sm">Ответы</h3>{Object.entries(operation.responses ?? {}).map(([status, response]) => <div key={status} className="mb-4 text-xs"><p className="mb-2"><code className="mr-2 text-copper">{status}</code>{response.description}</p>{Object.entries(response.content ?? {}).map(([contentType, media]) => <div key={contentType} className="mb-2"><p className="mb-1 font-mono text-muted-foreground">{contentType}</p>{contentType.includes('json') ? <><SchemaFields schema={media.schema} doc={doc} /><pre className="mt-2 overflow-x-auto rounded-md bg-muted p-3"><code>{mediaSample(media, doc)}</code></pre></> : <p className="text-muted-foreground">{contentType.includes('event-stream') ? 'Поток событий SSE' : 'Содержимое ответа'} · {typeName(media.schema, doc)}</p>}</div>)}</div>)}</section>
    </div></details>)}</div>}
    {notice && <NotificationToast key={`${notice.tone}:${notice.title}`} tone={notice.tone} title={notice.title} />}
  </div>
}
