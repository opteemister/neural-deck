import { useEffect, useState } from 'react'
import { Check, Copy, Download } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Dialog } from '@/components/ui/dialog'
import { ChatMarkdown } from '@/components/chat-markdown'
import { downloadAsset, imageAssetSource, type ImageAsset, type MediaResult, type VideoResult } from '@/lib/chat-media'

function DownloadButton({ asset, filename, onError, overlay = false }: { asset: ImageAsset | VideoResult; filename: string; onError?: (error: unknown) => void; overlay?: boolean }) {
  return <Button type="button" size={overlay ? 'icon-sm' : 'sm'} iconOnly={overlay} variant="secondary" aria-label={overlay ? 'Скачать изображение' : undefined} title={overlay ? 'Скачать изображение' : undefined} onClick={() => { void downloadAsset(asset, filename).catch((error) => onError?.(error)) }}><Download className="size-3.5" />{!overlay && 'Скачать'}</Button>
}

function ImageResult({ assets, onError }: { assets: ImageAsset[]; onError?: (error: unknown) => void }) {
  const [opened, setOpened] = useState<string | null>(null)
  const openedAsset = opened ? assets.find((asset) => imageAssetSource(asset) === opened) : undefined
  const openedSource = openedAsset ? imageAssetSource(openedAsset) : undefined
  useEffect(() => {
    if (!opened) return
    const close = (event: KeyboardEvent) => { if (event.key === 'Escape') setOpened(null) }
    document.addEventListener('keydown', close)
    return () => document.removeEventListener('keydown', close)
  }, [opened])
  return <>
    <div className="grid gap-3 sm:grid-cols-2">
      {assets.map((asset, index) => {
        const source = imageAssetSource(asset)
        if (!source) return null
        return <figure key={`${source}-${index}`} className="relative overflow-hidden rounded-md border border-border bg-muted">
          <button type="button" className="block w-full cursor-zoom-in focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-copper" aria-label={`Открыть изображение ${index + 1}`} onClick={() => setOpened(source)}><img src={source} alt={`Результат ${index + 1}`} className="max-h-[28rem] w-full object-contain" /></button>
          <div className="absolute bottom-2 right-2"><DownloadButton asset={asset} filename={`neural-deck-image-${index + 1}.png`} onError={onError} overlay /></div>
        </figure>
      })}
    </div>
    <Dialog open={openedSource !== undefined} title="Изображение" size="wide" onOpenChange={(open) => { if (!open) setOpened(null) }}>
      {openedSource && openedAsset && <div className="bg-black/30 p-4"><div className="flex min-h-80 items-center justify-center"><img src={openedSource} alt="Полный размер" className="max-h-[calc(100dvh-8rem)] max-w-full object-contain" /></div><div className="mt-3 flex justify-end"><DownloadButton asset={openedAsset} filename="neural-deck-image.png" onError={onError} /></div></div>}
    </Dialog>
  </>
}

function VideoResult({ result, onError }: { result: VideoResult; onError?: (error: unknown) => void }) {
  return <div className="overflow-hidden rounded-md border border-border bg-muted">
    <video src={result.url} controls playsInline className="max-h-[32rem] w-full" aria-label="Результат видео" />
    <div className="flex justify-end border-t border-border p-2"><DownloadButton asset={result} filename="neural-deck-video.mp4" onError={onError} /></div>
  </div>
}

function OcrResult({ markdown }: { markdown: string }) {
  const [copied, setCopied] = useState(false)
  return <div className="rounded-md border border-border bg-muted p-3">
    <div className="mb-2 flex items-center justify-end"><Button type="button" size="sm" variant="secondary" onClick={() => { void navigator.clipboard.writeText(markdown).then(() => setCopied(true)).catch(() => setCopied(false)) }}>{copied ? <Check className="size-3.5" /> : <Copy className="size-3.5" />}{copied ? 'Скопировано' : 'Копировать Markdown'}</Button></div>
    <ChatMarkdown source={markdown || 'OCR не вернул текст.'} />
  </div>
}

export function ChatMediaResult({ result, onError }: { result: MediaResult; onError?: (error: unknown) => void }) {
  if (result.kind === 'image') return <ImageResult assets={result.assets} onError={onError} />
  if (result.kind === 'video') return <VideoResult result={result} onError={onError} />
  return <OcrResult markdown={result.markdown} />
}
