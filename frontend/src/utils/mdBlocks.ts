/** 增量 markdown 的「冻结边界」。
 *  流式期每帧都在长文本上重解析整篇 markdown 是主要开销，参考项目的做法是：
 *  除末尾 2 个顶层块之外的内容已经不会再变，冻结成缓存，只重解析尾部。
 *  留 2 个而不是 1 个，是因为列表/引用这类块可能还在往后长。 */

export interface MdChunk {
  text: string
  /** 在原文中的绝对偏移，用作跨帧复用的 key */
  start: number
}

const UNSTABLE_TAIL_BLOCKS = 2

/** 按顶层块切分：空行是分隔，但围栏代码块内部的空行不算。 */
export function splitTopBlocks(src: string): MdChunk[] {
  const out: MdChunk[] = []
  let start = 0
  let inFence = false
  let fenceMarker = ''
  let hasContent = false
  let i = 0

  const push = (end: number) => {
    if (end > start) out.push({ text: src.slice(start, end), start })
  }

  while (i < src.length) {
    const nl = src.indexOf('\n', i)
    const lineEnd = nl === -1 ? src.length : nl + 1
    const line = src.slice(i, nl === -1 ? src.length : nl)

    const fence = /^\s*(`{3,}|~{3,})/.exec(line)
    if (fence) {
      const marker = fence[1][0].repeat(3)
      if (!inFence) {
        inFence = true
        fenceMarker = marker
      } else if (marker === fenceMarker && line.trim() === fenceMarker) {
        // 闭合行就是裸围栏符，不带 info string
        inFence = false
      }
      // 围栏行本身也是内容：只含代码块的消息在流式期很常见，漏了会算成 0 块整段不渲染
      hasContent = true
    } else if (!inFence && line.trim() === '') {
      if (hasContent) {
        push(i)
        hasContent = false
      }
      start = lineEnd
    } else if (line.trim() !== '') {
      hasContent = true
    }
    i = lineEnd
  }
  if (hasContent) push(src.length)
  return out
}

/** 已冻结的前缀 + 仍需每帧重解析的尾部。两者拼回原文一字不差。 */
export function splitStable(src: string): { stable: string; tail: string; stableEnd: number } {
  const blocks = splitTopBlocks(src || '')
  const frozen = Math.max(0, blocks.length - UNSTABLE_TAIL_BLOCKS)
  if (!frozen) return { stable: '', tail: src || '', stableEnd: 0 }
  const last = blocks[frozen - 1]
  const stableEnd = last.start + last.text.length
  return { stable: src.slice(0, stableEnd), tail: src.slice(stableEnd), stableEnd }
}
