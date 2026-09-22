import type { Block, TraceSpan } from '../types'
import { formatTokensPerSecond } from './messageChrome.ts'

/** 参考项目 `ui-conversation/src/client/chat/StatsLine.tsx:deriveStats` 的对应物：
 *  把块表与 /trace 的 span 折成一组窗口级显示总量。字段名照抄，分组规则照抄——
 *  **采不到的数就整组不出现**，不拿 0 顶（我们既没有 cacheReadTokens 也没有
 *  contextWindow，那两组天然不画）。
 *
 *  与参考项目的口径差（数据面决定的，不是偷懒）：
 *  - `steps` = span 条数：我们一笔 span 就是一次 LLM 调用，与它的 step 同义。
 *  - `toolMs` 只从 Terminal 取，理由见 toolWallMs。
 *  - 参考项目只折算**窗口平均**（`ui-conversation/src/client/chat/turn-metrics.ts` 与
 *    `StatsLine` 那套：持久投影必须 O(1)，只能存均值存不了分位数；窗口外的历史不计）。
 *    我们这边 `/trace` 一次把全量 span（≤5000 条）下发完，样本就在手边，所以多折一个 P95——
 *    尾部延迟恰是「平均 0.4s」那种读数藏住的缺陷。缺样本的口径照它：整组不出现，不顶 0。 */
export interface WindowStats {
  turns: number
  steps: number
  llmMs: number
  toolMs: number
  ttftMs: number
  ttftSteps: number
  /** 首 token 延迟的 P95（nearest-rank，样本集与 `ttftMs/ttftSteps` 同一批）。 */
  ttftP95Ms: number
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

/** nearest-rank 分位数（样本须升序）：n=1 时任何分位都落在那唯一一条上；空样本回 0，
 *  渲染侧靠 `ttftSteps > 0` 决定画不画（参考项目口径：取不到的数整组不出现，不拿 0 顶）。 */
function quantileAsc(asc: number[], q: number): number {
  return asc.length ? asc[Math.max(0, Math.ceil(q * asc.length) - 1)] : 0
}

export function deriveStats(blocks: Block[], spans: TraceSpan[]): WindowStats {
  let llmMs = 0
  let toolMs = 0
  let ttftMs = 0
  let decodeMs = 0
  let decodeTokens = 0
  const ttfts: number[] = []
  for (const b of blocks) toolMs += toolWallMs(b)
  for (const s of spans) {
    if (s.t0) llmMs += Math.max(0, (s.ts - s.t0) * 1000)
    // TTFT 的分子与分母必须同一批：只有一端就计进 ttftSteps 会把平均值算小。
    // 同一批也是 P95 的样本集——两个数才能并排读（均值 0.4s / P95 1.2s 说的是同一 17 笔）。
    if (s.t0 && s.ft) {
      const ms = Math.max(0, (s.ft - s.t0) * 1000)
      ttfts.push(ms)
      ttftMs += ms
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
    ttftSteps: ttfts.length,
    ttftP95Ms: quantileAsc(ttfts.sort((a, b) => a - b), 0.95),
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

/** B4 聚合：把会话表里的票按种类数一遍。真值就在 `Session.feedback`（键=尾行、值=like|dislike），
 *  所以不另开端点。**一张票是一个 (会话, 尾行) 对，不能按会话数**——一场会话投三票就是三票。
 *  认不出的值进 `other`：后端 validator 今天只放 like/dislike 进来，但这里不静默把第三种吞成任何一档。 */
export function countVotes(rows: { feedback?: Record<string, string> }[]):
  { like: number; dislike: number; other: number } {
  const out = { like: 0, dislike: 0, other: 0 }
  for (const r of rows) {
    for (const v of Object.values(r.feedback || {})) {
      if (v === 'like') out.like++
      else if (v === 'dislike') out.dislike++
      else out.other++
    }
  }
  return out
}

/** B4/T4-③ 同一条路：跨会话求和直接吃 `GET /api/sessions` 带出的那份 cost 快照，不开新端点。
 *  两个口径分开报——截断是端点把回答切了，未知命令是模型要的工具有些没给它看见，
 *  混成一个"浪费数"就没人知道该去修哪一个。 */
export function wasteTotals(rows: { cost?: Record<string, number> }[]):
  { unknown: number; truncated: number } {
  let unknown = 0
  let truncated = 0
  for (const r of rows) {
    unknown += r.cost?.unknown_command_calls ?? 0
    truncated += r.cost?.truncated_calls ?? 0
  }
  return { unknown, truncated }
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
    if (stats.ttftSteps > 0) {
      // 采样数一起报：十几笔的会话上 P95 ≈ 最大值，不标 n 会被读成稳态值；
      // 分母是总步数，读者一眼看出「有 3/20 笔没采到首 token」。
      speeds.push(`首 token 平均 ${formatDuration(stats.ttftMs / stats.ttftSteps)}`
        + ` · P95 ${formatDuration(stats.ttftP95Ms)}（${stats.ttftSteps}/${stats.steps} 笔）`)
    }
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
