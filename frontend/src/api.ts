const isDev = (import.meta as any).env?.DEV
const BASE = isDev ? '/api' : ''

export function getToken(): string {
  return localStorage.getItem('radar_token') || ''
}

export function setToken(t: string) {
  localStorage.setItem('radar_token', t)
}

export function clearToken() {
  localStorage.removeItem('radar_token')
}

export async function api<T = any>(path: string, options: RequestInit = {}): Promise<T> {
  const headers: Record<string, string> = { 'Content-Type': 'application/json' }
  if (getToken()) headers['Authorization'] = `Bearer ${getToken()}`
  const res = await fetch(`${BASE}${path}`, { ...options, headers })
  if (res.status === 401) {
    clearToken()
    window.location.hash = '#/login'
    throw new Error('未登录或登录已过期')
  }
  if (!res.ok) {
    const detail = await res.text()
    throw new Error(`API 错误 ${res.status}: ${detail.slice(0, 300)}`)
  }
  return res.json()
}

export const fmt = (d: string | null | undefined): string =>
  d ? new Date(d).toLocaleString('zh-CN', { hour12: false }) : '—'

export const fmtDate = (d: string | null | undefined): string =>
  d ? new Date(d).toLocaleDateString('zh-CN') : '未知'

/**
 * Return a URL that is safe to put in an href/src, or '' if it is not.
 *
 * The backend already rejects unsafe schemes when storing scraped URLs, but
 * this is the last line of defence: React escapes TEXT but does not filter
 * the href scheme, so a `javascript:` URL would execute in our own origin.
 * Only http(s) and scheme-less relative paths are allowed.
 */
export function safeHref(url: string | null | undefined): string {
  if (!url) return ''
  const cleaned = url.replace(/[\r\n\t\0]/g, '').trim()
  if (!cleaned) return ''
  const lower = cleaned.toLowerCase()
  if (lower.startsWith('http://') || lower.startsWith('https://')) return cleaned
  // scheme-less relative path is safe
  if (!/^[a-z][a-z0-9+.-]*:/i.test(cleaned)) return cleaned
  return ''
}
