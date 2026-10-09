/** 「加载更早」的行为自测（P4 路线三改过口径的那一条）。
 *  跑法：node frontend/scripts/check_page_replay.mjs
 *
 *  这一格在 10-09 之前钉的是另一件事：`mergeEarlierPage` 借道 `applyEvent` 合成块，那个 switch
 *  里混着「产生块/日志」与「写实时状态」两类事件，于是回放老页会把当前状态盖回历史值（C91），
 *  修法是一份名单（`PAGE_REPLAY_OPAQUE`）+ 一个回放期标志。
 *  路线三把那两条一起删了，因为病根是**同一本账被折了两遍、第二条路还是手写的**：
 *  现在「加载更早」= 前插进同一本 append-only 事件日志 + 整本按序重折（`ingestEarlier`→`replayLog`），
 *  「最新的赢」是折的顺序给的，不是名单挡的。所以这一格改钉新的不变量，四条：
 *    ① 前插老页再整本重折 ⇒ 实时槽仍等于**最新那条事件的主张**（七项逐个量）；
 *    ② 块按日志序合成：老页的块排前面，同一块被页边界切开的两半截**顺序不颠倒**；
 *    ③ 阳性对照：把同一批事件按「老页更晚」的顺序喂 ⇒ 主张跟着翻面
 *       （证明 ① 不是写死的，也不是名单挡出来的）；
 *    ④ 未登记的 kind 不进块、不进实时槽，但**必须留在 `unhandled` 里**（有发无看这一族的哨子）。
 *  再加 ⑤：`context/compact` 在真 store 上折得出 `Compact` 块——s8 t1 只查 BlockType，查不到它。
 *
 * 为什么先打包再跑：node 的 ESM 解析不了 `sessions.ts` 里那些**无扩展名**的相对导入
 *  （vite/esbuild 解析得了，node 不行）。用 vite 自带的 esbuild 现打一个自包含产物到 tmp 再 import
 *  ——零新依赖，也不改源码的导入风格。跑的是**真 pinia store + 真 reducer**，不是仿真状态对象。
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

const cur = (n) => `${String(n).padStart(13, '0')}-000000`
const ev = (kind, n, o = {}) => ({ kind, cursor: cur(n), seq: n, ts: 100 + n, ...o })

setActivePinia(createPinia())
const s = useSessionStore()
s.currentId = 's1'
s.sessions = [{ id: 's1', status: 'running', goal: '', goal_done_at: '', cost: {} }]

/* ---- 一场正在跑的会话：先按活流把「当前」折出来（这些事件都在日志的**尾部**） ---- */
const live = [
  ev('report', 10, { uuid: 'b-old', block: 'Thought', role: 'PM', name: 'content', value: '后半截' }),
  ev('status', 11, { value: { status: 'awaiting_human', cost: { cny: 1.23 }, message: '' } }),
  ev('goal', 12, { name: 'edit', value: { objective: 'G-now', done_at: '' } }),
  ev('feedback', 13, { name: 'set', value: { key: 'k1', vote: 'like' } }),
  ev('queue', 14, { name: 'add', value: { items: [{ id: 'live-q', content: '插话', send_to: '' }] } }),
  ev('approval', 15, { name: 'requested', value: { id: 'live-card', tool: 'shell' } }),
  ev('ask_human', 16, { value: '现在这个问题' }),
  // 老页里那张 `old-card` 早就决议过了：决议事件比请求事件**更新**，所以它一定在同一本日志里
  // 排在那条 requested 后面 ⇒ 按序重折会把它撤掉。这一条就是「不许复活」的全部内容，
  // 不需要名单（窗口是裁头的，`resolved` 不可能被裁掉而留下它的 `requested`）。
  ev('approval', 17, { name: 'resolved', value: { id: 'old-card', outcome: 'allowed-once' } })
]
for (const e of live) s.ingest(e)
eq('活流先折出当前态', [s.status, s.cost.cny, s.approvals.length, s.queue.length], ['awaiting_human', 1.23, 1, 1])

/* ---- 一页更早的历史（游标都比上面小），里面写满了「过去」的实时状态主张 ---- */
const older = [
  ev('report', 1, { uuid: 'b-old', block: 'Thought', role: 'PM', name: 'meta', value: { streaming: 'think' } }),
  ev('report', 2, { uuid: 'b-old', block: 'Thought', role: 'PM', name: 'content', value: '前半截' }),
  ev('report', 3, { uuid: 'b-first', block: 'Docs', role: 'Architect', name: 'content', value: '更早的一块' }),
  ev('log', 4, { value: 'old-log' }),
  ev('status', 5, { value: { status: 'running', cost: { cny: 0.01 }, message: '' } }),
  ev('goal', 6, { name: 'create', value: { objective: 'G-old', done_at: '' } }),
  ev('feedback', 7, { name: 'set', value: { key: 'k1', vote: 'dislike' } }),
  ev('queue', 8, { name: 'add', value: { items: [{ id: 'old-q', content: '旧插话', send_to: '' }] } }),
  ev('approval', 9, { name: 'requested', value: { id: 'old-card', tool: 'write_file' } })
]
s.ingestEarlier(older)

/* ---- ① 最新的赢：七项逐个量，全靠折的顺序，不是名单 ---- */
eq('① 状态来自最后一条 status，不是老页那条', s.status, 'awaiting_human')
eq('① 顶栏金额来自最后一条 cost', s.cost, { cny: 1.23 })
eq('① 目标来自最后一条 goal', s.sessions[0].goal, 'G-now')
eq('① 票来自最后一条 feedback', s.feedback, { k1: 'like' })
/* 队列是「基线（GET 那份）在前、老页的 add 补在后面」——显示次序不是游标序，这是刻意的取舍：
   清空重折的话，一枚还排着的插话如果它的 `add` 已被窗口裁掉（窗口裁头），就会从界面上消失，
   而用户既撤不掉也看不见。少一条比次序乱更贵（`protocol/fold.ts::resetFold` 那段记了这条）。 */
eq('① 插话队列只做覆盖：当前那条不丢，老页那条补在后面', s.queue.map((q) => q.id), ['live-q', 'old-q'])
eq('① 已决议的卡没被老页那条 requested 复活', s.approvals.map((a) => a.id), ['live-card'])
eq('① 问答卡是最后那条', s.humanQuestion?.value, '现在这个问题')

/* ---- ② 块的次序与跨页那一块 ---- */
// b-old 这颗在老页就开了（游标 1），所以它排在 b-first（游标 3）之前——次序来自日志，不是来自「哪一页」。
eq('② 老页的块按日志序排在前面', s.blockOrder, ['b-old', 'b-first'])
eq('② 被页边界切开的那一块，正文顺序没颠倒', s.blocks['b-old'].tokens, ['前半截', '后半截'])
eq('② 日志也在前头', s.logs, ['old-log'])
eq('② 游标不因前插而回退（SSE 续推位必须单调）', s.lastCursor, cur(17))

/* ---- ③ 阳性对照：同一批「写实时槽」的事件，这次走**活流**那条路（更晚到）⇒ 必须照旧生效 ----
 *   ① 保住的是「冷折不许把历史值盖回当前」，这一条保住的是「没把功能删没」——
 *   两半合起来才是有牙的：只做 ①，把 runtime 车道整支删掉也照样绿（C91 当年就是这么要求的）。 */
setActivePinia(createPinia())
const s2 = useSessionStore()
s2.currentId = 's2'
s2.sessions = [{ id: 's2', status: 'running', goal: '', goal_done_at: '', cost: {} }]
for (const e of live) s2.ingest(e)
for (const [i, e] of [older[4], older[5], older[6]].entries()) s2.ingest({ ...e, cursor: cur(300 + i), seq: 300 + i })
s2.syncSessionPatch()
eq('③ 活流路径里 status 照旧被最新那条改写', s2.status, 'running')
eq('③ 金额照旧翻面', s2.cost, { cny: 0.01 })
eq('③ 目标照旧翻面（走 sessionPatch）', s2.sessions[0].goal, 'G-old')
eq('③ 票照旧翻面', s2.feedback, { k1: 'dislike' })

/* ---- ④ 未登记的 kind：不进块、不进槽，但要留在 unhandled ---- */
setActivePinia(createPinia())
const s3 = useSessionStore()
s3.currentId = 's3'
s3.ingest(ev('memory', 1, { name: 'flush', value: { n: 3 } }))
s3.ingest(ev('report', 2, { uuid: 'ok', block: 'Thought', name: 'content', value: '还在流' }))
eq('④ 未登记的 kind 进了 unhandled', s3.unhandled, ['memory:flush'])
eq('④ 未登记不许断流：后面那条照样成块', s3.blocks['ok']?.tokens.join(''), '还在流')

/* ---- ⑤ 压缩那一条在真 store 上折得出行（s8 t1 只查 BlockType，查不到这个类型） ---- */
s3.ingest(ev('context', 3, { name: 'compact', role: 'Mike',
  value: { before: 912000, after: 210000, freed: 702000, evicted_n: 37, real_peak_pt: 0 } }))
const compact = Object.values(s3.blocks).find((b) => b.type === 'Compact')
eq('⑤ 折出 Compact 块', compact && compact.meta.evicted_n, 37)
eq('⑤ 峰值那个 0 是「没账本」，不许当读数渲染', compact && compact.meta.hasPeak, false)
s3.ingest(ev('context', 4, { name: 'compact', value: { before: 1, after: 1, freed: 0, evicted_n: 0, real_peak_pt: 158000 } }))
eq('⑤ 有账本时峰值照上', Object.values(s3.blocks).filter((b) => b.type === 'Compact').at(-1).meta.hasPeak, true)
eq('⑤ 压缩行进 unhandled 了吗——不许', s3.unhandled, ['memory:flush'])

rmSync(dir, { recursive: true, force: true })
console.log(failed ? `\n${failed} 条失败` : '\n全过')
process.exit(failed ? 1 : 0)
