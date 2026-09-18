import type { Health, Session } from '../types'

/* N1：token 存 localStorage；带 Authorization 出请求；401 即清票（App 层据 needLogin 切登录页）。
   auth 关闭（PLATFORM__AUTH=0）时服务端不校验，这里带不带都行——header 只在有票时附加。 */
const TOKEN_KEY = 'ch_token'

export function getToken(): string {
  return localStorage.getItem(TOKEN_KEY) || ''
}

export function setToken(t: string) {
  if (t) localStorage.setItem(TOKEN_KEY, t)
  else localStorage.removeItem(TOKEN_KEY)
}

async function req<T = any>(method: string, url: string, body?: any): Promise<T> {
  const headers: Record<string, string> = {}
  if (body) headers['Content-Type'] = 'application/json'
  const token = getToken()
  if (token) headers['Authorization'] = `Bearer ${token}`
  const rsp = await fetch(url, { method, headers, body: body ? JSON.stringify(body) : undefined })
  if (!rsp.ok) {
    if (rsp.status === 401 && !url.startsWith('/api/auth')) setToken('')
    let detail = rsp.statusText
    try {
      detail = (await rsp.json()).detail || detail
    } catch {}
    throw new Error(detail)
  }
  return rsp.json()
}

export const api = {
  health: () => req<Health>('GET', '/api/health'),
  login: (username: string, password: string) =>
    req<{ ok: boolean; token: string }>('POST', '/api/auth/login', { username, password }),
  register: (username: string, password: string) =>
    req<{ ok: boolean; token: string }>('POST', '/api/auth/register', { username, password }),
  logout: () => req('POST', '/api/auth/logout'),
  listSessions: () => req<Session[]>('GET', '/api/sessions'),
  getSession: (sid: string) => req<Session>('GET', `/api/sessions/${sid}`),
  createSession: (payload: {
    idea: string
    project_name?: string
    n_round?: number
    llm?: Record<string, any>
  }) => req<Session>('POST', '/api/sessions', payload),
  startSession: (sid: string) => req('POST', `/api/sessions/${sid}/start`),
  stopSession: (sid: string) => req('POST', `/api/sessions/${sid}/stop`),
  sendChat: (sid: string, content: string, sendTo = '') =>
    req('POST', `/api/sessions/${sid}/chat`, { content, send_to: sendTo }),
  answerHuman: (sid: string, content: string) =>
    req('POST', `/api/sessions/${sid}/human-input`, { content }),
  fileTree: (sid: string) =>
    req<{ exists: boolean; tree: any[] }>('GET', `/api/sessions/${sid}/workspace/files`),
  fileContent: (sid: string, path: string) =>
    req('GET', `/api/sessions/${sid}/workspace/file?path=${encodeURIComponent(path)}`),
  sessionGraph: (sid: string) => req<{ mermaid: string }>('GET', `/api/sessions/${sid}/graph`),
  sessionTrace: (sid: string) =>
    req<{ spans: { node: string; pt: number; ct: number; cost: number; ts: number }[] }>(
      'GET',
      `/api/sessions/${sid}/trace`
    )
}
