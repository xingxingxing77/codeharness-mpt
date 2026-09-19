import { defineStore } from 'pinia'

export interface Toast {
  seq: number
  text: string
  tone: 'info' | 'warn' | 'error' | 'success'
}

const HOLD = 2600

// 计时器不进 state：Pinia 会把它序列化进 devtools，而它是纯实现细节
const timers: Record<number, ReturnType<typeof setTimeout>> = {}

/** seq 键控：同一句提示重复触发时重置它的驻留时间，而不是叠一摞。 */
export const useToastStore = defineStore('toast', {
  state: () => ({
    items: [] as Toast[],
    seq: 0
  }),

  actions: {
    push(text: string, tone: Toast['tone'] = 'info') {
      const found = this.items.find((t) => t.text === text && t.tone === tone)
      if (found) {
        this.hold(found.seq)
        return found.seq
      }
      const seq = ++this.seq
      this.items.push({ seq, text, tone })
      this.hold(seq)
      return seq
    },

    dismiss(seq: number) {
      this.items = this.items.filter((t) => t.seq !== seq)
    },

    hold(seq: number) {
      const t = this.items.find((x) => x.seq === seq)
      if (!t) return
      clearTimeout(timers[seq])
      timers[seq] = setTimeout(() => this.dismiss(seq), HOLD)
    }
  }
})
