import { defineStore } from 'pinia'
import { api } from '../api/client'
import type { Block, Health, Session, WEvent } from '../types'

const MAX_LOGS = 800

function newBlock(ev: WEvent): Block {
  return {
    key: ev.uuid || `e${ev.seq}`,
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
    sessions: [] as Session[],
    currentId: '',
    blocks: {} as Record<string, Block>,
    blockOrder: [] as string[],
    logs: [] as string[],
    humanQuestion: null as WEvent | null,
    status: '',
    cost: {} as Record<string, number>,
    connected: false,
    evtSource: null as EventSource | null,
    lastSeq: 0,
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
    }
  },

  actions: {
    async init() {
      this.health = await api.health()
      await this.loadSessions()
      // 不再自动选中会话：落地页显示"我们应该构建什么"首页（参考 OpenHarness 布局）
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
      investment?: number
      n_round?: number
      llm?: Record<string, any>
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
    },

    resetStream() {
      this.evtSource?.close()
      this.evtSource = null
      this.blocks = {}
      this.blockOrder = []
      this.logs = []
      this.humanQuestion = null
      this.lastSeq = 0
      this.connected = false
    },

    connect() {
      this.evtSource?.close()
      if (!this.currentId) return
      const sid = this.currentId
      const src = new EventSource(`/api/sessions/${sid}/events?after=${this.lastSeq}`)
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
          this.applyEvent(JSON.parse(e.data))
        } catch {
          /* ignore malformed line */
        }
      }
      this.evtSource = src
    },

    applyEvent(ev: WEvent) {
      if (ev.seq <= this.lastSeq) return
      this.lastSeq = ev.seq

      if (ev.kind === 'report') {
        const key = ev.uuid || `e${ev.seq}`
        let b = this.blocks[key]
        if (!b) {
          b = newBlock(ev)
          this.blocks[key] = b
          this.blockOrder.push(key)
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
        if (this.current) this.patchSession(this.current.id, { status: 'awaiting_human' })
        this.status = 'awaiting_human'
      } else if (ev.kind === 'error') {
        this.logs.push(`[error] ${ev.value}`)
      } else if (ev.kind === 'status') {
        const v = ev.value || {}
        if (v.cost) this.cost = v.cost
        if (v.message) this.logs.push(`[status] ${v.message}`)
        if (v.status) {
          this.status = v.status
          if (this.current) this.patchSession(this.current.id, { status: v.status, cost: v.cost })
        }
        if (v.status !== 'awaiting_human') this.humanQuestion = null
      }
    },

    patchSession(sid: string, patch: Partial<Session>) {
      const s = this.sessions.find((x) => x.id === sid)
      if (s) Object.assign(s, patch)
    },

    async start() {
      if (!this.currentId) return
      this.busy = true
      try {
        await api.startSession(this.currentId)
        this.patchSession(this.currentId, { status: 'running' })
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

    async sendChat(content: string, sendTo = '') {
      await api.sendChat(this.currentId, content, sendTo)
      // 把用户发言插成伪块，Timeline 里按时间顺序渲染成右侧气泡（参考图布局）
      const key = `u${Date.now()}`
      this.blocks[key] = {
        key,
        type: 'User',
        role: 'user',
        closed: true,
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
      return `/workspace/${rel}`
    }
  }
})
