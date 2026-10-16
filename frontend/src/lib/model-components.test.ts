import { describe, expect, it } from 'vitest'
import { componentDownloadFiles, groupModelComponents, hasImageVideoStructure, isAuxiliaryModelPath, isDownloadableModelWeight, selectComponentPrecision } from './model-components'

const files = (...paths: string[]) => paths.map((path) => ({ path, size: 10 }))

describe('structural model components', () => {
  it('recognizes llama.cpp auxiliary projector files with the Hub .gguf_file suffix', () => {
    const projector = 'Shard_Rewrite/mmproj-BF16.gguf_file'
    expect(isAuxiliaryModelPath(projector)).toBe(true)
    expect(isDownloadableModelWeight(projector)).toBe(true)
    expect(isDownloadableModelWeight('Shard_Rewrite/00001-of-00005.gguf_file')).toBe(false)
    const [component] = groupModelComponents([{ path: projector, size: 123 }])
    expect(component.category).toBe('Вспомогательный блок')
    expect(component.variants[0].precision).toBe('BF16')
  })

  it('applies a common precision only where available and preserves other selections', () => {
    const all = files(
      'diffusion_models/example_bf16.safetensors', 'diffusion_models/example_int8_convrot.safetensors',
      'text_encoders/encoder_bf16.safetensors', 'text_encoders/encoder_int8_convrot.safetensors',
      'vae/decoder_fp16.safetensors',
    )
    const groups = groupModelComponents(all)
    const vae = groups.find((group) => group.category === 'VAE')!
    const selected = selectComponentPrecision(groups, { [vae.id]: vae.variants[0].id }, 'INT8_CONVROT')
    expect(hasImageVideoStructure(all)).toBe(true)
    expect(hasImageVideoStructure(files('text_encoders/encoder_bf16.safetensors'))).toBe(false)
    expect(hasImageVideoStructure(files('model-Q4_K_M.gguf'))).toBe(false)
    expect(selected[vae.id]).toBe(vae.variants[0].id)
    for (const group of groups.filter((item) => item !== vae)) {
      expect(group.variants.find((variant) => variant.id === selected[group.id])?.precision).toBe('INT8_CONVROT')
    }
  })

  it('keeps model identities distinct while grouping precision alternatives', () => {
    const groups = groupModelComponents(files(
      'diffusion_models/example_bf16.safetensors',
      'diffusion_models/example_int8_convrot.safetensors',
      'diffusion_models/example_turbo_bf16.safetensors',
      'diffusion_models/example_pruned_w6a8.safetensors',
      'vae/audio_vae_fp32.safetensors',
      'vae/video_vae_fp16.safetensors',
    ))
    expect(groups).toHaveLength(5)
    expect(groups.find((group) => group.name === 'example')?.variants.map((variant) => variant.precision)).toEqual(['BF16', 'INT8_CONVROT'])
  })

  it('recognizes categories beneath arbitrary prefixes and unknown names conservatively', () => {
    const groups = groupModelComponents(files(
      'split_files/text_encoders/encoder_nvfp4_awq.safetensors',
      'custom/unknown-a.safetensors', 'custom/unknown-b.safetensors',
    ))
    expect(groups).toHaveLength(3)
    expect(groups.find((group) => group.category === 'Текстовый энкодер')?.variants[0].precision).toBe('NVFP4_AWQ')
  })

  it('keeps all shards together and rejects incomplete sets', () => {
    const groups = groupModelComponents(files(
      'model-Q4_K_M-00001-of-00002.gguf', 'model-Q4_K_M-00002-of-00002.gguf',
      'model-Q8_0-00001-of-00002.gguf',
    ))
    expect(groups).toHaveLength(1)
    expect(groups[0].variants[0].complete).toBe(true)
    expect(groups[0].variants[0].files).toHaveLength(2)
    expect(groups[0].variants[1].complete).toBe(false)
  })

  it('includes checkpoint sidecars and shared metadata, never other weights or indexes', () => {
    const all = files(
      'transformer/diffusion_pytorch_model-00001-of-00002.safetensors',
      'transformer/diffusion_pytorch_model-00002-of-00002.safetensors',
      'transformer/diffusion_pytorch_model.safetensors.index.json',
      'transformer/other.safetensors.index.json', 'transformer/other_fp8.safetensors',
      'transformer/config.json', 'processor/config.json', 'scheduler/scheduler_config.json',
      'tokenizer/tokenizer.json', 'README.md',
    )
    const variant = groupModelComponents(all).find((group) => group.name === 'transformer')!.variants[0]
    const selected = componentDownloadFiles(variant, all).map((file) => file.path)
    expect(selected).toHaveLength(8)
    expect(selected).not.toContain('transformer/other_fp8.safetensors')
    expect(selected).not.toContain('transformer/other.safetensors.index.json')
  })

  it('uses identical identities for HF metadata and local catalogue files', () => {
    const paths = ['text_encoders/example_bf16.safetensors', 'text_encoders/example_w4a8.safetensors']
    const remote = groupModelComponents(files(...paths))
    const local = groupModelComponents(paths.map((path) => ({ path, size: 20 })))
    expect(local.map((group) => [group.id, group.variants.map((variant) => variant.id)])).toEqual(remote.map((group) => [group.id, group.variants.map((variant) => variant.id)]))
  })

  it('does not download both a split checkpoint and its single-file alternative', () => {
    const groups = groupModelComponents(files(
      'model-Q4_K_M.gguf', 'model-Q4_K_M-00001-of-00002.gguf',
      'model-Q4_K_M-00002-of-00002.gguf',
    ))
    expect(groups).toHaveLength(1)
    expect(groups[0].variants).toHaveLength(2)
    expect(groups[0].variants.every((variant) => variant.complete)).toBe(true)
  })
})
