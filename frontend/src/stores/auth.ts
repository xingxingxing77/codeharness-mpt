import { defineStore } from 'pinia'
import { api, getToken, REQUEST_TIMEOUT_MS, setToken } from '../api/client'

async function reqMe(): Promise<{ user: string }> {
  // client.req 的 401 会清票并 throw——这里只为拿 user。
  // 但绕过 req() 也就绕过了它的 deadline，所以信号要自己带（F2 同类面：挂起=init() 永久 pending）。
  const r = await fetch('/api/auth/me', {
    headers: { Authorization: `Bearer ${getToken()}` },
    signal: AbortSignal.timeout(REQUEST_TIMEOUT_MS)
  })
  if (!r.ok) throw new Error('unauthorized')
  return r.json()
}

/** N1 账号边界（前端半边）：health.auth_enabled 驱动登录页接管；
 *  token 在 client.ts 里随请求带上，401 时 clear() → needLogin 翻真。
 *  票状态在 store 里（localStorage 非响应式，getter 追不到变化）。 */
export const useAuthStore = defineStore('auth', {
  state: () => ({
    enabled: false,       // 服务端 PLATFORM__AUTH
    ready: false,         // health 已回（避免登录页闪现）
    hasToken: !!getToken(),
    user: '' as string
  }),
  getters: {
    needLogin: (s) => s.enabled && s.ready && !s.hasToken
  },
  actions: {
    clear() {
      setToken('')
      this.hasToken = false
      this.user = ''
    },
    async init() {
      const h = await api.health()
      this.enabled = !!h.auth_enabled
      this.ready = true
      // 有票就验真伪：/api/auth/me 401 时 clear → needLogin 接管
      if (this.enabled && this.hasToken) {
        try {
          const me = await reqMe()
          this.user = me.user
        } catch {
          this.clear()
        }
      }
    },
    async login(username: string, password: string) {
      const r = await api.login(username, password)
      setToken(r.token)
      this.hasToken = true
      this.user = username
    },
    async register(username: string, password: string) {
      const r = await api.register(username, password)
      setToken(r.token)
      this.hasToken = true
      this.user = username
    },
    async logout() {
      try { await api.logout() } catch {}
      this.clear()
    }
  }
})
