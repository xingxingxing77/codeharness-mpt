import type { Block, TraceSpan } from '../types'

/** 轮次聚合：一条用户发言到下一条之间的全部助手输出算一轮，轮尾挂 TurnTail
 *  （复制 + 时钟 + 用时/TTFT/tok/s）。对齐参考项目的 turn/step 模型——它的
 *  turn 也是「一次用户请求到收口」，中间的多次 LLM 调用是 step。
 *
 *  为什么不按 `role` 断轮：后端两条发射路径写的不是同一个名字——报道槽写角色名
 *  （PM/Architect/Engineer…），打字机流写 langgraph 节点名（act/think…），
 *  同一个角色的一段工作会被节点名切成两轮，尾行就落到句子中间了。 */
export interface Turn {
  key: string
  blocks: Block[]
  /** 收口轮的末块时间，秒。 */
  startTs: number
  endTs: number
  /** B3：本轮最后一个事件的游标——分叉就切在这里（含本轮）。 */
  endCursor?: string
  /** 最后一个有正文的块：复制取它，没有正文则尾行只剩时间读数。 */
  closing: Block | null
  runMs?: number
  ttftMs?: number
  tokensPerSecond?: number
}

export type ChatRow = { kind: 'node'; b: Block } | { kind: 'tail'; turn: Turn }

export const blockText = (b: Block): string => b.tokens.join('')

function summarize(blocks: Block[], spans: TraceSpan[]): Turn | null {
  const starts = blocks.map((b) => b.ts).filter((n): n is number => typeof n === 'number')
  if (!starts.length) return null
  const startTs = Math.min(...starts)
  const ends = blocks.map((b) => b.lastTs ?? b.ts).filter((n): n is number => typeof n === 'number')
  const endTs = ends.length ? Math.max(...ends) : startTs
  const firstTokens = blocks.map((b) => b.fts).filter((n): n is number => typeof n === 'number')
  const firstTokenTs = firstTokens.length ? Math.min(...firstTokens) : undefined
  const closing = [...blocks].reverse().find((b) => blockText(b).trim() !== '') ?? null
  // TTFT 取本轮窗口里第一笔同时带 t0 与 ft 的调用（span 由 server/runner.py 按 run_id 攒）。
  // 真实限制：这两个字段是批次16 才加的，**只有那之后新跑的会话才有**，老会话的读数自然缺席
  // ——宁缺不假，这里不会退化成 0 秒。structured 输出不走打字机，ft 为 null 同样跳过。
  // tok/s 只能从 /trace 的 ct 增量拿：块事件里没有 provider usage。
  const window = spans.filter((s) => s.ts >= startTs && s.ts <= endTs)
  const completion = window.reduce((a, s) => a + (s.ct || 0), 0)
  const decodeMs = firstTokenTs === undefined ? 0 : (endTs - firstTokenTs) * 1000
  const timed = window.find((s) => s.t0 && s.ft)
  return {
    // 游标是定宽补零串，字典序即到达序（SSE 那条铁律），所以直接排序取最后一个
    endCursor: blocks.map((b) => b.endCursor).filter((x): x is string => !!x).sort().at(-1),
    key: `t:${blocks[0].key}`,
    blocks,
    startTs,
    endTs,
    closing,
    runMs: Math.max(0, (endTs - startTs) * 1000),
    ...(timed ? { ttftMs: Math.max(0, (timed.ft! - timed.t0!) * 1000) } : {}),
    ...(completion > 0 && decodeMs > 0 ? { tokensPerSecond: completion / (decodeMs / 1000) } : {}),
  }
}

/** 把块表摊成「节点行 + 轮尾行」的一维序列，供 v-for 直接渲染。 */
export function buildRows(blocks: Block[], spans: TraceSpan[]): ChatRow[] {
  const rows: ChatRow[] = []
  let cur: Block[] = []
  const flush = () => {
    // 末块还没收到 end_marker 就说明这一轮仍在跑：参考项目也是 turn/end 才发布尾行，
    // 否则「用时」会在流式期先冒出来逐帧跳。
    const settled = cur.length && cur[cur.length - 1].closed
    const turn = settled ? summarize(cur, spans) : null
    if (turn) rows.push({ kind: 'tail', turn })
    cur = []
  }
  for (const b of blocks) {
    if (b.type === 'User') {
      flush()
      rows.push({ kind: 'node', b })
      continue
    }
    cur.push(b)
    rows.push({ kind: 'node', b })
  }
  flush()
  return rows
}
