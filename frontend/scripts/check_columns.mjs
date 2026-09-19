/** 让渡求解器的可跑断言：node --experimental-strip-types frontend/scripts/check_columns.mjs
 *  （node 22.14 要显式开类型剥离才能 import .ts；22.18+/23.6+ 可省该 flag）
 *  这块逻辑一旦写反（侧栏跟着退让 / details 关不掉 / center 被压成负数），
 *  在浏览器里表现为「拖一下栏位乱跳」，很难靠眼看定位，所以留测试。 */
import assert from 'node:assert/strict'
import {
  CENTER_MIN,
  DETAILS_MAX,
  DETAILS_MIN,
  SIDEBAR_COLLAPSED,
  SIDEBAR_DEFAULT,
  SIDEBAR_MAX,
  SIDEBAR_MIN,
  clampWidth,
  computeColumns
} from '../src/stores/columns.ts'

// 1. 宽带：三栏全开，轨道和恰好铺满视口，一像素都不剩
const wide = computeColumns(1920, SIDEBAR_DEFAULT, 360)
assert.equal(wide.sidebar + wide.center + wide.details, 1920, '宽带未铺满视口')
assert.equal(wide.details, 360)

// 2. 侧栏永不退让：窄到极限时 details 先缩后关，sidebar 始终是请求值
const tight = computeColumns(900, SIDEBAR_DEFAULT, DETAILS_MAX)
assert.equal(tight.sidebar, SIDEBAR_DEFAULT, '侧栏被让渡压窄了')
assert.equal(tight.details, 0, '极限窄下 details 应自动关闭')
assert.equal(tight.center, 900 - SIDEBAR_DEFAULT)

// 3. details 连续让位，落到 300 下限后再窄一档就整体关闭
const yield_ = computeColumns(1300, SIDEBAR_DEFAULT, DETAILS_MAX)
assert.equal(yield_.details, 380, 'details 应让到 380')
assert.equal(yield_.center, CENTER_MIN, '让位期间 center 守住下限')

const floor = computeColumns(1220, SIDEBAR_DEFAULT, DETAILS_MAX)
assert.equal(floor.details, DETAILS_MIN, '1220 正好压在 details 下限')

const closed = computeColumns(1219, SIDEBAR_DEFAULT, DETAILS_MAX)
assert.equal(closed.details, 0, '下限都放不下时必须整体关闭')
assert.ok(closed.center > yield_.center, '关闭 details 后 center 应拿回空间')

// 4. 收起是 0 哨兵 → 实际 56px 图标轨
assert.equal(computeColumns(1440, 0, 0).sidebar, SIDEBAR_COLLAPSED)
assert.equal(computeColumns(1440, 0, 0).details, 0)

// 5. 偏好不被改写：同一份偏好变宽后自动恢复（无滞回）
const pref = { sidebar: 320, details: 400 }
assert.equal(computeColumns(700, pref.sidebar, pref.details).details, 0)
assert.equal(computeColumns(1600, pref.sidebar, pref.details).details, 400, '变宽后 details 没恢复')

// 6. 超界输入被夹进合法区间，且收 0 与收小值不混淆
assert.equal(clampWidth(10, SIDEBAR_MIN, SIDEBAR_MAX), SIDEBAR_MIN)
assert.equal(clampWidth(9999, SIDEBAR_MIN, SIDEBAR_MAX), SIDEBAR_MAX)
assert.equal(computeColumns(1600, 5000, 5000).sidebar, SIDEBAR_MAX)
assert.equal(computeColumns(1600, 5000, 5000).details, DETAILS_MAX)

console.log('columns 让渡求解器 6 组断言全过')
