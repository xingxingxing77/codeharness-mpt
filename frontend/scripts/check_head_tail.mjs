/** 中段折叠算术的自测：node frontend/scripts/check_head_tail.mjs
 *  纯函数 + 断言，无框架。守的是「头尾切片加起来不能吞行、也不能凭空多行」。 */
import { headTailCap, sliceHeadTail, DEFAULT_MAX_LINES } from '../src/utils/headTailCap.ts'

const lines = (n) => Array.from({ length: n }, (_, i) => `L${i + 1}`)
let failed = 0
function eq(name, got, want) {
  const ok = JSON.stringify(got) === JSON.stringify(want)
  if (!ok) failed++
  console.log(`${ok ? '✅' : '❌'} ${name}${ok ? '' : ` 得到 ${JSON.stringify(got)} 期望 ${JSON.stringify(want)}`}`)
}

// 1) 上限内不折叠、不隐藏
eq('16 行不隐藏', headTailCap(16, DEFAULT_MAX_LINES, false).hidden, 0)
eq('16 行不切片', headTailCap(16, DEFAULT_MAX_LINES, false).capped, false)

// 2) 超上限：ceil(16/2)=8 头 + 8 尾
const over = headTailCap(20, DEFAULT_MAX_LINES, false)
eq('20 行隐藏 4', over.hidden, 4)
eq('头片 8 行', over.headLines, 8)
eq('尾片 8 行', over.tailLines, 8)
eq('切片生效', over.capped, true)

// 3) 展开后不切片
eq('展开即全给', headTailCap(20, DEFAULT_MAX_LINES, true).capped, false)

// 4) 奇数上限也要凑满 maxLines（head+tail === maxLines）
const odd = headTailCap(99, 17, false)
eq('奇数上限 head+tail=17', odd.headLines + odd.tailLines, 17)

// 5) 切片文本不吞行、不重复：头 8 + 尾 8 + 隐藏 4 === 20
const text = lines(20).join('\n')
const s = sliceHeadTail(text, DEFAULT_MAX_LINES, false)
const shown = [...s.head.split('\n'), ...s.tail.split('\n')]
eq('头 8 行', s.head.split('\n').length, 8)
eq('尾以末行结束', s.tail.split('\n').at(-1), 'L20')
eq('头尾无重叠', new Set(shown).size, shown.length)
eq('可见 + 隐藏 === 总数', shown.length + s.cap.hidden, 20)
eq('空文本不炸', sliceHeadTail('', DEFAULT_MAX_LINES, false).head, '')

console.log(failed ? `check_head_tail: ${failed} 条失败` : 'check_head_tail: 全绿')
process.exit(failed ? 1 : 0)
