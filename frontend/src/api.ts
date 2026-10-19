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
