/** C91 自测：「加载更早」的整页回放**不许**改实时状态。
 *  跑法：node frontend/scripts/check_page_replay.mjs
 *
 *  背景：`mergeEarlierPage` 借道 `applyEvent` 合成块，而那个 switch 里混着两类事件——
 *  「产生块/日志」（可回放：调用方对这两份做了快照还原）与「写实时状态」（status/cost/
 *  humanQuestion/approvals/queue/feedback/goal，**不可回放**）。三层判据：
 *    ① 回放期那六类一律不落地（实时状态整份不变）；
 *    ② 同一批事件在**非回放**路径下照旧落地（证明跳过只作用于回放，不是把功能改没了）；
 *    ③ 块仍被合成并前拼（证明回放没被整段跳过）——① 的阳性对照。
 *
 *  为什么先打包再跑：node 的 ESM 解析不了 `sessions.ts` 里那些**无扩展名**的相对导入
 *  （vite/esbuild 解析得了，node 不行）。这里用 vite 自带的 esbuild 现打一个自包含产物到
 *  tmp 再 import——**零新依赖**，也不改源码的导入风格。
 */
import { buildSync } from 'esbuild'
import { mkdtempSync, writeFileSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'

const storePath = fileURLToPath(new URL('../src/stores/sessions.ts', import.meta.url)).replaceAll('\\', '/')
const dir = mkdtempSync(join(tmpdir(), 'ch_pagereplay_'))
const entry = join(dir, 'entry.ts')
const out = join(dir, 'bundle.mjs')
writeFileSync(entry, `export { createPinia, setActivePinia } from 'pinia'\nexport { useSessionStore } from '${storePath}'\n`)
// 入口在 tmp 里，靠 nodePaths 把 `pinia` 指到前端的 node_modules（否则 esbuild 解析不到裸名）
const nodeModules = fileURLToPath(new URL('../node_modules', import.meta.url)).replaceAll('\\', '/')
buildSync({ entryPoints: [entry], outfile: out, bundle: true, format: 'esm', platform: 'node',
            logLevel: 'silent', nodePaths: [nodeModules] })
const { createPinia, setActivePinia, useSessionStore } = await import(pathToFileURL(out).href)

let failed = 0
function eq(name, got, want) {
  const ok = JSON.stringify(got) === JSON.stringify(want)
  if (!ok) failed++
  console.log(`${ok ? '✅' : '❌'} ${name}${ok ? '' : ` 得到 ${JSON.stringify(got)} 期望 ${JSON.stringify(want)}`}`)
}

const ev = (kind, o = {}) => ({ kind, cursor: o.cursor || '0000000000000-000000', seq: o.seq || 1, ts: 1, ...o })

setActivePinia(createPinia())
const s = useSessionStore()

/* ---- 实时状态：一场正停在「等你回答」的会话（还有一张待批卡、一条队列、一票、一个目标） ---- */
s.currentId = 's1'
s.sessions = [{ id: 's1', status: 'awaiting_human', goal: 'G-now', goal_done_at: '' }]
s.status = 'awaiting_human'
s.cost = { cny: 1.23 }
s.humanQuestion = ev('ask_human', { value: { question: '现在这个' } })
s.approvals = [{ id: 'live-card' }]
s.queue = [{ id: 'live-q' }]
s.feedback = { 'later:key': 'like' }
s.blocks = { 'live-block': { key: 'live-block', type: 'Assistant', tokens: ['B'], live: [], lines: [], raw: [], closed: false } }
s.blockOrder = ['live-block']
s.logs = ['live-log']
s.lastCursor = '0000000000009-000000'
s.lastSeq = 9

/* ---- 一页历史：六类「写实时状态」的事件（**status 排最后**——否则页里靠后的
 *  ask_human/approval 会把它写回 awaiting_human，那条断言就变成没有判别力的） ---- */
const page = [
  ev('ask_human', { cursor: '0000000000001-000000', seq: 1, value: { question: '早就答过的' } }),
  ev('approval', { cursor: '0000000000002-000000', seq: 2, name: 'requested', value: { id: 'old-card' } }),
  ev('queue', { cursor: '0000000000003-000000', seq: 3, name: 'add', value: { items: [{ id: 'old-q' }] } }),
  ev('feedback', { cursor: '0000000000004-000000', seq: 4, value: { key: 'later:key', vote: 'dislike' } }),
  ev('goal', { cursor: '0000000000005-000000', seq: 5, value: { objective: 'G-old' } }),
  ev('status', { cursor: '0000000000006-000000', seq: 6, value: { status: 'running', cost: { cny: 0.01 } } }),
  ev('report', { cursor: '0000000000007-000000', seq: 7, uuid: 'live-block', block: 'Assistant', name: 'content', value: 'A' }),
  ev('report', { cursor: '0000000000008-000000', seq: 8, uuid: 'old-block', block: 'Assistant', name: 'content', value: 'Z' }),
]
const pageLogs = [ev('log', { cursor: '0000000000009-000000', seq: 9, value: 'old-log' })]

s.mergeEarlierPage([...page, ...pageLogs])

/* ① 六类实时状态：整份不许动 */
eq('状态没被旧 status 盖掉', s.status, 'awaiting_human')
eq('顶栏金额没被旧 cost 盖掉', s.cost, { cny: 1.23 })
eq('问答卡还是当前那张（旧问答没顶上来）', s.humanQuestion?.value?.question, '现在这个')
eq('审批卡没冒出第二张', s.approvals.map((a) => a.id), ['live-card'])
eq('插话队列没被旧 add 加料', s.queue.map((q) => q.id), ['live-q'])
eq('票没被旧 feedback 改掉', s.feedback, { 'later:key': 'like' })
eq('会话目标没被旧 goal 盖掉', s.sessions[0].goal, 'G-now')
eq('会话状态没被旧 status 写进列表', s.sessions[0].status, 'awaiting_human')

/* ② 回放期该干的照干：块/日志合成、游标不回退 */
eq('新块已前拼', s.blockOrder, ['old-block', 'live-block'])
eq('同 key 的旧半截排在新半截前面（顺序没颠倒）', s.blocks['live-block'].tokens, ['A', 'B'])
eq('日志前拼', s.logs, ['old-log', 'live-log'])
eq('游标不回退（SSE 续推位不许往回走）', [s.lastCursor, s.lastSeq], ['0000000000009-000000', 9])

/* ③ 阳性对照：同一批事件在**非回放**路径下必须照旧落地（否则这条修法把功能改没了）。
 *  ⚠ 游标必须比**实时**游标大——回放把 lastCursor/lastSeq 还原成实时那份之后，再喂旧游标的
 *  事件会被去重挡在门口，那测的是去重不是本项（第一版判据就栽在这，形如「拿旧游标测新事件」）。 */
let liveN = 10
const live = (kind, o = {}) =>
  ev(kind, { cursor: `${++liveN}`.padStart(13, '0') + '-000000', seq: liveN, ...o })
s.applyEvent(live('status', { value: { status: 'running', cost: { cny: 0.5 } } }))
eq('非回放的 status 照旧生效', s.status, 'running')
eq('非回放的 cost 照旧生效', s.cost, { cny: 0.5 })
s.applyEvent(live('ask_human', { value: { question: '早就答过的' } }))
eq('非回放的 ask_human 照旧占住座位', s.humanQuestion?.value?.question, '早就答过的')
s.applyEvent(live('feedback', { value: { key: 'later:key', vote: 'dislike' } }))
eq('非回放的 feedback 照旧改票', s.feedback, { 'later:key': 'dislike' })
s.applyEvent(live('goal', { value: { objective: 'G-old' } }))
eq('非回放的 goal 照旧落库', s.sessions[0].goal, 'G-old')

rmSync(dir, { recursive: true, force: true })
console.log(failed ? `\n${failed} 条失败` : '\n全过')
process.exit(failed ? 1 : 0)
