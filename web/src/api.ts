// Thin client for the FlowSync FastAPI backend. The bearer token lives in sessionStorage only,
// so closing the tab signs the operator out.
const KEY = 'flowsync.auth'

export interface Auth { token: string; role: string; user: string }

export function loadAuth(): Auth | null {
  try {
    const j = sessionStorage.getItem(KEY)
    return j ? (JSON.parse(j) as Auth) : null
  } catch { return null }
}
export function saveAuth(a: Auth | null) {
  try {
    if (a) sessionStorage.setItem(KEY, JSON.stringify(a))
    else sessionStorage.removeItem(KEY)
  } catch { /* storage blocked: stay signed in for this page load only */ }
}

export class ApiError extends Error {
  status: number
  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

export async function api<T = unknown>(method: 'GET' | 'POST', path: string, token?: string, body?: unknown): Promise<T> {
  const r = await fetch(path, {
    method,
    headers: { ...(body !== undefined ? { 'Content-Type': 'application/json' } : {}), ...(token ? { Authorization: `Bearer ${token}` } : {}) },
    body: body !== undefined ? JSON.stringify(body) : undefined,
  })
  if (!r.ok) {
    let msg = r.statusText
    try { const j = await r.json(); msg = typeof j.detail === 'string' ? j.detail : JSON.stringify(j.detail ?? j) } catch { /* not json */ }
    throw new ApiError(r.status, msg || `HTTP ${r.status}`)
  }
  return (await r.json()) as T
}

export async function login(username: string, password: string): Promise<Auth> {
  const r = await api<{ token: string; role: string }>('POST', '/api/auth/login', undefined, { username, password })
  return { token: r.token, role: r.role, user: username }
}
