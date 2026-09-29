// spec: version-zero
import { describe, expect, it } from 'vitest'
import { previewProfileCommand } from '@/lib/profile-preview'

describe('profile command preview', () => {
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
