export interface Session {
  id: string
  idea: string
  project_name: string
  n_round: number
  paradigm: string // classic | dynamic | react（9.2 策略曲线）
  sop: string // 非空=按 N7 模板装配（扩展线）
  user_id: string // N1：创建者（auth 关恒 "default"）
  status: string // created | running | awaiting_human | stopping | finished | stopped | failed
  llm_override: Record<string, any>
  workspace: string
  error: string
  cost: Record<string, number>
  created_at: string
  started_at: string
  finished_at: string
  // F4 的 PATCH 路由提供；后端未升级前不会返回，故可选
  archived?: boolean
  pinned?: boolean
}

export interface WEvent {
  session_id: string
  seq: number
  cursor: string // 去重/续传唯一依据；seq 只做展示——Redis 总线上它超出 2^53
  ts: number
  kind: string // report | log | status | ask_human | error
  block: string | null
  uuid: string | null
  name: string | null
  value: any
  role: string | null
  extra: any
}

export interface Block {
  key: string
  type: string // Thought | Docs | Editor | Terminal | Task | Gallery | Browser | Notebook | ...
  role: string
  closed: boolean
  meta: any
  tokens: string[]
  doc: any
  obj: any
  lines: string[]
  cmd: string
  path: string
  url: string
  page: any
  raw: any[]
  /** 该块首个事件的 unix 秒（浮点）：轮次起始与时钟取它。 */
  ts?: number
  /** 首个 content 事件的 unix 秒：TTFT 的右端点，meta 事件不算。 */
  fts?: number
  /** 最近一个事件的 unix 秒：轮次收口时刻与「用时」的右端点。 */
  lastTs?: number
}

/** `/trace` 一行 = 一笔 LLM 调用的节点名与 token/成本增量。 */
export interface TraceSpan {
  node: string
  pt: number
  ct: number
  cost: number
  ts: number
  /** 派发时刻（unix 秒）。老 span 没有这个字段——缺就不显示 TTFT/耗时，不给 0。 */
  t0?: number | null
  /** 首 token 时刻（unix 秒）。structured 输出不走打字机，取不到即为 null。 */
  ft?: number | null
}

export interface FileNode {
  name: string
  path: string
  type: 'dir' | 'file'
  size?: number
  ext?: string
  children?: FileNode[]
}

export interface Health {
  ok: boolean
  llm_configured: boolean
  llm_problem: string
  model: string
  base_url: string
  api_key_masked: string
  auth_enabled: boolean // N1：PLATFORM__AUTH 开时登录页接管
  workspace_root: string
}
