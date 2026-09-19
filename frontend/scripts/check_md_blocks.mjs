/** 冻结边界的可跑断言：node --experimental-strip-types frontend/scripts/check_md_blocks.mjs
 *  切错一处就会在流式期把代码块从中间劈开，渲染出半截围栏——肉眼在动态流里最难发现。 */
import assert from 'node:assert/strict'
import { splitTopBlocks, splitStable } from '../src/utils/mdBlocks.ts'

// 1. 围栏代码块内部的空行不算分隔
const fenced = 'para one\n\n```py\na = 1\n\nb = 2\n```\n\npara two'
const blocks = splitTopBlocks(fenced)
assert.equal(blocks.length, 3, `围栏内空行把代码块劈开了：${blocks.length}`)
assert.ok(blocks[1].text.startsWith('```py'), blocks[1].text)

// 2. stable + tail 必须一字不差拼回原文（多组输入）
for (const src of [
  '', 'one block', 'a\n\nb', 'a\n\nb\n\nc', 'a\n\nb\n\nc\n\nd',
  '~~~sh\nx\n\ny\n~~~\n\nz\n\nw', 'trailing blank\n\n', '\n\n\nleading blank'
]) {
  const { stable, tail } = splitStable(src)
  assert.equal(stable + tail, src, `拼不回原文: ${JSON.stringify(src)}`)
}

// 3. 尾部留 2 块：1 块时不冻结，3 块时只冻结第 1 块
//    块文本含结尾换行，所以冻结边界落在换行之后——渲染结果与整篇一致
assert.equal(splitStable('only one block').stable, '')
const three = splitStable('a\n\nb\n\nc')
assert.equal(three.stable, 'a\n')
assert.equal(three.tail, '\nb\n\nc')
assert.ok(!three.stable || three.stable.endsWith('\n'), '冻结边界必须停在换行后')

// 4. 偏移是绝对位置且单调递增，跨帧可以按它复用而不重挂
const offs = splitTopBlocks('aa\n\nbbbb\n\nc').map((b) => b.start)
assert.deepEqual(offs, [0, 4, 10], JSON.stringify(offs))
assert.ok(offs.every((v, i) => i === 0 || v > offs[i - 1]))

// 5. ~~~ 围栏与 ``` 不互相误闭合
const mixed = splitTopBlocks('```\n~~~\n```')
assert.equal(mixed.length, 1, '~~~ 在 ``` 围栏内被当成了新围栏')

// 6. 收尾空行不产生幻影块
assert.equal(splitTopBlocks('x\n\n').length, 1)
assert.equal(splitTopBlocks('\n\n\ny').length, 1)

console.log('mdBlocks 冻结边界 6 组断言全过')
