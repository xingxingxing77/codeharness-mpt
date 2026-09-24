/** C11 的第四件（09-25）：从「超步」那一栏跳回对话流里它产出的那一块。
 *
 * 两边今天**没有共同主键**：超步那侧只有 `tasks`（这一步哪些节点在干活）与 `ts`（检查点写完的时刻，
 * langgraph 给的带时区 ISO 串），对话流这侧是块（`key / role / ts`，块的 ts 是它首个事件的 unix 秒）。
 * 所以映射只能建在这两样上，而且必须**承认自己是近似的**：
 *   ① 优先：`role ∈ tasks` 且 `块.ts ≤ 超步.ts` 的**最后一块**——这一步确实产出了东西；
 *   ② 兜底：这一步没产出块（路由步、空步、`__uuid__start`）→ 取时刻 ≤ 超步的最后一块，**就近往前**；
 *   ③ 都没有 → `key=''`，由界面把按钮置灰并说明原因，不假装跳到了。
 * `exact` 就是把①②分开报出去：兜底那一档必须让用户看见它是兜底（不然「跳错了」会被当成「对话流没这块」）。
 * 次序依赖调用方给的是**到达序**（`store.blockList` 就是），本函数不重排序，只在其上取末位。
 * 返回的 `key` 是**锚点键**（已过 `anchorKeyOf`），能直接喂给 `jumpToBlock`，不是 `b.key` 本身。
 */
export interface CpLike { ts: string; tasks: string[] }
export interface BlockLike { key: string; role: string; type?: string; ts?: number }

/** 块 → 对话流里那个 DOM 锚点的键。**唯一一处换算**：`ChatNode.vue` 给用户块多加了一层
 *  `user:` 前缀，其它类型直接用 `b.key`。跳转有两个方（中栏台账行、右栏超步行），
 *  前缀要是各拼各的，漏掉的那边就是「点了没反应」——滚动静默失败，比报错更难发现。 */
export function anchorKeyOf(b: BlockLike): string {
  return b.type === 'User' ? `user:${b.key}` : b.key
}

export function pickCpAnchor(blocks: BlockLike[], cp: CpLike): { key: string; exact: boolean } {
  const at = Date.parse(cp?.ts ?? '') / 1000          // ISO(带时区) → 秒，与块的 ts 同单位
  if (!Number.isFinite(at) || !cp?.tasks) return { key: '', exact: false }
  const upto = blocks.filter((b) => typeof b.ts === 'number' && b.ts <= at + 0.5)
  const byRole = upto.filter((b) => cp.tasks.includes(b.role))
  const hit = byRole.length ? byRole[byRole.length - 1] : (upto.length ? upto[upto.length - 1] : null)
  return hit ? { key: anchorKeyOf(hit), exact: byRole.length > 0 } : { key: '', exact: false }
}
