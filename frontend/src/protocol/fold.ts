/**
 * 时间线/实时槽的**唯一 reducer**（P4 路线三）。
 *
 * 三条路必须走同一个函数，这是本件的全部意义：
 *   ① 活流逐条推 `foldOne(st, ev)`；
 *   ② 首屏 `resetFold` + `foldAll(st, 第一屏事件)`；
 *   ③ 「加载更早」把整页**前插进同一本日志**，然后 `resetFold` + `foldAll(st, 全量日志)`。
 * 改前 ③ 是另一份手写合并（`mergeEarlierPage`：tokens/live/lines/raw 逐字段 concat、ts 谁覆盖谁、
 * 单值字段「留正片那份」），并且要靠 `PAGE_REPLAY_OPAQUE` 名单挡住六支实时槽被历史值盖回去
 * （C91）。两份代码就是两个真相：页边界切开的那一块只能靠人记得先排老半截，新加一个写实时状态的
 * kind 只要忘进名单，「加载更早」就会把它重新执行一遍。整本日志按序重折之后，「最新的赢」是
 * **构造出来的**，不需要名单，也不需要那份手写合并——所以两个都删了。
 *
 * 为什么 reducer 直接写 pinia 的 state 而不是「折成快照再发布」：Vue 的响应式在**深层 proxy** 上
 * 才通知，`{...blocks}` 换的是容器、块对象身份不变，正在流式的那一行的 `props.b` 就不会触发重渲染
 * （参照系那边要 copy-on-write 是 React 按身份复用的代价，不是这里的）。所以 st 就是 store 本身。
 */
import type { ApprovalItem, Block, QueueItem, WEvent } from '../types.ts'
import { disposalOf, subKeyOf } from './disposition.ts'

const MAX_LOGS = 800

/** reducer 写的那一份状态。store 的 state 必须结构上满足它（s8 的门禁格钉住这份契约）。 */
export interface FoldState {
  blocks: Record<string, Block>
  blockOrder: string[]
  logs: string[]
  status: string
  cost: Record<string, number>
  humanQuestion: WEvent | null
  approvals: ApprovalItem[]
  queue: QueueItem[]
  feedback: Record<string, string>
  /** 事件主张过的会话记录字段（B5 目标 / 状态 / 用量）。store 每批折完一次性套回记录，
   *  不再逐事件 `mergeSessionLocal`——那是同一个真相的第二本账。 */
  sessionPatch: Record<string, any>
  /** 处置表里查不到的 kind/name：喊出来 + 留痕，绝不静默吞。不渲染（参照系 dispatch 对未知
   *  type 也是 warn+drop），红的那一半交给门禁：生产端全集必须在表里。 */
  unhandled: string[]
  /** reducer 不许自己发请求，但「批完一张卡要重拉权威回执」「收口才重拉 trace」这两件是
   *  事实而非渲染。折的时候只记口令，调用方一批取走一次（store 的 `runEffects`）。 */
  effects: string[]
}

export function resetFold(st: FoldState) {
  st.blocks = {}
  st.blockOrder = []
  st.logs = []
  st.sessionPatch = {}
  st.unhandled = []
  st.effects = []
  // 只清「日志自己折得出来」的那几样。剩下这些的基线来自 GET，重折只做覆盖：
  //   status / cost / approvals / queue / feedback / humanQuestion
  // 为什么 `queue`、`feedback` 也只做覆盖（不清空）：它们的权威是 GET 那份快照，而事件窗口是
  // **裁头**的（`MAX_EVENTS_PER_SESSION` 丢最旧）——某一枚还排着的插话，它的 `add` 可能早就被裁掉，
  // 清空重折就会让这枚胶囊从界面上消失（用户撤不掉它、也看不见它）。覆盖的最坏结果只是
  // 队列的**显示次序**不按游标（基线在前、老页的 add 补在后面），不是少一条。
  // `humanQuestion` 同理：它连 GET 口都没有（处置表里 `src='none'` 记着那条缺口）。
}

function newBlock(ev: WEvent): Block {
  return {
    key: ev.uuid || `e${ev.cursor || ev.seq}`,
    type: ev.block || 'System',
    role: ev.role || '',
    closed: false,
    meta: null,
    tokens: [],
    live: [],
    doc: null,
    obj: null,
    lines: [],
    cmd: '',
    path: '',
    url: '',
    page: null,
    raw: []
  }
}

/** 合成一行块（error / 轮尾提示 / 压缩），与真块同一条管线：顺序、按游标去重、
 *  回放、「加载更早」全白拿；另开一份数组就是第二个游标（B1 那条注释说的正是这个）。
 *  `closed` 必须真：末块没收口就不发轮次尾行。 */
function synthBlock(st: FoldState, ev: WEvent, type: string, meta: any = null): Block | null {
  const key = ev.uuid || `e${ev.cursor || ev.seq}`
  const exist = st.blocks[key]
  if (exist) return exist
  const b = newBlock(ev)
  b.type = type
  b.closed = true
  b.meta = meta
  if (typeof ev.ts === 'number') {
    b.ts = ev.ts
    b.lastTs = ev.ts
  }
  st.blocks[key] = b
  st.blockOrder.push(key)
  return b
}

function pushLog(st: FoldState, line: string) {
  st.logs.push(line)
  if (st.logs.length > MAX_LOGS) st.logs.splice(0, st.logs.length - MAX_LOGS)
}

/** 喊「这一笔上屏了但没有落点」的唯一一处：记进 `unhandled`（探针与门禁读它）+ console.error，
 *  但**不断流**——一条看不懂的事件不该把整场对话换成一屏红。真正拦住它出门的是 s8 那一格。 */
function loud(st: FoldState, tag: string) {
  if (!st.unhandled.includes(tag)) st.unhandled.push(tag)
  console.error(`[fold] ${tag} 没有落点 —— 补 protocol/fold.ts 与 plan/frontend.md`)
}

/** 取到（或建出）这一笔 report 的落点块。 */
function target(st: FoldState, ev: WEvent): Block {
  const key = ev.uuid || `e${ev.cursor || ev.seq}`
  let b = st.blocks[key]
  if (!b) {
    b = newBlock(ev)
    st.blocks[key] = b
    st.blockOrder.push(key)
  }
  if (typeof ev.ts === 'number') {
    if (b.ts === undefined) b.ts = ev.ts
    b.lastTs = ev.ts
    if (ev.cursor) b.endCursor = ev.cursor       // B3：分叉点要按块拿游标
    if ((ev.name === 'content' || ev.name === 'live') && b.fts === undefined) b.fts = ev.ts
  }
  return b
}

export function foldAll(st: FoldState, evs: WEvent[]) {
  for (const ev of evs) foldOne(st, ev)
}

export function foldOne(st: FoldState, ev: WEvent) {
  const key = subKeyOf(ev.kind, ev.name, (ev.value as any)?.reason?.kind)
  const d = disposalOf(ev.kind, ev.name, (ev.value as any)?.reason?.kind)
  if (!d) {
    // 认不出来：喊一次并记进 `unhandled`，游标照推（这一条不许把整场拖死，也不许当没发生）。
    loud(st, key)
    return
  }
  if (d.lane === 'timeline') foldTimeline(st, ev, key)
  else foldRuntime(st, ev, key)
}

// ---------------------------------------------------------------- 时间线车道

function foldTimeline(st: FoldState, ev: WEvent, key: string) {
  if (ev.kind === 'report') {
    // 孤立收口标记（uuid 从未开过块）直接丢：否则下面会凭空创建一个空块
    if (ev.name === 'end_marker' && ev.uuid && !(ev.uuid in st.blocks)) return
    const b = target(st, ev)
    switch (ev.name) {
      case 'meta':
        b.meta = ev.value
        break
      case 'content':
        // 内核定稿到了：在飞的逐片整段撤掉（两份内容同屏是本仓要治的重复，不是过渡态）。
        if (b.closed) b.closed = false
        b.live = []
        b.tokens.push(String(ev.value ?? ''))
        break
      case 'live':
        // 打字机逐片：后端 `server/runner.py::_translate` 从 LLM 流里抽出的散文，只进 `live`，
        // 绝不进 `tokens`——所以定稿来了直接清空，不需要任何「比对是不是同一句话」的猜。
        if (b.closed) b.closed = false
        b.live.push(String(ev.value ?? ''))
        break
      case 'document':
        b.doc = ev.value
        break
      case 'object':
        b.obj = ev.value
        break
      case 'cmd':
        b.cmd = String(ev.value ?? '')
        break
      case 'output':
        b.lines.push(String(ev.value ?? ''))
        break
      case 'path':
        b.path = String(ev.value ?? '')
        break
      // url 与 local_url 落同一格：一个块要么有请求地址要么有部署地址，折叠行读的都是 `.url`
      // （改前 `local_url` 掉进 `raw`，ServerReporter 发出来的地址在界面上根本不存在）。
      case 'url':
      case 'local_url':
        b.url = String(ev.value ?? '')
        break
      case 'page':
        b.page = ev.value
        break
      case 'end_marker':
        b.closed = true
        break
      default:
        // 词表里登记过、但块没有对应字段的 name（今天没有这种）才走到这儿——留痕不静默。
        b.raw.push({ name: ev.name, value: ev.value })
    }
    return
  }
  if (ev.kind === 'log') {
    pushLog(st, String(ev.value ?? ''))
    return
  }
  if (ev.kind === 'error') {
    pushLog(st, `[error] ${ev.value}`)
    const b = synthBlock(st, ev, 'Error')
    if (b && b.lines.length === 0) {
      b.lines = String(ev.value ?? '').split('\n')
      b.code = ev.code ?? null      // C182：死因码进块，ChatNode 按它开第三列；旧事件没有就是 null
    }
    return
  }
  if (ev.kind === 'turn') {
    const reason = (ev.value as any)?.reason?.kind
    if (reason === 'max-tokens') {
      synthBlock(st, ev, 'MaxTokens')         // 不是 BlockType：t1 查不到，靠 s8 的文案格钉住
    } else if (reason === 'plan-unfinished') {
      const r = (ev.value as any).reason
      synthBlock(st, ev, 'PlanOpen', { open: Number(r?.open) || 0, total: Number(r?.total) || 0 })
    } else {
      // 没有 reason 的 turn 事件（或表里没登记的取值）不许安静地什么都不发。
      loud(st, key)
    }
    return
  }
  if (ev.kind === 'context') {
    // P2 那一格补上的渲染：内核报「压缩过、从多少压到多少」，界面上此前什么都没有。
    const v = (ev.value || {}) as Record<string, any>
    const b = synthBlock(st, ev, 'Compact', {
      before: Number(v.before) || 0,
      after: Number(v.after) || 0,
      freed: Number(v.freed) || 0,
      evicted_n: Number(v.evicted_n) || 0,
      // `real_peak_pt` 的 0 是「这一发还没账本」，不是读数（role_zero.py:280 的口径）——
      // 折的时候就把这件事做成布尔，渲染层不用再去猜 0 的含义。
      hasPeak: Number(v.real_peak_pt) > 0,
      real_peak_pt: Number(v.real_peak_pt) || 0
    })
    if (b) b.role = ev.role || b.role
    return
  }
  if (ev.kind === 'role') {
    // C190：一个角色这一趟的车道 = 一颗 `RoleLane` 块（`started` 开、`phase` 改相位文本、
    // `completed` 收口带 ms/aborted）。uuid 是 runner 攒的 `lane-<n>`——现证整场 `on_chain_*`
    // 共用一个 `run_id`，当不了配对键（`server/runner.py::_role_signal` 那段记了现证）。
    // 用它自己的 `target()`：车道行与块流同一条管线，「加载更早」整本重折自然把车道复原。
    const v = (ev.value || {}) as Record<string, any>
    const b = target(st, ev)
    const role = String(v.role || ev.role || b.role || '')
    b.role = role
    if (ev.name === 'started') {
      b.closed = false
      b.meta = { role, phase: String(v.phase || ''), ms: 0, aborted: false }
    } else if (ev.name === 'phase') {
      b.meta = { ...(b.meta || { role }), phase: String(v.phase || '') }
    } else {
      b.meta = { ...(b.meta || { role }), phase: '', ms: Number(v.ms) || 0, aborted: !!v.aborted }
      b.closed = true                  // 不收口就永远「在跑」：取消/异常那条路由 runner._close_lanes 补
    }
    return
  }
  // 处置表把这条判到了 timeline，但这里没有分支＝新加了一个「有处置、无落点」的 kind。
  loud(st, key)
}

// ------------------------------------------------------------------ 实时车道

function foldRuntime(st: FoldState, ev: WEvent, key: string) {
  if (ev.kind === 'status') {
    const v = ev.value || {}
    // B1：`{}` 在 JS 里是**真值**。后端读不到账本时会发一个空 cost，旧写法 `if (v.cost)`
    // 于是把顶栏金额覆盖成 0，而刷新（GET 拿落盘记录）又跳回批准前那个数——假读数。
    // 空对象是「这一发没带账本」，不是「账本是 0」。
    const hasCost = !!v.cost && Object.keys(v.cost).length > 0
    if (hasCost) {
      st.cost = v.cost
      st.sessionPatch.cost = v.cost
    }
    if (v.message) pushLog(st, `[status] ${v.message}`)
    if (v.status) {
      st.status = v.status
      st.sessionPatch.status = v.status
      if (['finished', 'stopped', 'failed'].includes(v.status)) st.effects.push('loadTrace')
    }
    if (v.status !== 'awaiting_human') st.humanQuestion = null
    return
  }
  if (ev.kind === 'ask_human') {
    st.humanQuestion = ev
    st.status = 'awaiting_human'
    st.sessionPatch.status = 'awaiting_human'
    return
  }
  if (ev.kind === 'approval') {
    // requested 按 id 去重入队（重放/双开标签页不该冒出两张一样的卡）；
    // resolved 只出队——之后会话回到什么状态由紧随其后的 status 事件说，这里不猜。
    const item = (ev.value || {}) as ApprovalItem
    if (ev.name === 'resolved') {
      st.approvals = st.approvals.filter((a) => a.id !== item.id)
      // C144：resolved 的载荷只有 {id,outcome}，不带 who/when——重拉一次拿权威回执。
      st.effects.push('loadApprovals')
    } else if (item.id && !st.approvals.some((a) => a.id === item.id)) {
      st.approvals.push(item)
      st.status = 'awaiting_human'
      st.sessionPatch.status = 'awaiting_human'
    }
    return
  }
  if (ev.kind === 'queue') {
    // B6：三个动作都只**按 id 增删**这一份投影，绝不整份覆盖——覆盖会把「add 到一半、
    // GET 还没回来」的中间态抹掉，那是第二个游标的老病。
    const items = ((ev.value as any)?.items || []) as QueueItem[]
    if (ev.name === 'add') {
      for (const it of items) if (!st.queue.some((q) => q.id === it.id)) st.queue.push(it)
    } else {
      st.queue = st.queue.filter((q) => !items.some((i) => i.id === q.id))
    }
    return
  }
  if (ev.kind === 'feedback') {
    // B4：按尾行键增删这一份投影（`clear` 的 vote 是空串 ⇒ 删键）。
    const v = (ev.value || {}) as { key?: string; vote?: string }
    if (!v.key) return
    const next = { ...st.feedback }
    if (v.vote) next[v.key] = v.vote
    else delete next[v.key]
    st.feedback = next
    return
  }
  if (ev.kind === 'goal') {
    // B5：真值在 Session 记录里，这条只让活流立刻跟上；`clear` 的空串是「清掉」，必须照收。
    const v = (ev.value || {}) as { objective?: string; done_at?: string }
    st.sessionPatch.goal = v.objective || ''
    st.sessionPatch.goal_done_at = v.done_at || ''
    return
  }
  loud(st, key)
}
