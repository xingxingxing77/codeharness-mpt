import type { ApprovalItem, Health, Session, WEvent } from '../types'
import type { VoteEntry } from '../utils/votes'

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

/** B12 知识库上传另给一档：`upload_kb` 在**同一个请求里**落盘 + 切块 + 向量化
 *  （`server/api/workspace.py` 里 `await UploadKB(...)._call(...)`），20MB×20 的上限摆在那儿，
 *  15s 那档是给「同步返回」那批端点算的余量，套到摄取上只会把「还在跑」报成「后端可能卡住」。 */
export const KB_UPLOAD_TIMEOUT_MS = 120000

async function req<T = any>(method: string, url: string, body?: any,
                            timeoutMs = REQUEST_TIMEOUT_MS): Promise<T> {
  const headers: Record<string, string> = {}
  // FormData 必须由浏览器自己带 `multipart/form-data; boundary=…`，这里设 Content-Type 会把 boundary 打掉
  const form = body instanceof FormData
  if (body && !form) headers['Content-Type'] = 'application/json'
  const token = getToken()
  if (token) headers['Authorization'] = `Bearer ${token}`
  let rsp: Response
  try {
    rsp = await fetch(url, {
      method, headers, body: body ? (form ? body : JSON.stringify(body)) : undefined,
      signal: AbortSignal.timeout(timeoutMs)
    })
  } catch (e) {
    // 超时抛的是 DOMException('TimeoutError')，文案既没 url 也不说人话；调用方直接把
    // e.message 塞进 toast（ApprovalCard / QuestionCard 等），所以在这里翻成「哪个操作几秒没回」。
    if ((e as Error)?.name === 'TimeoutError') {
      // 秒数必须取**本次这一发**的档位：写死常量的话，120s 档的上传等满两分钟后
      // 会说「超时（15s 无响应）」——报出来的数字是假的，比不报更容易把人带错方向。
      throw new Error(`${method} ${url} 超时（${timeoutMs / 1000}s 无响应，后端可能卡住）`)
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
  /** B12 知识库摄取（C3 那条链的界面入口）：字段名固定 `files`，可多个。
   *  **部分成功是合法结局**——`errors[]` 与 `uploaded_count` 会同时非零，调用方两条都得说出去。
   *  后缀/大小/件数三条判据只住在后端一份（`upload_kb.py::SUPPORTED` + 两个 MAX_ 常量），
   *  这里不 `accept=` 也不在前端重抄数字：抄一份就有第二处会漂，拒因照原文显示。 */
  uploadKb: (sid: string, files: File[]) => {
    const fd = new FormData()
    for (const f of files) fd.append('files', f)
    return req<{ uploaded_count: number; chunk_count: number; errors: string[]; written: string[] }>(
      'POST', `/api/sessions/${sid}/workspace/upload_kb`, fd, KB_UPLOAD_TIMEOUT_MS)
  },
  /** C30 下架单份知识库文档：只掉这一份的切片，别份一字不动（原件仍在工作区 `kb/` 下——那是取证）。
   *  `source` 走 query 而不是路径段：文件名里的分隔符/空格不做转义就是另一类 bug。
   *  不存在的 source 后端回 **404**（不是静默 ok），调用方照原文显示。 */
  removeKbDoc: (sid: string, source: string) =>
    req<{ source: string; deleted: number }>(
      'DELETE', `/api/sessions/${sid}/workspace/kb_doc?${new URLSearchParams({ source })}`),
  /** B5 目标三动作（`setGoal` 兼作 create/edit：后端按「原来有没有目标」分）。 */
  setGoal: (sid: string, objective: string) =>
    req<Session>('POST', `/api/sessions/${sid}/goal`, { objective }),
  completeGoal: (sid: string) => req<Session>('POST', `/api/sessions/${sid}/goal/complete`),
  clearGoal: (sid: string) => req<Session>('POST', `/api/sessions/${sid}/goal/clear`),
  /** B6 中途 steer 的面：看排着什么、撤回哪一条（不重排）。 */
  queue: (sid: string) => req<{ items: any[] }>('GET', `/api/sessions/${sid}/queue`),
  dropQueued: (sid: string, qid: string) =>
    req<{ ok: boolean; removed: string }>('DELETE', `/api/sessions/${sid}/queue/${qid}`),
  /** B4 反馈：真值在 `Session.feedback`，这三条只是它的读写口。回执里的值是两态的（旧裸串 / 新 `{v,at}`），
   *  所以类型引 `VoteEntry` 而不是 `string`——写 `string` 时用量页那格把新票数成了「未识别」。 */
  feedback: (sid: string) => req<{ votes: Record<string, VoteEntry> }>(
    'GET', `/api/sessions/${sid}/feedback`),
  putFeedback: (sid: string, key: string, vote: 'like' | 'dislike') =>
    req<{ ok: boolean; feedback: Record<string, VoteEntry> }>(
      'PUT', `/api/sessions/${sid}/feedback`, { key, vote }),
  deleteFeedback: (sid: string, key: string) =>
    req<{ ok: boolean; feedback: Record<string, VoteEntry> }>(
      'DELETE', `/api/sessions/${sid}/feedback?${new URLSearchParams({ key })}`),

  /** B3 分叉：`from_cursor` 留空 = 全量分叉。响应是新会话的字段 + 三个分叉回执。 */
  forkSession: (sid: string, fromCursor = '') =>
    req<Record<string, any>>('POST', `/api/sessions/${sid}/fork`, { from_cursor: fromCursor }),
  /** B9 招人三件：可勾选的工具表、让模型现写的草案、确认落库。
   *  草案与落库之间没有第二个真值——表单吃的就是后端那两张表的投影。 */
  hireTools: (sid: string) => req<{ tools: { name: string; tier: string }[] }>(
    'GET', `/api/sessions/${sid}/tools`),
  roleDraft: (sid: string) => req<Record<string, any>>(
    'GET', `/api/sessions/${sid}/roles/draft`),
  hireRole: (sid: string, def: Record<string, any>) =>
    req<Record<string, any>>('POST', `/api/sessions/${sid}/roles`, def),
  /** B9 余账：摘掉招进来的成员（只认 role_defs 里的名字，静态角色后端直接 422）。
   *  这里不套 encodeURIComponent：能走到这个名字的只有 `check_role_def` 放行过的合法节点名
   *  （字母开头、≤32、无空格/中文/标点），本来就是一整个安全路径段；套进模板反而让
   *  `s8 t3` 没法从 client.ts 反推出路径（它按 `${x}`→`{x}` 解析，函数调用不在它的语法里）。 */
  fireRole: (sid: string, name: string) =>
    req<Record<string, any>>('DELETE', `/api/sessions/${sid}/roles/${name}`),
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
      // 成本两桶（C12）：一笔调用只有一个币种在动，字段集必须与 runner 的 span 完全一致
      spans: { node: string; pt: number; ct: number; cost_usd: number; cost_cny: number;
               ts: number; t0: number | null; ft: number | null }[]
    }>('GET', `/api/sessions/${sid}/trace`),
  /** C11 时间旅行·列表：按 superstep 的历史快照摘要，新→旧。
   *  `before` 传上一页末条的 checkpoint_id（**开区间**上界），与 B2 的事件回放同一套翻页语义。 */
  checkpoints: (sid: string, before = '', limit = 80) =>
    req<{ checkpoints: { checkpoint_id: string; step: number; source: string; ts: string;
                         next: string[]; tasks: string[] }[];   // 09-25 删 writes：langgraph 的 metadata 里就没这个键（见 runner._ckpt_page）
         has_more: boolean; next_before: string; reason?: string }>(
      'GET', `/api/sessions/${sid}/checkpoints?before=${encodeURIComponent(before)}&limit=${limit}`),
  /** 单份超步的完整 state。超过后端上限回 413（调用方按 status 分流，别猜文案）。
   *  参数名刻意写成 `checkpoint_id`（不是 `cid`）：s8 t3 是按「模板段名 ≡ FastAPI 占位符名」比对形状的，
   *  名字对不上就报"前端消费了一条后端没有的路由"——这是它该管的。cid 也不 encode：
   *  它是 saver 给的 uuid7 串（只有 hex 与 `-`）。 */
  checkpointState: (sid: string, checkpoint_id: string) =>
    req<{ checkpoint_id: string; step: number; source: string; ts: string; next: string[];
         state: Record<string, unknown> }>(
      'GET', `/api/sessions/${sid}/checkpoints/${checkpoint_id}`)
}
