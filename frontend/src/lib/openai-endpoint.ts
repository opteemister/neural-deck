export function openAiEndpoint(configured: string): string {
  const base = configured.trim().replace(/\/+$/, '')
  return base.endsWith('/v1') ? base : `${base}/v1`
}
