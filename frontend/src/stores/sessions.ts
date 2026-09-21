import { defineStore } from 'pinia'
import { api, getToken } from '../api/client'
import type { ApprovalItem, Block, Health, QueueItem, Session, TraceSpan, WEvent } from '../types'

const MAX_LOGS = 800
/** 一屏的事件条数（B2）。单位是**事件**不是块——一块会合并整个流式节点的几十上百条
 *  token 事件，按块定页数就会把首屏撑回「一次吞完整条流」。 */
const HISTORY_PAGE = 400

function newBlock(ev: WEvent): Block {
  return {
    key: ev.uuid || `e${ev.cursor || ev.seq}`,
    type: ev.block || 'System',
    role: ev.role || '',
    closed: false,
    meta: null,
    tokens: [],
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

export const useSessionStore = defineStore('sessions', {
  state: () => ({
    health: null as Health | null,
    /** /api/models 的目录。modelsOk=false 表示端点不给列表，模型位退化成只读文本 */
    models: [] as string[],
    modelsOk: false,
    sessions: [] as Session[],
    currentId: '',
    blocks: {} as Record<string, Block>,
    blockOrder: [] as string[],
    logs: [] as string[],
    humanQuestion: null as WEvent | null,
    /** 当前会话的待批项（批次36）。SSE `approval` 事件驱动，切会话时 GET 补一次——
     *  刷新页面不该把已经挂着的审批弄没。 */
    approvals: [] as ApprovalItem[],
    /** B6：排着还没被 route 取走的插话。唯一真值仍是服务端队列，这里只是投影
     *  （开/切会话从 GET /queue 取一次，之后靠 kind=queue 事件跟着走）。 */
    queue: [] as QueueItem[],
    status: '',
    cost: {} as Record<string, number>,
    /** 活流三态（B1 断线横幅的唯一状态源）。刻意只有三值：参考项目 ConnectionBanner 的原子
     *  契约是「null / 首握手中的 connecting 保持安静，只有真在退避重连才现身」，所以
     *  idle 覆盖「没这条流」与「刚 new 出来还没 onopen」两种，open 过之后报错才进 down。
     *  ⚠ 别改成从 `evtSource.readyState` 直接派生：EventSource 实例不是 plain object，
     *  Vue 不 proxy 它，readyState 变了 computed 不会重算——实测那样横幅会在首屏亮起来
     *  且再也不消失（活后端下也一样），见 log/2026-09-21-B1断线横幅与轮内error行.md 段 3。 */
    stream: 'idle' as 'idle' | 'open' | 'down',
    evtSource: null as EventSource | null,
    /** 主游标：定宽补零串，字典序==到达序。 */
    lastCursor: '',
    /** B2 反向分页：`earliestCursor`=已加载的最老一条事件游标（下一次「加载更早」的 before），
     *  `hasMoreEarlier` 由服务端 has_more 给（决定胶囊显隐），`loadingEarlier` 只防连点。 */
    earliestCursor: '',
    hasMoreEarlier: false,
    loadingEarlier: false,
    /** 退化路径：后端未带 cursor 时才用 seq。Redis 总线的 seq≈1.79e18 过 JSON.parse
     *  会舍入到 ulp=256，同毫秒内上千事件塌缩成几个值，拿它去重会吞事件。 */
    lastSeq: 0,
    /** 合帧缓冲：SSE 事件先到这里，一帧结算一批（见 scheduleFlush） */
    pending: [] as WEvent[],
    /** /trace 的 LLM 调用记录：轮次尾行的 tok/s 与 StatsLine 按时间窗取用它 */
    spans: [] as TraceSpan[],
    flushHandle: undefined as { raf?: number; timer?: unknown } | undefined,
    busy: false
  }),

  getters: {
    current(state): Session | undefined {
      return state.sessions.find((s) => s.id === state.currentId)
    },
    blockList(state): Block[] {
      return state.blockOrder.map((k) => state.blocks[k]).filter(Boolean)
    },
    isRunning(state): boolean {
      return ['running', 'awaiting_human', 'stopping'].includes(state.status)
    },
    /** 队首那条占审批卡：状态可以并行多条，界面一次只占一个座位（照参考项目 apply.ts:108-109）。 */
    pendingApproval(state): ApprovalItem | null {
      return state.approvals[0] || null
    },
    hasPendingApproval(state): boolean {
      return state.approvals.length > 0
    }
  },

  actions: {
    async init() {
      this.health = await api.health()
      await this.loadSessions()
      // 模型目录是旁路信息：端点不给也不能把首屏拖挂，loadModels 自己吞错
      void this.loadModels()
      // 不再自动选中会话：落地页显示"我们应该构建什么"首页（参考 OpenHarness 布局）
    },

    async loadModels() {
      try {
        const r = await api.listModels()
        this.models = r.models || []
        this.modelsOk = !!r.ok
      } catch {
        this.models = []
        this.modelsOk = false
      }
    },

    goHome() {
      this.currentId = ''
      this.resetStream()
      this.status = ''
    },

    /** 会话列表按 project_name 分组：有项目名的进"项目"区，其余进"对话"区 */
    projects(): { name: string; chats: Session[] }[] {
      const map = new Map<string, Session[]>()
      for (const s of this.sessions) {
        const p = s.project_name || ''
        if (!p) continue
        if (!map.has(p)) map.set(p, [])
        map.get(p)!.push(s)
      }
      return [...map.entries()].map(([name, chats]) => ({ name, chats }))
    },

    looseChats(): Session[] {
      return this.sessions.filter((s) => !s.project_name)
    },

    async loadSessions() {
      this.sessions = await api.listSessions()
    },

    async createSession(payload: {
      idea: string
      project_name?: string
      n_round?: number
      paradigm?: string
      llm?: Record<string, any>
      permission?: string
    }): Promise<Session> {
      const s = await api.createSession(payload)
      await this.loadSessions()
      this.select(s.id)
      return s
    },

    select(sid: string) {
      if (this.currentId === sid) return
      this.currentId = sid
      this.resetStream()
      const s = this.sessions.find((x) => x.id === sid)
      this.status = s?.status || ''
      this.cost = s?.cost || {}
      // B2：首屏只拉「最新一屏」再开活流。原先直接 connect()，服务端会把保留窗口里
      // 那 5000 条整段回放完才活推——首屏时间与事件数成正比，且「加载更早」永远没内容。
      void this.loadFirstPage(sid)
      void this.loadTrace(sid)
      void this.loadApprovals(sid)
      this.queue = []            // 上一场的排队胶囊不许挂在这一场名下（同「第二个游标」那族洞）
      void this.loadQueue(sid)
    },

    async loadQueue(sid: string) {
      try {
        const r = await api.queue(sid)
        if (sid === this.currentId) this.queue = (r.items || []) as QueueItem[]
      } catch {
        /* 停着的会话没有队列：留空就是正确答案，不拿假数据填 */
      }
    },

    /** 拉最新一屏历史。成功后 lastCursor 落在页尾，connect() 从那儿只收增量。 */
    async loadFirstPage(sid: string) {
      try {
        const r = await api.eventHistory(sid, { limit: HISTORY_PAGE })
        if (sid !== this.currentId) return          // 连点两个会话：慢的那次不许把别人的块画进来
        for (const ev of r.events) this.applyEvent(ev)
        this.earliestCursor = r.events.length ? r.events[0].cursor : ''
        this.hasMoreEarlier = !!r.has_more
      } catch {
        /* 历史拉不到就退回老行为：connect() 里 after='' 会让服务端整段回放 */
      }
      if (sid === this.currentId) this.connect()
    },

    /** 「加载更早」：取 earliestCursor 之前的一屏拼到最前，返回**新出现**的块数
     *  （0 = 没动静，视图据此决定要不要补滚动锚点）。失败静默：has_more 保持原样，
     *  胶囊留在原地，用户能再点一次。 */
    async loadEarlier(): Promise<number> {
      if (!this.hasMoreEarlier || this.loadingEarlier || !this.earliestCursor) return 0
      this.loadingEarlier = true
      const sid = this.currentId
      try {
        const r = await api.eventHistory(sid, { before: this.earliestCursor, limit: HISTORY_PAGE })
        if (sid !== this.currentId) return 0
        this.hasMoreEarlier = !!r.has_more
        if (!r.events.length) return 0
        const beforeCount = this.blockOrder.length
        this.mergeEarlierPage(r.events)
        this.earliestCursor = r.events[0].cursor
        return this.blockOrder.length - beforeCount
      } catch {
        return 0
      } finally {
        this.loadingEarlier = false
      }
    },

    /** 整页往前拼（B2）。借道 applyEvent：先把这一页在**临时状态**里合成块（页内升序合并
     *  走的就是原来那份 switch），再拼到正片最前。两个坑：
     *  ① 被页边界切开的那一块，早半截必须排在已有的晚半截**前面**——直接 applyEvent 到正片
     *     会变成 `tokens.push`，把这块的文字顺序颠倒；
     *  ② lastCursor / lastSeq 不许往回走：SSE 续推位与 seq 去重都靠单调，回退会让活流重播整页。 */
    mergeEarlierPage(evs: WEvent[]) {
      const liveBlocks = this.blocks
      const liveOrder = this.blockOrder
      const liveCursor = this.lastCursor
      const liveSeq = this.lastSeq
      const liveLogs = this.logs
      this.blocks = {}
      this.blockOrder = []
      this.logs = []
      this.lastCursor = ''
      this.lastSeq = 0
      for (const ev of evs) this.applyEvent(ev)
      const pageBlocks = this.blocks
      const pageOrder = this.blockOrder
      const pageLogs = this.logs
      this.blocks = liveBlocks
      this.blockOrder = liveOrder
      this.logs = liveLogs
      this.lastCursor = liveCursor
      this.lastSeq = liveSeq

      const fresh: string[] = []
      for (const k of pageOrder) {
        const head = pageBlocks[k]
        const tail = liveBlocks[k]
        if (!tail) {
          liveBlocks[k] = head
          fresh.push(k)
          continue
        }
        tail.tokens = head.tokens.concat(tail.tokens)
        tail.lines = head.lines.concat(tail.lines)
        tail.raw = head.raw.concat(tail.raw)
        if (head.ts !== undefined) tail.ts = head.ts
        if (head.fts !== undefined) tail.fts = head.fts
        tail.closed = tail.closed || head.closed
        // 单值字段（meta/doc/obj/cmd/path/url/page）留正片那份：晚半截更接近最终态
      }
      this.blockOrder = [...fresh, ...liveOrder]
      if (pageLogs.length) this.logs = [...pageLogs, ...liveLogs].slice(-MAX_LOGS)
    },

    /** 待批列表拉一次（切会话 / 刷新页面）。失败静默：审批是旁路信息，不该把首屏拖挂。 */
    async loadApprovals(sid: string) {
      try {
        const r = await api.approvals(sid)
        this.approvals = sid === this.currentId ? r.pending || [] : this.approvals
      } catch {
        /* 后端未升级或越权：保持现状，不弹错 */
      }
    },

    /** trace 仅 Redis 模式有数据；进程内 runner 回空表，尾行自然不显示 tok/s。
     *  拉一次给轮次度量和右栏 trace 页共用，别各拉各的。 */
    async loadTrace(sid: string) {
      try {
        const t = await api.sessionTrace(sid)
        this.spans = sid === this.currentId ? t.spans || [] : this.spans
      } catch {
        this.spans = []
      }
    },

    resetStream() {
      this.evtSource?.close()
      this.evtSource = null
      this.blocks = {}
      this.blockOrder = []
      this.logs = []
      this.spans = []
      this.humanQuestion = null
      this.approvals = []
      this.lastSeq = 0
      this.lastCursor = ''
      this.earliestCursor = ''
      this.hasMoreEarlier = false
      this.loadingEarlier = false
      // 缓冲里可能还压着上一个会话的事件
      if (this.flushHandle !== undefined) this.flushHandle = undefined
      this.pending = []
      this.stream = 'idle'
    },

    connect() {
      this.evtSource?.close()
      if (!this.currentId) return
      const sid = this.currentId
      // N1：EventSource 发不了 Authorization header，auth 开时走 access_token 查询参数（client.ts 的 Bearer 只管 fetch）
      const tok = getToken()
      const authQ = tok ? `&access_token=${encodeURIComponent(tok)}` : ''
      const after = this.lastCursor || String(this.lastSeq)
      const src = new EventSource(`/api/sessions/${sid}/events?after=${encodeURIComponent(after)}${authQ}`)
      src.onopen = () => {
        this.stream = 'open'
      }
      src.onerror = () => {
        this.stream = 'down'
        // Browser retries automatically; if the socket was closed by us, ignore.
        // ponytail: 天花板=服务端回不可重试状态码（如鉴权 401）时 EventSource 进 CLOSED、
        // 浏览器不再重连，横幅会停在「正在重连」。升级路径=onerror 里按 readyState===2
        // 换文案或补一次 connect()，那才是施工5 F4 判「不做」的自造退避包装器。
      }
      src.onmessage = (e) => {
        if (this.currentId !== sid) {
          src.close()
          return
        }
        try {
          this.pending.push(JSON.parse(e.data))
        } catch {
          /* ignore malformed line */
          return
        }
        this.scheduleFlush()
      }
      this.evtSource = src
    },

    /** 一帧只结算一次：token 增量到达速率远高于帧率，逐条改状态会让 Vue
     *  每个 token 打一次补丁。rAF 在不可见页里不触发，所以再挂一个 32ms 定时器，
     *  谁先到谁结算并取消对方——后台标签页也能收流。 */
    scheduleFlush() {
      if (this.flushHandle !== undefined) return
      const raf =
        typeof requestAnimationFrame === 'function'
          ? requestAnimationFrame(() => this.flushEvents())
          : undefined
      const timer = setTimeout(() => this.flushEvents(), 32)
      this.flushHandle = { raf, timer }
    },

    flushEvents() {
      const h = this.flushHandle as { raf?: number; timer?: ReturnType<typeof setTimeout> } | undefined
      if (h) {
        if (h.raf !== undefined && typeof cancelAnimationFrame === 'function') cancelAnimationFrame(h.raf)
        clearTimeout(h.timer)
      }
      this.flushHandle = undefined
      const batch = this.pending
      if (!batch.length) return
      this.pending = []
      for (const ev of batch) this.applyEvent(ev)
    },

    applyEvent(ev: WEvent) {
      if (ev.cursor) {
        if (this.lastCursor && ev.cursor <= this.lastCursor) return
        this.lastCursor = ev.cursor
      } else if (ev.seq <= this.lastSeq) {
        return
      }
      if (ev.seq > this.lastSeq) this.lastSeq = ev.seq

      if (ev.kind === 'report') {
        // 孤立收口标记（uuid 从未开过块）直接丢：否则下面会凭空创建一个空块
        if (ev.name === 'end_marker' && ev.uuid && !(ev.uuid in this.blocks)) return
        const key = ev.uuid || `e${ev.cursor || ev.seq}`
        let b = this.blocks[key]
        if (!b) {
          b = newBlock(ev)
          this.blocks[key] = b
          this.blockOrder.push(key)
        }
        if (typeof ev.ts === 'number') {
          if (b.ts === undefined) b.ts = ev.ts
          b.lastTs = ev.ts
          if (ev.name === 'content' && b.fts === undefined) b.fts = ev.ts
        }
        switch (ev.name) {
          case 'meta':
            b.meta = ev.value
            break
          case 'content':
            b.tokens.push(String(ev.value ?? ''))
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
          case 'url':
            b.url = String(ev.value ?? '')
            break
          case 'page':
            b.page = ev.value
            break
          case 'end_marker':
            b.closed = true
            break
          default:
            b.raw.push({ name: ev.name, value: ev.value })
        }
      } else if (ev.kind === 'log') {
        this.logs.push(String(ev.value ?? ''))
        if (this.logs.length > MAX_LOGS) this.logs.splice(0, this.logs.length - MAX_LOGS)
      } else if (ev.kind === 'ask_human') {
        this.humanQuestion = ev
        if (this.current) this.mergeSessionLocal(this.current.id, { status: 'awaiting_human' })
        this.status = 'awaiting_human'
      } else if (ev.kind === 'approval') {
        // requested 按 id 去重入队（重放/双开标签页不该冒出两张一样的卡）；
        // resolved 只出队——之后会话回到什么状态由紧随其后的 status 事件说，这里不猜。
        const item = (ev.value || {}) as ApprovalItem
        if (ev.name === 'resolved') this.approvals = this.approvals.filter((a) => a.id !== item.id)
        else if (item.id && !this.approvals.some((a) => a.id === item.id)) {
          this.approvals.push(item)
          if (this.current) this.mergeSessionLocal(this.current.id, { status: 'awaiting_human' })
          this.status = 'awaiting_human'
        }
      } else if (ev.kind === 'error') {
        this.logs.push(`[error] ${ev.value}`)
        // 轮内红点行（B1）：走块管线，不另开一份 errors 数组——顺序、按游标去重、
        // 历史回放与「加载更早」的整页合并已经在 applyEvent/mergeEarlierPage/buildRows
        // 里了，另起一份就是第二个游标（SSE seq 精度丢事件那条洞正是第二个游标的产物）。
        const key = ev.uuid || `e${ev.cursor || ev.seq}`
        if (!(key in this.blocks)) {
          const b = newBlock(ev)
          b.type = 'Error'
          // closed 必须真：末块没收口就不发轮次尾行，而 error 事件本身就是收口信号
          b.closed = true
          b.lines = String(ev.value ?? '').split('\n')
          if (typeof ev.ts === 'number') {
            b.ts = ev.ts
            b.lastTs = ev.ts
          }
          this.blocks[key] = b
          this.blockOrder.push(key)
        }
      } else if (ev.kind === 'turn') {
        // B8：这一跑里有步被输出 token 上限截断（后端在收口时按记账出口的次数发这一条）。
        // 口径与参照系一致：它是轮级聚合、锚在轮尾，不是贴在截断那一步后面——参照系自己也
        // 把提示放在 closing Assistant 与 turn-tail 之间（turn-max-tokens.ts:29-40）。
        if ((ev.value as any)?.reason?.kind === 'max-tokens') {
          const key = ev.uuid || `e${ev.cursor || ev.seq}`
          if (!(key in this.blocks)) {
            const b = newBlock(ev)
            b.type = 'MaxTokens'      // 不是 BlockType：t1 查不到，靠 s8 t16 钉住
            b.closed = true            // 同 error 行：不给 closed 就不发尾行，见上面那条注释
            if (typeof ev.ts === 'number') {
              b.ts = ev.ts
              b.lastTs = ev.ts
            }
            this.blocks[key] = b
            this.blockOrder.push(key)
          }
        }
      } else if (ev.kind === 'queue') {
        // B6：队列变更。三个动作都只**按 id 增删**这一份投影，绝不整份覆盖——
        // 覆盖会把「add 到一半、GET 还没回来」的中间态抹掉，那就是第二个游标的老病。
        const items = ((ev.value as any)?.items || []) as QueueItem[]
        if (ev.name === 'add')
          for (const it of items) if (!this.queue.some((q) => q.id === it.id)) this.queue.push(it)
        else if (ev.name === 'drain' || ev.name === 'remove')
          this.queue = this.queue.filter((q) => !items.some((i) => i.id === q.id))
      } else if (ev.kind === 'goal') {
        // B5：目标变更。真值在 Session 记录里（GET 就拿得到），这条事件只让**活流**立刻跟上；
        // 回放走同一处落点，所以不另开第二份状态（`clear` 的 objective 是空串，必须照收）。
        const v = (ev.value || {}) as { objective?: string; done_at?: string }
        if (this.current)
          this.mergeSessionLocal(this.current.id,
                                 { goal: v.objective || '', goal_done_at: v.done_at || '' })
      } else if (ev.kind === 'status') {
        const v = ev.value || {}
        if (v.cost) this.cost = v.cost
        if (v.message) this.logs.push(`[status] ${v.message}`)
        if (v.status) {
          this.status = v.status
          if (this.current) this.mergeSessionLocal(this.current.id, { status: v.status, cost: v.cost })
          // 终态才重拉 trace：tok/s 与 StatsLine 的用量要等这一跑收口才完整，
          // 而中间态每步都在同步成本，每次拉就是打一串尖峰请求。
          if (['finished', 'stopped', 'failed'].includes(v.status)) void this.loadTrace(this.currentId)
        }
        if (v.status !== 'awaiting_human') this.humanQuestion = null
      }
    },

    mergeSessionLocal(sid: string, patch: Partial<Session>) {
      const s = this.sessions.find((x) => x.id === sid)
      if (s) Object.assign(s, patch)
    },

    async start() {
      if (!this.currentId) return
      this.busy = true
      try {
        await api.startSession(this.currentId)
        this.mergeSessionLocal(this.currentId, { status: 'running' })
        this.status = 'running'
      } finally {
        this.busy = false
      }
    },

    async stop() {
      if (!this.currentId) return
      this.busy = true
      try {
        await api.stopSession(this.currentId)
      } finally {
        this.busy = false
      }
    },

    /** PATCH 返回服务端权威对象，直接覆盖本地那一条，失败自然抛出给调用方提示。 */
    async patchSession(sid: string, patch: { idea?: string; archived?: boolean; pinned?: boolean }) {
      const s = await api.patchSession(sid, patch)
      this.mergeSessionLocal(sid, s)
      return s
    },

    async removeSession(sid: string) {
      await api.deleteSession(sid)
      this.sessions = this.sessions.filter((x) => x.id !== sid)
      if (this.currentId === sid) this.goHome()
    },

    async sendChat(content: string, sendTo = '') {
      await api.sendChat(this.currentId, content, sendTo)
      // 把用户发言插成伪块，Timeline 里按时间顺序渲染成右侧气泡（参考图布局）
      const key = `u${Date.now()}`
      this.blocks[key] = {
        key,
        type: 'User',
        role: 'user',
        closed: true,
        ts: Date.now() / 1000,
        lastTs: Date.now() / 1000,
        meta: null,
        tokens: [content],
        doc: null,
        obj: null,
        lines: [],
        cmd: '',
        path: '',
        url: '',
        page: null,
        raw: []
      }
      this.blockOrder.push(key)
    },

    async answerHuman(content: string) {
      const ok = await api.answerHuman(this.currentId, content)
      if (ok) this.humanQuestion = null
      return ok
    },

    /** 回执：先出队让卡片立刻消失，失败再塞回去（网络抖一下不该把用户困在原地）。 */
    async respondApproval(aid: string, outcome: 'allowed-once' | 'rejected') {
      const keep = this.approvals
      this.approvals = this.approvals.filter((a) => a.id !== aid)
      try {
        return await api.respondApproval(this.currentId, aid, outcome)
      } catch (e) {
        // 只回滚不重取=把「别处已决议」的卡复活（SSE resolved 先出队，快照里它还在）。
        // 顺序不能反：loadApprovals 失败是静默的，断网时唯一能让卡回队列的就是这句快照回滚。
        this.approvals = keep
        void this.loadApprovals(this.currentId)
        throw e
      }
    },

    /** 会话中途切免审档：从下一个节点边界起生效（跑图任务已持有旧值）。 */
    async setPermission(permission: string) {
      const s = await api.patchSession(this.currentId, { permission })
      this.mergeSessionLocal(s.id, { permission: s.permission })
      return s
    },

    dismissHuman() {
      this.humanQuestion = null
    },

    workspaceUrl(absPath: string): string {
      if (!absPath) return ''
      // /workspace is mounted at the repo workspace root, so the relative
      // path already contains the per-session project directory.
      const root = (this.health?.workspace_root || '').replace(/\\/g, '/')
      const norm = absPath.replace(/\\/g, '/')
      let rel = norm.toLowerCase().startsWith(root.toLowerCase()) ? norm.slice(root.length) : norm
      rel = rel.replace(/^\//, '')
      // S1：/workspace 静态挂载在 auth 开时要求 access_token（<img> 发不了 header）。
      // 有票就无条件带上，auth 关时服务端忽略它——少一个分支。
      const token = getToken()
      return token ? `/workspace/${rel}?access_token=${encodeURIComponent(token)}` : `/workspace/${rel}`
    }
  }
})
