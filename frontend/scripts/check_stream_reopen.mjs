/** 流式 UX 批自测：打字机块的「建块 / 重开 / 首 token 时刻」三条语义。
 *  跑法：node frontend/scripts/check_stream_reopen.mjs
 *
 *  背景（09-28 一跑 SSE 录像坐实的三处）：
 *    · structured 的逐片 JSON 过去原样上屏，`server/runner.py` 改成抽散文后，
 *      `on_chat_model_start` 会先发一条 `meta` 把这一笔的流块**建起来**（首 token 前实测
 *      要静默 40~51 秒，行得先在那里、得是 running 态）；
 *    · 同一节点在一场里被调多次 ⇒ 第二笔的 `content` 落在上一笔的 `end_marker` 之后，
 *      不开回来这一行就既不振 running 也不跟着滚；
 *    · `fts`（块的首 token 时刻）只能由**真散文**点着——被 start 的建块事件点着就是假 TTFT。
 *  判的是 store 的真行为（esbuild 现打包 sessions.ts，零新依赖，同 check_page_replay.mjs）。
 */
import { buildSync } from 'esbuild'
import { mkdtempSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'

const storePath = fileURLToPath(new URL('../src/stores/sessions.ts', import.meta.url)).replaceAll('\\', '/')
const dir = mkdtempSync(join(tmpdir(), 'ch_streamreopen_'))
const entry = join(dir, 'entry.ts')
const out = join(dir, 'bundle.mjs')
writeFileSync(entry, `export { createPinia, setActivePinia } from 'pinia'\nexport { useSessionStore } from '${storePath}'\n`)
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

setActivePinia(createPinia())
const s = useSessionStore()
s.currentId = 's1'
s.sessions = [{ id: 's1', status: 'running', goal: '', goal_done_at: '' }]
s.blocks = {}
s.blockOrder = []
s.lastCursor = '0000000000000-000000'
s.lastSeq = 0

let n = 0
const ev = (o) => ({
  kind: 'report', cursor: `${++n}`.padStart(13, '0') + '-000000', seq: n, ts: 100 + n,
  block: 'Thought', role: 'PM', uuid: 'stream-PM', ...o,
})
const B = () => s.blocks['stream-PM']

/* ① start 的建块事件：行先在那里、是 running 态、一个字都没有 */
s.applyEvent(ev({ name: 'meta', value: { streaming: 'PM' } }))
eq('start 的 meta 把这一笔的流块建起来了', B() ? true : false, true)
eq('建块即 running（未收口），静默期有得看', B().closed, false)
eq('建块不写正文', B().tokens, [])
eq('静默期不许点 fts（那是假 TTFT）', B().fts, undefined)

/* ② 真散文逐片到达：fts 由第一片点着，正文按序拼 */
s.applyEvent(ev({ name: 'content', value: '快排是典型的分治' }))
const fts1 = B().fts
eq('散文首片点着 fts', fts1, 102)
s.applyEvent(ev({ name: 'content', value: '排序算法。' }))
eq('两片按序拼成一行', B().tokens, ['快排是典型的分治', '排序算法。'])

/* ③ 收口 → 第二笔的 content 必须把行开回来（同节点被调多次） */
s.applyEvent(ev({ name: 'end_marker', value: null }))
eq('end_marker 收口', B().closed, true)
s.applyEvent(ev({ name: 'meta', value: { streaming: 'PM' } }))
s.applyEvent(ev({ name: 'content', value: '第二轮的思考。' }))
eq('第二笔的 content 把已闭合的块开回来', B().closed, false)
eq('第二笔续在同一行里（不另起一块）', B().tokens, ['快排是典型的分治', '排序算法。', '第二轮的思考。'])
eq('fts 仍是第一笔首片的时刻（没被第二笔改写）', B().fts, fts1)
s.applyEvent(ev({ name: 'end_marker', value: null }))
eq('最后一笔照样收口（不是一直开着）', B().closed, true)

/* ④ 阳性对照：孤立的 end_marker（uuid 从未开过块）仍被丢弃，不凭空造块 */
s.applyEvent(ev({ name: 'end_marker', value: null, uuid: 'stream-NEVER' }))
eq('孤立 end_marker 不落新块', 'stream-NEVER' in s.blocks, false)
/* 阳性对照：content 之外的名字照旧走各自的槽，没被重开逻辑牵连 */
s.applyEvent(ev({ name: 'document', value: 'D', uuid: 'doc-1' }))
eq('document 仍写 doc 槽', s.blocks['doc-1'].doc, 'D')

/* ⑤ 历史回放路径同语义（mergeEarlierPage 借道同一个 switch） */
s.blocks = {}
s.blockOrder = []
s.lastCursor = '0000000000000-000000'
s.lastSeq = 0
let m = 0
const pev = (o) => ({
  kind: 'report', cursor: `${++m}`.padStart(13, '0') + '-000000', seq: m, ts: 200 + m,
  block: 'Thought', role: 'PM', uuid: 'stream-Old', ...o,
})
s.mergeEarlierPage([
  pev({ name: 'meta', value: { streaming: 'PM' } }),
  pev({ name: 'content', value: '上一场' }),
  pev({ name: 'end_marker', value: null }),
  pev({ name: 'content', value: '·后半笔' }),
  pev({ name: 'end_marker', value: null }),
])
const OLD = () => s.blocks['stream-Old']
eq('回放里同样开回来（终态=收口且两片都在）', [OLD().closed, OLD().tokens], [true, ['上一场', '·后半笔']])

rmSync(dir, { recursive: true, force: true })
console.log(failed ? `\n${failed} 条失败` : '\n全过')
process.exit(failed ? 1 : 0)
