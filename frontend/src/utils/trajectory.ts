import type { Block, TraceSpan } from '../types'

/** Trajectory 台账一行 = 一次 LLM 调用（/trace 的一个 span）+ 它这段时间里产出的块。
 *
 *  ponytail: 参考项目的轨迹视图能展开每条记录的**原始请求/响应体**，我们看不到——
 *  `/trace` 只落了节点名与 token/成本增量，prompt 与响应原文都没落盘。所以这里的
 *  台账是「用量 + 产出」台账，不是报文检视。升级路径：给 span 补 t0 与请求/响应摘要
 *  （`server/runner.py` 接 `on_chat_model_start`，并把 messages 的指纹存进 trace）。 */
export interface TrajRow {
  idx: number
  node: string
  /** 调用结束时刻，unix 秒。 */
  ts: number
  /** 本次调用耗时 ms = ts − t0。批次16 之前的 span 没有 t0，留 undefined。 */
  durMs?: number
  pt: number
  ct: number
  cost: number
  /** 产出块：块首事件 ts 落在「上一次调用结束 ~ 本次调用结束」窗口内。 */
  blocks: Block[]
}

/** 把 span 序列与块序列按时间窗对上。span 与块都按 ts 单调，所以是一次线性扫描。 */
export function buildTrajectory(spans: TraceSpan[], blocks: Block[]): TrajRow[] {
  const ordered = [...spans].sort((a, b) => a.ts - b.ts)
  const timed = blocks.filter((b): b is Block & { ts: number } => typeof b.ts === 'number')
  const rows: TrajRow[] = []
  let cursor = -Infinity
  ordered.forEach((s, i) => {
    // 时间窗归属：块可能不是严格按调用顺序落 ts（并发节点），所以整表扫一遍而不是一路推进。
    // ponytail: O(调用数 × 块数)。当前一次跑几十调用 × 几十块，无所谓；
    // 真到需要虚拟化的量级再按 ts 排序后双指针。
    const produced = timed.filter((b) => b.ts > cursor && b.ts <= s.ts)
    cursor = s.ts
    rows.push({
      idx: i + 1,
      node: s.node || '—',
      ts: s.ts,
      ...(s.t0 ? { durMs: Math.max(0, (s.ts - s.t0) * 1000) } : {}),
      pt: s.pt,
      ct: s.ct,
      cost: s.cost,
      blocks: produced,
    })
  })
  return rows
}

/** 一行的产出简述：`Thought×2 · Docs×1` 这种计数串，全空回 '—'。 */
export function producedLabel(blocks: Block[]): string {
  const counts = new Map<string, number>()
  for (const b of blocks) counts.set(b.type, (counts.get(b.type) || 0) + 1)
  if (!counts.size) return '—'
  return [...counts].map(([t, n]) => `${t}×${n}`).join(' · ')
}
