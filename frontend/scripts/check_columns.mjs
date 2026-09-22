/** A1「整栏栅格宽度」判据：三栏求解器的定点 + 与参照系逐字对齐。
 *  跑法：node --experimental-strip-types frontend/scripts/check_columns.mjs
 *  为什么值得单独一份：布局是**纯函数**（`stores/columns.ts::computeColumns`），所以"整栏宽度对不对"
 *  这件事不需要开面板、不需要后端——能在这里算死；DOM 有没有照做，另由 shot.mjs 的活体几何读数钉
 *  （两个口径分开，别拿纯函数绿去冒充渲染对）。
 *  参照系那份不在（换机/没 clone）时**显式跳过并说明**，与 s5 t25 / s4 t44 同一条纪律。 */
import { existsSync, readFileSync } from 'node:fs'
import {
  CENTER_MIN, DETAILS_DEFAULT, DETAILS_MAX, DETAILS_MIN,
  SIDEBAR_AUTO_COLLAPSE, SIDEBAR_COLLAPSED, SIDEBAR_DEFAULT, SIDEBAR_MAX, SIDEBAR_MIN,
  clampWidth, computeColumns
} from '../src/stores/columns.ts'

let failed = 0
function eq(name, got, want) {
  const ok = JSON.stringify(got) === JSON.stringify(want)
  if (!ok) failed++
  console.log(`${ok ? '✅' : '❌'} ${name}${ok ? '' : ` 得到 ${JSON.stringify(got)} 期望 ${JSON.stringify(want)}`}`)
}

/* ---------- 1) 常量与参照系逐字对齐（源值出处 = 它的 columns.ts） ---------- */
const REF = 'E:/deepseek-harness/packages/client/ui-layout/src/client/columns.ts'
if (existsSync(REF)) {
  const src = readFileSync(REF, 'utf-8')
  const val = (n) => {
    const m = src.match(new RegExp(`export const ${n} = (\\d+)`))
    if (!m) throw new Error(`参照系里没有 ${n}，源改名了？去核 ${REF}`)
    return Number(m[1])
  }
  const ours = { CENTER_MIN, SIDEBAR_MIN, SIDEBAR_MAX, SIDEBAR_DEFAULT, SIDEBAR_COLLAPSED,
                 SIDEBAR_AUTO_COLLAPSE, DETAILS_MIN, DETAILS_MAX, DETAILS_DEFAULT }
  for (const [k, v] of Object.entries(ours)) eq(`常量 ${k} 与参照系同值`, v, val(k))
} else {
  console.log(`⏭ 跳过常量对照：本机没有参照系（${REF}）——不是通过，是没测`)
}

/* ---------- 2) 让渡链的三个定点（侧栏永不退让 → details 缩到 300 再关 → center 吸收缺口） ---------- */
eq('宽视口 1600：三方各按偏好', computeColumns(1600, SIDEBAR_DEFAULT, DETAILS_DEFAULT),
  { sidebar: 280, center: 960, details: 360 })
eq('details 吃掉 center 下限前不动', computeColumns(1600, SIDEBAR_MAX, DETAILS_MAX),
  { sidebar: 420, center: 660, details: 520 })
eq('step1 边界 1380 = 280+360+640 → 刚好全按偏好', computeColumns(1380, 280, 360),
  { sidebar: 280, center: 740, details: 360 })
/* step2 的存在区间很窄：要同时满足「按偏好放不下」(vp < 1280) 与「缩到 300 后放得下」(vp ≥ 1220)。
 * 边界是 280+300+640 = **1220**，不是随手写的 1200——第一版期望算错了，是这道门禁把它抓出来的。 */
eq('step2 边界 1220：details 缩到 300、center 恰好钉在下限', computeColumns(1220, 280, 360),
  { sidebar: 280, center: 640, details: 300 })
eq('step2 差 1px（1219）就整个跳过、直接关 details', computeColumns(1219, 280, 360),
  { sidebar: 280, center: 939, details: 0 })
eq('step3 1100：details 自动关，center 吸收缺口（仍 ≥640）', computeColumns(1100, 280, 360),
  { sidebar: 280, center: 820, details: 0 })
eq('侧栏是**唯一不退让**的：窄到 700 也保持 280', computeColumns(700, 280, 360),
  { sidebar: 280, center: 420, details: 0 })
eq('收起的侧栏占 56px 轨（不是 0，图标栏要看得见）', computeColumns(1000, 0, 0),
  { sidebar: 56, center: 944, details: 0 })
eq('center 可以掉到 0（视口比侧栏还窄时不出现负数）', computeColumns(40, 280, 360),
  { sidebar: 280, center: 0, details: 0 })

/* ---------- 3) 偏好先夹再算：拖出界的值不能把布局撑坏，也不改写存储 ---------- */
eq('偏好 999 被夹到 420', computeColumns(1600, 999, 999), { sidebar: 420, center: 660, details: 520 })
eq('偏好 10 被夹到各自下限', clampWidth(10, SIDEBAR_MIN, SIDEBAR_MAX), SIDEBAR_MIN)
eq('夹取会取整（拖拽给的是浮点）', clampWidth(280.6, SIDEBAR_MIN, SIDEBAR_MAX), 281)
eq('details=0 与"传个小值"不是一回事（0=关闭哨兵）',
  [computeColumns(1600, 280, 0).details, computeColumns(1600, 280, 10).details], [0, 300])

/* 让渡顺序的不变量（写成函数扫一遍视口区间，比钉单点更难被"改一行凑绿"糊过去）：
 *  ① details 还开着时，center 永远 ≥ 下限；
 *  ② details 被关的充要条件是 viewport < s + DETAILS_MIN + CENTER_MIN（即"缩到 300 也放不下"）；
 *  ③ 侧栏宽度在整个区间里恒定不动。 */
eq('不变量①：details 开着时 center 必 ≥ CENTER_MIN',
  (() => { for (let vp = 600; vp <= 2000; vp++) { const r = computeColumns(vp, 280, 360)
    if (r.details > 0 && r.center < CENTER_MIN) return `vp=${vp} 破到 ${r.center}` } return true })(), true)
eq('不变量②：details 关闭 ⟺ vp < 280+300+640',
  (() => { for (let vp = 600; vp <= 2000; vp++) { const r = computeColumns(vp, 280, 360)
    if ((r.details === 0) !== (vp < 280 + DETAILS_MIN + CENTER_MIN)) return `vp=${vp} 与阈值不符` }
    return true })(), true)
eq('不变量③：整个区间里侧栏恒为 280（它永不退让）',
  (() => { for (let vp = 600; vp <= 2000; vp++) if (computeColumns(vp, 280, 360).sidebar !== 280) return `vp=${vp}`
    return true })(), true)

/* ---------- 5) DOM 侧：轨道确实由求解器拼、把手定位那条已知缺陷有账可查 ----------
 * 活体几何已量过（见 PLAN §1 A1 那行）：1600/1280/1220/1100 三轨 = 280 / (vw-280) / 0，
 * 1023 与 700 自动收成 56px 轨（生效点实测就在 SIDEBAR_AUTO_COLLAPSE=1024 之下）。
 * 这里退一步钉源码，是因为"右栏开着"那一态要起隔离后端 + 临时代理才进得去——那一态**没取活体读数**，
 * 别把下面的字符串断言当成渲染已验。 */
const frame = readFileSync(new URL('../src/components/frame/AppFrame.vue', import.meta.url), 'utf-8')
eq('三轨模板串按求解器顺序拼（sidebar → 弹性 center → details）',
  frame.includes('gridTemplateColumns: `${cols.value.sidebar}px minmax(0, 1fr) ${cols.value.details}px`'), true)
eq('中心列不是硬编码 px：模板里只有一个 minmax(0,1fr) 占位',
  (frame.match(/gridTemplateColumns: `\$\{[^}]+\}px minmax\(0, 1fr\) \$\{[^}]+\}px`/g) || []).length, 1)
/* 已知缺陷的现值钉法（不是认可它，是让"改了就看得见"）：details 把手用 `viewport - cols.details`，
 * 只有 center ≥ CENTER_MIN 时才对得上右边界；step2/step3 下 center 掉破下限，把手就会离开轨道接缝。
 * 撤掉这条断言的正确时机是"改成按轨道实测位置定位"，届时同步改 PLAN §3 A1 行。 */
eq('【已知缺陷·现值钉住】details 把手仍按 viewport - cols.details 定位',
  frame.includes('left: `${viewport - cols.details}px`'), true)
eq('【已知缺陷·配套事实】sidebar 把手用的是实测轨道宽（两条把手不对称，正是缺陷的形状）',
  frame.includes('left: `${cols.sidebar}px`') && !frame.includes('left: `${viewport - cols.sidebar}px`'), true)

console.log(failed ? `\n${failed} 条失败` : '\n全过')
process.exit(failed ? 1 : 0)
