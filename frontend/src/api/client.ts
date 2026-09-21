import type { ApprovalItem, Health, Session, WEvent } from '../types'

/* N1：token 存 localStorage；带 Authorization 出请求；401 即清票（App 层据 needLogin 切登录页）。
   auth 关闭（PLATFORM__AUTH_ENABLED=0，默认值）时服务端不校验，这里带不带都行——header 只在有票时附加。 */
const TOKEN_KEY = 'ch_token'

export function getToken(): string {
  return localStorage.getItem(TOKEN_KEY) || ''
}

export function setToken(t: string) {
  if (t) localStorage.setItem(TOKEN_KEY, t)
  else localStorage.removeItem(TOKEN_KEY)
}

/** F2：请求 deadline。**导出**是给唯一一处有意绕过 `req()` 的直接 fetch 共用
 *  （`stores/auth.ts` 的 `/api/auth/me`——它要 401 不清票），两处取值不许漂移。
 *  取值依据——所有端点都在请求内同步返回（runner 只登记任务、`stop` 只发 cancel），
 *  最重的 import_repo 实测 12287 文件 0.71s（log/probe_import_timing.py）。 */
export const REQUEST_TIMEOUT_MS = 15000

async function req<T = any>(method: string, url: string, body?: any): Promise<T> {
  const headers: Record<string, string> = {}
  if (body) headers['Content-Type'] = 'application/json'
  const token = getToken()
  if (token) headers['Authorization'] = `Bearer ${token}`
  let rsp: Response
  try {
    rsp = await fetch(url, {
      method, headers, body: body ? JSON.stringify(body) : undefined,
      signal: AbortSignal.timeout(REQUEST_TIMEOUT_MS)
    })
  } catch (e) {
    // 超时抛的是 DOMException('TimeoutError')，文案既没 url 也不说人话；调用方直接把
    // e.message 塞进 toast（ApprovalCard / QuestionCard 等），所以在这里翻成「哪个操作几秒没回」。
    if ((e as Error)?.name === 'TimeoutError') {
      throw new Error(`${method} ${url} 超时（${REQUEST_TIMEOUT_MS / 1000}s 无响应，后端可能卡住）`)
    }
    throw e
  }
  if (!rsp.ok) {
    if (rsp.status === 401 && !url.startsWith('/api/auth')) setToken('')
    let detail: unknown = rsp.statusText
    try {
      detail = (await rsp.json()).detail || detail
    } catch {}
    const err: Error & { status?: number } = new Error(flattenDetail(detail))
    err.status = rsp.status       // 调用方要按状态分流（413 不是"出错"，是"不在浏览器里预览"）。
                                  // 给状态码而不是让调用方去猜 message 里的字面词。
    throw err
  }
  return rsp.json()
}

/** 校验失败时 FastAPI 给的 detail 是数组，直接塞进 Error 会渲染成 [object Object]。 */
function flattenDetail(detail: unknown): string {
  if (typeof detail === 'string') return detail
  if (Array.isArray(detail)) {
    return detail
      .map((d: any) => {
        const where = Array.isArray(d?.loc) ? d.loc.filter((x: any) => x !== 'body').join('.') : ''
        const msg = d?.ctx?.msg || d?.msg || JSON.stringify(d)
        return where ? `${where}: ${msg}` : String(msg)
      })
      .join('；')
  }
  return String(detail)
}

export const api = {
  health: () => req<Health>('GET', '/api/health'),
  /** 端点侧 /models 摊平。ok=false 表示拿不到目录，模型位退化成只读文本 */
  listModels: () =>
    req<{ models: string[]; current: string; ok: boolean; error: string }>('GET', '/api/models'),
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
    paradigm?: string
    llm?: Record<string, any>
    permission?: string
  }) => req<Session>('POST', '/api/sessions', payload),
  startSession: (sid: string) => req('POST', `/api/sessions/${sid}/start`),
  stopSession: (sid: string) => req('POST', `/api/sessions/${sid}/stop`),
  /** 侧栏写操作：只接受 idea / archived / pinned / permission，不传的字段保持原值 */
  patchSession: (sid: string, patch: { idea?: string; archived?: boolean; pinned?: boolean;
                                       permission?: string }) =>
    req<Session>('PATCH', `/api/sessions/${sid}`, patch),
  deleteSession: (sid: string) => req<{ ok: boolean; deleted: string }>('DELETE', `/api/sessions/${sid}`),
  /** 工具审批（批次36）：pending=还在等人，decided=已给结论（含 outcome） */
  approvals: (sid: string) =>
    req<{ pending: ApprovalItem[]; decided: ApprovalItem[] }>('GET', `/api/sessions/${sid}/approvals`),
  respondApproval: (sid: string, aid: string, outcome: 'allowed-once' | 'rejected') =>
    req<{ ok: boolean; outcome: string }>('POST', `/api/sessions/${sid}/approvals/${aid}/respond`,
                                          { outcome }),
  /** 有界回放 + 游标分页（B2）：`limit` 取**窗口尾部**＝「最新一屏」，`before` 往回翻，
   *  `has_more` 说前面还有没有（胶囊显隐靠它，不必再打一次请求）。
   *  query 必须另拼：`tests/s8_frontend_contract.py` 的 t3 按「`/api` 起、遇空白或 `?` 止」
   *  抠前端消费的路径，把 `${qs}` 写进同一个模板串会被抠成非法路径、门禁判红。 */
  eventHistory: (sid: string, q: { after?: string; before?: string; limit?: number } = {}) => {
    const p = new URLSearchParams()
    if (q.after) p.set('after', q.after)
    if (q.before) p.set('before', q.before)
    if (q.limit) p.set('limit', String(q.limit))
    const qs = p.toString()
    const path = `/api/sessions/${sid}/events/history` + (qs ? `?${qs}` : '')
    return req<{ events: WEvent[]; has_more: boolean }>('GET', path)
  },
  importRepo: (sid: string, payload: { repo_path: string; save_name?: string; include_files?: boolean }) =>
    req<Record<string, any>>('POST', `/api/sessions/${sid}/workspace/import_repo`, payload),
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
    req<{
      spans: { node: string; pt: number; ct: number; cost: number; ts: number; t0: number | null; ft: number | null }[]
    }>('GET', `/api/sessions/${sid}/trace`)
}
