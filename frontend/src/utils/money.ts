/** C12：成本展示的唯一出口。两种币种各标各的符号，**永远不相加**——
 *  本仓不做汇率换算（也不做预算管理），把 ¥ 和 $ 加成一个数就是这次修掉的口径错误。
 *  符号在这里定义一次，四个消费处（顶栏、用量页、轨迹表、右栏产物表）才不会漂。 */
const SYMBOL: Record<'cost_usd' | 'cost_cny', string> = { cost_usd: '$', cost_cny: '¥' }

export type CostLike = Partial<Record<'cost_usd' | 'cost_cny', number>> | undefined | null

export function money(bucket: 'cost_usd' | 'cost_cny', n: number, digits = 3): string {
  return `${SYMBOL[bucket]}${(n || 0).toFixed(digits)}`
}

/** 一行里同时呈现两桶：只有一桶有值就只印那一个，两桶都空时给 ¥0.000（界面不留空白）。
 *  人民币在前——本仓默认模型与多数国产模型都是 CNY 计价。 */
export function moneyBoth(cost: CostLike, digits = 3): string {
  const parts: string[] = []
  if (cost?.cost_cny) parts.push(money('cost_cny', cost.cost_cny, digits))
  if (cost?.cost_usd) parts.push(money('cost_usd', cost.cost_usd, digits))
  return parts.length ? parts.join(' ') : money('cost_cny', 0, digits)
}

/** 逐笔相加用不上——分桶后各自累加。留给表格的 reduce 用，避免又写出一个 `+ cost` 的混加。 */
export function sumCosts(rows: CostLike[]): Required<Record<'cost_usd' | 'cost_cny', number>> {
  // 显式给累加器类型：不写会被 TS 按元素类型（CostLike，两桶都是可选）推成 Partial，
  // 返回处就报「Partial 不能赋给 Required」。
  return rows.reduce<{ cost_usd: number; cost_cny: number }>(
    (a, r) => ({ cost_usd: a.cost_usd + (r?.cost_usd || 0), cost_cny: a.cost_cny + (r?.cost_cny || 0) }),
    { cost_usd: 0, cost_cny: 0 }
  )
}
