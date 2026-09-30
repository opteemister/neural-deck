import { createElement, type ReactNode, useMemo, useState } from 'react'
import { marked } from 'marked'
import { Check, Copy } from 'lucide-react'
import { Button } from '@/components/ui/button'

function CodeBlock({ content, language }: { content: string; language: string }) {
  const [copied, setCopied] = useState(false)
  return <div className="chat-code my-5 overflow-hidden rounded-md border border-border bg-muted">
    <div className="flex items-center justify-between border-b border-border px-3 text-[11px] text-muted-foreground"><span>{language || 'Код'}</span><Button size="sm" iconOnly variant="ghost" aria-label={copied ? 'Код скопирован' : 'Копировать код'} title={copied ? 'Скопировано' : 'Копировать код'} onClick={() => { void navigator.clipboard.writeText(content).then(() => setCopied(true)).catch(() => setCopied(false)) }}>{copied ? <Check className="size-3.5" /> : <Copy className="size-3.5" />}</Button></div>
    <pre className="overflow-x-auto p-4 text-xs leading-6"><code>{content}</code></pre>
  </div>
}

const allowed = new Set(['p', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'strong', 'b', 'em', 'i', 'del', 's', 'ul', 'ol', 'li', 'blockquote', 'code', 'hr', 'br', 'table', 'thead', 'tbody', 'tr', 'td', 'th'])
function renderNode(node: Node, key: string): ReactNode {
  if (node.nodeType === Node.TEXT_NODE) return node.textContent
  if (node.nodeType !== Node.ELEMENT_NODE) return null
  const element = node as HTMLElement
  const tag = element.tagName.toLowerCase()
  if (['script', 'style', 'iframe', 'object', 'embed', 'img', 'video', 'audio', 'form', 'input'].includes(tag)) return null
  if (tag === 'pre') {
    const code = element.querySelector('code')
    return <CodeBlock key={key} content={element.textContent ?? ''} language={code?.className.match(/language-([\w+-]+)/)?.[1] ?? ''} />
  }
  const children = Array.from(element.childNodes).map((child, index) => renderNode(child, `${key}-${index}`))
  if (tag === 'a') {
    const href = element.getAttribute('href') ?? ''
    return /^(https?:\/\/|mailto:)/i.test(href) ? <a key={key} href={href} target="_blank" rel="noopener noreferrer">{children}</a> : <span key={key}>{children}</span>
  }
  if (!allowed.has(tag)) return <span key={key}>{children}</span>
  if (tag === 'table') return <div key={key} className="my-5 overflow-x-auto"><table>{children}</table></div>
  return createElement(tag, { key, ...(tag === 'ol' ? { start: Number(element.getAttribute('start')) || 1 } : {}) }, ['br', 'hr'].includes(tag) ? undefined : children)
}

export function ChatMarkdown({ source }: { source: string }) {
  const content = useMemo(() => {
    const html = marked.parse(source, { async: false, gfm: true })
    const document = new DOMParser().parseFromString(html, 'text/html')
    return Array.from(document.body.childNodes).map((node, index) => renderNode(node, `md-${index}`))
  }, [source])
  return <div className="chat-markdown min-w-0 break-words text-sm leading-7">{content}</div>
}
