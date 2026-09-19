/** 相对时间六档，逐档照抄参考项目 ui-workspace/tree.ts 的 relativeTime。
 *  注意参考项目**不按日历分桶**（没有「今天/昨天」标题），时间只是每行右侧一个紧凑标签。 */
export type TimeUnit = 'now' | 'minutes' | 'hours' | 'days' | 'months' | 'years'

export interface RelativeTime {
  unit: TimeUnit
  n: number
}

const MINUTE = 60_000
const HOUR = 3_600_000
const DAY = 86_400_000
const MONTH = 30 * DAY
const YEAR = 365 * DAY

export function relativeTime(iso: string | number, now: number = Date.now()): RelativeTime | null {
  const t = typeof iso === 'number' ? iso : parseBackendTime(iso)
  if (!t) return null
  const diff = Math.max(0, now - t)
  if (diff < MINUTE) return { unit: 'now', n: 0 }
  if (diff < HOUR) return { unit: 'minutes', n: Math.floor(diff / MINUTE) }
  if (diff < DAY) return { unit: 'hours', n: Math.floor(diff / HOUR) }
  if (diff < MONTH) return { unit: 'days', n: Math.floor(diff / DAY) }
  if (diff < YEAR) return { unit: 'months', n: Math.floor(diff / MONTH) }
  return { unit: 'years', n: Math.floor(diff / YEAR) }
}

const ZH: Record<TimeUnit, (n: number) => string> = {
  now: () => '刚刚',
  minutes: (n) => `${n}分钟`,
  hours: (n) => `${n}小时`,
  days: (n) => `${n}天`,
  months: (n) => `${n}个月`,
  years: (n) => `${n}年`
}

export function formatRelative(iso: string | number, now?: number): string {
  const r = relativeTime(iso, now)
  return r ? ZH[r.unit](r.n) : ''
}

/** 后端给的是 "%Y-%m-%d %H:%M:%S" 本地时间串（无时区、非 ISO）。
 *  new Date(该串) 在规范上是 implementation-defined，Chrome 恰好能解，但不能依赖，
 *  所以自己按本地时区拆开算。 */
export function parseBackendTime(s: string): number {
  if (!s) return 0
  const m = /^(\d{4})-(\d{1,2})-(\d{1,2})[ T](\d{1,2}):(\d{2}):(\d{2})/.exec(s)
  if (!m) {
    const t = Date.parse(s)
    return Number.isNaN(t) ? 0 : t
  }
  return new Date(+m[1], +m[2] - 1, +m[3], +m[4], +m[5], +m[6]).getTime()
}
