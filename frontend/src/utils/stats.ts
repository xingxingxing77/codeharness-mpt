import type { Block, TraceSpan } from '../types'
import { formatTokensPerSecond } from './messageChrome.ts'

/** 参考项目 `ui-conversation/src/client/chat/StatsLine.tsx:deriveStats` 的对应物：
 *  把块表与 /trace 的 span 折成一组窗口级显示总量。字段名照抄，分组规则照抄——
 *  **采不到的数就整组不出现**，不拿 0 顶（我们既没有 cacheReadTokens 也没有
 *  contextWindow，那两组天然不画）。
 *
 *  与参考项目的口径差（数据面决定的，不是偷懒）：
 *  - `steps` = span 条数：我们一笔 span 就是一次 LLM 调用，与它的 step 同义。
 *  - `toolMs` 只从 Terminal 取，理由见 toolWallMs。 */
export interface WindowStats {
  turns: number
  steps: number
  llmMs: number
  toolMs: number
  ttftMs: number
  ttftSteps: number
  decodeMs: number
  decodeTokens: number
}

/** 工具墙钟时间只认 Terminal：它是事件词汇表里唯一有「调用→结果」两次事件的块
 *  （cmd 先、output 若干），`ts → lastTs` 就是命令真的跑了多久。
 * ponytail: 天花板——Docs/Editor 这些后端整份一次下发的块，ts 与 lastTs 只差几十毫秒的
 *  事件到达抖动，逐块累加会给出「工具调用 0s」这种没有证据的读数，所以一律算 0。
 *  升级路径：后端给工具也记 span（与 LLM span 同表），届时这里改成读 span。 */
const toolWallMs = (b: Block): number =>
  b.type === 'Terminal' && b.ts !== undefined && b.lastTs !== undefined
    ? Math.max(0, (b.lastTs - b.ts) * 1000)
    : 0

/** 一轮 = 一条用户发言到下一条之间的全部助手输出（与 utils/turns.ts 同一断轮依据）。
 *  只有 User 没有输出的那段不算一轮。 */
function countTurns(blocks: Block[]): number {
  let turns = 0
  let hasOutput = false
  for (const b of blocks) {
    if (b.type === 'User') {
      if (hasOutput) turns += 1
      hasOutput = false
    } else if (b.type !== 'Thought' || b.tokens.length) {
      hasOutput = true
    }
  }
  return turns + (hasOutput ? 1 : 0)
}

export function deriveStats(blocks: Block[], spans: TraceSpan[]): WindowStats {
  let llmMs = 0
  let toolMs = 0
  let ttftMs = 0
  let ttftSteps = 0
  let decodeMs = 0
  let decodeTokens = 0
  for (const b of blocks) toolMs += toolWallMs(b)
  for (const s of spans) {
    if (s.t0) llmMs += Math.max(0, (s.ts - s.t0) * 1000)
    // TTFT 的分子与分母必须同一批：只有一端就计进 ttftSteps 会把平均值算小。
    if (s.t0 && s.ft) {
      ttftSteps += 1
      ttftMs += Math.max(0, (s.ft - s.t0) * 1000)
    }
    if (s.ft) {
      decodeMs += Math.max(0, (s.ts - s.ft) * 1000)
      decodeTokens += s.ct || 0
    }
  }
  return {
    turns: countTurns(blocks),
    steps: spans.length,
    llmMs,
    toolMs,
    ttftMs,
    ttftSteps,
    decodeMs,
    decodeTokens,
  }
}

/** 紧凑 token 数：517 / 12.2K / 517K / 1.2M（三位数以下留一位小数）。 */
export function formatTokens(n: number): string {
  const scaled = (v: number): string => (v >= 100 ? String(Math.round(v)) : String(Math.round(v * 10) / 10))
  if (n < 1000) return String(n)
  if (n < 1_000_000) return `${scaled(n / 1000)}K`
  return `${scaled(n / 1_000_000)}M`
}

/** 紧凑时长：不到一分 45.2s，往后 2m42s。 */
export function formatDuration(ms: number): string {
  const s = ms / 1000
  if (s < 60) return `${Math.round(s * 10) / 10}s`
  const whole = Math.round(s)
  return `${Math.floor(whole / 60)}m${whole % 60}s`
}

/** 会话账本（GET /api/sessions 的 cost）与 span 之和取其一：账本是全量持久数，
 *  只有它空着时才退回 span 累加。 */
export function tokenTotals(
  spans: TraceSpan[],
  cost?: { total_prompt_tokens?: number; total_completion_tokens?: number }
): { input: number; output: number } {
  const input = cost?.total_prompt_tokens || 0
  const output = cost?.total_completion_tokens || 0
  if (input || output) return { input, output }
  return spans.reduce(
    (a, s) => ({ input: a.input + (s.pt || 0), output: a.output + (s.ct || 0) }),
    { input: 0, output: 0 }
  )
}

/** 竖线分组（参考项目的 stats strip）：每段内部用「·」，段间用「 | 」，没数据的整段丢掉。 */
export function statsGroups(stats: WindowStats, usage: { input: number; output: number }): string[] {
  const groups: string[] = []
  if (stats.steps > 0) {
    groups.push(`${stats.turns} 轮 · ${stats.steps} 步`)
    const durations: string[] = []
    if (stats.llmMs > 0) durations.push(`LLM ${formatDuration(stats.llmMs)}`)
    if (stats.toolMs > 0) durations.push(`工具调用 ${formatDuration(stats.toolMs)}`)
    if (durations.length) groups.push(durations.join(' · '))
    const speeds: string[] = []
    if (stats.ttftSteps > 0) speeds.push(`首 token 平均 ${formatDuration(stats.ttftMs / stats.ttftSteps)}`)
    if (stats.decodeMs > 0) {
      speeds.push(`${formatTokensPerSecond(stats.decodeTokens / (stats.decodeMs / 1000))} tok/s`)
    }
    if (speeds.length) groups.push(speeds.join(' · '))
  }
  if (usage.input > 0 || usage.output > 0) {
    groups.push(`输入 ${formatTokens(usage.input)} tok · 输出 ${formatTokens(usage.output)} tok`)
  }
  return groups
}
