import type { EngineType, LocalModelArtifact } from '@/types/api'

export type ProfileModelOption = { path: string; label: string; disabled: boolean }

/** Resolve catalogue groups into launch targets, preserving nested directories. */
export function profileModelOptions(models: LocalModelArtifact[], engineTypes: EngineType[] = [], auxiliary = false): ProfileModelOption[] {
  const options: ProfileModelOption[] = []
  for (const model of models) {
    const name = model.hf_match.repo_id ?? ([model.vendor, model.model_name].filter(Boolean).join('/') || model.path.split('/').pop()!)
    const isImage = model.model_kind === 'image'
    if (isImage && !engineTypes.includes('image')) continue
    if (!isImage && !engineTypes.includes('text')) continue
    if (isImage) {
      const diffusers = model.format === 'diffusers'
      options.push({ path: model.path, label: `${name} · ${diffusers ? 'Diffusers — нужен набор ComfyUI' : 'ComfyUI Image'}`, disabled: diffusers })
      continue
    }
    const groups = auxiliary ? model.auxiliary_blocks ?? [] : model.groups ?? []
    if (!auxiliary && model.kind === 'file') {
      const path = model.format === 'gguf' || model.path.toLowerCase().endsWith('.gguf') ? model.path : model.path.slice(0, model.path.lastIndexOf('/')) || '/'
      options.push({ path, label: name, disabled: false })
      continue
    }
    for (const group of groups) {
      const gguf = group.files.filter((file) => file.format === 'gguf' || file.path.toLowerCase().endsWith('.gguf'))
      const entry = gguf.find((file) => !/-\d{5}-of-\d{5}\.gguf$/i.test(file.path) || /-00001-of-\d{5}\.gguf$/i.test(file.path))
      const relative = gguf.length ? entry?.path : group.path ?? ''
      if (relative === undefined) continue
      const path = `${model.path.replace(/\/$/, '')}${relative ? `/${relative}` : ''}`
      const showVariant = gguf.length > 0 || Boolean(group.path)
      const label = !showVariant || group.label === 'Модель' || group.label === 'model' || group.label === model.model_name ? name : `${name} — ${group.label}`
      options.push({ path, label: `${label}${group.status === 'incomplete' ? ' · не полностью загружена' : ''}`, disabled: group.status === 'incomplete' })
    }
  }
  return options.filter((option, index) => options.findIndex((item) => item.path === option.path) === index)
}
