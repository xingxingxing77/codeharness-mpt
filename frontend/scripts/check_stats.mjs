/** StatsLine 算术的自测：node --experimental-strip-types frontend/scripts/check_stats.mjs
 *  纯函数 + 断言，无框架。守三条：① 取不到的数不许变成 0 顶上去；② 紧凑格式化不吞量级；
 *  ③ 分位数（C12 的 P95 半边）与均值共用同一批样本，缺端点的调用不进样本集。 */
import { countVotes, deriveStats, formatDuration, formatTokens, statsGroups, tokenTotals } from '../src/utils/stats.ts'

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

// 4b) 分位数（C12 的 P95 半边）：样本集与均值同一批，缺端点的调用根本不进样本
const mk = (n) => Array.from({ length: n }, (_, k) => {
  const i = k + 1
  const t0 = 1000 + i * 16
  // 增量取 1/8 秒：二进制里精确，读数不会漂成 1900.0000000000002
  return span(t0, t0 + i * 0.125, t0 + i * 0.125 + 0.25, 10, 10)
})   // 第 i 笔的首 token 延迟 = i*125ms
const twenty = deriveStats([], mk(20))                    // 样本 125..2500ms
eq('20 笔都进样本', twenty.ttftSteps, 20)
eq('P95 = 第 19 小（2375ms），不是最大值 2500', twenty.ttftP95Ms, 2375)
eq('乱序输入照样分位（打过排序）', deriveStats([], [...mk(20)].reverse()).ttftP95Ms, 2375)
eq('单笔样本：任何分位都落在那一笔上', deriveStats([], mk(1)).ttftP95Ms, 125)
const clamp = deriveStats([], [span(10, 9.5, 12)])
eq('ft 早于 t0 钳到 0 而不是负数（样本仍在：n=1）', [clamp.ttftSteps, clamp.ttftP95Ms], [1, 0])
eq('双端缺一端 → 没样本 → P95 不假装', deriveStats([], [span(null, 10.5, 12), span(10, null, 12)]).ttftP95Ms, 0)
eq('均值与 P95 说的是同一批', twenty.ttftMs / twenty.ttftSteps, 1312.5)

// 5) 分组：没数据的整组消失
eq('零调用不画计数组', statsGroups(deriveStats([], []), { input: 0, output: 0 }), [])
const full = statsGroups(mix, { input: 200, output: 80 })
eq('计数组在最前', full[0], '0 轮 · 2 步')
eq('速度组含平均 TTFT 与 tok/s', /首 token 平均 0\.5s · .* tok\/s/.test(full[2]), true)
eq('速度组含 P95 与采样数（1/2 笔）', full[2].includes('P95 0.5s（1/2 笔）'), true)
eq('没采到首 token 就不许出现 P95 字样',
  statsGroups(deriveStats([], [span(null, 1, 2, 5, 5)]), { input: 5, output: 5 })
    .some((g) => g.includes('P95')), false)
eq('账单组', full.at(-1), '输入 200 tok · 输出 80 tok')
eq('无 LLM 耗时的组不出现', statsGroups(deriveStats([], [span(null, 1, 2, 5, 5)]), { input: 5, output: 5 })
  .some((g) => g.includes('LLM')), false)

// 6) token 口径：账本优先，账本空着才退到 span 累加
eq('账本优先', tokenTotals([span(1, 2, 3, 7, 7)], { total_prompt_tokens: 999, total_completion_tokens: 111 }),
  { input: 999, output: 111 })
eq('账本为空退 span', tokenTotals([span(1, 2, 3, 7, 9)], {}), { input: 7, output: 9 })

// 7) B4 票聚合：一张票 = 一个 (会话, 尾行) 对，**不是**一个会话
eq('按票不按会话（一场两票 + 另一场一票）',
  countVotes([{ feedback: { a: 'like', b: 'like' } }, { feedback: { c: 'dislike' } }]),
  { like: 2, dislike: 1, other: 0 })
eq('改票不重复计（同 key 覆盖后只剩一张）',
  countVotes([{ feedback: { a: 'dislike' } }]), { like: 0, dislike: 1, other: 0 })
eq('没有会话 → 三档全零（不藏格也不假亮）', countVotes([]), { like: 0, dislike: 0, other: 0 })
eq('行里根本没 feedback 字段也不炸', countVotes([{}, { cost: {} }]), { like: 0, dislike: 0, other: 0 })
// 认不出的值不许被吞进任何一档：写成 `else out.like++` 这一格当场红
eq('未识别值单列，不进 like 也不进 dislike',
  countVotes([{ feedback: { a: 'spam' } }]), { like: 0, dislike: 0, other: 1 })
eq('混合现场：like/dislike/未识别各归位',
  countVotes([{ feedback: { a: 'like', z: '?' } }, { feedback: { b: 'dislike' } }, {}]),
  { like: 1, dislike: 1, other: 1 })

console.log(failed ? `\n${failed} 条失败` : '\n全过')
process.exit(failed ? 1 : 0)
