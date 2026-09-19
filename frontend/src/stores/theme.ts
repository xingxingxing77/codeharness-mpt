import { defineStore } from 'pinia'

export type ThemePref = 'light' | 'dark' | 'system'

const KEY = 'ch.theme'
const mq = typeof matchMedia === 'function' ? matchMedia('(prefers-color-scheme: dark)') : null

/** 深色是 presence 属性：body[data-ds-dark-theme] 命中即深色，浅色是该属性不存在。
 *  写值（data-ds-dark-theme="light"）会让浅色态规则永不命中——令牌层就是这么声明的。 */
function isDark(pref: ThemePref): boolean {
  return pref === 'dark' || (pref === 'system' && !!mq?.matches)
}

export const useThemeStore = defineStore('theme', {
  state: () => ({
    pref: ((localStorage.getItem(KEY) as ThemePref | null) ?? 'light') as ThemePref
  }),

  getters: {
    dark: (s) => isDark(s.pref)
  },

  actions: {
    set(pref: ThemePref) {
      this.pref = pref
      localStorage.setItem(KEY, pref)
      this.apply()
    },

    toggle() {
      this.set(this.dark ? 'light' : 'dark')
    },

    apply() {
      document.body.toggleAttribute('data-ds-dark-theme', isDark(this.pref))
      document.documentElement.style.colorScheme = isDark(this.pref) ? 'dark' : 'light'
    },

    /** system 偏好下跟随控制台换肤；light/dark 是显式选择，不响应。 */
    watch() {
      if (!mq) return
      const on = () => this.apply()
      if (mq.addEventListener) mq.addEventListener('change', on)
      else mq.addListener(on)
    }
  }
})
