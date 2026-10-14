/** Group model files by structural role, model identity and precision, never repository ID. */
export interface ComponentFile { path: string; size: number | null }
export interface ComponentVariant {
  id: string
  precision: string
  files: ComponentFile[]
  size: number | null
  complete: boolean
}
export interface ModelComponent {
  id: string
  category: string
  name: string
  variants: ComponentVariant[]
}

const weights = /\.(gguf|ggml|mmproj|safetensors|bin|pt|pth|ckpt|onnx|nemo|tflite)$/i
const categories: Record<string, string> = {
  diffusion_models: 'Основная модель', unet: 'Основная модель', transformer: 'Основная модель',
  checkpoints: 'Checkpoint', text_encoders: 'Текстовый энкодер', text_encoder: 'Текстовый энкодер',
  clip: 'Текстовый энкодер', vae: 'VAE', audio_vae: 'Аудио VAE',
  loras: 'LoRA', model_patches: 'Патчи модели', controlnet: 'ControlNet',
  embeddings: 'Embeddings', clip_vision: 'Визуальный энкодер',
  audio_encoders: 'Аудио энкодер', upscale_models: 'Upscale',
}
const precisionSuffix = /(?:[_.-])((?:bf16|fp16|fp32|f16|f32|fp8(?:[_-](?:scaled|e4m3fn|e5m2))?|int8(?:[_-]convrot)?|nvfp4(?:[_-]awq)?|w\d+a\d+|q\d+(?:[_-][a-z0-9]+)*|iq\d+(?:[_-][a-z0-9]+)*))$/i
const shardPattern = /-(\d{5})-of-(\d{5})$/

export function componentFileSize(files: ComponentFile[]): number | null {
  return files.every((file) => file.size !== null) ? files.reduce((sum, file) => sum + (file.size ?? 0), 0) : null
}

export function hasImageVideoStructure(files: Pick<ComponentFile, 'path'>[]): boolean {
  const directories = new Set(files.flatMap((file) => file.path.split('/').slice(0, -1)))
  return ['diffusion_models', 'unet', 'checkpoints'].some((name) => directories.has(name))
    || (['vae', 'audio_vae'].some((name) => directories.has(name))
      && [...directories].some((name) => /^(?:transformer(?:_.*)?|text_encoders?|clip)$/.test(name)))
}

export function componentPrecisions(components: ModelComponent[]): string[] {
  return [...new Set(components.flatMap((component) => component.variants
    .filter((variant) => variant.complete && variant.precision !== 'Не указана')
    .map((variant) => variant.precision)))].sort()
}

export function selectComponentPrecision(components: ModelComponent[], selected: Record<string, string>, precision: string): Record<string, string> {
  const next = { ...selected }
  for (const component of components) {
    const current = component.variants.find((variant) => variant.id === selected[component.id]) ?? component.variants[0]
    // Keep the current representation when it already matches (single file vs shards).
    const match = current.precision === precision && current.complete ? current
      : component.variants.find((variant) => variant.precision === precision && variant.complete)
    if (match) next[component.id] = match.id
  }
  return next
}

export function groupModelComponents(files: ComponentFile[]): ModelComponent[] {
  const result = new Map<string, ModelComponent>()
  for (const file of files) {
    if (!weights.test(file.path)) continue
    const parts = file.path.replaceAll('\\', '/').split('/')
    const directory = parts.slice(0, -1).join('/')
    let stem = parts.at(-1)!.replace(/\.[^.]+$/, '')
    // GGUF shards put the shard suffix after precision; other formats may reverse them.
    let shard = stem.match(shardPattern)
    if (shard) stem = stem.replace(shardPattern, '')
    const quant = stem.match(precisionSuffix)
    if (quant) stem = stem.slice(0, quant.index)
    shard ??= stem.match(shardPattern)
    stem = stem.replace(shardPattern, '')
    const precision = quant ? quant[1].replaceAll('-', '_').toUpperCase() : 'Не указана'
    const role = parts.slice(0, -1).find((part) => categories[part])
    const category = role ? categories[role] : directory || 'Модель'
    const generic = /^(?:model|diffusion_pytorch_model|pytorch_model)$/i.test(stem)
    const name = generic && directory ? directory.split('/').at(-1)! : stem
    // Keep the directory as part of identity: unrelated checkpoints never merge.
    const id = `${directory}/${stem}`
    let component = result.get(id)
    if (!component) { component = { id, category, name, variants: [] }; result.set(id, component) }
    const variantId = `${id}/${precision}/${file.path.split('.').at(-1)!.toLowerCase()}/${shard ? 'sharded' : 'single'}`
    let variant = component.variants.find((item) => item.id === variantId)
    if (!variant) { variant = { id: variantId, precision, files: [], size: null, complete: true }; component.variants.push(variant) }
    if (!variant.files.some((item) => item.path === file.path)) variant.files.push(file)
  }
  for (const component of result.values()) for (const variant of component.variants) {
    variant.files.sort((a, b) => a.path.localeCompare(b.path))
    variant.size = componentFileSize(variant.files)
    const shards = variant.files.map((file) => file.path.replace(/\.[^.]+$/, '').match(/-(\d{5})-of-(\d{5})(?:[_.-].*)?$/)).filter((match) => match !== null)
    if (shards.length) {
      const total = Number(shards[0]![2])
      const indices = new Set(shards.map((match) => Number(match![1])))
      variant.complete = shards.length === variant.files.length && shards.every((match) => Number(match![2]) === total) && indices.size === total && [...indices].every((index) => index >= 1 && index <= total)
    }
  }
  const order = ['Основная модель', 'Checkpoint', 'Текстовый энкодер', 'VAE', 'Аудио VAE']
  const rank = (category: string) => order.includes(category) ? order.indexOf(category) : order.length
  return [...result.values()].sort((a, b) => rank(a.category) - rank(b.category) || a.category.localeCompare(b.category) || a.name.localeCompare(b.name))
}

/** Include sidecars only from the selected checkpoint's directory, never sibling weights. */
export function componentDownloadFiles(variant: ComponentVariant, allFiles: ComponentFile[]): ComponentFile[] {
  const directories = new Set(variant.files.map((file) => file.path.slice(0, file.path.lastIndexOf('/') + 1)))
  const checkpointNames = new Set(variant.files.map((file) => file.path.split('/').at(-1)!.replace(/-\d{5}-of-\d{5}/, '')))
  const sidecar = /(?:\.json$|\.jinja$|\.model$|^vocab\.txt$|^merges\.txt$|^README\.md$|^LICENSE(?:\..*)?$)/i
  const files = [...variant.files, ...allFiles.filter((file) => {
    const slash = file.path.lastIndexOf('/')
    const directory = file.path.slice(0, slash + 1)
    const name = file.path.slice(slash + 1)
    if (name.endsWith('.index.json') && !checkpointNames.has(name.replace(/\.index\.json$/, ''))) return false
    const sharedDirectory = /(?:^|\/)(?:tokenizer|processor|scheduler)(?:_\d+)?\/$/.test(directory)
    return (directories.has(directory) || directory === '' || sharedDirectory) && sidecar.test(name)
  })]
  return [...new Map(files.map((file) => [file.path, file])).values()]
}
