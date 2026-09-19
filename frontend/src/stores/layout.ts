import { defineStore } from 'pinia'
import {
  DETAILS_DEFAULT,
  DETAILS_MAX,
  DETAILS_MIN,
  SIDEBAR_DEFAULT,
  SIDEBAR_MAX,
  SIDEBAR_MIN,
  clampWidth
} from './columns'

export * from './columns'

interface LayoutState {
  sidebar: number
  details: number
  narrow: boolean
  narrowExpanded: boolean
}

/** 不持久化（同参考项目）：让渡求解器本身已按视口收敛，刷新回默认即可。 */
export const useLayoutStore = defineStore('layout', {
  state: (): LayoutState => ({
    sidebar: SIDEBAR_DEFAULT,
    details: 0,
    narrow: false,
    narrowExpanded: false
  }),

  getters: {
    /** 有效收起态由帧宽决定而不是 store：窄视口下的「展开」只是一次性的。 */
    sidebarCollapsed(s): boolean {
      return s.narrow ? !s.narrowExpanded : s.sidebar === 0
    },
    detailsCollapsed(s): boolean {
      return s.details === 0
    }
  },

  actions: {
    setSidebar(px: number) {
      this.sidebar = clampWidth(px, SIDEBAR_MIN, SIDEBAR_MAX)
    },

    setDetails(px: number) {
      this.details = clampWidth(px, DETAILS_MIN, DETAILS_MAX)
    },

    toggleSidebar() {
      if (this.narrow) {
        // 窄视口只翻「本次展开」，用户存的偏好原样保留
        this.narrowExpanded = !this.narrowExpanded
        return
      }
      this.sidebar = this.sidebar === 0 ? SIDEBAR_DEFAULT : 0
    },

    setNarrow(n: boolean) {
      if (this.narrow === n) return
      this.narrow = n
      this.narrowExpanded = false
    },

    openDetails() {
      if (this.details === 0) this.details = DETAILS_DEFAULT
    },

    toggleDetails() {
      this.details === 0 ? this.openDetails() : this.closeDetails()
    },

    closeDetails() {
      this.details = 0
    }
  }
})
