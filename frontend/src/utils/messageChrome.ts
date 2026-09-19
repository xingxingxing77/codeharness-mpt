/** 参考项目 chat/message-chrome.ts 的四只格式化器，模板本地化成中文。
 *  规则照抄：同日只给 `HH:mm`，同年补 `M月D日`，跨年补 `YYYY年M月D日`；
 *  秒数小于 10 给一位小数，tok/s 小于 10 给一位小数。 */

const pad2 = (n: number) => String(n).padStart(2, '0')

export function formatMessageClock(ms: number, now: number = Date.now()): string {
  const d = new Date(ms)
  const n = new Date(now)
  const clock = `${pad2(d.getHours())}:${pad2(d.getMinutes())}`
  if (d.getFullYear() === n.getFullYear() && d.getMonth() === n.getMonth() && d.getDate() === n.getDate()) {
    return clock
  }
  const md =
    d.getFullYear() === n.getFullYear()
      ? `${d.getMonth() + 1}月${d.getDate()}日`
      : `${d.getFullYear()}年${d.getMonth() + 1}月${d.getDate()}日`
  return `${md} ${clock}`
}

export function formatRunDuration(ms: number): string {
  const total = Math.max(0, Math.floor(ms / 1000))
  const minutes = Math.floor(total / 60)
  return minutes > 0 ? `${minutes}分${pad2(total % 60)}秒` : `${total % 60}秒`
}

export function formatLatencySeconds(ms: number): string {
  const s = Math.max(0, ms) / 1000
  return s < 10 ? String(Math.round(s * 10) / 10) : String(Math.round(s))
}

export function formatTokensPerSecond(tps: number): string {
  const c = Math.max(0, tps)
  return c >= 10 ? String(Math.round(c)) : String(Math.round(c * 10) / 10)
}
