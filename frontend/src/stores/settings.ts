import { defineStore } from 'pinia'

const KEY = 'webui-settings-v1'

const defaults = {
  /* 常规 */
  workMode: 'coding',
  defaultPermission: true,
  autoReview: true,
  fullAccess: true,
  openTarget: 'Visual Studio',
  agentEnv: 'Windows 原生',
  shell: 'PowerShell',
  language: '自动检测',
  enterToSend: false,
  popupShortcut: '',
  noProjectChat: false,
  dictationHold: '关闭',
  dictationToggle: '关闭',
  noticeTurn: '仅当应用失焦时',
  noticePermission: true,
  noticeIssue: true,
  contextUsage: false,
  /* 个性化 */
  tone: '亲和',
  instructions: '',
  memoryEnabled: true,
  memorySkipTools: false,
  /* 配置 */
  approval: 'On request',
  sandbox: 'Read only',
  depsToggle: true,
  /* Git */
  branchPrefix: 'codex/',
  prMerge: '合并',
  prIcon: false,
  forcePush: false,
  draftPr: true,
  autoDeleteTrees: true,
  treeLimit: 15,
  commitGuide: '',
  prGuide: '',
  /* MCP */
  mcpRepl: true,
  /* 浏览器 */
  screenshot: '始终包含',
  webApproval: '始终访问',
  blockedDomains: [] as string[],
  allowedDomains: [] as string[]
}

type Prefs = typeof defaults

function load(): Prefs {
  try {
    return { ...defaults, ...JSON.parse(localStorage.getItem(KEY) || '{}') }
  } catch {
    return { ...defaults }
  }
}

export const useSettingsStore = defineStore('settings', {
  state: () => ({ prefs: load() }),
  actions: {
    persist() {
      try {
        localStorage.setItem(KEY, JSON.stringify(this.prefs))
      } catch {
        /* storage unavailable */
      }
    },
    reset() {
      this.prefs = { ...defaults }
      this.persist()
    }
  }
})
