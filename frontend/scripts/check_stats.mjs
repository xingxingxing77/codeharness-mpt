/** StatsLine 算术的自测：node --experimental-strip-types frontend/scripts/check_stats.mjs
 *  纯函数 + 断言，无框架。守两条：① 取不到的数不许变成 0 顶上去；② 紧凑格式化不吞量级。 */
import { deriveStats, formatDuration, formatTokens, statsGroups, tokenTotals } from '../src/utils/stats.ts'

let failed = 0
function eq(name, got, want) {
  const ok = JSON.stringify(got) === JSON.stringify(want)
  if (!ok) failed++
  console.log(`${ok ? '✅' : '❌'} ${name}${ok ? '' : ` 得到 ${JSON.stringify(got)} 期望 ${JSON.stringify(want)}`}`)
}

const blk = (type, ts, lastTs, tokens = ['x']) => ({ type, ts, lastTs, tokens })
const span = (t0, ft, ts, pt = 0, ct = 0) => ({ node: 'act', pt, ct, cost: 0, ts, t0, ft })

// 1) 紧凑格式
eq('517 tok 原样', formatTokens(517), '517')
eq('1000 tok → 1K', formatTokens(1000), '1K')
eq('12234 tok → 12.2K', formatTokens(12234), '12.2K')
eq('1234567 tok → 1.2M', formatTokens(1234567), '1.2M')
eq('45.2 秒', formatDuration(45200), '45.2s')
eq('2 分 42 秒', formatDuration(162000), '2m42s')

// 2) 轮数：没有 User 也有一轮；User 只切轮不计数
eq('无 User 的一段输出算一轮', deriveStats([blk('Thought', 10, 12)], []).turns, 1)
eq('两轮', deriveStats(
  [blk('User', 1, 1), blk('Thought', 2, 4), blk('User', 5, 5), blk('Docs', 6, 9)], []
).turns, 2)
eq('只有用户发言不算一轮', deriveStats([blk('User', 1, 1)], []).turns, 0)

// 3) 工具耗时：单事件块（Docs 整份下发）天然 0，真跑起来的 Terminal 才有读数
const toolOnly = deriveStats([blk('Docs', 100, 100), blk('Terminal', 200, 203)], [])
eq('Terminal 3 秒', toolOnly.toolMs, 3000)
eq('Thought 不计工具耗时', deriveStats([blk('Thought', 100, 400)], []).toolMs, 0)
eq('Docs 的毫秒抖动不算工具耗时', deriveStats([blk('Docs', 100, 100.03)], []).toolMs, 0)
eq('没有 Terminal 就没有工具耗时段', statsGroups(deriveStats([blk('Docs', 100, 100.03)], [span(1, 2, 3, 5, 5)]),
  { input: 5, output: 5 }).some((g) => g.includes('工具调用')), false)

// 4) TTFT 的分子分母同批：只有 ft 没有 t0 的调用不进 ttftSteps
const mix = deriveStats([], [span(10, 10.5, 12, 100, 40), span(null, 20.5, 22, 100, 40)])
eq('步数=span 条数', mix.steps, 2)
eq('LLM 耗时只算带 t0 的', mix.llmMs, 2000)
eq('ttftSteps 只算双端齐的', mix.ttftSteps, 1)
eq('平均 TTFT 0.5 秒', mix.ttftMs / mix.ttftSteps, 500)
eq('解码 token 累计', mix.decodeTokens, 80)

// 5) 分组：没数据的整组消失
eq('零调用不画计数组', statsGroups(deriveStats([], []), { input: 0, output: 0 }), [])
const full = statsGroups(mix, { input: 200, output: 80 })
eq('计数组在最前', full[0], '0 轮 · 2 步')
eq('速度组含平均 TTFT 与 tok/s', /首 token 平均 0\.5s · .* tok\/s/.test(full[2]), true)
eq('账单组', full.at(-1), '输入 200 tok · 输出 80 tok')
eq('无 LLM 耗时的组不出现', statsGroups(deriveStats([], [span(null, 1, 2, 5, 5)]), { input: 5, output: 5 })
  .some((g) => g.includes('LLM')), false)

// 6) token 口径：账本优先，账本空着才退到 span 累加
eq('账本优先', tokenTotals([span(1, 2, 3, 7, 7)], { total_prompt_tokens: 999, total_completion_tokens: 111 }),
  { input: 999, output: 111 })
eq('账本为空退 span', tokenTotals([span(1, 2, 3, 7, 9)], {}), { input: 7, output: 9 })

console.log(failed ? `\n${failed} 条失败` : '\n全过')
process.exit(failed ? 1 : 0)
