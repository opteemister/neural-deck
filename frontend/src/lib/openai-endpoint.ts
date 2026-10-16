export function openAiEndpoint(configured: string): string {
  const base = configured.trim().replace(/\/+$/, '')
  return base.endsWith('/v1') ? base : `${base}/v1`
}

export function mcpEndpoint(origin: string): string {
  const base = origin.trim().replace(/\/+$/, '')
  return `${base}/mcp`
}
