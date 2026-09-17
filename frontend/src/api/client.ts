import type { Health, Session } from '../types'

async function req<T = any>(method: string, url: string, body?: any): Promise<T> {
  const rsp = await fetch(url, {
    method,
    headers: body ? { 'Content-Type': 'application/json' } : undefined,
    body: body ? JSON.stringify(body) : undefined
  })
  if (!rsp.ok) {
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
  sessionGraph: (sid: string) => req<{ mermaid: string }>('GET', `/api/sessions/${sid}/graph`)
}
