// spec: version-zero
import { describe, expect, it } from 'vitest'
import { previewProfileCommand } from '@/lib/profile-preview'

describe('profile command preview', () => {
  it('quotes the public alias and preserves placeholder-like text in values', () => {
    expect(previewProfileCommand('{{engine}} --model {{model}} --alias {{model_name}}', { engine: '/bin/engine', model: '/models/{{model_name}}', model_name: 'public $& {{model}}' })).toEqual({ ok: true, command: "/bin/engine --model '/models/{{model_name}}' --alias 'public $& {{model}}'" })
  })

  it('requires a public alias when requested and rejects unsafe placements', () => {
    expect(previewProfileCommand('echo {{model_name}}', { engine: '/bin/engine', model: '/models/x' })).toMatchObject({ ok: false })
    for (const command of ['echo "{{model_name}}"', 'NAME={{model_name}}', 'echo {{model_name}}x', 'echo $({{model_name}})']) {
      expect(previewProfileCommand(command, { engine: '/bin/engine', model: '/models/x', model_name: 'qwen' })).toMatchObject({ ok: false })
    }
  })

  it('quotes substituted paths as one shell word', () => {
    expect(previewProfileCommand('{{engine}} --model {{model}}', { engine: '/opt/bin/llama server', model: '/models/space model' })).toEqual({ ok: true, command: "'/opt/bin/llama server' --model '/models/space model'" })
  })

  it('rejects the same placeholder placements as the backend', () => {
    expect(previewProfileCommand('echo "{{model}}"', { engine: '/bin/engine', model: '/models/model' })).toMatchObject({ ok: false })
    expect(previewProfileCommand('NAME={{engine}}', { engine: '/bin/engine', model: '/models/model' })).toMatchObject({ ok: false })
    expect(previewProfileCommand('echo {{model}}x', { engine: '/bin/engine', model: '/models/model' })).toMatchObject({ ok: false })
  })

  it('rejects backtick command substitution whenever a placeholder is present', () => {
    expect(previewProfileCommand('echo `printf ok` {{model}}', { engine: '/bin/engine', model: '/models/model' })).toMatchObject({ ok: false })
    expect(previewProfileCommand('echo `{{model}}`', { engine: '/bin/engine', model: '/models/model' })).toMatchObject({ ok: false })
    expect(previewProfileCommand('echo `printf ok`', { engine: '/bin/engine', model: '/models/model' })).toMatchObject({ ok: true })
  })
})
