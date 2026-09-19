import { defineStore } from 'pinia'

export const useUiStore = defineStore('ui', {
  state: () => ({
    showCreate: false,
    /* 从首页输入卡的 + 按钮进高级设置时预填需求 */
    createIdea: '',
    /* 右侧工具面板 */
    rightPanel: false,
    rightView: 'cards' as 'cards' | 'files' | 'review' | 'graph' | 'trace',
    /* 底部终端面板 */
    terminalOpen: false,
    /* 侧栏底部"设置"弹出小菜单 */
    settingsMenuOpen: false,
    /* 全页设置视图（OpenHarness 风格） */
    settingsFull: false,
    settingsPage: 'general' as string,
    /* 落地页会话参数 */
    composer: {
      project: 'MetaGPT',
      rounds: 5
    }
  })
})
