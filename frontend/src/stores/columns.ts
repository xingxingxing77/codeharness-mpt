/** 布局几何契约，逐值照抄参考项目 ui-layout/src/client/columns.ts。
 *  这些数字是契约冻结的：侧栏收起宽度 = 16 pad + 24 icon + 16 pad = 56。
 *  单列成文件是为了能被无依赖脚本直接断言（见 scripts/check_columns.mjs）。 */
export const CENTER_MIN = 640
export const SIDEBAR_MIN = 264
export const SIDEBAR_MAX = 420
export const SIDEBAR_DEFAULT = 280
export const SIDEBAR_COLLAPSED = 56
export const SIDEBAR_AUTO_COLLAPSE = 1024
export const DETAILS_MIN = 300
export const DETAILS_MAX = 520
export const DETAILS_DEFAULT = 360

export interface Columns {
  sidebar: number
  center: number
  details: number
}

export function clampWidth(px: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, Math.round(px)))
}

/** 让渡链：侧栏永不退让 → details 先缩到 300 再自动关 → center 吸收最后缺口。
 *  纯函数、无断点、无滞回，所以重新变宽时偏好会自动恢复（偏好从不被改写）。
 *  约定 sidebar/details 传 0 表示「收起/关闭」，不是宽度 0。 */
export function computeColumns(viewport: number, sidebar: number, details: number): Columns {
  const s = sidebar === 0 ? SIDEBAR_COLLAPSED : clampWidth(sidebar, SIDEBAR_MIN, SIDEBAR_MAX)
  const d0 = details === 0 ? 0 : clampWidth(details, DETAILS_MIN, DETAILS_MAX)

  if (s + d0 + CENTER_MIN <= viewport) return { sidebar: s, center: viewport - s - d0, details: d0 }

  const d1 = d0 === 0 ? 0 : Math.max(DETAILS_MIN, viewport - s - CENTER_MIN)
  if (s + d1 + CENTER_MIN <= viewport) return { sidebar: s, center: CENTER_MIN, details: d1 }

  return { sidebar: s, center: Math.max(0, viewport - s), details: 0 }
}
