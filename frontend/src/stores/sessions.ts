import { defineStore } from 'pinia'
import { api, getToken } from '../api/client'
import type { ApprovalItem, Block, Health, Session, TraceSpan, WEvent } from '../types'

const MAX_LOGS = 800

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
    status: '',
    cost: {} as Record<string, number>,
    connected: false,
    evtSource: null as EventSource | null,
    /** 主游标：定宽补零串，字典序==到达序。 */
    lastCursor: '',
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
      this.connect()
      void this.loadTrace(sid)
      void this.loadApprovals(sid)
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
      // 缓冲里可能还压着上一个会话的事件
      if (this.flushHandle !== undefined) this.flushHandle = undefined
      this.pending = []
      this.connected = false
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
        this.connected = true
      }
      src.onerror = () => {
        this.connected = false
        // Browser retries automatically; if the socket was closed by us, ignore.
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
        this.approvals = keep
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
