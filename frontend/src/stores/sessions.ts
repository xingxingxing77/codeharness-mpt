import { defineStore } from 'pinia'
import { api, getToken } from '../api/client'
import { normalizeVotes } from '../utils/votes'
import { foldAll, foldOne, resetFold } from '../protocol/fold'
import type { ApprovalItem, Block, ContextTier, Health, QueueItem, Session, TraceSpan, WEvent } from '../types'

/** 一屏的事件条数（B2）。单位是**事件**不是块——一块会合并整个流式节点的几十上百条
 *  token 事件，按块定页数就会把首屏撑回「一次吞完整条流」。 */
const HISTORY_PAGE = 400

/** 本场已加载的**事件日志**（append-only，按游标升序）。它是时间线的唯一真相：
 *  活流往里推、首屏装它、「加载更早」往**头里**插它然后整本重折——三条路喂的是同一个 reducer。
 *  放 pinia 外面是刻意的：Vue 会给每条事件建深层 proxy，而这份数据从来没有渲染消费者，
 *  只有 reducer 读它。
 *  ponytail: 天花板＝内存 O(已加载事件)，一条事件实测均长 1475B（§1.10 那本账），
 *  翻 30 页 ≈ 1.2 万条 ≈ 18MB，是块里那份文本的第二本拷贝。升级路径＝按页冻结成
 *  不可变前缀段（折完就把段里的事件丢掉，只留折出来的块），那要再处理跨页开口的块——
 *  今天没有消费者报这个痛，先不做。 */
const evlog = new Map<string, WEvent[]>()

/** C110：`connect()` 里那条 `visibilitychange` 监听只挂一次（挂多次 ⇒ 回一次前台重开 N 条流）。 */
let foregroundHooked = false

export const useSessionStore = defineStore('sessions', {
  state: () => ({
    health: null as Health | null,
    /** /api/models 的目录。modelsOk=false 表示端点不给列表，模型位退化成只读文本 */
    models: [] as string[],
    modelsOk: false,
    /** C184：会话级上下文预算档位与模型窗口，都由后端出（与入口校验同一份名单）。
     *  空数组=后端没给档位 ⇒ composer 不画档位选择（不是画一个点开是空的箭头）。 */
    contextTiers: [] as ContextTier[],
    modelWindow: null as number | null,
    sessions: [] as Session[],
    currentId: '',
    // ---- 下面这一段就是 `protocol/fold.ts::FoldState`：reducer 写的字段与渲染读的字段必须同一份。
    //      对账在 `frontend/scripts/fold_check.ts`（它按 FoldState 构造状态跑不变量）＋ s8 那一格
    //      （它 grep 这段字段名，少一个就红）。----
    blocks: {} as Record<string, Block>,
    blockOrder: [] as string[],
    logs: [] as string[],
    status: '',
    cost: {} as Record<string, number>,
    humanQuestion: null as WEvent | null,
    /** 当前会话的待批项（批次36）。SSE `approval` 事件与 GET /approvals 两路，后者带 who/when。 */
    approvals: [] as ApprovalItem[],
    /** C144：已决议的审批（谁批的、何时批的）。**只**由 GET 喂——事件载荷里没有这两笔。 */
    decidedApprovals: [] as ApprovalItem[],
    /** B6：排着还没被 route 取走的插话。服务端队列是唯一真值，这里只是投影。 */
    queue: [] as QueueItem[],
    /** B4：尾行键 → 票。同样只是投影，真值在 `Session.feedback`。 */
    feedback: {} as Record<string, string>,
    /** reducer 折出来的会话记录主张（状态/目标/用量），每批折完一次性套回 `sessions` 里那条。 */
    sessionPatch: {} as Record<string, any>,
    /** 处置表查不到、或查到了但 reducer 没这一支的事件键。界面上不画，探针与门禁读它。 */
    unhandled: [] as string[],
    effects: [] as string[],
    // ---- FoldState 到此为止 ----
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
    loadingFirst: false,
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
      // 不再自动选中会话：落地页显示"我们应该构建什么"首页（参考 OpenHarness 布局）
      // 模型目录是旁路信息：端点不给也不能把首屏拖挂，loadModels 自己吞错
      void this.loadModels()
    },

    async loadModels() {
      try {
        const r = await api.listModels()
        this.models = r.models || []
        this.modelsOk = !!r.ok
        // C184：档位与模型窗口是后端常量，端点探活失败（ok=false）也照收——
        // 它们跟 modelsOk 无关，收不到就留空/ null，消费端按缺键不渲染。
        this.contextTiers = r.context_tiers || []
        this.modelWindow = r.model_window ?? null
      } catch {
        this.models = []
        this.modelsOk = false
        this.contextTiers = []
        this.modelWindow = null
      }
    },

    goHome() {
      const prev = this.currentId
      this.currentId = ''
      this.resetStream(prev)
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

    /** B3：从某一轮分叉出新会话并**切过去**——留在源会话里看不见自己刚分出去的那场，
     *  而分叉的意义就是接着那儿往下走。返回整份响应而不只是 id：`copied_truncated` 得有人看见，
     *  否则「产物只拷了一部分」这件事就只存在于后端日志里（F-E/B7 那族「上限要说出去」的口径）。 */
    async forkFrom(fromCursor: string): Promise<Record<string, any>> {
      if (!this.currentId) return {}
      const r = await api.forkSession(this.currentId, fromCursor)
      await this.loadSessions()
      this.select(r.id)
      return r
    },

    select(sid: string) {
      if (this.currentId === sid) return
      const prev = this.currentId
      this.currentId = sid
      this.resetStream(prev)
      const s = this.sessions.find((x) => x.id === sid)
      this.status = s?.status || ''
      this.cost = s?.cost || {}
      // B2：首屏只拉「最新一屏」再开活流。原先直接 connect()，服务端会把保留窗口里
      // 那 5000 条整段回放完才活推——首屏时间与事件数成正比，且「加载更早」永远没内容。
      void this.loadFirstPage(sid)
      void this.loadTrace(sid)
      void this.loadApprovals(sid)
      this.queue = []            // 上一场的排队胶囊不许挂在这一场名下（同「第二个游标」那族洞）
      this.feedback = {}
      void this.loadQueue(sid)
      void this.loadFeedback(sid)
    },

    async loadFeedback(sid: string) {
      try {
        const r = await api.feedback(sid)
        // 票值有两态（旧票裸字符串、新票 {v,at}），图标那层只吃票种 ⇒ 在这里归一一次。
        if (sid === this.currentId) this.feedback = normalizeVotes(r.votes || {})
      } catch {
        /* 老会话没有这个字段：空表就是正确答案 */
      }
    },

    async loadQueue(sid: string) {
      try {
        const r = await api.queue(sid)
        if (sid === this.currentId) this.queue = (r.items || []) as QueueItem[]
      } catch {
        /* 停着的会话没有队列：留空就是正确答案，不拿假数据填 */
      }
    },

    /** 拉最新一屏历史：装进日志、整本重折。成功后 lastCursor 落在页尾，connect() 从那儿只收增量。 */
    async loadFirstPage(sid: string) {
      this.loadingFirst = true
      try {
        const r = await api.eventHistory(sid, { limit: HISTORY_PAGE })
        if (sid !== this.currentId) return          // 连点两个会话：慢的那次不许把别人的块画进来
        evlog.set(sid, r.events.slice())
        this.replayLog(sid)
        this.earliestCursor = r.events.length ? r.events[0].cursor : ''
        this.hasMoreEarlier = !!r.has_more
      } catch {
        /* 历史拉不到就退回老行为：connect() 里 after='' 会让服务端整段回放 */
      } finally {
        // C143：连点两个会话时，早退的旧请求不许替新会话摘「载入中」——
        // 摘早了，新会话加载期间空态会短暂说成「暂无事件」（C96 同族的瞬态假话）。
        if (sid === this.currentId) this.loadingFirst = false
      }
      if (sid === this.currentId) this.connect()
    },

    /** 「加载更早」：**前插**进同一本日志再整本重折——这就是路线三要的那件事：
     *  改前这里有一份手写合并（`mergeEarlierPage`：逐字段 concat、单值字段「留正片那份」、
     *  还得靠 `PAGE_REPLAY_OPAQUE` 名单挡住六支实时槽），因为它把「老页」和「新页」当成两条
     *  路来折。按序重折之后「最新的赢」是构造出来的，那份合并和那个名单一起删掉。
     *  返回**新出现**的块数（0 = 没动静，视图据此决定要不要补滚动锚点）。失败静默。 */
    async loadEarlier(): Promise<number> {
      if (!this.hasMoreEarlier || this.loadingEarlier || !this.earliestCursor) return 0
      this.loadingEarlier = true
      const sid = this.currentId
      try {
        const r = await api.eventHistory(sid, { before: this.earliestCursor, limit: HISTORY_PAGE })
        if (sid !== this.currentId) return 0
        this.hasMoreEarlier = !!r.has_more
        if (!r.events.length) return 0
        const before = this.blockOrder.length
        this.ingestEarlier(r.events)
        this.earliestCursor = r.events[0].cursor
        return this.blockOrder.length - before
      } catch {
        return 0
      } finally {
        this.loadingEarlier = false
      }
    },

    /** 「加载更早」的下半截：**前插进同一本日志再整本重折**。单独成一条动作是为了让
     *  `check_page_replay.mjs` 能在真 store（pinia + 真 reducer）上直接量这一转——
     *  上半截是网络，判据不该为了走到下半截而造假端点。 */
    ingestEarlier(evs: WEvent[]) {
      const sid = this.currentId
      const older = evlog.get(sid) || []
      evlog.set(sid, evs.concat(older))
      this.replayLog(sid)
    },

    /** 整本日志重折（首屏与「加载更早」共用这一条路）。`resetFold` 刻意不清 status/cost/approvals：
     *  它们的基线来自 GET，重折只做覆盖。
     *  「已决议的卡会不会被老页那条 `requested` 复活」这一族在这里不成立，而且**不靠额外规则**：
     *  窗口是裁头的（`MAX_EVENTS_PER_SESSION` 丢最旧），`resolved` 永远比它的 `requested` 新 ⇒
     *  只要那条 requested 在日志里，对应的 resolved 必在它后面，按序重折就把它撤掉了。
     *  `check_page_replay.mjs` 量的就是这句话，而不是「有没有名单」。 */
    replayLog(sid: string) {
      resetFold(this)
      foldAll(this, evlog.get(sid) || [])
      this.runEffects()
      this.syncSessionPatch()
    },

    /** 待批列表拉一次（切会话 / 刷新页面 / 有卡决议完）。失败静默：审批是旁路信息，不该把首屏拖挂。 */
    async loadApprovals(sid: string) {
      try {
        const r = await api.approvals(sid)
        this.approvals = sid === this.currentId ? r.pending || [] : this.approvals
        // C144：同一口带出的已决议列表——批完的卡不该从界面上消失得无影无踪。
        this.decidedApprovals = sid === this.currentId ? r.decided || [] : this.decidedApprovals
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

    /** `dropSid`＝离开那一场，要连带丢掉它的事件日志（日志是 per-session 的，不丢就是内存里
     *  第二本没人翻的账）。调用方传**切换前**的 id——`select` 会先把 currentId 换成新的。 */
    resetStream(dropSid?: string) {
      this.evtSource?.close()
      this.evtSource = null
      if (dropSid) evlog.delete(dropSid)
      resetFold(this)
      this.spans = []
      this.approvals = []
      this.decidedApprovals = []
      this.status = ''
      this.cost = {}
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
      // C110：后台标签页被浏览器冻结时，服务端那条订阅队列按有界设计「丢最旧」（C90/C103），
      // 而这条 EventSource **连接没断** ⇒ 浏览器不会自动重连 ⇒ 回到前台后对话中间留一个洞，
      // 直到用户手动刷新。补法不新造机制：`/events` 路由本来就是「先按 `after` 回放历史、
      // 再跟活流」（`server/api/sessions.py:591-596`），而 `after` 取的就是已应用的游标 ⇒
      // **回到前台这一刻重开一次流**，被丢掉的那段自己从历史补回来（重复的由 `ingest` 按 cursor 去重）。
      if (!foregroundHooked && typeof document !== 'undefined') {
        foregroundHooked = true
        document.addEventListener('visibilitychange', () => {
          if (document.visibilityState === 'visible' && this.currentId) this.connect()
        })
      }
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
      for (const ev of batch) this.ingest(ev)
      this.runEffects()
      this.syncSessionPatch()
    },

    /** 活流的一批折完了：把 reducer 记下的口令执行掉。同名口令一批只跑一次——
     *  整本重折时「某张卡决议过」这种历史事实会再来一遍，重复发请求就是白打尖峰。 */
    runEffects() {
      const names = [...new Set(this.effects)]
      this.effects = []
      for (const n of names) {
        if (n === 'loadApprovals') void this.loadApprovals(this.currentId)
        else if (n === 'loadTrace') void this.loadTrace(this.currentId)
      }
    },

    /** 会话记录是投影的另一头：reducer 只主张「这些事件说过什么」，套回记录在这儿一次性做，
     *  不再逐事件 `mergeSessionLocal`（那同一件事就有两本账）。 */
    syncSessionPatch() {
      const p = this.sessionPatch
      this.sessionPatch = {}
      if (this.current && Object.keys(p).length) this.mergeSessionLocal(this.current.id, p)
    },

    /**  transport→reducer 的唯一接缝：先去重、再进日志、再折。
     *  去重留在 store 是因为它是**传输**的事（游标单调），不是渲染的事——
     *  折一份已经去过重的日志，重放与活流才会得到同一个终态。 */
    ingest(ev: WEvent) {
      if (ev.cursor) {
        if (this.lastCursor && ev.cursor <= this.lastCursor) return
        this.lastCursor = ev.cursor
      } else if (ev.seq <= this.lastSeq) {
        return
      }
      if (ev.seq > this.lastSeq) this.lastSeq = ev.seq
      let log = evlog.get(this.currentId)
      if (!log) evlog.set(this.currentId, (log = []))
      log.push(ev)
      foldOne(this, ev)
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

    /** 用户发言**不再在前端造块**（C189）：`/chat` 与 `/human-input` 现在各发一颗真 `User` 块
     *  上事件流（`server/api/sessions.py::_publish_user_block`），这一屏的气泡与刷新后的回放、
     *  「加载更早」的整本重折、分叉切点吃的都是同一本账。
     *  改前这里是「本地乐观插一颗假块」：顶得住这一屏，顶不住刷新（C188 之前连翻页都会抹掉它），
     *  而且一旦生产端补上就会变成两份同屏——所以生产端接上的同一批，这里必须删干净，
     *  不是留着「更跟手」。 */
    async sendChat(content: string, sendTo = '') {
      await api.sendChat(this.currentId, content, sendTo)
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
