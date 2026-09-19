import { defineStore } from 'pinia'
import { useLayoutStore } from './layout'

export const useUiStore = defineStore('ui', {
  state: () => ({
    rightView: 'cards' as 'cards' | 'files' | 'review' | 'graph' | 'trace',
    /* 设置弹窗开关 */
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
