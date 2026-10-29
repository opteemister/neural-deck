import { useRef, useState } from 'react'
import { Check, Copy, Download, Maximize2 } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Dialog } from '@/components/ui/dialog'
import { ChatMarkdown } from '@/components/chat-markdown'
import { downloadAsset, imageAssetSource, type ImageAsset, type MediaResult, type VideoResult } from '@/lib/chat-media'

function DownloadButton({ asset, filename, onError, overlay = false }: { asset: ImageAsset | VideoResult; filename: string; onError?: (error: unknown) => void; overlay?: boolean }) {
  const label = 'kind' in asset && asset.kind === 'video' ? 'Скачать видео' : 'Скачать изображение'
  return <Button type="button" shape="pill" size={overlay ? 'icon-sm' : 'sm'} iconOnly={overlay} variant={overlay ? 'overlay' : 'secondary'} aria-label={label} title={label} onClick={() => { void downloadAsset(asset, filename).catch((error) => onError?.(error)) }}><Download className="size-3.5" />{!overlay && 'Скачать'}</Button>
}

function ImageResult({ assets, onError }: { assets: ImageAsset[]; onError?: (error: unknown) => void }) {
  const [opened, setOpened] = useState<string | null>(null)
  const openedAsset = opened ? assets.find((asset) => imageAssetSource(asset) === opened) : undefined
  const openedSource = openedAsset ? imageAssetSource(openedAsset) : undefined
  return <>
    <div className="grid gap-3 sm:grid-cols-2">
      {assets.map((asset, index) => {
        const source = imageAssetSource(asset)
        if (!source) return null
        return <figure key={`${source}-${index}`} className="relative w-fit max-w-full overflow-hidden rounded-2xl">
          <button type="button" className="block cursor-zoom-in focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-copper" aria-label={`Открыть изображение ${index + 1}`} onClick={() => setOpened(source)}><img src={source} alt={`Результат ${index + 1}`} className="max-h-[28rem] max-w-full object-contain" /></button>
          <div className="absolute bottom-2 right-2"><DownloadButton asset={asset} filename={`neural-deck-image-${index + 1}.png`} onError={onError} overlay /></div>
        </figure>
      })}
    </div>
    <Dialog open={openedSource !== undefined} title="Изображение" variant="media" onOpenChange={(open) => { if (!open) setOpened(null) }}>
      {openedSource && openedAsset && <><img src={openedSource} alt="Полный размер" className="block max-h-[calc(100dvh-2rem)] max-w-[calc(100vw-2rem)] object-contain" /><div className="absolute bottom-3 right-3"><DownloadButton asset={openedAsset} filename="neural-deck-image.png" onError={onError} overlay /></div></>}
    </Dialog>
  </>
}

function VideoResult({ result, onError }: { result: VideoResult; onError?: (error: unknown) => void }) {
  const [opened, setOpened] = useState(false)
  const videoRef = useRef<HTMLVideoElement>(null)
  return <>
    <div className="relative w-fit max-w-full overflow-hidden rounded-2xl">
      <video ref={videoRef} src={result.url} controls playsInline className="block max-h-[32rem] max-w-full" aria-label="Результат видео" />
      <Button type="button" variant="overlay" shape="pill" size="icon-sm" iconOnly aria-label="Открыть видео" title="Открыть видео" className="absolute right-3 top-3" onClick={() => { videoRef.current?.pause(); setOpened(true) }}><Maximize2 className="size-3.5" /></Button>
      <div className="absolute bottom-14 right-3"><DownloadButton asset={result} filename="neural-deck-video.mp4" onError={onError} overlay /></div>
    </div>
    <Dialog open={opened} title="Видео" variant="media" onOpenChange={setOpened}>
      <video src={result.url} controls playsInline className="block max-h-[calc(100dvh-2rem)] max-w-[calc(100vw-2rem)]" aria-label="Полный размер видео" />
      <div className="absolute bottom-14 right-3"><DownloadButton asset={result} filename="neural-deck-video.mp4" onError={onError} overlay /></div>
    </Dialog>
  </>
}

function OcrResult({ markdown }: { markdown: string }) {
  const [copied, setCopied] = useState(false)
  return <div className="rounded-2xl border border-border/60 bg-card p-3">
    <div className="mb-2 flex items-center justify-end"><Button type="button" shape="pill" size="sm" variant="secondary" onClick={() => { void navigator.clipboard.writeText(markdown).then(() => setCopied(true)).catch(() => setCopied(false)) }}>{copied ? <Check className="size-3.5" /> : <Copy className="size-3.5" />}{copied ? 'Скопировано' : 'Копировать Markdown'}</Button></div>
    <ChatMarkdown source={markdown || 'OCR не вернул текст.'} />
  </div>
}

export function ChatMediaResult({ result, onError }: { result: MediaResult; onError?: (error: unknown) => void }) {
  if (result.kind === 'image') return <ImageResult assets={result.assets} onError={onError} />
  if (result.kind === 'video') return <VideoResult result={result} onError={onError} />
  return <OcrResult markdown={result.markdown} />
}
