export interface Session {
  id: string
  idea: string
  project_name: string
  n_round: number
  paradigm: string // classic | dynamic（S9.1 对照）
  sop: string // 非空=按 N7 模板装配（扩展线）
  status: string // created | running | awaiting_human | stopping | finished | stopped | failed
  llm_override: Record<string, any>
  workspace: string
  error: string
  cost: Record<string, number>
  created_at: string
  started_at: string
  finished_at: string
}

export interface WEvent {
  session_id: string
  seq: number
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
  workspace_root: string
}
