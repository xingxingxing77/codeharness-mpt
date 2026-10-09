/**
 * 事件处置表（P4 路线三的第一份文件）：每一个上到 wire 的 kind / 子名都必须在这里登记三件事，
 * 少登记一个就在编译期红（下面的 `Record<Union, Disposal>` 是全查的），生产端漏登记由门禁 s8
 * 拿 `server/`+`codeharness/` 现 grep 出来的全集对账。
 *
 * 为什么要这张表：改前 `sessions.ts::applyEvent` 是一条 `else if` 链，链尾**没有 else**——
 * 于是「内核发了、前端没有分支」这件事在界面上完全静默。本仓现证过两条：
 *   · `kind="context" name="compact"`（`codeharness/roles/role_zero.py:287`）——链里没有 context 支，
 *     整条事件被无声吞掉，压缩在界面上看不见；
 *   · `name="local_url"`（`codeharness/report.py:256`，ServerReporter 的默认名）——掉进 `raw`，
 *     折叠行里连"有个本地服务地址"都没留下。
 * 这两条都不是渲染 bug，是**词表没有对账机制**的必然产物（参照系为此专门立了
 * `runtime/events/disposition.py` + 穷举测试 + 消费字段棘轮，账在它 `docs/03-AI核心/SSE事件与录制回放.md` §一）。
 *
 * 三个轴：
 *   lane  = 它折进哪条车道。`timeline`＝块与日志（可以整段重放，三条路共用同一个 reducer）；
 *           `runtime`＝实时槽（status/cost/待批/队列/票/目标），**只有活流能写**；
 *           `none`＝有意不上屏（留一句为什么）。
 *   src   = 刷新之后谁是权威。`journal`＝事件流自己回放就能重建；`rest`＝GET 快照才是权威
 *           （事件只是"变了"的信号）；`derived`＝同一份正文的另一种形态，权威是定稿那一份；
 *           `none`＝刷新后拿不回来（要么是事实缺失，要么是已知缺口，理由必须写在这）。
 *   why   = 一句事实，不许写"应该没问题"。
 */

export type Lane = 'timeline' | 'runtime' | 'none'
export type Source = 'journal' | 'rest' | 'derived' | 'none'

export interface Disposal {
  lane: Lane
  src: Source
  why: string
}

// ---- 词表本体（生产端全集，逐条对过码，不是凭印象列的）----

/** SSE 信封的 `kind`。来源：`server/`+`codeharness/` 里所有 `bus.publish(kind=…)` 与 `emit_event(…)`。 */
export const WIRE_KINDS = [
  'report', 'log', 'status', 'ask_human', 'approval', 'error', 'turn',
  'feedback', 'queue', 'goal', 'context', 'role'
] as const
export type WireKind = (typeof WIRE_KINDS)[number]

/** `kind=report` 的 `name`（块通道）。来源：`codeharness/report.py` 各 Reporter 的默认名 +
 *  `BlockReporter` 的方法名 + `live`/`end_marker` 由 `server/runner.py` 直接发。 */
export const REPORT_NAMES = [
  'meta', 'content', 'live', 'document', 'object', 'cmd', 'output',
  'path', 'url', 'page', 'local_url', 'end_marker'
] as const
export type ReportName = (typeof REPORT_NAMES)[number]

/** `kind=turn` 的 `value.reason.kind`（轮尾提示）。来源：`server/runner.py:1048,1077`。 */
export const TURN_REASONS = ['max-tokens', 'plan-unfinished'] as const
export type TurnReason = (typeof TURN_REASONS)[number]

/** `kind=turn` 的 `name`——轮尾那个信封本身。它和 reason 是两层：信封在 wire 上永远是 `end`，
 *  真正决定「上不上屏、上哪一行」的是 `value.reason.kind`。登记它是因为**生产端确实在发这配对**
 *  （`runner.py:1048` 那句 `kind="turn", name="end"`），s8 t32 拿这对账就是拿它当阳性的第一项。 */
export const TURN_NAMES = ['end'] as const
export type TurnName = (typeof TURN_NAMES)[number]

/** `kind=approval` 的 `name`。来源：`server/runner.py:1007`（requested）、`server/api/approvals.py:45`（resolved）。 */
export const APPROVAL_NAMES = ['requested', 'resolved'] as const
export type ApprovalName = (typeof APPROVAL_NAMES)[number]

/** `kind=queue` 的 `name`＝`ChatQueue.on_change` 的 action。消费端历史上认这三个。 */
export const QUEUE_NAMES = ['add', 'drain', 'remove'] as const
export type QueueName = (typeof QUEUE_NAMES)[number]

/** `kind=goal` 的 `name`＝operation。来源：`server/api/sessions.py:213-218` 的注释与调用点。 */
export const GOAL_NAMES = ['create', 'edit', 'complete', 'clear'] as const
export type GoalName = (typeof GOAL_NAMES)[number]

/** `kind=feedback` 的 `name`。来源：`server/api/sessions.py:406`（set）与 DELETE 口（clear，票值为空串）。 */
export const FEEDBACK_NAMES = ['set', 'clear'] as const
export type FeedbackName = (typeof FEEDBACK_NAMES)[number]

/** `kind=context` 的 `name`。来源：`codeharness/roles/role_zero.py:287`。 */
export const CONTEXT_NAMES = ['compact'] as const
export type ContextName = (typeof CONTEXT_NAMES)[number]

/** `kind=role` 的 `name`（C190 角色生命周期）。来源：`server/runner.py::_role_signal`
 *  （本体 `on_chain_start/end` 与内层节点的相位）。 */
export const ROLE_NAMES = ['started', 'phase', 'completed', 'paused'] as const
export type RoleName = (typeof ROLE_NAMES)[number]

/** 子名的复合键。写成模板串联合，是为了让下面那一张表**一次全查**：加一个取值不登记就 red。 */
export type SubKey =
  | `report:${ReportName}`
  | `turn:${TurnName}`
  | `turn:${TurnReason}`
  | `approval:${ApprovalName}`
  | `queue:${QueueName}`
  | `goal:${GoalName}`
  | `feedback:${FeedbackName}`
  | `context:${ContextName}`
  | `role:${RoleName}`

export const KIND_DISPOSAL: Record<WireKind, Disposal> = {
  report: { lane: 'timeline', src: 'journal', why: '块流是时间线的唯一构成；首屏、活流、「加载更早」三条路都走同一个 reducer' },
  log: { lane: 'timeline', src: 'journal', why: '右栏日志页读 `store.logs`（DetailsPanel.vue:23），重放能整段重建' },
  status: { lane: 'runtime', src: 'rest', why: '状态与用量的权威是 Session 记录（GET）；事件只让活流立刻跟上。回放旧状态会把当前盖回去——C91 那六支就是这个洞的形状' },
  ask_human: { lane: 'runtime', src: 'journal', why: 'C195（10-10 现场改判）：**这条不是缺口，是被 C188 顺手治好的**。原记 `src=\'none\'` 说的是「只有 POST /human-input、没有 GET 口 ⇒ 停在待答时刷新丢卡」，那是 else-if 链年代的读数。现证：一场真停在 `awaiting_human` 的会话冷开浏览器，卡照样在——而喂它的**不是**首屏那一屏（`/events/history?limit=400` 那扇窗里 `ask_human` 实测 0 条），是 `connect()` 拿 `after=0` 把保留窗口**整段重播**（同场实测 467 条 data 帧、含那条 ask_human）。所以刷新后的权威就是日志本身 ⇒ `journal`。**残余只有一条**：那一问被 `MAX_EVENTS_PER_SESSION=15000` 裁出保留窗口之后才会丢，而这不是开 GET 口能顺手解决的（真撞到了再说，账 `plan/frontend.md` §1.7 末）。' },
  approval: { lane: 'runtime', src: 'rest', why: 'GET /approvals 给 pending+decided 两份，是权威；事件只做按 id 增删的通知' },
  error: { lane: 'timeline', src: 'journal', why: '轮内红点行走块管线（B1）：顺序、按游标去重、回放都白拿，另开一份 errors 就是第二个游标' },
  turn: { lane: 'timeline', src: 'journal', why: '轮尾提示（截断/计划未完）锚在轮尾建合成块，与参照系同形状' },
  feedback: { lane: 'runtime', src: 'rest', why: '真值在 `Session.feedback`（B4）；事件只是让这一屏立刻亮起来' },
  queue: { lane: 'runtime', src: 'rest', why: '真值是服务端队列（GET /queue）；事件只按 id 增删，绝不整份覆盖（参照系对 `turn_queued` 的口径同）' },
  goal: { lane: 'runtime', src: 'rest', why: '真值在 Session 记录里，GET 就拿得到；这条只让活流跟上' },
  context: { lane: 'timeline', src: 'journal', why: 'P4 路线三接上的那一格：压缩是发生过的事实，要在时间线留一行（`Compact` 合成块）' },
  role: { lane: 'timeline', src: 'journal', why: 'C190：一个角色这一趟的「开始 / 正在干什么 / 收工 / 停下等人」折成一颗 `RoleLane` 块——整本重折能复原“谁在跑、跑到哪一步被打断或被谁挂起”，所以它是日志能重放的那一类，不是只活在连接上的通知' }
}

export const SUB_DISPOSAL: Record<SubKey, Disposal> = {
  // ---- report 通道：块生命周期 ----
  'report:meta': { lane: 'timeline', src: 'journal', why: '开块 + 块头事实（工具名/参数摘要/prose_fields 名单）' },
  'report:content': { lane: 'timeline', src: 'journal', why: '内核定稿正文，落 `tokens' },
  'report:live': { lane: 'timeline', src: 'derived', why: '打字机逐片只服务"正在写"这一态：定稿一到就整段撤掉，重放缺它也不丢字（正文在 tokens 里）' },
  'report:document': { lane: 'timeline', src: 'journal', why: '产物 {filename, content}，Docs 块整块一次性' },
  'report:object': { lane: 'timeline', src: 'journal', why: '计划卡那类整份对象；同 (块,角色) 收敛成一颗（runner.py:796 C175）' },
  'report:cmd': { lane: 'timeline', src: 'journal', why: 'Terminal 块的命令行' },
  'report:output': { lane: 'timeline', src: 'journal', why: 'Terminal 逐行输出，落 `lines' },
  'report:path': { lane: 'timeline', src: 'journal', why: '文件绝对路径（产物链接与 Editor 台账都读它）' },
  'report:url': { lane: 'timeline', src: 'journal', why: 'Browser 块的请求地址' },
  'report:page': { lane: 'timeline', src: 'journal', why: '{page_url,title,screenshot}（report.py:241 拼的形状）' },
  'report:local_url': { lane: 'timeline', src: 'journal', why: 'ServerReporter（BROWSER_RT）部署出来的本机地址——改前掉进 `raw` 看不见，现在与 url 同一格' },
  'report:end_marker': { lane: 'timeline', src: 'journal', why: '收口：closed=true，是轮尾行与流式尾标的判据。孤立收口（uuid 没开过块）丢弃，不凭空造空块' },

  // ---- turn：轮尾信封与它的两种提示 ----
  // `kind=turn` 在 wire 上永远是 `name="end"`，真正分流的是 `value.reason.kind`（runner.py:1048,1077）。
  // 这一格必须登记：s8 t32 拿生产端的 (kind,name) 对来对账，漏它就是「发了但没人接」的形状。
  'turn:end': { lane: 'timeline', src: 'journal', why: '轮尾信封本身不建块，落点在 reason.kind 上选；没有 reason 的 turn 由 reducer 喊出来，不安静地什么都不发' },
  'turn:max-tokens': { lane: 'timeline', src: 'journal', why: '这一跑有步撞了输出上限（runner.py:1048 按记账次数发）' },
  'turn:plan-unfinished': { lane: 'timeline', src: 'journal', why: '收工时 Plan 还有没做完的条目（runner.py:1077 读图里那本状态机）' },

  // ---- runtime 车道：四个 src=rest 的槽 ----
  'approval:requested': { lane: 'runtime', src: 'rest', why: '按 id 入队，重放/双开标签页不该冒出两张一样的卡' },
  'approval:resolved': { lane: 'runtime', src: 'rest', why: '只出队；会话回到什么状态由紧随的 status 说，这里不猜（C144 另重拉一次拿"谁批的"）' },
  'queue:add': { lane: 'runtime', src: 'rest', why: '按 id 增，不整份覆盖' },
  'queue:drain': { lane: 'runtime', src: 'rest', why: '按 id 删（route 取走了）' },
  'queue:remove': { lane: 'runtime', src: 'rest', why: '按 id 删（用户撤回）' },
  'goal:create': { lane: 'runtime', src: 'rest', why: '目标文本落 Session，事件只让活流跟上' },
  'goal:edit': { lane: 'runtime', src: 'rest', why: '同上' },
  'goal:complete': { lane: 'runtime', src: 'rest', why: '同上（done_at 也在这条上）' },
  'goal:clear': { lane: 'runtime', src: 'rest', why: '空串不是"没带值"，是"清掉"，必须照收' },
  'feedback:set': { lane: 'runtime', src: 'rest', why: '按尾行键写这一份投影' },
  'feedback:clear': { lane: 'runtime', src: 'rest', why: '票值为空串 ⇒ 删键' },

  // ---- role：C190 的角色生命周期（车道）----
  'role:started': { lane: 'timeline', src: 'journal', why: '开一条车道：uuid=`lane-<n>`（现证 `run_id` 整场共用，当不了配对键，所以 uuid 由 runner 自己攒），meta 带角色与初始相位' },
  'role:phase': { lane: 'timeline', src: 'derived', why: '只改同一颗车道的相位文本；权威是收口那一颗（相位是「此刻在干什么」的另一种形态，收工后无意义，只有被打断时它才是留下的痕迹）' },
  'role:completed': { lane: 'timeline', src: 'journal', why: '收口 + 用时；`aborted=True` 是「没跑完」（取消/异常走不到 `on_chain_end`，由 `runner._close_lanes` 在散会时补发），界面据此不许画成正常收工' },
  'role:paused': { lane: 'timeline', src: 'journal', why: 'C193（真模型活体现证）：这一段是**停下来等人**（`park`=approval|question），不是没跑完也不是收工。图停在待批处时跑图那个循环同样退出，走的是同一个散场清扫——原来只有 `aborted` 一支，于是跑到 `finished` 的会话 12 颗收口有 6 颗写「没跑完」。**这一支不带 `ms`**：那值是「开行到散场」的墙钟差，停车时常见几十毫秒，跟在角色名后面会读成「他只跑了 46 毫秒」' },

  // ---- context ----
  'context:compact': { lane: 'timeline', src: 'journal', why: '{before,after,freed,evicted_n,real_peak_pt}；0 在 real_peak_pt 上不是读数（没账本时记 0），渲染要分清' }
}

/** 复合键：`kind` 加它自己的子名（report 用 `name`、turn 用 `value.reason.kind`，其余用 `name`）。
 *  turn 没有 reason 时退回裸 kind——那条会在 reducer 里喊（处置表说它走时间线，落点却找不到），
 *  不许静默折成「什么都没有」。 */
export function subKeyOf(kind: string, name: string | null | undefined, reasonKind?: string): string {
  if (kind === 'turn' && reasonKind) return `turn:${reasonKind}`
  return name ? `${kind}:${name}` : kind
}

/** 查处置，认不出来返回 null（调用方决定是喊出来还是跳过——`foldOne` 喊，去重层不喊）。 */
export function disposalOf(kind: string, name?: string | null, reasonKind?: string): Disposal | null {
  const k = subKeyOf(kind, name, reasonKind)
  if (KIND_DISPOSAL[k as WireKind]) return KIND_DISPOSAL[k as WireKind]
  return SUB_DISPOSAL[k as SubKey] || null
}

/** 给门禁与自检用：把两张表摊平成同一个键空间。 */
export function declaredKeys(): string[] {
  const out: string[] = [...WIRE_KINDS]
  for (const n of REPORT_NAMES) out.push(`report:${n}`)
  for (const n of TURN_NAMES) out.push(`turn:${n}`)
  for (const r of TURN_REASONS) out.push(`turn:${r}`)
  for (const n of APPROVAL_NAMES) out.push(`approval:${n}`)
  for (const n of QUEUE_NAMES) out.push(`queue:${n}`)
  for (const n of GOAL_NAMES) out.push(`goal:${n}`)
  for (const n of FEEDBACK_NAMES) out.push(`feedback:${n}`)
  for (const n of CONTEXT_NAMES) out.push(`context:${n}`)
  for (const n of ROLE_NAMES) out.push(`role:${n}`)
  return out
}
