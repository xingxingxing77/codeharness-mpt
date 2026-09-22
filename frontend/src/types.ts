// 票值两态（裸串=09-23 之前的旧票、`{v,at}`=新票）的归一规则在 `utils/votes.ts::parseVote`，
// 类型也从那一份引——两处各写一套就会长出「后端说有票、图标说不亮」。
import type { VoteEntry } from './utils/votes'

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
  // 装配出口回填（runner._prepare）：直聊下拉只渲染 roles，空目标走 entry_role
  roles?: string[]
  entry_role?: string
  // 工具审批的免审档：readonly | workspace_write | full_access（判定表 codeharness/tools/_approval.py）
  permission?: string
  workspace: string
  error: string
  // B5 会话目标：单条、由用户在建会话/目标条里填；完成只由用户点确认（模型无写入口）
  goal?: string
  goal_done_at?: string
  cost: Record<string, number>
  // B4：尾行键 → 票。真值在后端 `Session.feedback`（`_JSON_FIELDS` 那份），
  // `GET /api/sessions` 回的就是整份 model_dump，所以用量页的聚合不需要新端点（`utils/stats.ts::countVotes`）。
  // 值**不是** `string`：新票带时刻，按 `string` 声明会让读侧把 `{v,at}` 数成「未识别」（09-23 活体读数量出来的）。
  feedback?: Record<string, VoteEntry>
  created_at: string
  started_at: string
  finished_at: string
  // F4 的 PATCH 路由提供；后端未升级前不会返回，故可选
  archived?: boolean
  pinned?: boolean
}

/** gate 节点登记的待批项（platforms/approval_store.new_item 的形状）。
 *  outcome 只在 decided 列表里出现——pending 项还没有结论。 */
export interface ApprovalItem {
  id: string
  tool: string
  kind: string // tool | action
  args_preview: string
  reason: string
  tier_required: string
  tier_session: string
  node: string
  ts: number
  outcome?: string
}

export interface WEvent {
  session_id: string
  seq: number
  cursor: string // 去重/续传唯一依据；seq 只做展示——Redis 总线上它超出 2^53
  ts: number
  kind: string // report | log | status | ask_human | approval | error | turn
  block: string | null
  uuid: string | null
  name: string | null
  value: any
  role: string | null
  extra: any
}

/** B6 排队中的插话。`id` 只为「撤回这一条」存在（route 取走后就不再出现在队列里）。 */
export interface QueueItem {
  id: string
  content: string
  send_to: string
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
  /** B3：最后一个落到这块上的事件游标（分叉点取它）。只在内存里，不进任何接口响应。 */
  endCursor?: string
}

/** `/trace` 一行 = 一笔 LLM 调用的节点名与 token/成本增量。
 *  成本是**两桶**（C12）：一笔调用只会有一个币种在动，但两桶都发——把它们加成一个是
 *  这次修掉的口径错误，前端也就没处判断该印 ¥ 还是 $。 */
export interface TraceSpan {
  node: string
  pt: number
  ct: number
  cost_usd: number
  cost_cny: number
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
  auth_enabled: boolean // N1：PLATFORM__AUTH_ENABLED 开时登录页接管
  workspace_root: string
}
