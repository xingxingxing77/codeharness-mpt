/** 流式 UX 批自测：打字机落点、逐片/定稿两条通道的关系、建块与重开语义。
 *  跑法：node frontend/scripts/check_stream_reopen.mjs
 *
 *  后端形状（`server/runner.py`）：一笔 LLM 调用的逐片散文发 `name=live`，落点是**这一笔所在的那一块**
 *  （内核报道槽里最后开着且未收口的 `Thought`/`Docs`/`Task`；没有块就落 `stream-{node}` 兜底）；
 *  内核解析完发的定稿发 `name=content`。前端的规矩是「正文 = tokens + live，而 content 一到就把 live
 *  整段撤掉」——所以逐片与定稿在结构上不会同屏，不需要任何「是不是同一句话」的比对。
 *  判据全在真 store 上跑（esbuild 现打包 sessions.ts，零新依赖，同 check_page_replay.mjs）。
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
const B = (k = 'stream-PM') => s.blocks[k]
const body = (k = 'stream-PM') => B(k).tokens.join('') + B(k).live.join('')

/* ① start 的建块事件：行先在那里、是 running 态、一个字都没有 */
s.applyEvent(ev({ name: 'meta', value: { streaming: 'PM' } }))
eq('start 的 meta 把这一笔的流块建起来了', !!B(), true)
eq('建块即 running（未收口），静默期有得看', B().closed, false)
eq('建块不写正文', [B().tokens.length, B().live.length], [0, 0])
eq('静默期不许点 fts（那是假 TTFT）', B().fts, undefined)

/* ② 逐片走 live 通道：fts 由第一片点着，正文看得见 */
s.applyEvent(ev({ name: 'live', value: '快排是典型的分治' }))
const fts1 = B().fts
eq('逐片首片点着 fts', fts1, 102)
s.applyEvent(ev({ name: 'live', value: '排序算法。' }))
eq('两片进 live（不进 tokens）', [B().live, B().tokens], [['快排是典型的分治', '排序算法。'], []])
eq('正文读起来是两片的接续', body(), '快排是典型的分治排序算法。')

/* ③ 定稿一到：live 整段撤掉，tokens 接手——两份绝不同屏 */
s.applyEvent(ev({ name: 'content', value: '快排是分治排序算法，平均 O(n log n)。' }))
eq('content 把在飞的逐片清空', B().live, [])
eq('定稿进 tokens', B().tokens, ['快排是分治排序算法，平均 O(n log n)。'])
eq('正文只剩定稿', body(), '快排是分治排序算法，平均 O(n log n)。')
eq('fts 仍是逐片首片那一刻（没被定稿改写）', B().fts, fts1)

/* ④ 收口 → 第二笔的 live 必须把行开回来（同一节点被调多次） */
s.applyEvent(ev({ name: 'end_marker', value: null }))
eq('end_marker 收口', B().closed, true)
s.applyEvent(ev({ name: 'live', value: '第二轮的思考。' }))
eq('第二笔的 live 把已闭合的块开回来', B().closed, false)
eq('第二笔续在同一块里（不另起一块）', B().live, ['第二轮的思考。'])
s.applyEvent(ev({ name: 'end_marker', value: null }))
eq('最后一笔照样收口', B().closed, true)

/* ⑤ 内核块落点：Docs 块开着时，逐片进那块，不新起 stream- 行 */
s.applyEvent(ev({ block: 'Docs', uuid: 'doc-9', name: 'meta', value: { type: 'prd' } }))
s.applyEvent(ev({ block: 'Docs', uuid: 'doc-9', name: 'live', value: '需求分析正文' }))
s.applyEvent(ev({ block: 'Docs', uuid: 'doc-9', name: 'content', value: '## requirement analysis\n需求分析正文' }))
eq('Docs 块逐片 + 定稿都落在这块', [B('doc-9').live, B('doc-9').tokens], [[], ['## requirement analysis\n需求分析正文']])
eq('Docs 那两事件没另起一行（块数还是三个）', Object.keys(s.blocks).length, 2)
/* ⑥ 阳性对照：孤立的 end_marker 仍被丢弃，不凭空造块 */
s.applyEvent(ev({ name: 'end_marker', value: null, uuid: 'stream-NEVER' }))
eq('孤立 end_marker 不落新块', 'stream-NEVER' in s.blocks, false)

/* ⑦ 历史回放路径同语义（mergeEarlierPage 借道同一个 switch） */
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
  pev({ name: 'live', value: '上一场在飞的' }),
  pev({ name: 'content', value: '上一场定稿' }),
  pev({ name: 'end_marker', value: null }),
])
eq('回放里逐片被定稿撤掉、终态收口',
   [B('stream-Old').closed, B('stream-Old').live, B('stream-Old').tokens],
   [true, [], ['上一场定稿']])

/* ⑧ 半块拼接（加载更早）：live 也按序拼，别让在飞那半截丢了 */
s.blocks = {}
s.blockOrder = []
s.lastCursor = '0000000000000-000000'
s.lastSeq = 0
s.blocks['half'] = { key: 'half', type: 'Thought', role: 'PM', closed: false, meta: null,
                     tokens: ['后半场定稿'], live: ['后半场在飞'], doc: null, obj: null,
                     lines: [], cmd: '', path: '', url: '', page: null, raw: [] }
s.blockOrder = ['half']
let q = 0
const qev = (o) => ({
  kind: 'report', cursor: `${++q}`.padStart(13, '0') + '-000000', seq: q, ts: 300 + q,
  block: 'Thought', role: 'PM', uuid: 'half', ...o,
})
s.mergeEarlierPage([qev({ name: 'live', value: '前半场在飞' })])
eq('跨页拼接把 live 也接上（顺序：旧的在前）', s.blocks['half'].live, ['前半场在飞', '后半场在飞'])

rmSync(dir, { recursive: true, force: true })
console.log(failed ? `\n${failed} 条失败` : '\n全过')
process.exit(failed ? 1 : 0)
