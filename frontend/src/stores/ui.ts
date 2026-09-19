import { defineStore } from 'pinia'
import { useLayoutStore } from './layout'

export const useUiStore = defineStore('ui', {
  state: () => ({
    showCreate: false,
    /* 从首页输入卡的 + 按钮进高级设置时预填需求 */
    createIdea: '',
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
      rounds: 5,
      paradigm: 'classic'
    }
  }),

  getters: {
    /** 右栏开关的唯一真相在 layout（宽度 0 = 关），这里只做只读投影，
     *  免得「拖过宽度」和「点按钮」两条路各持一份布尔互相打脸。 */
    rightPanel: () => !useLayoutStore().detailsCollapsed
  },

  actions: {
    openRight() {
      useLayoutStore().openDetails()
    },
    closeRight() {
      useLayoutStore().closeDetails()
    },
    toggleRight() {
      useLayoutStore().toggleDetails()
    }
  }
})
