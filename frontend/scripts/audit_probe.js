/** 逐值审计探针：把我们在各状态下的计算样式打出来，与参考项目的源 CSS 值对表。
 *  用法（复用 shot.mjs 的 CDP 通道，零新依赖）：
 *    node frontend/scripts/shot.mjs --width 1280 --height 900 \
 *      --url "http://127.0.0.1:8718/?v=audit" --eval "$(cat frontend/scripts/audit_probe.js)" \
 *      > logs/audit_ours.txt
 *  只测不判：判定在人（或对照表）那边。缺元素的标签会进 MISSING，不当成 0 蒙混。 */
const PROPS = [
  'width', 'height', 'minHeight', 'maxWidth', 'padding', 'margin', 'gap',
  'fontSize', 'lineHeight', 'fontWeight', 'color', 'backgroundColor',
  'borderTopWidth', 'borderTopStyle', 'borderTopColor', 'borderRadius', 'boxShadow', 'opacity'
]

const SEL = {
  /* 侧栏 */
  '侧栏.品牌名': '.brandName',
  '侧栏.新建会话钮': '.newSession',
  '侧栏.项目行': '.projectRow',
  '侧栏.会话行': '.sessionRow',
  '侧栏.行标题': '.sessionRow .title',
  '侧栏.行时间': '.sessionRow .time',
  '侧栏.状态槽': '.sessionRow .slot',
  '侧栏.设置入口': '.settingsTrigger',
  '侧栏.用户行': '.userRow',
  '侧栏.溢出钮': '.overflowBtn',
  /* 会话头与页签 */
  '头.面包屑': '.crumbs',
  '头.面包屑段': '.crumb',
  '头.当前段': '.crumb.cur',
  '头.模式标签': '.modeChip',
  '头.成本读数': '.cost',
  '头.图标钮': '.header .iconBtn',
  '页签条': '.tabs',
  '页签': '.tab',
  '页签.未选中': '.tab:not(.tabActive)',
  '页签.选中': '.tab.tabActive',
  /* 对话流 */
  '流.用户气泡': '.userRow .bubble',
  '流.正文块': '.prose',
  '流.披露行': '[data-chat-flow] .wrap .row',
  '流.披露行标题': '[data-chat-flow] .wrap .row .title',
  '流.披露行摘要': '[data-chat-flow] .summary',
  '流.披露行图标': '[data-chat-flow] .lead',
  '流.思考体': '.thinkBody',
  '流.终端卡': '.card.term',
  '流.终端输出': '.termOut',
  '流.终端提示': '.termPrompt',
  '流.折叠开关': 'button.fold',
  '流.尾行': '[data-turn-tail]',
  '流.尾行动作钮': '[data-turn-tail] .act',
  /* composer 与统计条 */
  'composer.卡': '[data-composer-card]',
  'composer.文本域': '[data-composer-card] textarea',
  'composer.行': '[data-composer-card] .row',
  'composer.chip': '[data-composer-card] .chip',
  'composer.模型字': '[data-composer-card] .modelChip',
  'composer.主按钮': '[data-composer-card] .primary',
  'composer.统计条': '.composerSeat .band .root',
  'hero.标题': '.hero .brand',
  'hero.徽标': '.hero .badge',
  'hero.chip': '.hero .wsChip',
  'hero.组内间距': '.hero .inner',
  /* 右栏与设置 */
  '右栏.面板': '.panel',
  '右栏.页签条': '.panel .tabs',
  '右栏.页签': '.panel .tab',
  '右栏.节标题': '.sectionLabel',
  '设置.弹窗': '.options',
  '设置.导航项': '.navCell',
  '设置.内容区': '.content',
  /* 台账表（要先切到 Trajectory 视图才有 DOM） */
  '台账.表': '.tbl',
  '台账.表头': '.tbl th',
  '台账.单元格': '.tbl td',
  '台账.数字列': '.num'
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms))
const missing = []
const out = []

/* 伪元素（页签的下划条等）必须带第二个参数才量得到 */
const PSEUDO = {
  '页签.下划条': ['.tab.tabActive', '::after'],
  '页签.未选中下划条': ['.tab:not(.tabActive)', '::after']
}
const PSEUDO_PROPS = ['content', 'width', 'height', 'left', 'right', 'bottom', 'top',
  'borderRadius', 'backgroundColor', 'opacity']

function measurePseudo(state) {
  for (const [label, [sel, pe]] of Object.entries(PSEUDO)) {
    const el = document.querySelector(sel)
    if (!el) { missing.push(`${state} :: ${label} (${sel}${pe})`); continue }
    const cs = getComputedStyle(el, pe)
    const bits = PSEUDO_PROPS.map((p) => `${p}=${cs[p]}`).filter((s) => !/=(normal|auto|0px|rgba\(0, 0, 0, 0\)|none)$/.test(s))
    out.push(`${state} | ${label} | ${bits.join(' ')}`)
  }
}

function measure(state) {
  for (const [label, sel] of Object.entries(SEL)) {
    const el = document.querySelector(sel)
    if (!el) {
      missing.push(`${state} :: ${label} (${sel})`)
      continue
    }
    const cs = getComputedStyle(el)
    const r = el.getBoundingClientRect()
    const bits = [`w=${Math.round(r.width)}`, `h=${Math.round(r.height)}`]
    for (const p of PROPS) {
      if (p === 'width' || p === 'height') continue
      const v = cs[p]
      if (v === '' || v === 'none' || v === 'normal' || v === 'auto' || v === '0px' || v === 'rgba(0, 0, 0, 0)') continue
      bits.push(`${p}=${v}`)
    }
    out.push(`${state} | ${label} | ${bits.join(' ')}`)
  }
  measurePseudo(state)
}

async function clickText(rootSel, text) {
  const el = [...document.querySelectorAll(rootSel)].find((e) => e.textContent.includes(text))
  if (el) el.click()
  await sleep(600)
  return !!el
}

for (let i = 0; i < 80; i++) {
  await sleep(250)
  if (document.querySelectorAll('.sessionRow').length > 1) break
}
measure('hero')

/* 选一个内容最全的会话：默认 s9_curve_role_zero（4 张终端卡 + 折叠开关 + 18 步台账）。
   点第一行不行——那往往是刚建的空会话，对话流与台账会全量成空。 */
const GROUP = 's9_curve_role_zero'
const grp = [...document.querySelectorAll('.groupSection')].find((e) =>
  e.textContent.includes(GROUP)
)
const firstRow = grp && grp.querySelector('.sessionRow')
if (firstRow) firstRow.click()
else await clickText('.sessionRow', '')
for (let i = 0; i < 120; i++) {
  await sleep(250)
  if (document.querySelectorAll('[data-chat-anchor-key]').length > 2) break
}
await sleep(1200)
measure('会话内')

/* 展开工具行，让终端卡与折叠开关进 DOM */
const rows = [...document.querySelectorAll('[data-chat-flow] .row[role=button]')]
rows.forEach((r) => r.click())
await sleep(1200)
measure('工具行展开')

/* 切到台账视图（表根/th/td 只在这里存在） */
const tt = [...document.querySelectorAll('.tab')].find((e) => e.textContent.includes('Trajectory'))
if (tt) { tt.click(); await sleep(1200) }
measure('台账')
if (tt) { document.querySelector('.tab').click(); await sleep(800) }

/* 右栏 */
const panelBtn = [...document.querySelectorAll('.header .iconBtn')].at(-1)
if (panelBtn) { panelBtn.click(); await sleep(900) }
measure('右栏')

/* 设置弹窗 */
const st = document.querySelector('.settingsTrigger')
if (st) { st.click(); await sleep(900) }
measure('设置')

return { lines: out, missing }
