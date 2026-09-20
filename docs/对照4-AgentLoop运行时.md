# 对照 4 · Agent Loop 运行时 · 本项目 vs 源 MetaGPT

> 口径与状态词汇见 `对照1-上下文管理.md` 文件头。源快照 `E:\MetaGPT\metagpt`（version=1.0.0），本项目 `E:\Codeharness`。
> 这是账本里 `重` 得最狠的一片：R3（`roles/role.py` 596 行 + `team.py` + `base_env.py` 手写轮次循环）、R4a（持久化 → checkpointer）、R5（阻塞 `input()` → `interrupt`/`Command`）、R2（`action_node.py` 结构化引擎 → 逐字段 validator）。本文件核对：**换掉之后，循环语义是等价、更强、还是有丢**。
>
> **复核时间 2026-09-19（批次0/1 落地后）**：批次1（`c973726` 的初版 → 批次1 重写）把 §结论-3「ToT 系四件漏判 + 空壳」彻底补齐：`strategy/base.py` 纯 Python 树（去 anytree）、`strategy/tot.py` 照源移植 `ThoughtSolverBase` 四拍 + `BFSSolver`/`DFSSolver`（**MCTS 判弃**），并接 `RoleZero.plan_fn` 真钩子（旧的空挂 `role._plan` 废）。**两处 bug 已修**：evaluate 现真写回 `node.value`、剪枝不再引用不存在的 `n.id`。判定表 §三 已补 ToT 移植行。其余 interrupt 覆盖、role_zero resume、修复管线、死装饰器族本轮未触碰，结论按当前代码重新核对。

## 结论速览

**实现度：约 84%（上版 80%）——单角色循环、精准激活、断点续跑、结构化输出与定向重试、跨 worker 停止与插话都已落地且门禁密度最高；丢的是「角色内恢复」与「人在环的覆盖面」。** ToT 经批次1 已从「坏的空壳」移植成能跑的 BFS/DFS 树搜索（见 §结论-3）。

四条最要紧的缺口：

1. **经典线一个 `interrupt()` 都没有**（已核实）：全仓 `interrupt(` 命中只有 `role_zero.py:259`、`plan_and_act.py:110,127`（`checkpoint.py:6` 是注释）。`classic_team`（`team.py:116-149`）五个角色全是 `Agent`，评审类 Action（`WritePRDReview`/`WriteCodeReview`）没有 review 闸 → 默认组队下 `runner.answer_human`（`:181-201`）、`POST /{sid}/answer`（`server/api/sessions.py:191`）与前端 ask_human 卡片（`frontend/.../stores/sessions.ts:201`）**永不被触达**。通道是通的，触发面只覆盖两条非经典范式。本轮未改（复核：`grep interrupt codeharness/actions/write_prd_review.py` 为空）。
2. **角色内没有恢复语义**。`agent.py:173-185 as_node` 每次激活都新建内层初始态（`action_cursor: -1, loops: 0, chosen: ""`），且 `graph = self.build()`（`:174`）**编译时不带 checkpointer**。即使将来在 `Agent` 里加 interrupt，resume 也只能重放整个 `_run`、游标归零——`checkpoint.py:6-7` 承诺的语义在角色内部断了。
3. **ToT 本轮补了代码，但是「空壳 + 两处 bug」**（§结论-3 更新）：源 `strategy/tot.py:1-277` + `solver.py:44-77` + `search_space.py` + `strategy/base.py`(109) 四件此前既不在判定表「复/改」也不在「弃」（清单 `:119` 只列了 planner/thinking_command/base/task_type/experience_retriever），文档 grep 零命中。本轮 `strategy/tot.py` 落了 `ThoughtNode/ThoughtTree/TotAgent` 并接进 `build_role(strategy="tot")`（`registry.py:245-252`）——**接线这一半是真的**。但：① `TotAgent.think` 里 `self.tree.evaluate(child)` **返回分数却不写回 `child.score`**（`tot.py:132,146`），而 `best_leaf:116` 按 `n.score` 取 max，所有 score 恒 0.0 → **「多解择优」实际是任取一支**；② 剪枝分支 `kept = set(n.id …)`（`:151`）但 `ThoughtNode` 无 `id` 属性 → 若触发即 `AttributeError`（当前 `if len(leaves)>num_paths` 在深度 1 未必触发，属潜伏）；③ 仍**没在 `判定-复制与重构清单.md` 补这条判定**，「漏判」未闭环。
4. **修复管线大半空转**（已核实）：`provider/repair.py` 保留了 14 个源签名，生产只接了两档——`repair_to_model`（`gateway.py:225-226`）与 `llm_repair_json`（`role_zero.py:231-233`）。`repair_llm_raw_output:156`、`retry_parse_json_text:259`、`extract_state_value_from_output` 在 `codeharness/`+`server/` 零调用方。

## 一、源项目怎么做（循环的真实形状）

| 位置 | 机制 |
|---|---|
| `metagpt/environment/base_env.py:198-211` | **全员轮询**：`for _ in range(k)` 内层遍历所有 role，`is_idle` 则 continue，其余 `role.run()` 收 futures 后 `asyncio.gather`。无条件边，谁干活由角色自己翻收件箱决定 |
| `metagpt/team.py:128-134` | 团队级停止：`while n_round > 0` + `env.is_idle` break + `_check_balance` 抛 `NoMoneyException` |
| `metagpt/roles/role.py:465` | 角色级停止：`while actions_taken < self.rc.max_react_loop` |
| `metagpt/roles/role.py:534-558` | 单轮形状：注入消息 → `_observe()` 返回 0 就 return（挂起等待）→ `react()` → `set_todo(None)` → `publish_message` |
| `metagpt/roles/role.py:406-423` | 观察即去重闸门：`find_news`/`pop_all` → `cause_by in watch or name in send_to` 过滤 → 写回 memory → 记 `latest_observed_msg` |
| `metagpt/roles/role.py:342-379` | `_think` 三模式：单动作直选 / `recovered` 原位续跑（`:348-351`，随后清 `recovered=False` 以免 max_react_loop 失效）/ BY_ORDER 游标自增（`:353-357`）/ REACT 用 LLM 回数字选 state，非法值降级 -1 终止（`:371-378`） |
| `metagpt/utils/common.py:689-716` | **异常恢复靠"删记忆"**：`role_raise_decorator` 任何异常都 `rc.memory.delete(latest_observed_msg)` 让该消息重新被 observe，再 re-raise |
| `metagpt/utils/common.py:675-686` + `team.py:59-81` | `serialize_decorator` 吞 KeyboardInterrupt/Exception 后落盘 `storage/team.json`，deserialize 复原 |
| `metagpt/utils/human_interaction.py:23,49-71` | 人在环全是阻塞 stdin（`input()` 循环直到合法）；`strategy/planner.py:96,104,119-132` 的 `ask_review` 在 `auto_run=False`（默认，`:63`）时同步等输入 |
| `metagpt/actions/action_node.py:597-663,680-716,768-806` | 结构化输出引擎 `fill()`（simple/complex 逐 child）+ `auto_review` + `auto_revise`；修复档在 `utils/repair_llm_raw_output.py:157,184,259-292`（tenacity 重试解析） |
| `metagpt/actions/action_graph.py:33-49` + `strategy/solver.py:35-42` | SOP DAG（字段级依赖）拓扑排序 + `NaiveSolver` 按序 `op.fill(mode="root")`——**只在 DI 线用**，经典软件公司线不走 |
| `metagpt/subscription.py:60-100` | 独立异步 trigger 流（公众号推送），驱动 `role.run(msg)`；**不是角色间路由** |

## 二、本项目怎么做

| 位置 | 机制 | 性质 |
|---|---|---|
| `codeharness/environment/team_graph.py:145-165` | `StateGraph` + router 虚节点 + `add_conditional_edges("router", route)` | **新栈原生替换**（R3） |
| `team_graph.py:88-142` | `route()`：`Send(node, {"_inbox": [msg]})` 精准激活；`stats` 记每轮「激活节点数 vs 角色数」 | 改造（订阅表→出边） |
| `team_graph.py:100-103,136-139` | `debug_rounds >= 3` 两处硬闸（QA 自环 + DEBUG_ERROR 回路） | **新增**（源无，源靠 test_round 语义） |
| `codeharness/roles/agent.py:66-119` | observe→think→(route)→act→think 内层图；`_route:112-119` 只有 `loops`/`chosen` 两个停止条件 | 改造，逐行对 `role.py:340-379,458-474` |
| `codeharness/roles/agent.py:150-158` | Action 抛错转 `[错误]` 消息回喂记忆，下一轮自愈 | **新栈自有**（源是 re-raise + 删记忆） |
| `codeharness/provider/gateway.py:196-270` | `structured()` → `with_structured_output(include_raw)` → 失败走 `repair_to_model` → 仍失败抛 ValueError | 替换 R2/R1 |
| `codeharness/provider/gateway.py:292-320` | `_retryable` + `_acall` tenacity 指数退避 3 次；流式不重放 | 改造 |
| `codeharness/base/action.py:65-84` | `_structured` 补空字段定向重试（`max_field_retries=2`、`patch_exempt` 白名单） | R2 替代件 |
| `codeharness/environment/checkpoint.py:37-91` | `AsyncSqliteSaver` + msgpack 白名单 + 路径缓存（`build_team` 默认仍内存型） | R4a |
| `codeharness/server/runner.py:115-130,161-208` | `_ensure_graph` 按会话重建 (graph, config)；`answer_human` → `Command(resume)`；`_pending` 用 `aget_state` | R5 |
| `codeharness/server/runner.py:59-104,292-321` | `stop` = `task.cancel()` + Redis `ch:ctl` 跨 worker 转发；`CancelledError` 落 stopped 并 re-raise | **新增**（源只有 KeyboardInterrupt） |
| `codeharness/strategy/plan_and_act.py:108-132` | plan_review / accept 双 interrupt 闸，独立**无 LLM 节点**（避开 resume 重放） | 替换 `planner.py:96,104` |
| `codeharness/strategy/tot.py`（本轮 B5） | `ThoughtNode/ThoughtTree/TotAgent`，接进 `build_role(strategy="tot")` 换 `RoleZero._plan` | **新增 MVP**，两处 bug（§结论-3/§五-新1）；不替换主循环，只换 RoleZero 的一次规划调用 |
| `codeharness/roles/role_zero.py:246-283` | `_act` 显式声明 `config: RunnableConfig` + `var_child_runnable_config` set/reset；`GraphInterrupt` 先 except 照抛 | 同 R5 |
| `codeharness/team.py:40,59` | `recursion_limit: 60` 取代 `Team.run(n_round=3)` | 语义换轨 |
| `codeharness/report.py:26,65-74` + `runtime.py:13-17` | `REPORT_SINK`/`CHAT_SINK` ContextVar；块事件走内核、插话走 route drain | 替换 R6 源 HTTP callback |

## 三、逐能力对照

| 源能力 | 源位置 | 本项目位置 | 状态 | 依据 / 缺口 |
|---|---|---|---|---|
| observe-think-act 单轮 | `role.py:399/340/381` | `agent.py:78-167` | 已复刻 | 过滤条件与源 `:415` 一字未改（`agent.py:79-80`） |
| 多轮与终止条件 | `role.py:458-474`; `team.py:128` | `agent.py:112-119` + `team_graph.py:139` + `team.py:59` | 现代化替换 | 三重：`max_loops` / `chosen==END` / `recursion_limit` |
| 并行 / 条件激活 | `base_env.py:198-211` | `team_graph.py:119,151-165` | 现代化替换 | `Send` 扇出取代全员 gather；`t2/t5` 钉「4 角色只激活 1」 |
| interrupt 与 resume | `human_interaction.py:23`; `planner.py:119-132` | `plan_and_act.py:110,127`; `role_zero.py:259`; `runner.py:195` | **部分实现** | 经典线零触发（§结论-1） |
| checkpoint 断点续跑 | `team.py:57-73` | `checkpoint.py:37-91` | 现代化替换 | 但 `runner._run` 未传 `interrupt_before`（§五-B） |
| LLM 解析失败自修复 | `repair_llm_raw_output.py:157,184,259` | `provider/repair.py:156,254,376,399` | 部分实现 | 只接两档（§结论-4） |
| 结构化输出 | `action_node.py:597-663` | `gateway.py:196` + `action.py:65-84` | 现代化替换 | 字段级定向重试保留；**`auto_review:680` / `auto_revise:768` 未做** |
| SOP action 图（字段级 DAG） | `action_graph.py:33-49` + `solver.py:35-42` | `schema.py:383-460`（仅任务排序）；`team_graph.py:29-38` | **未实现** | 无字段级节点图，路由表是 `cause_by→角色名` 粗粒度 |
| 按因装配动作序 | `engineer.py:_new_code_actions` | `agent.py:31-41` + `team.py:138-141` | 已复刻 | `plans`/`default_plan` 构造即校验（`:38,:41`） |
| 策略可插拔 sop/react/role_zero/plan_and_act | `role.py:82-89,261-282` | `registry.py:231-259`; `team.py:74-81` | 部分实现 | 三档可换且非法值 raise；`plan_and_act` 是**另一个类**，不经 `Agent`（§五-F）。本轮新增第四值 `tot`（仅 RoleZero，§结论-3） |
| 人在环（回答与追问） | `mgx_env.py:64-67` | `role_zero`/`plan_and_act` + `runner.answer_human`；`api/sessions.py:191` | 部分实现 | 通道已通，覆盖面同 §结论-1 |
| 取消 / 停止 | 源无协作式取消 | `runner.py:59-104` | **本项目新增** | 整任务级 `task.cancel`，**不能停单个角色** |
| `is_idle` / `<all>` 广播 | `role.py:561`; `base_env.py:229-235` | `team_graph.py:106-114` | 有意不做 | `<all>` 刻意不当广播（`s3b t6` 钉死），散会改判「无 Send 即 END」 |
| 异常恢复（删记忆重观察） | `common.py:689-716` | 无对应物；改由 `agent.py:150` 错误回喂 | 现代化替换（**语义有变**） | 见 §四-3 |
| ToT / 树搜索 | `strategy/tot.py:1-277`、`solver.py:44-77`、`search_space.py`、`strategy/base.py`(109) | `strategy/base.py`（纯 Python 树，`update_value` 写回）+ `strategy/tot.py`（`BFSSolver`/`DFSSolver`）+ `registry.build_role strategy=tot` → `RoleZero.plan_fn` | **已移植、可择优**（2026-09-21 复核） | `3f975f7` 批次1 修掉旧「空壳」两 bug（evaluate 真写回 `node.value`、剪枝不再引用不存在的 `id`），判定表已补这行（`判定-复制与重构清单.md:120`，MCTS 判 `弃`）；本轮实测 `tests/s14_tot.py` **6/6 全绿**，其中 t2 写回 / t3 贪心保留 / t4 BFS 真选高分支是**行为断言**（旧 §五-新1 说的「浅断言」已闭）。边界不变：只换 RoleZero 的一次规划调用，**不替换主循环** |
| 经验检索编排 | `strategy/experience_retriever.py` | `role_zero.py:214-235`（experience 槽）+ `@exp_cache` | 部分实现 | 判定要求「认真做」，现为经验池命中而非源的多路检索编排 |
| 公众号订阅推送 | `subscription.py:11-100` | 无 | 有意不做 | 清单 `:145`「接渠道时按渠道抽象重做」 |
| SK skill_manager | `management/skill_manager.py` | 无 | 有意不做 | 清单 `:135` |
| 预算超限停机 | `team.py:98-100` | 无 | 有意不做 | 清单 §零 |

## 四、现代化带来的语义变化（不全是收益）

1. **节点重放**。LangGraph resume 会重跑所在节点。`plan_and_act.py:6-7` 已在注释里承认并把闸拆成独立无 LLM 节点——但 `role_zero.py:246-283` 的 `_act` 在**同一节点里既执行工具又 `interrupt()`**，resume 会把该节点前面已跑过的工具**再跑一遍**。写文件类工具非幂等，这不是理论问题。
2. **超限不再是自然收口**。`recursion_limit=60`（`team.py:59`）取代 `n_round=3`；超限抛 `GraphRecursionError`，被 `runner.py:318` 归为 `failed`。源的行为是跑完 k 轮正常返回 history。前端因此会看到「失败」而不是「到轮次上限」。
3. **错误处理从"崩了退出"变成"崩了继续烧模型"**。`agent.py:150-158` 把 Action 异常转成 `[错误]` 消息回喂，成本上界只剩 `loops` 计数（`max_loops` 默认 3，`agent.py:28`）。源是 re-raise + 序列化退出。这个改动方向是对的（真模型输出漂移是常态，注释里记了第十二处实例），但**没有总成本闸**（预算强制按 §零 已废），只有轮次闸。
4. **`stats` 是本仓自证手段**：`team_graph.py:140-142` 每轮记 `{cause_by, activated, roles}`，门禁据此断言「精准激活」而非全员轮询。源没有这个观测量。

## 五、「看着像有、其实没接线」

- **新1.【本轮】`strategy/tot.py` 是「接了线但不会择优」的空壳**：`TotAgent.think`（`:126+`）两处 `self.tree.evaluate(...)`（`:132,146`）**返回一个 float 却没赋回 `node.score`**（`evaluate:89` 签名 `-> float`，调用处丢弃返回值），于是所有节点 `score` 恒 0.0，`best_leaf:111-116` 的 `max(leaves, key=n.score)` 退化为首个叶子 → **所谓「多解择优」不会真的挑**。且剪枝分支 `kept = set(n.id ...)`（`:151`）访问 `ThoughtNode` 上不存在的 `id`（类定义 `:16-24` 只有 `thought/parent/children/score`），一旦 `len(leaves)>num_paths` 触发即 `AttributeError`。`s14_tot.py` 的 t1 只跑「实例化 + think() 返回字符串」、t2 只验 build_role 换装，**都绕开了这两处**——think() 在 depth≤1、leaves 未超阈时不触发剪枝，返回值又不是择优结果，所以门禁绿但功能坏。
- **A. 经典线 interrupt 无人触发**（§结论-1）——前端 ask_human 卡片与 `/answer` 端点在默认组队下是死路径。
- **B. resume 后子图状态不重建**（§结论-2）。另：`runner._run` 未传 `interrupt_before`，暂停完全靠节点内 `interrupt()`，配置层面看不到"哪些节点会停"。
- **C. 修复管线只接两档**（§结论-4）。`extract_state_value_from_output` 对应的是源 `_think` 的数字选择路径，本仓已被 `structured` 取代——它属于"为 S6 逐字复制预留"而未兑现，可以删。
- **D. 装饰器族整体未接线**：`utils/common.py:677-704 role_raise_decorator`、`:663-674 serialize_decorator` 在 `codeharness/`+`server/`+`tests/` **零引用**（只定义）。连带其依赖的 `latest_observed_msg`/`recovered`/`rc`/`Memory.delete`/`find_news`（`memory.py:89`）全部成为无入口的死码。
- **E. `TeamState` 僵尸字段**（已核实）：`team_graph.py:24 round` 在三处 init（`team.py:42,61`、`sop/builder.py:34`）写 0 后**无人读、无人增**；`:25 finished` 只有 `route:89` 读、**全仓无处置 True**。这正是清单 `:19` 批评 `budget_guard` 的那类「恒假位」以另一种形式复现——散会实际靠「无 Send → END」。
- **F. `profile["strategy"]` 是只写字段**：`agent.py:46`、`role_zero.py:52`、`registry.py:247` 三处写，全仓**无读者**（含 `server/`、前端契约）。换装的真实入口是 `build_role(strategy=)`（`registry.py:231-259`，对 RoleZero 族显式 raise，对未知值 raise——这两处是硬的）。另 `team.react_assembly:74-81` 只改 `r.react_mode` 不改 `profile["strategy"]`，两者会漂移；因为无读者，目前无害。
- **G. `ActionChoice` 的 REACT 选择面很窄**：`agent.py:104-108` 只把「最新一条 inbox 的前 2000 字」交给模型选动作（详见对照 1 §四-3）。循环是复刻了，**决策输入不是**。

## 六、门禁覆盖

`tests/` 是自测脚本（不吃 pytest），循环语义的断言集中在 `s3b_runtime.py`（16 组）：

- `t1:78` BY_ORDER 游标 off-by-one；`t2:96` / `t5:137` 精准激活 + 订阅可反证（改掉 `cause_by` 必须 END）；`t6:151` 钉死 `<all>` 不广播；`t7:161` 全新连接读得到落盘断点；`t8:194` interrupt 暂停 + **换实例跨重启 resume**；`t11:267` `watch` × SOP 双向自洽；`t12:305` Action 异常回喂 + `GraphInterrupt` 必须照抛；`t12b:489` 自愈重试带上游产物。
- `s3_report_action.py:163-245` t7–t12：字段级定向重试（只补缺、齐全不重发、合并不覆盖、plain-text 路不受影响）。该文件 `:4,:329` 明确声明「R3/R4a/R5 尚未做」——**这是它写给自己当时的状态，现在已过期**，别再据此判断现状。
- `s5_memory_rag.py:858-884` t27：唯一真跑双 interrupt 闸的人在工作流（不 resume 不放行 + confirm 词表照源）。
- `s7_platform.py:71,145,181`：msgpack 白名单、跨 worker stop、跨 worker chat。
- ~~本轮 `s14_tot.py`（3 组）：t1 实例化 + think() 返回 str、t2 build_role 换 tot、t3 tot/role_zero 互斥，**都是浅断言**~~ —— **2026-09-21 复核：现为 6 组**，`t1_generate_builds_children` / **`t2_evaluate_writes_value`** / **`t3_select_keeps_highest`** / **`t4_bfs_solve_picks_high_path`** / `t5_plan_fn_on_rolezero` / `t6_dfs_runs`，加粗那三组正是「多条路径里真挑分最高那条」的行为断言（旧结论出自 B5 那轮的 3 组状态，保留作证据）。
- `s9_benchmark.py` 是离线检索质量回归（hit@1/hit@5/mrr），**与循环无关**；策略曲线三档在 `manual_strategy_curve.py:28-29`，真钱通道、不进门禁。

零断言：经典线 interrupt（因为无实现）；`recursion_limit` 触发后的状态归因；~~**ToT 的择优正确性**~~（**已闭，2026-09-21：`s14_tot` t2/t3/t4 是行为断言，本轮 6/6 绿**）；`debug_rounds` 两处硬闸的上限行为只在 QA 自环一例里间接见过，没有独立断言。

## 七、建议的收口顺序

1. **给经典线一个 review 闸**：源 `auto_review`/`auto_revise`（`action_node.py:680,768`）在本仓没有对应物，而评审类 Action 已经存在——在 `WritePRDReview`/`WriteCodeReview` 后加一个无 LLM 的 `interrupt` 节点（照 `plan_and_act.py:108-132` 的现成姿势），一次性把 §结论-1、§五-A、§五-B 和前端死路径全部激活。
2. **把 `role_zero._act` 的 interrupt 挪出工具执行节点**（§四-1），否则 resume 重复执行非幂等工具。
3. ~~**ToT 先修 bug 再谈推广**~~ —— **已闭合（`3f975f7` 批次1，2026-09-21 复核实测）**：① evaluate 的返回值真写回节点（`strategy/base.py:26 update_value`）；② 剪枝不再引用不存在的 `id`；③ `tests/s14_tot.py` 本轮跑 **6/6 全绿**，t2 `evaluate_writes_value` / t3 `select_keeps_highest` / t4 `bfs_solve_picks_high_path` 就是那条「分数可区分的树里真选高分支」的行为断言；④ 判定表已补（`判定-复制与重构清单.md:120`，MCTS 判弃）。**本条不再占排队位**；要推广（接进主循环）是另一件，未做。
4. 清 D/E/F 三处死码与只写字段（`role_raise_decorator`、`serialize_decorator`、`round`、`finished`、`profile["strategy"]`、`extract_state_value_from_output`）——`finished` 若保留就该有处置 True 的地方，否则删。
5. `recursion_limit` 命中时给一个明确的 stopped-with-rounds 状态，不要归 `failed`。

## 复核方式

```bash
cd /e/Codeharness
grep -rn "interrupt(" --include=*.py codeharness | grep -v __pycache__          # §结论-1
grep -n "score" codeharness/strategy/tot.py                                       # §五-新1：evaluate 返回未写回、best_leaf 读 score
grep -n "\.id" codeharness/strategy/tot.py                                         # §五-新1：ThoughtNode 无 id
grep -rn "role_raise_decorator\|serialize_decorator" --include=*.py .             # §五-D
grep -rn "state\[.round.\]\|\"round\"" --include=*.py codeharness                 # §五-E
grep -rn "repair_llm_raw_output\|retry_parse_json_text" --include=*.py codeharness server  # §结论-4
PYTHONPATH=. PYTHONIOENCODING=utf-8 python tests/s3b_runtime.py
PYTHONPATH=. PYTHONIOENCODING=utf-8 python tests/s14_tot.py                       # §五-新1：绿，但浅（不测择优）
# 源侧锚点
sed -n '196,214p' /e/MetaGPT/metagpt/environment/base_env.py
sed -n '340,380p;406,424p;458,476p' /e/MetaGPT/metagpt/roles/role.py
```
