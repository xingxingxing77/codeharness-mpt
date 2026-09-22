/** B4 两视图的自测：按票筛 + 按时间分。
 *  跑法：node --experimental-strip-types frontend/scripts/check_votes.mjs
 *  为什么值得单独一份：票值有两态（旧票裸字符串、新票 {v,at}），而归一规则只该有一份
 *  （后端 `_vote_of`、前端 `utils/votes.ts::parseVote`）。这个文件钉的就是前端这一份——
 *  特别是**旧票不许被算进任何一天**：那段时间戳压根没记，拿 created_at 顶上去就是编数据。 */
import { filterRowsByVote, normalizeVotes, parseVote, sessionVote, voteBuckets } from '../src/utils/votes.ts'

let failed = 0
function eq(name, got, want) {
  const ok = JSON.stringify(got) === JSON.stringify(want)
  if (!ok) failed++
  console.log(`${ok ? '✅' : '❌'} ${name}${ok ? '' : ` 得到 ${JSON.stringify(got)} 期望 ${JSON.stringify(want)}`}`)
}

const row = (id, feedback) => ({ id, feedback })

/* 1) 两态票值的读法 */
eq('新票：票种与时刻都在', parseVote({ v: 'like', at: '2026-09-23 02:5x:11'.replace('x', '0') }),
  { vote: 'like', at: '2026-09-23 02:50:11' })
eq('旧票（裸字符串）：有票、无时刻 —— 时刻是 null 不是空串冒充', parseVote('dislike'),
  { vote: 'dislike', at: null })
eq('缺键/空值都不炸', [parseVote(undefined), parseVote(''), parseVote({})],
  [{ vote: '', at: null }, { vote: '', at: null }, { vote: '', at: null }])
eq('dict 但没 at（半条数据）：票照收、时刻记 null', parseVote({ v: 'like' }), { vote: 'like', at: null })
eq('图标层只吃票种：normalizeVotes 把两态拉平',
  normalizeVotes({ a: 'like', b: { v: 'dislike', at: 'x' }, c: {} }), { a: 'like', b: 'dislike' })

/* 2) 一场会话的票：同时有有用与没用时报"没用"（那是行动信号） */
eq('混合票取 dislike', sessionVote({ a: 'like', b: { v: 'dislike', at: 'x' } }), 'dislike')
eq('全 Like 取 like', sessionVote({ a: 'like' }), 'like')
eq('没票取空串', sessionVote({}), '')
eq('认不出的值不算票', sessionVote({ a: 'meh' }), '')

/* 3) 按票筛：五档各筛各的，且"未评"与"有票"互补（并集=全部、交集=空） */
const rows = [
  row('1', { a: { v: 'like', at: '2026-09-22 10:00:00' } }),
  row('2', { a: { v: 'dislike', at: '2026-09-23 09:00:00' }, b: { v: 'like', at: '2026-09-23 09:01:00' } }),
  row('3', {}),
  row('4', { a: 'like' }),                       // 旧票：有票、无时刻
  row('5', { a: { v: 'dislike', at: '2026-09-23 23:59:59' } }),
]
const ids = (k) => filterRowsByVote(rows, k).map((r) => r.id)
eq('all', ids('all'), ['1', '2', '3', '4', '5'])
eq('any（有票，含旧票）', ids('any'), ['1', '2', '4', '5'])
eq('none（未评）', ids('none'), ['3'])
eq('like', ids('like'), ['1', '4'])
eq('dislike', ids('dislike'), ['2', '5'])
eq('互补性：any ∪ none = 全部 且 any ∩ none = 空',
  [ids('any').length + ids('none').length, [...ids('any')].filter((x) => ids('none').includes(x)).length],
  [rows.length, 0])

/* 4) 按时间分：旧票进「未记时刻」，绝不并进任何一天 */
const { days, undated } = voteBuckets(rows)
eq('分天结果（新→旧排序）', days, [
  { day: '2026-09-23', like: 1, dislike: 2 },
  { day: '2026-09-22', like: 1, dislike: 0 },
])
eq('未记时刻那一档（旧票）', undated, { like: 1, dislike: 0 })
eq('总票数对得上（三天桶 + 未记 = 全部票）',
  days.reduce((n, d) => n + d.like + d.dislike, 0) + undated.like + undated.dislike, 5)
eq('没票时两个都是空，不是 0 顶上去', voteBuckets([row('x', {}), row('y')]),
  { days: [], undated: { like: 0, dislike: 0 } })
eq('认不出的票不进任何桶（含未记时刻档）',
  voteBuckets([row('z', { a: 'meh', b: { v: 'meh', at: '2026-09-23 01:00:00' } })]),
  { days: [], undated: { like: 0, dislike: 0 } })

console.log(failed ? `\n${failed} 条失败` : '\n全过')
process.exit(failed ? 1 : 0)
