/** Browser contracts for configured runtime plugins exposed by the preview service. */
export interface PluginError {
  code: string
  message: string
}

export interface RuntimePlugin {
  pluginId: string
  name: string
  version: string
  capabilities: string[]
  uiKind: string
  state: string
  error: PluginError | null
  reloadable: boolean
}

interface ApiErrorResponse {
  code?: unknown
  message?: unknown
}

/** Represents a rejected plugin catalog or reload request. */
export class PluginApiError extends Error {
  public readonly status: number
  public readonly code: string

  public constructor(status: number, code: string, message: string) {
    super(message)
    this.name = 'PluginApiError'
    this.status = status
    this.code = code
  }
}

/** Retrieves configured plugins without exposing import paths or implementation details. */
export async function listPlugins(signal?: AbortSignal): Promise<RuntimePlugin[]> {
  const payload = await requestJson('/api/plugins', { signal })
  if (typeof payload !== 'object' || payload === null || !Array.isArray((payload as { plugins?: unknown }).plugins)) {
    throw new PluginApiError(502, 'invalid_response', '插件列表响应无效。')
  }

  return (payload as { plugins: unknown[] }).plugins.map(parsePlugin)
}

/** Requests a configured-plugin reload. The server owns authorization and lifecycle transitions. */
export async function reloadPlugins(pluginIds: string[]): Promise<void> {
  await requestJson('/api/plugins/reload', {
    method: 'POST',
    body: { plugin_ids: pluginIds },
  })
}

function parsePlugin(payload: unknown): RuntimePlugin {
  if (typeof payload !== 'object' || payload === null) {
    throw new PluginApiError(502, 'invalid_response', '插件条目无效。')
  }

  const value = payload as Record<string, unknown>
  if (
    typeof value.plugin_id !== 'string' ||
    typeof value.name !== 'string' ||
    typeof value.version !== 'string' ||
    !Array.isArray(value.capabilities) ||
    !value.capabilities.every((capability) => typeof capability === 'string') ||
    typeof value.ui_kind !== 'string' ||
    typeof value.state !== 'string' ||
    typeof value.reloadable !== 'boolean'
  ) {
    throw new PluginApiError(502, 'invalid_response', '插件条目不完整。')
  }

  return {
    pluginId: value.plugin_id,
    name: value.name,
    version: value.version,
    capabilities: value.capabilities as string[],
    uiKind: value.ui_kind,
    state: value.state,
    error: parsePluginError(value.error),
    reloadable: value.reloadable,
  }
}

function parsePluginError(payload: unknown): PluginError | null {
  if (payload === null || payload === undefined) {
    return null
  }
  if (typeof payload === 'string') {
    return { code: 'plugin_error', message: payload }
  }
  if (
    typeof payload === 'object' &&
    typeof (payload as Record<string, unknown>).code === 'string' &&
    typeof (payload as Record<string, unknown>).message === 'string'
  ) {
    const value = payload as Record<string, unknown>
    return { code: value.code as string, message: value.message as string }
  }

  throw new PluginApiError(502, 'invalid_response', '插件错误状态无效。')
}

async function requestJson(
  path: string,
  options: { method?: 'GET' | 'POST'; body?: unknown; signal?: AbortSignal } = {},
): Promise<unknown> {
  let response: Response
  try {
    response = await fetch(apiUrl(path), {
      method: options.method ?? 'GET',
      headers: options.body === undefined ? undefined : { 'Content-Type': 'application/json' },
      body: options.body === undefined ? undefined : JSON.stringify(options.body),
      signal: options.signal,
    })
  } catch (error) {
    if (error instanceof DOMException && error.name === 'AbortError') {
      throw error
    }
    throw new PluginApiError(503, 'plugin_service_unavailable', '无法连接插件服务。')
  }

  const payload = await parseResponse(response)
  if (!response.ok) {
    const errorPayload = payload as ApiErrorResponse | null
    throw new PluginApiError(
      response.status,
      typeof errorPayload?.code === 'string' ? errorPayload.code : 'plugin_request_failed',
      typeof errorPayload?.message === 'string' ? errorPayload.message : '插件请求失败。',
    )
  }

  return payload
}

async function parseResponse(response: Response): Promise<unknown> {
  if (response.status === 204) {
    return null
  }
  try {
    return await response.json()
  } catch {
    return null
  }
}

/** Builds a same-origin URL unless Vite has been configured to proxy a remote API. */
function apiUrl(path: string): string {
  const configuredBaseUrl = import.meta.env.VITE_API_BASE_URL
  if (!configuredBaseUrl) {
    return path
  }
  return `${configuredBaseUrl.replace(/\/$/, '')}${path}`
}
