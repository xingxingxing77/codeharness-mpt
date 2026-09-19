import { defineStore } from 'pinia'
import { useLayoutStore } from './layout'

export const useUiStore = defineStore('ui', {
  state: () => ({
    rightView: 'cards' as 'cards' | 'files' | 'review' | 'graph' | 'trace' | 'inspect',
    /** 被检视的块 key：对话流的 inspect 钮写、右栏读，两边不各持一份。 */
    selectedKey: '',
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
    },
    /** 参考项目的 inspectCall：选中即联动，一次做完「记选中 + 切页 + 开栏」。 */
    inspect(key: string) {
      this.selectedKey = key
      this.rightView = 'inspect'
      this.openRight()
    },
    closeDetails() {
      this.selectedKey = ''
      this.closeRight()
    }
  }
})
