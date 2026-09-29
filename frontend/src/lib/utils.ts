export function cn(...values: Array<string | false | null | undefined>): string {
  return values.filter(Boolean).join(' ')
}

export function formatBytes(bytes: number | null | undefined): string {
  if (bytes === null || bytes === undefined || !Number.isFinite(bytes)) return 'недоступно'
  if (bytes < 1024) return `${bytes} Б`
  const units = ['КБ', 'МБ', 'ГБ', 'ТБ']
  let value = bytes / 1024
  let unit = units[0]
  for (let index = 0; index < units.length && value >= 1024; index += 1) {
    value /= 1024
    unit = units[index + 1] ?? units[index]
  }
  return `${value.toFixed(value >= 10 ? 0 : 1)} ${unit}`
}

export function formatPercent(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return 'недоступно'
  return `${value.toFixed(value % 1 === 0 ? 0 : 1)}%`
}

export function formatTimestamp(value: string | null | undefined): string {
  if (!value) return 'время неизвестно'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return 'время неизвестно'
  return new Intl.DateTimeFormat('ru-RU', { dateStyle: 'short', timeStyle: 'medium' }).format(date)
}

export function stateLabel(state: string): string {
  const labels: Record<string, string> = {
    ok: 'Работает',
    degraded: 'Есть ограничения',
    error: 'Ошибка',
    ready: 'Готово',
    running: 'Работает',
    starting: 'Запускается',
    stopping: 'Останавливается',
    exited: 'Остановлен',
    unknown: 'Неизвестно',
    broken: 'Проблема',
    candidate: 'Кандидат',
    succeeded: 'Завершено',
    failed: 'Не выполнено',
    queued: 'В очереди',
    unavailable: 'Недоступно',
    stale: 'Устарело',
    downloaded: 'Скачано',
    incomplete: 'Неполно',
  }
  return labels[state] ?? state
}
