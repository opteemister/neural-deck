/** Deterministic client preview for the Version Zero profile command contract. */

const PLACEHOLDERS = ['{{engine}}', '{{model}}', '{{model_draft}}'] as const

function isSeparator(value: string | undefined): boolean {
  return value === undefined || ' \t\r\n|;&<>()'.includes(value)
}

function shellQuote(value: string): string {
  // This is the POSIX shlex.quote algorithm used by the backend.
  return /^[A-Za-z0-9_@%+=:,./-]+$/.test(value)
    ? value
    : `'${value.replace(/'/g, `'"'"'`)}'`
}

/**
 * Validate and render the standalone placeholders accepted by
 * backend/app/profiles/formatting.py. The UI must never claim a preview for a
 * command the backend would reject before execution.
 */
export function previewProfileCommand(command: string, values: { engine: string; model: string; model_draft?: string | null }): { ok: true; command: string } | { ok: false; error: string } {
  if (command.includes('{{model_draft}}') && !values.model_draft) return { ok: false, error: 'Выберите draft-модель для {{model_draft}}.' }
  // The backend rejects backtick command substitution whenever a placeholder
  // is present. Keep this guard before scanning so the preview has the same
  // contract even when the backtick is otherwise balanced.
  if (command.includes('`') && PLACEHOLDERS.some((placeholder) => command.includes(placeholder))) {
    return { ok: false, error: 'Заполнители нельзя использовать в command substitution.' }
  }
  let quote: "'" | '"' | null = null
  let escaped = false
  let commandSubstitution = 0
  let comment = false

  for (let index = 0; index < command.length;) {
    const char = command[index]
    if (comment) {
      if (PLACEHOLDERS.some((placeholder) => command.startsWith(placeholder, index))) {
        return { ok: false, error: 'Заполнитель нельзя использовать в комментарии.' }
      }
      index += 1
      continue
    }
    if (escaped) {
      escaped = false
      index += 1
      continue
    }
    if (quote === "'") {
      if (PLACEHOLDERS.some((placeholder) => command.startsWith(placeholder, index))) {
        return { ok: false, error: 'Заполнитель нельзя использовать внутри кавычек.' }
      }
      if (char === "'") quote = null
      index += 1
      continue
    }
    if (quote === '"') {
      if (PLACEHOLDERS.some((placeholder) => command.startsWith(placeholder, index))) {
        return { ok: false, error: 'Заполнитель нельзя использовать внутри кавычек.' }
      }
      if (char === '"') quote = null
      else if (char === '\\') escaped = true
      index += 1
      continue
    }
    if (char === "'" || char === '"') {
      quote = char
      index += 1
      continue
    }
    if (char === '\\') {
      if (PLACEHOLDERS.some((placeholder) => command.startsWith(placeholder, index + 1))) {
        return { ok: false, error: 'Заполнитель нельзя экранировать.' }
      }
      escaped = true
      index += 1
      continue
    }
    if (char === '#' && (index === 0 || /\s/.test(command[index - 1]))) {
      comment = true
      index += 1
      continue
    }
    if (command.startsWith('$(', index)) {
      commandSubstitution += 1
      index += 2
      continue
    }
    if (char === '(' && commandSubstitution > 0) {
      commandSubstitution += 1
      index += 1
      continue
    }
    if (char === ')' && commandSubstitution > 0) {
      commandSubstitution -= 1
      index += 1
      continue
    }

    const placeholder = PLACEHOLDERS.find((item) => command.startsWith(item, index))
    if (placeholder) {
      const end = index + placeholder.length
      if (commandSubstitution > 0 || !isSeparator(command[index - 1]) || !isSeparator(command[end])) {
        return { ok: false, error: `${placeholder} должен быть самостоятельным shell-словом вне кавычек.` }
      }
      let left = index - 1
      while (left >= 0 && !isSeparator(command[left])) left -= 1
      const token = command.slice(left + 1, end)
      if (/^[A-Za-z_][A-Za-z0-9_]*=/.test(token)) {
        return { ok: false, error: `${placeholder} нельзя использовать в assignment.` }
      }
      index = end
      continue
    }
    index += 1
  }
  if (quote || escaped || commandSubstitution > 0) {
    return { ok: false, error: 'Незавершённая shell-конструкция в команде.' }
  }

  return {
    ok: true,
    command: command
      .replaceAll('{{engine}}', shellQuote(values.engine))
      .replaceAll('{{model}}', shellQuote(values.model))
      .replaceAll('{{model_draft}}', shellQuote(values.model_draft ?? '')),
  }
}

/** Profile-specific environment shown separately above the launch command. */
export function previewProfileEnvironment(environment: Record<string, string>): string {
  return Object.entries(environment).map(([name, value]) => `${name}=${shellQuote(value)}`).join('\n')
}
