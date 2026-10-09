/** 子 agent 身份读数：这场会话**此刻**是哪个角色在干活。
 *
 * 为什么要单独一个纯函数：后端两条发射路径写的不是同一个名字——报道槽写**角色名**
 * （`codeharness/report.py:_role_name` ← `set_role(name)`，值是 `Session.roles` 里的节点名），
 * 而 runner 的打字机流写 **langgraph 内层节点名**（`server/runner.py` 的 `role=node`，即
 * `think`/`act`/`gate`/`observe`）。同一角色的一段工作因此会两种名字混着来，
 * 直接取"最后一个块的 role"会把 `act` 当成一个 agent 显示出来。
 * 所以：只在**在册角色**里取，且内层节点名一律不算。 */

/** langgraph 子图自己定义的内层节点名——它们是"步骤"，不是"人"。 */
export const INNER_NODES = ['think', 'act', 'gate', 'observe']

/** 按到达顺序给出每个块的 `role`（可能含 '' 与内层节点名），返回**最近一个**在册角色。
 *  `roles` 来自 `Session.roles`（装配出口回填）；没跑起来或还没出角色级事件 → null。 */
export function activeRole(roles: string[] | undefined | null, blockRoles: string[]): string | null {
  const roster = roles || []
  if (!roster.length) return null
  for (let i = blockRoles.length - 1; i >= 0; i--) {
    const r = (blockRoles[i] || '').trim()
    if (!r || INNER_NODES.includes(r.toLowerCase())) continue
    if (roster.includes(r)) return r
  }
  return null
}

/** 徽标文案：`PM` 这种光有名字的胶囊看不出"这场一共几个人"，参照系那侧的语义最近类比
 *  （`SubagentHeaderLineage`）是 `label · role · activity` 拼接。这里取两段：角色名 + 在册人数。 */
export function agentChipLabel(role: string | null, roles: string[] | undefined | null): string {
  const roster = roles || []
  if (!role) return roster.length ? `${roster.length} 个角色` : ''
  return roster.length > 1 ? `${role} · ${roster.length} 个角色` : role
}

/** 车道行的相位文本（C190）。后端只发**事实**——`value.phase` 就是 langgraph 的内层节点名
 *  （`observe`/`think`/`gate`/`act`，见 `server/runner.py::_role_signal`），动词由这里说人话。
 *  与 `toolRow.ts` 同一口径：不新造一个「意图」字段去求模型填。认不出来的名字一律回
 *  「干活中」——宁可笼统，也不把内部节点名原样打在界面上（那正是本文件开头那条病）。 */
const LANE_PHASE: Record<string, string> = {
  observe: '看进展', think: '思考中', gate: '自检中', act: '执行动作'
}
export function lanePhase(phase: string | undefined | null): string {
  return LANE_PHASE[(phase || '').trim()] || '干活中'
}
