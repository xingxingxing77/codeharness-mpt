/**
 * fold 的自检（P4 路线三留给自己的那一根针，PLAN §4 第 1 条「判据不绿不许勾」的最小便版本）。
 * 跑法：`node frontend/scripts/fold_check.ts`（Node 24 直接吃 TS，类型都是可擦除语法，无框架无 fixture）。
 * 门禁 s8 里那一格跑它并断言 exit 0——它红了就是「三条路不再同一个 reducer」。
 *
 * 它验的是**结构性不变量**，不是某个块的文案：
 *   ① 逐条增量折（活流）与一次性整本折（首屏/重连）终态逐字节相同；
 *   ② 页边界切开一块时，「前插再整本重折」== 顺序折全量（改前那份手写合并正是死在这儿：
 *      直接往正片 `tokens.push` 会把这块的文字顺序颠倒）；
 *   ③ 定稿一到，逐片整段撤掉 ⇒ 屏幕上永远只有一份正文；
 *   ④ 最新的赢：实时槽按日志顺序覆盖，不需要 `PAGE_REPLAY_OPAQUE` 那份名单；
 *   ⑤ 处置表与 reducer 对得上：每个登记过的 kind/name 都有落点，认不出的一定喊出来；
 *   ⑥ P2 那两条「有发无看」修好了：`context/compact` 出得来一行、`local_url` 不再掉进 raw。
 */
import { readFileSync } from 'node:fs'
import { foldAll, foldOne, resetFold } from '../src/protocol/fold.ts'
import type { FoldState } from '../src/protocol/fold.ts'
import { declaredKeys, disposalOf } from '../src/protocol/disposition.ts'

// 与 store 的 state 同源的那一份字段（fold_check 不 import store：pinia 在 node 里起不来，
// 而 s8 另有一格 grep store 的字段名钉这份名单，两边合起来才把契约钉死）。
function newState(): FoldState {
  return {
    blocks: {}, blockOrder: [], logs: [], status: '', cost: {},
    humanQuestion: null, approvals: [], queue: [], feedback: {},
    sessionPatch: {}, unhandled: [], effects: []
  }
}

type Ev = any
let seq = 0
const cur = (n: number) => String(n).padStart(19, '0') + '-0'
function ev(kind: string, extra: Record<string, any> = {}): Ev {
  seq += 1
  return {
    session_id: 's', seq, cursor: extra.cursor === '' ? '' : cur(seq), ts: 1000 + seq,
    kind, block: null, uuid: null, name: null, value: null, role: null, code: null, extra: null,
    ...extra
  }
}
const R = (uuid: string, block: string, name: string, value: any, role = 'PM') =>
  ev('report', { uuid, block, name, value, role })

const fails: string[] = []
function check(what: string, cond: boolean, detail = '') {
  if (!cond) fails.push(`${what}${detail ? ' —— ' + detail : ''}`)
}
/** 两份 FoldState 的**渲染读得到的一切**必须相等（effects/sessionPatch 也一起比：它们是谁该重拉、
 *  会话记录该改成什么的账，折的顺序不同就不该有差异）。 */
function same(a: FoldState, b: FoldState, what: string) {
  const keys = [...new Set([...Object.keys(a.blocks), ...Object.keys(b.blocks)])]
  check(what + '：块集一致', keys.every((k) => a.blocks[k] && b.blocks[k]),
        `一边缺一块（${keys.filter((k) => !a.blocks[k] || !b.blocks[k]).join(',')}）`)
  check(what + '：块顺序一致', a.blockOrder.join('|') === b.blockOrder.join('|'),
        `${a.blockOrder.join('|')} ≠ ${b.blockOrder.join('|')}`)
  for (const k of a.blockOrder) {
    const x = a.blocks[k], y = b.blocks[k]
    if (!x || !y) continue
    check(`块 ${k}：正文一致`, x.tokens.join('') + x.live.join('') === y.tokens.join('') + y.live.join(''),
          `[${x.tokens.join('')}${x.live.join('')}] ≠ [${y.tokens.join('')}${y.live.join('')}]`)
    for (const f of ['closed', 'type', 'role', 'cmd', 'path', 'url', 'doc', 'page', 'obj', 'meta', 'ts', 'fts', 'lastTs', 'code'])
      check(`块 ${k}：${f} 一致`, JSON.stringify(x[f]) === JSON.stringify(y[f]), `${x[f]} ≠ ${y[f]}`)
    check(`块 ${k}：lines 一致`, x.lines.join('\n') === y.lines.join('\n'))
  }
  check(what + '：日志一致', a.logs.join('\n') === b.logs.join('\n'))
  check(what + '：实时槽一致', JSON.stringify({ ...a, blocks: 0, blockOrder: 0, logs: 0, unhandled: a.unhandled }) ===
        JSON.stringify({ ...b, blocks: 0, blockOrder: 0, logs: 0, unhandled: b.unhandled }))
  check(what + '：unhandled 一致', a.unhandled.join('|') === b.unhandled.join('|'),
        `${a.unhandled.join('|')} ≠ ${b.unhandled.join('|')}`)
}

// ---------------------------------------------------------------- ① 增量 == 整本
const stream: Ev[] = [
  ev('status', { value: { status: 'running', message: 'team started', cost: {} } }),
  R('blk-1', 'Thought', 'meta', { streaming: 'think' }),
  R('blk-1', 'Thought', 'live', '让我'),
  R('blk-1', 'Thought', 'live', '想想这件事。'),
  R('blk-1', 'Thought', 'end_marker', null),
  R('t1', 'ToolCall', 'meta', { type: 'tool_call', tool: 'read_file', args: { path: 'a.py' }, ok: true }, 'Engineer'),
  R('t1', 'ToolCall', 'content', 'a.py:1: def main():'),
  R('t1', 'ToolCall', 'end_marker', null),
  ev('status', { value: { status: 'finished', cost: { prompt_tokens: 10, completion_tokens: 5 } } })
]
const inc = newState()
for (const e of stream) foldOne(inc, e)                 // 活流：一条一条来
const cold = newState()
foldAll(cold, stream)                                   // 首屏/重连：整本一次
same(inc, cold, '① 增量 vs 整本')
check('① 逐片被定稿撤掉前只有一份正文', inc.blocks['blk-1'].live.join('') === '让我想想这件事。')
check('① 收口真的收了', inc.blocks['blk-1'].closed === true)
check('① 空 cost 不许当读数（B1 那条假 0：中间那发没带账本，最后的读数还是真值）',
      inc.cost.prompt_tokens === 10 && inc.cost.completion_tokens === 5, JSON.stringify(inc.cost))
// 单独再钉一次「空对象覆盖」这一族：真值后面跟一发空的，读数必须原地不动
const emptyCost = newState()
foldAll(emptyCost, [
  ev('status', { value: { status: 'running', cost: { prompt_tokens: 7 } } }),
  ev('status', { value: { status: 'running', cost: {} } })
])
check('① 空 cost 不覆盖已有读数', emptyCost.cost.prompt_tokens === 7, JSON.stringify(emptyCost.cost))
check('① 终态才排 loadTrace', inc.effects.includes('loadTrace'), inc.effects.join(','))

// ---------------------------------------------------------------- ② 页边界切开一块
// 同一场被页数切成两半：blk-2 的 meta+前半在老页，后半+收口在新页。
const partA: Ev[] = [R('blk-2', 'Docs', 'meta', { prose_fields: ['content'] }), R('blk-2', 'Docs', 'content', '上半截：')]
const partB: Ev[] = [R('blk-2', 'Docs', 'content', '下半截'), R('blk-2', 'Docs', 'end_marker', null)]
const whole = newState()
foldAll(whole, [...partA, ...partB])
const paged = newState()
foldAll(paged, partA)                                   // 「加载更早」先拿到老页
const afterPrepend = newState()
foldAll(afterPrepend, [...partA, ...partB])             // 前插成同一本日志后整本重折
same(afterPrepend, whole, '② 前插重折 vs 顺序全折')
check('② 跨页那块的文字顺序没颠倒', whole.blocks['blk-2'].tokens.join('') === '上半截：下半截',
      whole.blocks['blk-2'].tokens.join(''))

// ---------------------------------------------------------------- ③ 定稿到，逐片清空
const dup = newState()
foldAll(dup, [R('b3', 'Thought', 'meta', {}), R('b3', 'Thought', 'live', '逐片的话'), R('b3', 'Thought', 'content', '定稿的话')])
check('③ 定稿一到，live 整段撤掉', dup.blocks['b3'].live.length === 0)
check('③ 正文只剩一份', dup.blocks['b3'].tokens.join('') === '定稿的话' && dup.blocks['b3'].live.join('') === '')

// ---------------------------------------------------------------- ④ 最新的赢（不再要名单）
const slots = newState()
foldAll(slots, [
  ev('goal', { name: 'create', value: { objective: '旧目标', done_at: '' } }),
  ev('queue', { name: 'add', value: { items: [{ id: 'q1', content: '插话', send_to: '' }] } }),
  ev('feedback', { name: 'set', value: { key: 't:blk-1', vote: 'like' } }),
  ev('status', { value: { status: 'running', message: '' } }),
  ev('approval', { name: 'requested', value: { id: 'a1', tool: 'shell', kind: 'tool', args_preview: '', reason: '', tier_required: 'full_access', tier_session: 'workspace_write', node: 'act', ts: 1 } }),
  ev('approval', { name: 'resolved', value: { id: 'a1', outcome: 'allowed-once' } }),
  ev('goal', { name: 'edit', value: { objective: '新目标', done_at: '' } }),
  ev('queue', { name: 'drain', value: { items: [{ id: 'q1', content: '插话', send_to: '' }] } }),
  ev('feedback', { name: 'clear', value: { key: 't:blk-1', vote: '' } }),
  ev('status', { value: { status: 'awaiting_human', message: 'awaiting human input' } })
])
check('④ 目标取最后一次主张', slots.sessionPatch.goal === '新目标', String(slots.sessionPatch.goal))
check('④ 票按 key 增删都收（clear 是删键不是写空）', Object.keys(slots.feedback).length === 0)
check('④ 队列按 id 增删', slots.queue.length === 0)
check('④ 批完的卡出队', slots.approvals.length === 0)
check('④ 决议后该重拉权威回执（C144 的 who/when 不在事件里）', slots.effects.includes('loadApprovals'), slots.effects.join(','))
check('④ 待答槽由最后一条 status 决定（非 awaiting_human 才清）', slots.humanQuestion === null)
// 整本重折必须幂等，且走的就是 store 那条路：`resetFold` + 折**全量**日志
// （半截折完不再折第二遍——「加载更早」是前插后整体重折，不是往已折的终态上再叠一层）。
const idemA = newState()
foldAll(idemA, stream)
const idemB = newState()
foldAll(idemB, stream.slice(0, 4))              // 先只看到老页（页尾那块还开着）
resetFold(idemB)
foldAll(idemB, stream)                          // 前插成整本日志后重折 = replayLog
same(idemA, idemB, '④ 整本重折幂等')
// 但「不 reset 就再折一遍」必须**有害**——这一条是 resetFold 存在的全部理由，
// 把它当摆设删了就会在这里红。
const twice = newState()
foldAll(twice, stream)
foldAll(twice, stream)
check('④ 同一本日志折两遍会叠字（所以 replayLog 必须先 resetFold）',
      twice.blocks['blk-1'].live.join('') !== idemA.blocks['blk-1'].live.join(''))

// ---------------------------------------------------------------- ⑤ 处置表 ↔ reducer 对得上
// 每个 timeline 车道的取值都要有落点：喂一条该事件，块或日志必须动，且不许进 unhandled。
// 需要「已开着的块」当落点的探针自己带 seed（`end_marker` 尤其硬：孤立收口按设计直接丢弃）。
const SEED = () => [R('p1', 'Thought', 'meta', { x: 1 })]
const TIMELINE_PROBE: [string, Ev[], (s: FoldState) => boolean][] = [
  ['report:meta', [R('p1', 'Thought', 'meta', { x: 1 })], (s) => !!s.blocks['p1'].meta],
  ['report:content', [...SEED(), R('p1', 'Thought', 'content', '正文')], (s) => s.blocks['p1'].tokens.length === 1],
  ['report:live', [...SEED(), R('p1', 'Thought', 'live', '逐片')], (s) => s.blocks['p1'].live.length === 1],
  ['report:document', [...SEED(), R('p1', 'Docs', 'document', { filename: 'a.md', content: 'x' })], (s) => !!s.blocks['p1'].doc],
  ['report:object', [...SEED(), R('p1', 'Task', 'object', { tasks: [] })], (s) => !!s.blocks['p1'].obj],
  ['report:cmd', [...SEED(), R('p1', 'Terminal', 'cmd', 'ls')], (s) => s.blocks['p1'].cmd === 'ls'],
  ['report:output', [...SEED(), R('p1', 'Terminal', 'output', 'one line')], (s) => s.blocks['p1'].lines.length === 1],
  ['report:path', [...SEED(), R('p1', 'Docs', 'path', '/tmp/a')], (s) => s.blocks['p1'].path === '/tmp/a'],
  ['report:url', [...SEED(), R('p1', 'Browser', 'url', 'https://x')], (s) => s.blocks['p1'].url === 'https://x'],
  // P2 那条「有发无看」之一：ServerReporter 的默认名改前掉进 raw，界面上根本不存在
  ['report:local_url', [...SEED(), R('p1', 'Browser-RT', 'local_url', 'http://127.0.0.1:8000')], (s) => s.blocks['p1'].url === 'http://127.0.0.1:8000'],
  ['report:page', [...SEED(), R('p1', 'Browser', 'page', { page_url: 'https://x', title: 'X' })], (s) => !!s.blocks['p1'].page],
  ['report:end_marker', [...SEED(), R('p1', 'Thought', 'end_marker', null)], (s) => s.blocks['p1'].closed === true],
  ['log', [ev('log', { value: '一条日志' })], (s) => s.logs.includes('一条日志')],
  ['error', [ev('error', { value: 'boom\nValueError: x', code: 'crash' })], (s) => Object.values(s.blocks).some((b) => b.type === 'Error' && b.code === 'crash')],
  ['turn:max-tokens', [ev('turn', { value: { reason: { kind: 'max-tokens' } } })], (s) => Object.values(s.blocks).some((b) => b.type === 'MaxTokens')],
  ['turn:plan-unfinished', [ev('turn', { value: { reason: { kind: 'plan-unfinished', open: 2, total: 5 } } })], (s) => Object.values(s.blocks).some((b) => b.type === 'PlanOpen' && b.meta.open === 2)],
  // P4 路线三接上的那一格：整条 kind 此前在 else-if 链里根本没有分支
  ['context:compact', [ev('context', { name: 'compact', value: { before: 90000, after: 20000, freed: 70000, evicted_n: 12, real_peak_pt: 0 } })], (s) => Object.values(s.blocks).some((b) => b.type === 'Compact')],
  // C190 的角色车道：一颗块走完「开跑 → 相位 → 收工」全生命周期（uuid 是 runner 攒的 `lane-<n>`）
  ['role:started→phase→completed', [
    ev('role', { name: 'started', uuid: 'lane-1', block: 'RoleLane', role: 'PM', value: { role: 'PM', phase: 'observe' } }),
    ev('role', { name: 'phase', uuid: 'lane-1', block: 'RoleLane', role: 'PM', value: { role: 'PM', phase: 'think' } }),
    ev('role', { name: 'completed', uuid: 'lane-1', block: 'RoleLane', role: 'PM', value: { role: 'PM', ms: 4200 } })
  ], (s) => {
    const b = s.blocks['lane-1']
    return !!b && b.type === 'RoleLane' && b.role === 'PM' && b.closed === true &&
      b.meta.ms === 4200 && b.meta.phase === '' && s.blockOrder.length === 1
  }],
  // 取消/异常那条路：runner 散会清扫补的那一颗带 aborted，界面据此不许画成正常收工
  ['role:completed(aborted)', [
    ev('role', { name: 'started', uuid: 'lane-7', block: 'RoleLane', role: 'QA', value: { role: 'QA', phase: 'act' } }),
    ev('role', { name: 'completed', uuid: 'lane-7', block: 'RoleLane', role: 'QA', value: { role: 'QA', ms: 800, aborted: true } })
  ], (s) => s.blocks['lane-7'].meta.aborted === true && s.blocks['lane-7'].closed === true],
  // 车道本体没开过（started 落在已裁掉的那段窗口里）也不许炸、不许留一条永远在跑的行
  ['role:completed(孤立)', [
    ev('role', { name: 'completed', uuid: 'lane-9', block: 'RoleLane', role: 'Engineer', value: { role: 'Engineer', ms: 120 } })
  ], (s) => s.blocks['lane-9'].closed === true && s.blocks['lane-9'].role === 'Engineer']
]
for (const [label, evs, landed] of TIMELINE_PROBE) {
  const s = newState()
  foldAll(s, evs)
  check(`⑤ ${label} 在时间线有落点`, landed(s), '界面上读不到这一笔')
  check(`⑤ ${label} 不许进 unhandled`, s.unhandled.length === 0, s.unhandled.join(','))
}
// runtime 车道的四个槽同样要有落点（它们不进块，但必须写得进实时槽）
const RUNTIME_PROBE: [string, Ev, (s: FoldState) => boolean][] = [
  ['status', ev('status', { value: { status: 'running', message: 'team started' } }), (s) => s.status === 'running' && s.logs.some((l) => l.startsWith('[status]'))],
  ['ask_human', ev('ask_human', { value: '要选哪一个' }), (s) => s.humanQuestion !== null && s.status === 'awaiting_human'],
  ['approval:requested', ev('approval', { name: 'requested', value: { id: 'a1', tool: 'shell' } }), (s) => s.approvals.length === 1],
  ['queue:add', ev('queue', { name: 'add', value: { items: [{ id: 'q1' }] } }), (s) => s.queue.length === 1],
  ['feedback:set', ev('feedback', { name: 'set', value: { key: 't:x', vote: 'like' } }), (s) => s.feedback['t:x'] === 'like'],
  ['goal:edit', ev('goal', { name: 'edit', value: { objective: '新目标', done_at: '' } }), (s) => s.sessionPatch.goal === '新目标']
]
for (const [label, e, landed] of RUNTIME_PROBE) {
  const s = newState()
  foldAll(s, [e])
  check(`⑤ ${label} 写进了实时槽`, landed(s), '槽里什么都没有')
  check(`⑤ ${label} 不许进 unhandled`, s.unhandled.length === 0, s.unhandled.join(','))
}
// 词表的自洽：每条 declaredKeys 都查得到处置，且 src/why 不许空着（空着＝没想过刷新后怎么办）
for (const k of declaredKeys()) {
  const kind = k.split(':')[0]
  const name = k.includes(':') ? k.split(':')[1] : null
  const d = disposalOf(kind, name, name && kind === 'turn' ? name : undefined)
  check(`⑤ 登记过的 ${k} 查得到处置`, !!d, '表里少这一条')
  check(`⑤ ${k} 的理由不许空`, !!d && d.why.trim().length > 1)
}
// 认不出的一定喊出来：这条正是改前那两条「有发无看」的形状
const ghost = newState()
foldAll(ghost, [ev('memory', { name: 'flush' }), R('p9', 'Thought', 'content', '还在流')])
check('⑤ 未登记的 kind 一定进 unhandled', ghost.unhandled.includes('memory:flush'), ghost.unhandled.join(','))
check('⑤ 未登记不许断流（后面的事件照折）', ghost.blocks['p9']?.tokens.join('') === '还在流')
// 登记了、也上了路，但那一支要求的判别字段没有——同样不许安静（`turn/end` 的 reason 就是这种）
const noReason = newState()
foldAll(noReason, [ev('turn', { name: 'end', value: { reason: {} } })])
check('⑤ 没有 reason 的轮尾信封一定喊出来', noReason.unhandled.includes('turn:end'), noReason.unhandled.join('|'))

// ---------------------------------------------------------------- ⑥ 压缩行的事实与「0 不是读数」
const cmp = newState()
foldAll(cmp, [ev('context', { name: 'compact', value: { before: 912_000, after: 210_000, freed: 702_000, evicted_n: 37, real_peak_pt: 0 } })])
const cb = Object.values(cmp.blocks).find((b) => b.type === 'Compact')!
check('⑥ 压缩行数字照事件', cb.meta.evicted_n === 37 && cb.meta.freed === 702_000)
check('⑥ real_peak_pt=0 是「没账本」，不许当峰值显示', cb.meta.hasPeak === false)
const cmp2 = newState()
foldAll(cmp2, [ev('context', { name: 'compact', value: { before: 1, after: 1, freed: 0, evicted_n: 0, real_peak_pt: 158_000 } })])
check('⑥ 有账本时峰值照上', Object.values(cmp2.blocks).find((b) => b.type === 'Compact')!.meta.hasPeak === true)

// ---------------------------------------------------------------- ⑦ resetFold 的取舍
const rs = newState()
foldAll(rs, stream)
rs.status = 'running'
rs.approvals = [{ id: 'keep' } as any]
resetFold(rs)
check('⑦ 重折不清 GET 基线（status/approvals 的权威不在日志里）', rs.status === 'running' && rs.approvals.length === 1)
check('⑦ 重折清的是日志折得出来的那份', Object.keys(rs.blocks).length === 0 && rs.logs.length === 0)

// ---------------------------------------------------------------- --events <file>：真事件序列的三条路对账
// 由 `tests/manual_fold_real_events.py` 产出（真 astream_events + 真 _translate + 真报道槽，桩在本机、零花费）。
// 上面那些是我手搭的例子——本仓在「按印象造事件」上应验过两次（`manual_stream_landing.py` 文件头），
// 所以这一半吃**发射点产出的原样序列**：终态必须与例子那半同一条 reducer，且 `unhandled` 必须为空。
const evIdx = process.argv.indexOf('--events')
if (evIdx >= 0) {
  const file = process.argv[evIdx + 1]
  if (!file) { console.error('--events 后面要给文件路径'); process.exit(2) }
  const real: Ev[] = JSON.parse(readFileSync(file, 'utf-8'))
  if (!real.length) { console.error('真事件序列是空的——空转不算绿'); process.exit(1) }

  const rInc = newState(); for (const e of real) foldOne(rInc, e)
  const rCold = newState(); foldAll(rCold, real)
  same(rInc, rCold, '真流 增量 vs 整本')

  // 「加载更早」的形状：先只看到尾屏，再前插成整本重折（store 里就是 replayLog 那一条路）。
  // 页宽取真序列的三分之一——写死 400 的话 95 条只有一页，这一半就成了空转。
  const PAGE = Math.max(1, Math.ceil(real.length / 3))
  const rPaged = newState()
  foldAll(rPaged, real.slice(-PAGE))
  resetFold(rPaged)
  foldAll(rPaged, real)
  same(rCold, rPaged, '真流 整本 vs 分页重折')

  check('真流里每个 kind/name 都在处置表里', rInc.unhandled.length === 0, `未登记：${rInc.unhandled.join(', ')}`)
  const proseBlocks = rInc.blockOrder.filter((k) => rInc.blocks[k].tokens.join('').trim() || rInc.blocks[k].live.join('').trim())
  check('真流折得出正文', proseBlocks.length > 0, '一块正文都没有')
  const hist: Record<string, number> = {}
  for (const b of rInc.blockOrder) hist[rInc.blocks[b].type] = (hist[rInc.blocks[b].type] || 0) + 1
  const chars = rInc.blockOrder.reduce((a, k) => a + (rInc.blocks[k].tokens.join('') + rInc.blocks[k].live.join('')).length, 0)
  console.log(`真流 ${real.length} 条 → ${rInc.blockOrder.length} 块（${JSON.stringify(hist)}），正文合计 ${chars} 字，` +
              `日志 ${rInc.logs.length} 行，实时槽 status=${rInc.status}`)
}

if (fails.length) {
  console.error(`fold_check 红了 ${fails.length} 条：`)
  for (const f of fails) console.error('  · ' + f)
  process.exit(1)
}
console.log(`fold_check 全绿（${TIMELINE_PROBE.length} 条落点 + 7 组不变量）`)
