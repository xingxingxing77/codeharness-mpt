/** 子 agent 身份读数的自测：node frontend/scripts/check_agents.mjs
 *  纯函数 + 断言，无框架。守的是「内层节点名不许被当成一个 agent 显示出来」——
 *  后端两条发射路径写的是两套名字（报道槽=角色名，打字机流=think/act/gate/observe），
 *  直接取最后一个块的 role 会在头部冒出「act」这种根本不是人的名字。 */
import { activeRole, agentChipLabel, INNER_NODES } from '../src/utils/agents.ts'

let failed = 0
function eq(name, got, want) {
  const ok = JSON.stringify(got) === JSON.stringify(want)
  if (!ok) failed++
  console.log(`${ok ? '✅' : '❌'} ${name}${ok ? '' : ` 得到 ${JSON.stringify(got)} 期望 ${JSON.stringify(want)}`}`)
}

const ROSTER = ['PM', 'Architect', 'Engineer', 'QA', 'PMManager']

// 1) 没名册 / 没块 → null（胶囊整颗不出现，不是「未知」）
eq('名册为空给 null', activeRole([], ['PM']), null)
eq('名册缺字段给 null', activeRole(undefined, ['PM']), null)
eq('一个块都没有给 null', activeRole(ROSTER, []), null)

// 2) 倒着取最近一个在册角色
eq('取最近的角色', activeRole(ROSTER, ['PM', 'Architect', 'Engineer']), 'Engineer')
eq('内层节点名不算人', activeRole(ROSTER, ['PM', 'act', 'think']), 'PM')
eq('内层名大小写也不算', activeRole(ROSTER, ['PM', 'ACT', 'Gate', 'observe']), 'PM')
eq('不在册的名字不算', activeRole(ROSTER, ['PM', 'nobody_here']), 'PM')
eq('空串与空白不算', activeRole(ROSTER, ['PM', '', '   ']), 'PM')
eq('全是内层名给 null', activeRole(ROSTER, ['think', 'act', 'gate']), null)

// 3) 动态线（Mike/Alice + 现场招的人）
eq('动态线认队长', activeRole(['Mike', 'Alice'], ['Mike', 'act', 'Alice']), 'Alice')

// 4) 文案
eq('单角色只写名字', agentChipLabel('PM', ['PM']), 'PM')
eq('多角色带人数', agentChipLabel('QA', ROSTER), 'QA · 5 个角色')
eq('还没出角色但有名册 → 报人数', agentChipLabel(null, ROSTER), '5 个角色')
eq('两头都没有 → 空串（不造假读数）', agentChipLabel(null, []), '')
eq('INNER_NODES 覆盖四个内层节点', INNER_NODES, ['think', 'act', 'gate', 'observe'])

console.log(failed ? `check_agents: ${failed} 条失败` : 'check_agents: 全绿')
process.exit(failed ? 1 : 0)
