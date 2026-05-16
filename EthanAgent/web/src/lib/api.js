/**
 * EthanAgent REST / WebSocket client.
 * All HTTP paths are relative to getApiBase() (Vite dev proxies to backend).
 */

export class ApiError extends Error {
  constructor(status, message) {
    super(message)
    this.name = 'ApiError'
    this.status = status
  }
}

/** @returns {string} Base URL without trailing slash; empty = same origin */
export function getApiBase() {
  const b = import.meta.env.VITE_API_BASE
  return typeof b === 'string' && b.length > 0 ? b.replace(/\/$/, '') : ''
}

function resolveOrigin() {
  return getApiBase() || window.location.origin
}

/**
 * WebSocket URL for a session. session_id is the bare hex from POST /api/sessions.
 * @param {string} sessionId
 */
export function getWsUrl(sessionId) {
  const u = new URL(resolveOrigin())
  u.protocol = u.protocol === 'https:' ? 'wss:' : 'ws:'
  u.pathname = '/ws'
  u.searchParams.set('session_id', sessionId)
  return u.toString()
}

/**
 * @template T
 * @param {string} path - e.g. "/api/sessions"
 * @param {RequestInit} [init]
 * @returns {Promise<T>}
 */
async function request(path, init) {
  const url = `${getApiBase()}${path}`
  const res = await fetch(url, init)
  if (!res.ok) {
    const text = await res.text().catch(() => '')
    throw new ApiError(res.status, text || `HTTP ${res.status}`)
  }
  return res.json()
}

/** @returns {Promise<{ ok: boolean }>} */
export function healthCheck() {
  return request('/api/health')
}

/** @returns {Promise<{ session_id: string }>} */
export function createSession() {
  return request('/api/sessions', { method: 'POST' })
}

/**
 * @returns {Promise<{ sessions: Array<{
 *   session_id: string
 *   key: string
 *   created_at: string | null
 *   updated_at: string | null
 *   message_count: number
 * }> }>}
 */
export function listSessions() {
  return request('/api/sessions')
}

/**
 * @param {string} sessionId - bare hex id
 * @returns {Promise<{ session_id: string, key: string, messages: object[] }>}
 */
/**
 * @param {string} sessionId
 * @param {AbortSignal} [signal]
 */
export function getSessionMessages(sessionId, signal) {
  const encoded = encodeURIComponent(sessionId)
  return request(`/api/sessions/${encoded}/messages`, { signal })
}

/**
 * @param {unknown} content
 * @returns {string}
 */
export function normalizeContent(content) {
  if (typeof content === 'string') return content
  if (content == null) return ''
  if (Array.isArray(content)) {
    return content
      .filter(item => item && item.type === 'text' && typeof item.text === 'string')
      .map(item => item.text)
      .join('\n')
  }
  return JSON.stringify(content, null, 2)
}

/**
 * Map API message rows to UI messages.
 * @param {object[]} rows
 * @returns {{ id: string, role: string, text: string, timestamp?: string }[]}
 */
export function mapApiMessages(rows) {
  return (rows || [])
    .map((m, i) => ({
      id: `msg-${i}-${m.role ?? 'unknown'}-${m.timestamp ?? ''}`,
      role: m.role || 'system',
      text: normalizeContent(m.content),
      timestamp: m.timestamp,
    }))
    .filter(m => m.text.length > 0)
}

/**
 * @returns {Promise<{
 *   agent: { status: string, model: string, uptime_seconds: number, version: string, pid: number, start_time: string },
 *   sessions: { total: number, web: number, cli: number, cron: number },
 *   channels: { active_connections: number, registered: number },
 *   cron: { total: number, jobs: Array<{ id: string, name: string, enabled: boolean, schedule_kind: string, next_run_at_ms: number | null, last_status: string | null, last_duration_ms: number | null }> } | null,
 *   tools: { total: number, names: string[] },
 *   mcp: { connected: boolean, servers: string[] },
 *   memory: { initialized: boolean },
 *   usage: { prompt_tokens: number, completion_tokens: number, context_total: number },
 *   activity: Array<{ time: string, text: string, kind: string, context_total: number, context_token: number }>
 * }>}
 */
export function getAgentStatus() {
  return request('/api/agent/status')
}

/** Outbound WS chat frame */
export function buildChatFrame(message) {
  return JSON.stringify({ type: 'chat', message })
}
