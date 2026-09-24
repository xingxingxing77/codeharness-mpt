/** C11 超步→对话流 的映射自测（纯函数，不碰浏览器）。
 *  跑法：node --experimental-strip-types frontend/scripts/check_cp_jump.mjs
 *  为什么单独一份：这条映射是**近似**的（两边没有共同主键，只能按角色与时刻凑），
 *  而近似的判据最容易悄悄退化成「永远走兜底」——所以①命中档与②兜底档必须各自钉住，
 *  且 `exact` 要能区分它们（界面靠这个决定标不标「就近」）。 */
import { pickCpAnchor } from '../src/utils/cpJump.ts'

let failed = 0
function eq(name, got, want) {
  const ok = JSON.stringify(got) === JSON.stringify(want)
  if (!ok) failed++
  console.log(`${ok ? '✅' : '❌'} ${name}${ok ? '' : ` 得到 ${JSON.stringify(got)} 期望 ${JSON.stringify(want)}`}`)
}

const iso = (sec) => new Date(sec * 1000).toISOString()
const B = (key, role, ts, type) => ({ key, role, ts, ...(type ? { type } : {}) })
const blocks = [B('b1', 'PM', 10), B('b2', 'Engineer', 20), B('b3', 'PM', 30), B('b4', 'Engineer', 40)]

/* ① 命中档：这一步的节点产出过块 → 取它**最靠后**的那块，exact=true */
eq('①Engineer 在 45s 这一步 → 跳到第 40 秒那块（同角色两块取靠后的）',
  pickCpAnchor(blocks, { ts: iso(45), tasks: ['Engineer'] }), { key: 'b4', exact: true })
eq('①同一步里两个角色都有块 → 仍按到达序取末位（不重排）',
  pickCpAnchor(blocks, { ts: iso(45), tasks: ['PM', 'Engineer'] }), { key: 'b4', exact: true })

/* ② 兜底档：这一步没产出块 → 就近往前，且必须报 exact=false */
eq('②QA 这一步没有任何块 → 就近落到 40s 那块并标「非精确」',
  pickCpAnchor(blocks, { ts: iso(50), tasks: ['QA'] }), { key: 'b4', exact: false })
eq('②路由步（tasks 空）→ 就近往前',
  pickCpAnchor(blocks, { ts: iso(25), tasks: [] }), { key: 'b2', exact: false })

/* ③ 无路可走：超步在所有块之前 → 空 key（界面据此把按钮置灰，不假装跳到了） */
eq('③超步早于所有块 → 不给假目标', pickCpAnchor(blocks, { ts: iso(5), tasks: ['PM'] }), { key: '', exact: false })
eq('③ts 不是合法 ISO（脏数据）→ 同样不给假目标',
  pickCpAnchor(blocks, { ts: '昨天下午', tasks: ['PM'] }), { key: '', exact: false })
eq('③空块表 → 不给假目标', pickCpAnchor([], { ts: iso(50), tasks: ['PM'] }), { key: '', exact: false })

/* 单位一致性：块 ts 是 unix **秒**、检查点是带时区 ISO——差一个 1000 倍就会「永远走兜底」，
   上面①那格当场会红成 exact=false，所以这条不需要另加断言，注释在此说明为什么它值得存在 */
eq('秒/毫秒不混：一小时后的超步仍算不到未来块',
  pickCpAnchor(blocks, { ts: iso(3600), tasks: ['PM'] }).key, 'b3')

/* ④ 锚点键 ≠ 块键：ChatNode 给用户块多一层 `user:` 前缀。这一步没产出、兜底落到用户块上时，
   返回原始 key 会让 scrollIntoView 静默找不到元素（点了没反应，比报错难查），所以前缀必须在**这里**加。 */
const withUser = [B('b1', 'PM', 10), B('u7', 'user', 15, 'User'), B('b2', 'PM', 20)]
eq('④兜底落到用户块 → 回锚点键 user:u7（不是裸 b.key）',
  pickCpAnchor(withUser, { ts: iso(18), tasks: ['QA'] }), { key: 'user:u7', exact: false })
eq('④非用户块不加前缀（同一份表里另一种类型）',
  pickCpAnchor(withUser, { ts: iso(28), tasks: ['PM'] }).key, 'b2')

console.log(failed ? `\n❌ check_cp_jump: ${failed} 处不符` : '\n✅ check_cp_jump: 全绿')
process.exit(failed ? 1 : 0)
