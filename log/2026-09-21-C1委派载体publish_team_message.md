# 2026-09-21 21:4x · C1-② 委派载体：publish_team_message 真的把成员唤醒了

C1 的三片里第二片。21:0x 落了地基（`08a147a`：未知收件人不再静默丢），本片把**出站通路**接上：
队长说「这条给 Alice」，Alice 这个节点就得被激活、而且只拿到她那条任务。

## 段 1 · 现象（改之前）

动态线（`paradigm="dynamic"` → `team.dynamic_assembly`）只有队长一个人干活：

- 路由表 `{UserRequirement: [Mike]}` 里没有任何指向 Alice/Bob 的边；
- `TeamLeader` 的 prompt（`prompts/di/team_leader.py:12`）**早就写着** "You should use
  `TeamLeader.publish_team_message` to team members"，但这个命令名在本仓**零实现**——
  模型真调它就落到 `_act` 的 `else` 分支，回一句「未知命令」，然后原样 `end` 收场；
- `{team_info}` 槽恒为空串（`roles/registry.py:49` 写死 `team_info=""`），模型连队友叫什么都不知道，
  只能编名字。

净效果：三角色的动态会话与单角色会话**没有任何行为差别**，另两个角色是纯摆设。

## 段 2 · 排查过程（含一条我自己取错的前提）

1. **我上一片给自己下的判据写的是「让 `ActionNode.assignee` 成真 Send 目标」——这条前提是错的。**
   去源里核：`grep -rn assignee E:\MetaGPT\metagpt` 只有三类命中——`schema.py` 的字段与
   `append_task/replace_task` 形参、`actions/di/write_plan.py:69-74`（写计划时填进去）、
   `strategy/experience_retriever.py`（示例 JSON 里的字符串）。**源里 `assignee` 也零路由消费者**，
   它和 `Plan` 一样是「给队长自己看的进度板」。真正的委派载体是
   `roles/di/team_leader.py:81-91 publish_team_message(content, send_to)`，它
   `publish_message(UserMessage(..., send_to=send_to, cause_by=RunCommand))`。
   → 载体照源做，不改 plan 字段语义（对照 5 §结论-2 那句「让 assignee 成 Send 目标」的建议一并作废）。
2. 源的 `publish_team_message` 是**工具**（`@register_tool(include_functions=["publish_team_message"])`），
   命令在 `tool_execution_map` 里注册。本仓 RoleZero 的 `self.tools` 是 langchain 工具表，
   角色自己的方法不在里面（`Plan.*`、`ask_human`、`reply_to_human` 都是 `_act` 的特殊分支）——
   所以照本仓既有形态做成第三个特殊命令面，而不是为它造一条工具注册通路。
3. **载体怎么跨边界**是个真设计问题，两种走法摆了一下：
   - 节点自己 `return Command(goto=[Send("Alice", ...)])`：LangGraph 支持，但**绕开 `route()`**——
     21:0x 那道 `UnknownRecipient` 闸、`stats` 的每轮激活数、插话通道全都失效，
     等于把「谁是收件人」的判定又拆成两处。弃。
   - 节点产消息、`route()` 投递（本仓既有不变量：`route` = 源 `base_env.publish_message` 的对等物）：
     聚合 → 拆包 → 定向。取这一支，拆包复用已有的 `CONTEXT_WIRING` 接缝。
4. **拆包后必须能定向**，否则是错的：`route()` 原来那行
   `Send(t, ...) for t in targets for m in payload_msgs` 是笛卡尔积——两名成员各一条任务时，
   Alice 会同时收到自己和 Bob 的任务。`CONTEXT_WIRING` 已有的两件（WriteTasks→Engineer、
   SummarizeCode→QA）都是「多载荷 × 单目标」，从没暴露过这半格。
5. 改完跑门禁，**先把 t2 的断言写错了**：我断言「所有 `cause_by=RunCommand` 的 stats 条目 activated==1」，
   实际读数 `[{UserRequirement,1},{RunCommand,1},{RunCommand,0}]`——第二条 0 是 Alice 干完活那条
   正常产出（`<all>` 无订阅者 = 散会）。判据错不是代码错，改成「整场只允许一次 activated>0 且为 1」。
6. 另一处实测出来的既有事实（本片没动，但 ②b 要用）：`as_node` 的对外 `content` 只读
   **最后一轮** history 的 `results`，所以 `reply_to_human` 若不在收尾那一轮，回报就蒸发在
   `thought` 里（我第一次给 Alice 排两轮脚本时，黑板上收到的是第二轮的「收工」而不是 PRD 那句话）。
   这条不是委派引入的，是 RoleZero 出口一直如此的形状。

## 段 3 · 根因

不是 bug 而是一块没接的接线：`team.py` 的注释、`registry.py:45` 的「委派批次前 team_info 空串」
都写明它在等一个「委派批次」，而那个批次一直没人起——所以 prompt、路由表、命令面三处各自停在
「差别人来接」的状态，任何一处单独看都像正常代码。

## 段 4 · 解决方案

1. **命令面**（`roles/role_zero.py`）：`PUBLISH_COMMANDS = {TeamLeader.publish_team_message,
   TeamLeader.publish_message}`（源 `_update_tool_execution:41-47` 那两个键，别名照抄）。
   `_act` 里在工具表之前先吃它 → `_publish_team_message(args)` 把 `(指令, 成员)` 记进 `self._outbox`。
   - **名字不在册 = `[已拒绝]` 回喂，不抛**：与 21:0x 那条「委派指名不存在的角色当场抛」不矛盾——
     那一条管的是**代码**写错路由表（编程错误），这一条管的是**模型**输出（错名字是常态）。
     拒绝文本经 `_observe` 进记忆，下一轮 think 看得见，走 B9 同一条自愈路。
     判据里带**阳性对照**（同形状换个对的名字必须真激活），否则这条反证可能是「根本不触发」。
   - **无名册 = `[已忽略]`**：`build_role("TeamLeader", llm)` 直建的单人角色没队伍，
     不校验也不投递（避免 registry 线因新增命令面而多出一条抛错路径）。
   - 顺带删掉 `RoleZero.SPECIAL`：全仓零读者的死类属性（`grep -rn SPECIAL` 只有它自己那一行定义），
     留着就是一份会和新命令面分叉的假表。
2. **名册**：`RoleZero.teammates: dict[名字, "profile, goal"]`，`team.default_team` 组队后回填，
   `team_info()` 出源 `_get_team_info:50-57` 同格式的文本；队长的 `instruction_provider`
   换回 `TL_INSTRUCTION.format(team_info=...)`（此前恒空串）。prompt 面补一行
   `Team Delegation: use {"command_name":"TeamLeader.publish_team_message","args":{"content":…,"send_to":…}}`
   ——本仓 `available_commands` 只列 langchain 工具，特殊命令的 args 形状历来靠 prompt 明写
   （`ask_human`/`reply_to_human` 同例），不写模型就只能猜参数名。
3. **跨边界**（`as_node`）：一次节点运行攒下的委派在收口时发成**一条聚合消息**
   （`cause_by=RunCommand`、`send_to={成员…}`、`instruct_content.delegations=[{member,instruction}]`、
   `instruct_schema="TeamDelegation"`），且**排在返回值最后一条**——`route()` 只读
   `state["messages"][-1]`，顺序反了整条委派就看不见。投给自己不算是委派（源 `:84` 同判）。
4. **拆包 + 定向**（`environment/team_graph.py`）：
   - `CONTEXT_WIRING[RUN_COMMAND] = _wire_delegation`：认 `instruct_schema`，非委派的
     RoleZero 正常产出原样透传（同一个对象返回，故下面的收窄不会误伤它）。
   - `route()` 的发送改为一载荷一寻址：**只有装配器产出的载荷**（`m is not last`）且自带具名收件人时
     才按 `send_to ∩ targets` 收窄；原始消息与非具名载荷仍按 `targets` 全发。
     收窄只在 `targets` 内做、**不新增收件人**，所以 `UnknownRecipient` 绕不过去（t1 有这一格）。
     经典线 `run_code.py:104` 那条 `send_to={"Engineer"}` 与 SOP 目标一致，行为不变（t5 钉住）。
5. **门禁**：新增 `tests/s22_delegate_route.py` 五组（拆包与定向 / 真图端到端 / 错名字自愈 /
   无名册不投 / 原始消息不收窄），其中真图那组同时钉「Bob 零次模型调用」与「一次点名两名不互串」。

## 段 5 · 解决后效果（读数）

`tests/s22_delegate_route.py` **5/5 exit 0**。真图那组的实测 stats：
`[{cause_by: UserRequirement, activated: 1, roles: 3}, {cause_by: RunCommand, activated: 1}, {RunCommand, activated: 0}]`
——队长被唤醒 1 次、委派唤醒 Alice 1 次、Alice 收场散会，Bob 全程 `llm.calls == 0`。
t2b：同一轮点名 Alice + Bob，`_task_of(Alice)` 含「写 PRD」不含「写系统设计」，反之亦然。

按 §0.3 门禁面全量重跑（`codeharness/roles/**` + `codeharness/environment/**` + `team.py` + prompts），
`log/c1b_*.out`，**全部 exit 0**：s1 12 组 / s2 15 组 / s3a 13 组 / **s3b 17 组** / s4 41 组 /
s5 32 组（6 处 Qdrant 探活跳过）/ s6 23 组 / s14 6/6 / s16 6 组 / s17 4 组 / s18 2/2 /
s21 3/3 / s8_runner_meter 8/8 / test_roles_registry 18 角色 / **s8 14/14** /
**s7 13/13 双配置**（默认 + `PLATFORM__USE_REDIS=1 REDIS__DB=15`）。前端零改动，故未跑 build。

**未验边界（照实记）**：
- 全程 FakeLLM，**真模型没验**（本轮零花费、没碰云端 key）。真模型会不会按新 prompt 那行
  填对 `content`/`send_to` 两个参数名，只有 A4 那条线跑起来才知道。
- **成员→队长的回报通路（②b）不存在**：Alice 干完活，她的产出消息 `cause_by=RunCommand`、
  `send_to=<all>`，无订阅者即散会——队长不会 `finish_current_task`，源 TL 的「跟踪进度」这一半还没接。
  另注：回报接上时得先解决段 2-6 那条（`as_node` 只读最后一轮 results）与「新任务会清掉 `self.plan`」
  （`as_node` 里 `task != "continue"` 就 `self.plan = None`，回报若按新任务进队长，队长的计划会被自己抹掉）。
- **`/api/sessions/{sid}/graph` 画不出委派边**：那张图渲染的是 SOP 路由表，委派是消息级定向，
  表里仍只有 `UserRequirement → Mike`。要显示得让 mermaid 生成侧读 `send_to`，另案。
- `Plan.assignee` 仍是计划元数据（源同款，不是折扣）；C1 的第三片（planner 现场产 profile +
  工具名声明校验 + 新角色持久到 `session.roles`）未起。
