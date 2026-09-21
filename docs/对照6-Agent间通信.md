# 对照 6 · Agent 间通信 · 本项目 vs 源 MetaGPT

> 口径与状态词汇见 `对照1-上下文管理.md` 文件头。源快照 `E:\MetaGPT\metagpt`（version=1.0.0），本项目 `E:\Codeharness`。
> 账本 R3 把源的订阅路由判 `重`：`base_env.publish_message` 遍历投递 + `_observe` 过滤 → LangGraph 条件边 + `Send` 精准激活。本文件核对：**换完之后，"谁把消息交给谁"这条线还剩多少源的能力，以及本项目自建的对外通道是否闭合。**
>
> **复核时间 2026-09-19**：本轮 B1–B7 + P0 **未触碰通信面**（`team_graph.route`、`Send` 扇出、`ChatQueue`、`events` SSE 均无改动）。核实结论不变：新增的 `SearchEnhancedQA`/`ImportRepo`/`UploadKB` action 都**不设 `send_to`、不进任何路由**（`grep send_to codeharness/actions/*.py` 仍只有 `run_code.py`/`debug_error.py`）——本轮没造出任何新的 agent→agent 通道。下面按当前代码重新核对。

## 结论速览

**实现度：约 65%。经典线的订阅式路由是等价迁移（过滤条件逐字一致，且比源多了「每轮激活数」自证）；多 worker 一致性、前端事件流、插话与跨 worker 停止是源没有的增量。丢的是动态线——严格意义上，本项目目前只有少数几处 agent→agent 具名投递（QA↔Engineer 一条回路 + debug 回流），且动态线仍没有 agent 间通信。**

四条最要紧的缺口：

1. **agent→agent 具名通信极少**：`actions/run_code.py:98-109`——QA 测试失败且分诊判定该回开发时 `send_to={"Engineer"}`，经 `team_graph.py:112-114` 具名分支 + `agent.py:80`（`s["name"] in m.send_to`）双侧闭环；`actions/debug_error.py` 回流带 `send_to`。除此之外全部靠 `cause_by`→SOP 表路由。动态线（对应源 MGXEnv 那条线）**没有** agent 间通信。
2. **动态线委派未实现**（与对照 5 §结论-2 同一条，从通信视角看）：源 `TeamLeader.publish_team_message`（`roles/di/team_leader.py:75-86`）可点名唤醒任意队友；本仓 `team.py:65-71` 注释自认「需求只喂队长（`TEAMLEADER_NAME`=源逐字 "Mike"）、Alice/Bob 空转」，`Command.assignee` 只是展示字段（`role_zero.py:165`）。本轮未动。
3. **`<all>` 不再是广播**（有意，代价需记清）：源 `is_send_to`（`utils/common.py:411-418`）见 `<all>` 即投全员，而 `send_to` 的默认值就是 `{<all>}`（`schema.py:241`）；本仓 `team_graph.py:108-110` 明确否决——「多数 Action 不显式设 send_to，一旦把 `<all>` 当广播，每个动作都会唤醒全部角色，正好毁掉订阅式路由的精准激活」。判断成立且 `s3b t6` 钉死了它，但结果是**源里"任何角色都能被指名广播触达"这个能力在本仓没有对等物**（要广播必须显式列出收件人）。
4. ~~**产物文档的 state 通道是假的**~~ → **已删干净（2026-09-21 C2）**：`TeamState.docs`（原 `team_graph.py:22`，注释「filename -> Document（产物仓）」）在三处 init 写 `{}` 后**零读零写**，交接实际全走磁盘 `ArtifactStore`（`actions/import_repo.py:121` 的 `save(subdir="docs")` 就是证据）。处置：字段从 TypedDict 删、`team.py:42,61` 与 `sop/builder.py:34` 三处 `"docs": {}` 一并撤、msgpack 白名单里为它挂的 `Document`/`Documents` 两登记项同批摘掉（摘后跑整套 `s3b`，含真落断点的 t7/t8 → langgraph 侧 **0 条 unregistered 告警**，证明确实无人用它），`tests/s3b_runtime` 新增 **t15** 正向钉住不复燃。**为什么必须留断言而不是靠运行期报错**：实测 LangGraph 对未知状态键**静默丢弃**（删掉字段后 `tests/s16_route_state.py` 五组仍全绿、exit 0），写者不会自己炸。文档交接的唯一真通道 = 磁盘 `ArtifactStore` + `CONTEXT_WIRING` 装配进消息。

## 一、源项目怎么做（一条消息的完整路径）

| 步骤 | 位置 | 机制 |
|---|---|---|
| 1. 载荷 | `metagpt/schema.py:232-242` | `Message`：路由字段只有 `sent_from` / `send_to`（默认 `{<all>}`，`:241`）/ `cause_by` / `instruct_content` |
| 2. 出站 | `metagpt/roles/role.py:433-451` | `publish_message` → `env.publish_message` |
| 3. 投递 | `metagpt/environment/base_env.py:176-191` | 遍历 `member_addrs` + `is_send_to` → `role.put_message`（`role.py:452-456`）push 进**接收方私有 buffer** |
| 4. 收取 | `metagpt/roles/role.py:410-423` | `_observe`：`pop_all()` → 过滤 `n.cause_by in self.rc.watch or self.name in n.send_to`（`:415`）→ `memory.add_batch` |
| 5. 调度 | `metagpt/environment/base_env.py:198-212` | 全员轮询 `role.run()`；谁真干活由第 4 步的过滤结果决定 |
| — | 经典线交接 | `software_company.py:44-66` 串行 `role.run()`，靠 `document.py:138` + `document_store/` 落盘产物作跨角色媒介，**causal roles 顺序即隐式交接** |
| — | 动态线委派 | `team_leader.py:75-86 publish_team_message(content, send_to)` → 成员 `run()` 真触发；`mgx_env.py:24-62` 负责定向投递 + `ask_human`/`reply_to_human` |
| — | 对人输出 | `metagpt/utils/report.py`（12 个 Reporter 类、九值 `BlockType`）——见 R6 |

> **与常见印象不符、已核实**：本版本源里**没有** `MessagePriority`、没有 `MessageRoute` 类、没有 `REPLY_WITH_*`（全仓 grep 零命中）。优先级只存在于 PRD 提示词的语义里（`actions/write_prd_an.py:145`），**不参与调度**。另外 `context.py` 里没有 msg_buffer（它在 `RoleContext`），`subscription.py` 全文只有 `SubscriptionRunner:11-100`，是一条独立异步 trigger 流，**不是角色路由**——清单 `:145` 判它「公众号推送」不搬是对的。真正的「订阅」在本版本就是 `watch`。

## 二、本项目怎么做

| 位置 | 机制 | 性质 |
|---|---|---|
| `codeharness/schema.py:166-214` | `Message`（dict + `instruct_schema` 取代源的动态 BaseModel，`:12`） | **改造复制** |
| `codeharness/const.py:83-91` | `MESSAGE_ROUTE_FROM/TO/CAUSE_BY/TO_ALL/TO_NONE/TO_SELF` 逐字照源 `const.py:80-85` | 复制 |
| `codeharness/environment/team_graph.py:82-142,164` | `route()` = 源 `publish_message` 语义 + `Send`；`add_conditional_edges` 挂上 | **新栈原生替换**（R3） |
| `team_graph.py:19-25` | `TeamState.messages`（黑板，等价源 history）+ `memories` reducer（角色私有记忆） | 新栈 |
| `team_graph.py:96-104` | `<self>` 自投递（QA 的 WriteTest→RunCode→DebugError 内环不广播） | 已复刻 |
| `team_graph.py:111-114` | 目标 = 订阅表命中 ∪ 消息显式指名 | 已复刻（`send_to` 那半件） |
| `team_graph.py:44-79` | `CONTEXT_WIRING`：按 `cause_by` 从产物仓现装 `CodingContext`/`TestingContext` 后随消息发出 | **改造**（源的文档交接换成交接装配） |
| `codeharness/roles/agent.py:78-85` | `_observe` 过滤条件与源 `role.py:415` 逐字一致 | 已复刻 |
| `codeharness/runtime.py:39-52` + `team_graph.py:121-129` | `ChatQueue` + 每轮 `drain()` 把用户插话变成额外 `Send` | **现代化替换**（源 MGXEnv 直聊） |
| `codeharness/document_store/artifact_store.py:43-68` | 磁盘产物仓 = 跨角色真通道 | 改造 |
| `codeharness/server/events.py:23-56` + `runner.py:107-111,181-201` | `SessionEventBus`（seq + `after=` 重放）、`enqueue_chat`、`answer_human`→`Command(resume)` | **新（平台面）** |
| `codeharness/server/runner.py:59-104` | 跨 worker `stop`：Redis `ch:ctl` 转发 | **新（N1/N4）** |
| `codeharness/report.py:26,65-74` | 块事件经 `REPORT_SINK` 出到前端（R6：类名与九值 `BlockType` 逐字对齐） | 现代化替换 |

## 三、逐能力对照

| 源能力 | 源位置 | 本项目位置 | 状态 | 依据 / 缺口 |
|---|---|---|---|---|
| 消息模型与载荷 | `schema.py:232-242` | `schema.py:166-214` | 已复刻 | `instruct_content` 改 dict + `instruct_schema`，语义等价 |
| 发布/订阅精准路由 | `base_env.py:176-191` + `role.py:413-416` | `team_graph.py:107-119` | 现代化替换 | SOP 表 + `Send`；`stats` 自证「4 角色激活 1」 |
| 按角色消息缓冲与隔离 | `role.py:100,453-456` | `agent.py:17 inbox` + `TeamState.memories:21` | 已复刻 | `Send` 载荷取代 `put_message`；`MessageQueue` 复制件未接线（对照 1 §五-1） |
| 自投递 `<self>`（内环） | `const.py:85` + `run_code` mappings | `team_graph.py:96-104`; `run_code.py:98` | 已复刻 | 含 `debug_rounds>=3` 硬闸（源靠 test_round 语义） |
| 具名跨角色投递 | `role.py:415` + `base_env.py:176-191` | `run_code.py:104` + `debug_error.py` + `team_graph.py:112` + `agent.py:80` | **部分实现** | 全仓仅 QA↔Engineer 一例回路（§结论-1）；本轮新增 action 无一设 `send_to` |
| 广播 `<all>` | `common.py:411-418`；`schema.py:241` 默认值 | 刻意不实现（`team_graph.py:108-110`） | 有意不做 | `t6` 钉；副作用：`is_send_to` 在本仓**零调用者**成死码（本轮复核仍零） |
| 优先级 / 顺序调度 | 源无此机制 | `agent.py:95-101` BY_ORDER 游标 | 有意不做 | 源不存在，不算缺口 |
| 共享文档产物交接（经典线主通道） | `software_company.py:44-66` + `document_store/` | `artifact_store.py:43-68`（磁盘）+ `team_graph.py:44-79 CONTEXT_WIRING`；~~`TeamState.docs`~~（**C2 已删**，见 §结论-4） | 已复刻（走磁盘） | state 通道不再存在，别再按"文档经图传递"理解本仓；产物的读者是 `CONTEXT_WIRING` 装配进消息那一步 |
| 文档经消息装配 | `qa_engineer.i_context` | `team_graph.py:44-79 CONTEXT_WIRING` | 已复刻 | 读产物仓装 `CodingContext`/`TestingContext` |
| 动态线任务分派与回收 | `team_leader.py:75-86` + `mgx_env.py:24-58` | `team.py:65-71` | **未实现** | `Command.assignee` 无路由读者 |
| agent→用户前端通道 | `utils/report.py:1-330` | `report.py` + `events.py:34-44` + SSE | 现代化替换 | `s8 t1/t2` 钉块类型与信封词汇 |
| 用户→运行中 agent 插话 | `mgx_env.py:24-62` | `runtime.py:46` + `team_graph.py:121-129` | 已复刻 | drain 时机在 route 尾，**必须等当前节点结束**（非即时） |
| 中断 / 人工回答 | `role.py ask_human` | `role_zero.py:258-259`; `runner.py:181-201` | 现代化替换 | `s3b t8` 断言跨实例 resume；经典线无触发（对照 4 §结论-1） |
| 多 worker 通信一致性 | 源单进程 | `runner.py:59-104`; `events.py` seq | **已复刻 + 增量** | `s7 t2/t3/t11`：双 worker 事件重放逐条一致、跨 worker stop/chat、start 409 防双开 |
| 全局消息回放审计面 | `env.history` | 仅 `TeamState.messages` 累加 | 部分实现 | 无 `sent_from` 索引、无「这条消息谁收到了」的回放 |

## 四、「看着像有、其实没接线」

- **A. ~~`TeamState.docs`~~（原 `team_graph.py:22`）→ C2 已删干净（2026-09-21）**：原本三处 init 写空 dict、零读零写（`grep "docs\[" codeharness` 只命中 `document.py:204` 那个**同名不同类**的 `DocumentStore`）。真正的文档交接是磁盘 `ArtifactStore` + `CONTEXT_WIRING` 装配进消息。处置：字段删 + `team.py:42,61`/`sop/builder.py:34` 三处 `"docs": {}` 撤 + `checkpoint.py:23-24` 白名单里 `Document`/`Documents` 两行同批摘（摘后整套 `s3b` 含 t7/t8 真落断点 → **0 条 unregistered 告警**）+ 新增 `s3b t15` 钉不复燃。**给下一个人的实测事实**：LangGraph 对**未知的状态键静默丢弃**，所以「删了字段但还有人写 `docs=`」这种残留**不会报错**（删字段后 `s16` 五组仍全绿 exit 0）——只能靠 t15 那种正向断言守。
- **B. `utils/common.py:411 is_send_to`**：定义完整（含 `<all>` 判断），**全仓零调用者**（本轮复核 `grep -v "def is_send_to"` 仍空）。LangGraph 靠节点名查表取代了它。同类的还有 `addresses` / `member_addrs` 概念在本仓根本不存在。
- **C. `actions/talk_action.py` 是对人通道，不是 agent 间**（17 行全读）：`run()` 只做 `llm.aask` 并返回 `role="assistant"`、**不带 `send_to`** 的消息（`:11-17`），profile 文案明写 `reply_to_human`（`registry.py:77`）。它挂在 Sales/CustomerService 人设下面向终端用户。**别把它误认成"两个 agent 在对话"**。
- **D. `default_team` 的三个 RoleZero 角色（Mike/Alice/Bob）名不在任何路由表**：见对照 5 §五-B。它们即便被 `dynamic_assembly` 装进图，也只能收需求、不会互发。
- **E. `MESSAGE_ROUTE_TO_ALL`（`const.py:89`）与 `MESSAGE_ROUTE_TO_NONE`（`:90`）**：常量逐字照搬过来了，生产无读者（`route()` 只认 `<self>` 与具名）。属于「为了对账齐而保留」，与对照 4 §五-E 的 `finished` 同类，无害但要清楚它们不驱动任何行为。
- **F.【本轮】B2 新增的 `SearchEnhancedQA`/`ImportRepo`/`UploadKB` 都不参与通信**：这三件既不设 `send_to`、也不在任何 `SOP`/`CONTEXT_WIRING` 表里（对照 3 §四-A、对照 5 §五-E 同一条），所以它们不新增、也不改变本子系统任何一条 agent→agent 边。列在这里是为了堵住「加了 action ≠ 加了通信能力」的误读。

## 五、门禁覆盖

**有断言**：精准激活 `s3b:96-107`（4 角色激活 1）；`<all>`≠广播 `s3b:151-157`；显式 `send_to` 可路由 `s3b:112-120`；未知节点不发 `Send` `s3b:124-133`；订阅表可反证（改 `cause_by` 必须 END）`s3b:137-145`；`watch` × SOP 双向自洽 `s3b:267-290`；三角色链落盘（需求→PRD→任务→代码）`test_e2e_classic_line.py:51-66`；QA 自环收场 `:92`；interrupt/resume 跨实例 `s3b:194-217`；跨 worker stop `s7:145-173`；双 worker 事件重放一致 `s7:125-140`；start 409 防双开 `s7:101-119`；跨 worker chat `s7:181-190`；前端 kind 全覆盖 `s8:39-57`；`/graph` 画的是真装的表 `s8:77-106`。

**零断言**：
- **`send_to={"Engineer"}` 这条唯一的生产级具名投递**——`s3b t3` 用的是合成的 `"QA"`，`run_code.py:104` 的真实分支没有专门断言；
- `CONTEXT_WIRING` 的文档装配路径（`TeamState.docs` 已随 C2 删除并由 `s3b t15` 钉住，此项不再适用；但**装配本身**仍零断言）；
- **动态线多角色委派**（`s3b t14:398` 只断单队长图）；
- `talk_action.py` 无任何测试引用；
- 「一条消息被哪几个角色收到」的回放/审计面。

## 六、建议的收口顺序

1. **先给已存在的唯一具名投递补断言**：拿 `run_code.py:98-109` 两个分支（ok→自环、失败且分诊=Engineer→具名）各做一条 FakeLLM 断言。这是当前全仓唯一的 agent→agent 语义，裸奔不值得。
2. **`TeamState.docs` 二选一**：接成真通道（route 里读写、`ArtifactStore` 作 backend），或删字段 + 改 `checkpoint.py:21` 白名单。别留第三种「注释里是通道」。
3. **委派这条线要有个结论**：要么在 `dynamic_assembly` 补队友节点 + 让 `Command.assignee` 成为 `Send` 目标（机制已具备，缺的是路由表和断言），要么在 `判定-复制与重构清单.md` 里明写「动态线=单引擎，多角色委派 `弃`」。**这是六份对照里唯一一处"范式级"缺口**，它决定这个平台到底能不能跑多 agent 协作，值得单独决策。本轮未触及，仍是那条。
4. 若确实不需要广播，把 `is_send_to` 删掉（对照 4 §五-D 那批死装饰器同理），并让 `<all>` 出现在 `send_to` 时**显式告警**而不是静默忽略——现在它是被静默丢掉的目标。

## 复核方式

```bash
cd /e/Codeharness
grep -rn "send_to" --include=*.py codeharness | grep -v __pycache__        # 三处读者：team_graph:96,112 / agent:80；设方：run_code/debug_error
grep -rn "docs\[" --include=*.py codeharness                                # §四-A：仅 document.py 的 DocumentStore，非 TeamState.docs
grep -rn "is_send_to" --include=*.py codeharness server | grep -v "def "    # §四-B：空（零调用）
cat codeharness/actions/talk_action.py                                      # §四-C 全文 17 行
grep -rln "send_to" codeharness/actions/*.py                                # §四-F：新 action 无一设 send_to
PYTHONPATH=. PYTHONIOENCODING=utf-8 python tests/s3b_runtime.py
PYTHONPATH=. PYTHONIOENCODING=utf-8 python tests/s7_platform.py
# 源侧锚点
sed -n '176,212p' /e/MetaGPT/metagpt/environment/base_env.py
sed -n '432,456p' /e/MetaGPT/metagpt/roles/role.py
sed -n '75,86p' /e/MetaGPT/metagpt/roles/di/team_leader.py
```
