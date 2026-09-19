/** 参考项目 ui-primitives/src/head-tail-cap.ts 的移植：长结果统一按
 *  `ceil(maxLines / 2)` 行做头切片、其余按尾切片，头尾之间放「… 其余 N 行」。
 *  算术是纯函数，所以调用方自己切片——某个块可以在上面再叠自己的讲究。 */
export interface HeadTailCap {
  /** 超出上限的行数（总行数 − maxLines）；≤ 0 表示没有隐藏行。 */
  hidden: number
  /** 是否超上限且未展开，即需要显示头尾切片。 */
  capped: boolean
  headLines: number
  tailLines: number
}

/** 差异卡与终端卡用同一个上限，两类卡的头尾切口才对得齐。 */
export const DEFAULT_MAX_LINES = 16

export function headTailCap(total: number, maxLines: number, expanded: boolean): HeadTailCap {
  const hidden = total - maxLines
  const headLines = Math.ceil(maxLines / 2)
  return { hidden, capped: hidden > 0 && !expanded, headLines, tailLines: maxLines - headLines }
}

/** 按上限把一段文本切成头尾两片；没超就整段落在 head、tail 为空串。 */
export function sliceHeadTail(
  text: string,
  maxLines: number,
  expanded: boolean
): { head: string; tail: string; cap: HeadTailCap } {
  const lines = text ? text.split('\n') : []
  const cap = headTailCap(lines.length, maxLines, expanded)
  if (!cap.capped) return { head: text, tail: '', cap }
  return {
    head: lines.slice(0, cap.headLines).join('\n'),
    tail: lines.slice(lines.length - cap.tailLines).join('\n'),
    cap,
  }
}
