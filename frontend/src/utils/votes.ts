/** B4 票的读法与两种视图（按票筛 / 按时间分）。
 *
 * 为什么单独一个文件：票值有两种形态，而**归一规则必须只有一份**——
 *   · 裸字符串 `"like"` ＝ 09-23 之前投的旧票，**没有时刻**（后端 `_vote_of` 同一口径）；
 *   · `{"v": "like", "at": "2026-09-23 02:5x:xx"}` ＝ 新票。
 * 两处各写一份就会长出「后端说有票、图标说不亮」这种打脸。
 *
 * ⚠ 旧票没时间戳是**事实缺失**，不是 0：按时间分那一视图里它们单独成一档「未记时刻」，
 *   绝不拿 `created_at` 顶上去（同 `utils/stats.ts` 那条「取不到的数不许变成 0 顶上去」）。 */

export type VoteEntry = string | { v?: string; at?: string }

export function parseVote(entry: VoteEntry | undefined): { vote: string; at: string | null } {
  if (entry == null) return { vote: '', at: null }
  if (typeof entry === 'string') return { vote: entry, at: null }      // 旧票：有票、无时刻
  return { vote: entry.v || '', at: entry.at || null }
}

/** 会话图标那一层只吃票种，不吃时刻 ⇒ 归一成 `key -> "like"|"dislike"` 再交出去。 */
export function normalizeVotes(map: Record<string, VoteEntry>): Record<string, string> {
  const out: Record<string, string> = {}
  for (const [k, entry] of Object.entries(map || {})) {
    const v = parseVote(entry).vote
    if (v) out[k] = v
  }
  return out
}

export function sessionVote(map: Record<string, VoteEntry> | undefined): 'like' | 'dislike' | '' {
  const vals = Object.values(map || {}).map((e) => parseVote(e).vote)
  if (vals.includes('dislike')) return 'dislike'      // 一票会话里同时有两种时，"没用"优先（它是行动信号）
  return vals.includes('like') ? 'like' : ''
}

export type VoteFilter = 'all' | 'any' | 'none' | 'like' | 'dislike'

export function filterRowsByVote<T extends { feedback?: Record<string, VoteEntry> }>(
  rows: T[], kind: VoteFilter): T[] {
  if (kind === 'all') return rows
  return rows.filter((r) => {
    const v = sessionVote(r.feedback)
    return kind === 'any' ? !!v : kind === 'none' ? !v : v === kind
  })
}

/** 按天分桶。`at` 的格式与 `created_at` 同源（`_now()` 的 `YYYY-MM-DD HH:MM:SS`），
 *  所以取日只需切前 10 位——不去解析时区，因为两份时间戳本来就是同一台机器的同一种串。 */
export function voteBuckets(rows: { feedback?: Record<string, VoteEntry> }[]):
  { days: { day: string; like: number; dislike: number }[], undated: { like: number; dislike: number } } {
  const byDay = new Map<string, { like: number; dislike: number }>()
  const undated = { like: 0, dislike: 0 }
  for (const r of rows) {
    for (const entry of Object.values(r.feedback || {})) {
      const { vote, at } = parseVote(entry)
      if (vote !== 'like' && vote !== 'dislike') continue      // 认不出的值不进任何桶（与用量页那一格同一口径）
      if (!at) { undated[vote]++; continue }
      const day = at.slice(0, 10)
      const b = byDay.get(day) || { like: 0, dislike: 0 }
      b[vote]++
      byDay.set(day, b)
    }
  }
  return { days: [...byDay.entries()].sort((a, b) => (a[0] < b[0] ? 1 : -1))
    .map(([day, v]) => ({ day, ...v })), undated }
}
