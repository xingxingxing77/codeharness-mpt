# Codeharness · agent 开发平台 · 总入口

## 这个项目是什么

**目标不是复刻 MetaGPT，而是建一个以你的技术栈为目标的 agent 开发平台**：
`Python + FastAPI + LangChain/LangGraph + Vue3 + Pydantic + Qdrant + Redis`

`E:\MetaGPT\metagpt` 在这个计划里的身份是**供体与蓝本**——提供 prompt 资产、业务流定义、以及一份已被验证过的能力清单。它不是模具。

> **口径变更（2026-09-14，第三次）**：前两版口径先后是「产品主体 1:1」和「metagpt 全包除 ext 的 350 文件每个都要有落点」。**两者都作废**。现在不按文件数考核，按**能力覆盖 + 机制现代化**考核。`判定-复制与重构清单.md` 取代旧落点表成为唯一账本。

> **口径变更（2026-09-14，第四次）：源项目的「预算管理」不移植。** 边界是**砍预算强制、保留计量**——`max_budget`/`check_budget`/`NoMoneyException`/图上的 `budget_guard`/`TeamState.budget_used`/建会话的预算输入/前端「$已用·$预算」全部作废；token 计数与每轮成本累计**保留且只读**（上下文压缩依赖计数，成本只用于观测）。N5 随之拆开：**分布式限流留（归 N1），每会话预算废**。细则与实测证据见 `判定-复制与重构清单.md` §零。
>
> 一句话背景：这条线在现存代码里**从未生效过**——`budget_guard` 比较的 `budget_used` 全仓无人累加（恒 0.0），`check_budget`/`is_within_budget` 生产路径零调用方，前端预算字段取错了恒为 0 的 `total_budget`。删它是清理死代码，不是砍功能。

这条变更的实际效果：待写量从"移植 44,300 行"收敛到**约 10–12k 行新写 + 约 9k 行复制**——因为源里约 10.1k 行判 `弃`、6.7k 行判 `重`（不复制）。

> 这两组数字经过一轮**自查改判**：`base_serialization`(67) + `serialize.py` 映射函数(49) 从 `重` 移回 `复`，`graph_repository` 三件套(724) + `mermaid.py` 门面(164) 从 `弃` 移回 `改`。动任何判定前先读判定表 §一 末的**两条纪律**：① 一个文件承担几件正交的事就分几判；② 判 `弃`/`重` 前先 grep 调用者，保证与调用方自洽。

## 阅读集

| 文档 | 作用 | 什么时候查 |
|---|---|---|
| `判定-复制与重构清单.md` | **核心账本**：§零 不做清单（预算强制已作废）+ 源项目每块代码判 `复`/`改`/`重`/`弃`/`新`，带源行号 | 动手前先读 §零；每步开工再查对应段 |
| `施工1-地基与运行时-S1-S3.md` | const/schema/logs → LLM 网关 → LangGraph 运行时 | 现在 |
| `施工2-能力层-S4-S5.md` | 工具与沙箱 → 记忆、检索、经验池 | S3 绿后 |
| `施工3-SOP资产与扩展点-S6.md` | prompts 与 actions 搬运 → 扩展点三件套 | S4/S5 后 |
| `施工4-平台服务与度量-S7-S9.md` | Redis 多 worker → 前端与可观测 → 双跑对照 | 最后 |

## 五种判定（全套文档统一符号）

| 标记 | 含义 | 你的动作 |
|---|---|---|
| `复` | 纯资产/纯函数，与新栈无冲突 | cp，只改 import 前缀 |
| `改` | 业务逻辑可留，上下层调用要换 | cp 当底稿，替换 llm/config/落盘调用 |
| `重` | 源实现与新栈**功能重叠** | **不许 cp**，按新栈原生形态写 |
| `弃` | 平台不需要 | 不搬，留理由 |
| `新` | 源没有、平台必须有 | 自写 |

`重` 的 9 处（R1–R9）就是"用我的技术栈"这句话的全部内容——**把它们复制过来，这条验收口径就等于放弃**。`新` 的 N1–N8 就是"平台"两个字的全部内容——源里没有，别指望搬。（⚠ 术语约定：本文档里「作废」只用于描述被撤销的条目/口径，如 §零 的 N5 预算条目作废；表达"复制即失职"不用这个词。）

## 九步总表

| 步 | 内容 | 主要动作 | 门禁（不花钱的那件事） |
|---|---|---|---|
| **S1** | const / schema / document / logs | `复` 为主 | 消息往返 8 断言 |
| **S2** | LLM 网关 + FakeLLM | **R1** 弃源 provider；**R7** 配置换 pydantic-settings；**预算强制不写**（§零） | 零联网 payload 快照；FakeLLM 记账非零；**`stream_usage=True` 否则真模型流式恒 0 记账** |
| **S3** | LangGraph 运行时内核 | **R2 R3 R4 R5 R6** 五处重构 | 单 agent 双 Action；三角色链；interrupt→resume；**每轮执行节点数 vs 角色数** |
| **S4** | 工具注册表 + 沙箱 | **R8**；沙箱是 `新` | 注册表完整性；联网全 mock |
| **S5** | 记忆 / Qdrant / 经验池 | **R9** hybrid + 多租户 payload | 有/无 Redis 两种配置都绿；**单向量 vs hybrid 的 hit-rate@5 对比** |
| **S6** | SOP 资产 + 扩展点 | prompt 逐字 `复`；Action `改`；**N2 N3 N7** | 每 Action 一 fixture；**外部注册新角色跑通会话** |
| **S7** | 平台服务（Redis 多 worker） | **N1 N4** + 限流（N5 拆半，见 §零）；sync→async 桥 | 双 worker：`after=seq` 逐条一致、跨 worker stop/插话/resume；**计量真值 + 并发隔离** |
| **S8** | 前端 + 可观测 | `复` webui/frontend；**N6** 编排可视化与时间旅行 | **真模型 + 真浏览器五项清单** |
| **S9** | 度量与评测门禁 | 双跑对照；**N8**；策略可插拔曲线 | **S3/S5/S6 三个升级度量都有数字**（token 口径，成本仅记录） |

**S8 的终验要早于 S7 的 Redis 化切换**：先双实现不切默认，终验过了再切。理由——S7 改的 `sessions/events/runner` 这条链路从未被浏览器验证过，不拿基线就没法归因回归。

## 三条全局规则

1. **顺序服从 import 拓扑**。`Message` 不存在时 Action 连类型都写不出来，所以 schema 第一、actions 靠后。不要从 actions 开始，不要先写 UI。
2. **每步一个不花钱的门禁**（FakeLLM、零联网、零 token）。没有门禁的层，不要开始下一步。
3. **包内禁 `import fastapi`**；平台适配器只在包外 `platform/`。内核靠 `runtime.py` 的 ContextVar 与外界接缝，且拿不到 Redis 时必须能退回进程内实现（否则 S1–S6 自测全要起容器）。

## 每步的固定动作循环

```
读源文件（判定表给了路径与行号）
→ 定接口草图（单独一次 commit）
→ 按判定 cp 或重写
→ 写该步门禁测试
→ 跑绿 → git commit -m "SN: xxx"
```

"定接口草图再写实现"不是仪式：本仓库历史 4 个静默 bug（`action_cursor` off-by-one、`_route` 掐死多 Action、`except Exception` 吞 `GraphInterrupt`、节点未声明 `RunnableConfig`）**全部发生在接口边写边改的地方**。

## 环境准备

```bash
# 0) 先修 .gitignore：删掉 docs/ 那一行，然后把文档提交（见下方警示）
cd /e/Codeharness
docker compose up -d          # qdrant + redis，tag 必须 pin 死
pip install -e .
```
`.env`（不进 git）：`LLM__API_KEY` `LLM__MODEL` `LLM__BASE_URL` `QDRANT__URL` `REDIS__HOST` `EMBEDDING__BASE_URL`（bge-m3）

**自测跑法**（本机实测唯一可用姿势）：
```bash
PYTHONPATH=/e/Codeharness PYTHONIOENCODING=utf-8 F:/anaconda/python.exe tests/s1_schema.py
```
- 依赖只装在 `F:\anaconda\python.exe`，裸 `python`/`py` 缺 `pydantic_settings`
- `tests/*.py` 是模块级 `asyncio.run(main())` 的自测脚本，**不被 pytest 收集**
- GBK 控制台打印 `✅` 会 `UnicodeEncodeError` 退 1 —— 那是编码不是失败

## 现状基线（2026-09-14 晚实测）

`codeharness` 90 + `server` 10 + `tests` 6 = **105 个 py / 10,938 行**（另有 `workspace/` 下沙箱生成的 47 个产物文件，不计账）。五个自测脚本**全部 exit 0**：`s1_schema`（12 组）/ `s2_gateway`（12 组，含 `t11_budget`）/ `test_p1` / `test_roles_registry`（18 角色，实测 `ALL_ROLES` 确为 18）/ `test_e2e_classic_line`（SOP 链 + 沙箱真跑 pytest）。**全部 FakeLLM 驱动，真实模型从未端到端跑完过一次。** `/api/health` 已实测可返回 `llm_configured=true`。

### 本次审查缺陷清单（标「实测」的都已当场复现，非推测）

| 级 | 位置 | 现象 | 根因 |
|---|---|---|---|
| P0 | `team.py:26` | `run_project(idea, pid)` 一调即 `NameError: name 'cost_manager' is not defined`（**实测**） | 文档里写着"脚本场景入口"，从没跑过 |
| P0 | `runner.py:89` ↔ `team.py:47` | 前端拿到的 `total_cost`/token **恒为 0** | runner 建的 `CostManager` 没传进 `prepare_project`，图内部另建真账本，无人读它 |
| P0 | `runner.py:100-102` | 两会话并发时 `finally` 里 `ContextVar.reset` 抛 `ValueError: Token was created in a different Context`（**实测复现**），并跳过 `tasks.pop` → 会话永久卡 running | `_project_token`/`_sink_token`/`_chat_token` 挂在 `self` 上，被别的任务覆盖 |
| P0 | `runner.py:64` `_resume` | 人工回答之后产物块**静默消失**、插话失效，无一行日志 | `create_task` 复制 HTTP 请求的 context，没重装 `REPORT_SINK`/`CHAT_SINK`；`report.py:67` 是 `if not sink: return` |
| P0 | `gateway.py:63-84` | 真模型**流式**路径记账恒 0（而 `LLMConfig.stream` 默认 `True`） | `_build()` 没传 `stream_usage=True` → 末块无 `token_usage` → `add_usage(0,0)`；FakeLLM 自造 metadata，测不出来 |
| P0 | `utils/tree.py` / `utils/repo_to_markdown.py` | `import` 即 `ModuleNotFoundError: gitignore_parser`（**实测**） | 复制件带出的依赖没进 `pyproject.toml` |
| P1 | `provider/cost.py:9`、`provider/gateway.py:30`、`utils/common.py:323` | 三个 `NoMoneyException` 互不相干（`is` 全 **False**，**实测**），`runner.py:115` 只能用 `type(exc).__name__` 字符串兜底 | 同名异常三处定义。⚠ 预算作废后**整体删除**，不要合并成一个 |
| P1 | `utils/token_counter.py:18` ↔ `provider/token_costs.py:8` | 两份 `TOKEN_COSTS` 各 100 条、当前逐键相同（**实测**） | 真源分裂，调价必漏一处；单源定在 `provider/token_costs.py` |
| P1 | `utils/common.CodeParser.parse_code` ↔ `actions/write_code._parse_code` | 同一份多块 markdown，前者取**第一块**、后者取**最后一块**（**实测** `'a=1'` vs `'b=2'`），6 个 Action 依赖后者 | 两套解析器并存、行为已分叉 |
| P1 | `actions/debug_error.py:48` | `"Ran N tests ... OK"` 正则对 pytest 恒不匹配 | 死分支；真实判据只有 `return_code == 0` |
| P1 | `exp_pool/decorator.py`、`memory/memory.py`、`memory/summarizing.py`、`LongTermMemory`、`tools/libs/{git,editor}.py`、`rag/knowledge.KnowledgeBase` | 全部**零调用方**；`role_zero.py:41/49` 只留 `longterm_memory=None` 从不传入 | 经验池与长期记忆两条闭环根本没接线——**这是真实缺口，不是行数缺口** |
| P1 | `rag/knowledge.py:53` | reranker 抛错即 `return texts[:top_n]`，无日志 | 精排长期缺席也不会被发现 |
| P2 | `codeharness/actions/action.py` | **0 字节空文件已随 `9a765ba` 入库**，真正基类在 `base/action.py` | 残留占位；谁按包名惯例 import 它就 ImportError |
| P2 | `roles/registry.py` 等处 | 约 141 行注释仍是「来源 metagpt/xxx:NN」「判 `复`」式历史引用 | 历史账本该留在 docs，代码注释只写工程事实 |

## ⚠ 三个仓库级陷阱（都已实际发生）

1. **`.gitignore` 最后一行 `docs/` 仍未删**（2026-09-14 复测：`git ls-files docs` 为空）。全套文档至今**零版本控制**——本轮全部改判只存在于工作区，一次误 `clean` 就蒸发。**开工第一件事仍是删这行并提交 docs/。**
2. **只在真实路径上才炸的洞，FakeLLM 主线路径照不出来**。这已经是第二次：先是 `debug_error.py` 用了未 import 的 `Document`；这次是 `stream_usage`——五条自测全绿，而真模型流式一秒账都记不上。**立规矩：每条 FakeLLM 门禁都要写明"断言打在回放上，还是打在 `_build()` 的构造参数上"；凡是"真模型才会有的字段"，必须有一条打在构造参数上的断言。**
3. **本仓库存在并发写入者**。2026-09-14 20:12:57 出现提交 `9a765ba`（23 文件 / 2,632 行，含那个 0 字节 `actions/action.py`），当时有审查会话正在读同一工作区，根目录的一次性脚本 `_neutralize_batch1.py`、`_revert.py` 同时消失。多个会话同仓工作时，"改前 `git status`、改后立刻小口分批提交"不是仪式，是防丢工。

## 还债清单（按真实缺口排，不按行数）

1. **计量与上报这条链是断的**（`stream_usage` + runner 双账本 + 前端 `max_budget` 取错字段）—— 先修，否则 S9 的度量拿不到可信数字。
2. **`repo_parser.py` 63 行** vs 源 1,023：类图/序列图是前端与 RAG 的共同数据源，全项目最大的真实缺口。
3. **S5 两条闭环零接线**：经验池 `@exp_cache`、`LongTermMemory` 无人构造。行数看着够，能力其实不存在。
4. **`gitignore_parser` 依赖未声明**：S1 交付的 `tree.py` 目前 import 不动。
5. ~~`cost.py` 62 vs 源 149~~ —— **划掉**：预算强制作废后，这个"行数差"不再是债（见判定表 §零）。

## 开工顺序建议

**第 0 步（比 S9.1 更早）：把预算强制删干净、把计量链修通。** 具体是：删 `budget_guard` 节点与 `TeamState.budget_used` 与 `settings.max_budget`；`CostManager` 去掉 `max_budget/total_budget/check_budget/is_within_budget/update_budget` 与三处 `NoMoneyException`；`gateway._build()` 补 `stream_usage=True`；runner 的 `CostManager` 一路传进 `prepare_project`（消灭双账本）；`runner.py:151` 的快照字段改回真值；前端删预算输入与假计费页。理由：这条链不通，S9 拿不到任何可信数字，而它现在全是 0。

**然后才是 S9.1 的双跑基线**：在源项目用真实模型跑一条经典线，把 **token / 轮次 / LLM 调用次数 / 产物快照**存下来（成本同表记录，只作观测不作判据）。源随时能跑，自己这边会越改越远——**没有这份基线，三个月后你无法回答"我到底升级了什么"**。

之后 S1 → S2 → S3。S9 其余部分和 S7 的切换放最后，S8 终验插在 S7 之前。
