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

### 2026-09-15 增量（全部当场实测或当场复现后修掉）

- **第 0 步已落地**（并发会话提交 `07e6db1` + `3a75989`）：源码树里 `max_budget`/`budget_guard`/`budget_used`/`check_budget`/`is_within_budget`/三处 `NoMoneyException` **grep 已为空**（只剩陈旧 `.pyc` 与 `sessions.json` 里的历史数据字段）；`runner` 建单一 `CostManager` 并 `cost_manager=` 传进 `prepare_project`，双账本消失。
- `gateway._build()` 已补 `stream_usage`（`gateway.py:70`）；`gitignore-parser` 已进 `pyproject.toml:15`，`utils/{tree,repo_to_markdown}.py` import 实测通过。
- **八个自测脚本全 exit 0**：`s1_schema`(12) / `s2_gateway`(12) / `s3_report_action`(12) / `s3b_runtime`(9) / **`s4_tools`(35)** / `test_p1` / `test_roles_registry` / `test_e2e_classic_line`。仍**全部 FakeLLM 驱动，真实模型至今没端到端跑过一次**。
- **S4 推进四件**：① R8 按第一条纪律拆开判——注册表数据结构与「按名字/tag 选工具」自写 60 行（`fc705ef`），反射 YAML schema 那半件判 `重` 不搬，连带 `tool_data_type.py` 改判 `弃`；② `Terminal` 保态终端移植（`15c0970`），修掉源三处会卡死 web worker 的点；③ `linter` 以 110 行标准库优先实现落地（`778b707`），替掉 `_shims.py` 里那个让 editor「改完自动 lint」变成空话的假垫片，**不引入 `grep_ast`/`tree_sitter_languages`**（未装 + 上游停维护，且源的 Python 检查链本来就不经它们）；④ `env.py` 158 行判 `弃`——唯一调用点由 `_config_compat.get_env_default` 按 R7 的 `APP__KEY` 口径覆盖，并修掉它与调用点签名不匹配（同步被 `await` + 关键字名对不上，一调即 TypeError）。
- **S4 收口本轮（per-session + 联网）**：① **per-session 隔离**判 `新` 落地——出口只有一个 `runtime.session_root()` = `workspace_root/{CURRENT_PROJECT}`，文件工具边界 / shell cwd / Terminal cwd / 沙箱 scratch / `ArtifactStore` 五处统一走它，S7 换容器只改这一处；② 联网搜索三件收成一件 `tools/search_engine.py`（72 行，serper + ddg 直取 HTML，**零新依赖**），`settings.search` 从"声明了没人读"变成真接缝；③ 超时执行收进一个 `sandbox.run_proc`，Windows **按进程树杀** + 自控 pump 留输出 + `PYTHONUNBUFFERED=1`；④ 浏览器类（`libs/browser.py` + `web_browser_engine`+playwright）判 **`推迟`**：playwright 未装、依赖的 `utils/a11y_tree.py` 本仓没有、全仓零 import 零 prompt 引用——照抄只会得到无人调又装不上的文件。**S4 仍缺**：`Editor` 命令面接进注册表（S6 接线义务）、per-session 的**强制**边界（等 S7 容器）、S8 级 API 门禁（本轮 server 三处改动只手工端到端验过）。
- 本轮顺带修掉四处（全部当场复现过）：① 工具层此前写的是 `workspace_root` 根而非会话目录，前端文件树读 `session.workspace` → **agent `write_file` 的产物在 UI 里根本看不见**（TestClient 端到端复验：改后同一份文件立刻出现在树里）；② `server/api/workspace.py` 的读取口用 `str.startswith` 判越界，与 t2 同源缺陷（兄弟目录前缀命中）→ 改 `is_relative_to`；③ `project_name` 直接拼成会话目录，`../../evil` 就能越出 workspace 建会话 → 入口 `CreateSessionReq` 校验 422 + `session_root()` 只认直接子目录名兜底；④ `create_subprocess_exec` 在 Windows 上不会摊平单个 list 参数（实测 `TypeError: expected str...not list`），`run_proc` 自己摊。
- 上一轮新修两处（提交 `8db857d`）：① `tools/_safe` 用 `str.startswith` 判越界，`workspace_root=ws` 时 `../ws_probe/x` 因前缀命中被判区内，`write_file` 可写到沙箱外——改 `Path.is_relative_to`，门禁 t2 锁死；② 三处超时分支 `kill()` 后不 `drain`，Windows 退出时抛 `unclosed transport` 噪声，`sandbox` 两处还丢超时前已产出的 stdout（**本轮实测：只补 `drain` 并不够，见上条 ③**）。
- 仓库级陷阱 #1 已清：`docs/` 从 `.gitignore` 移除并入库（`33244d8`）；P2 的 0 字节 `actions/action.py` 已 `git rm`（`a30b91c`）。
- **仍未闭合的真实缺口**：S5 整层偏薄且**无门禁**（`memory` 185 行 / `document_store` 84 / `rag` 60 / `exp_pool` 29 / `strategy` 111，`tests/s5_memory_rag.py` 不存在，R9 hybrid 检索要求的 Qdrant named vectors 无处落）；`repo_parser.py` 63 vs 源 1,023；S4 侧剩 `Editor` 命令面接线（S6）+ per-session 强制边界与浏览器类（同批等 S7）；server/S8 层零门禁。供体 `E:\MetaGPT` **2026-09-15 已授权读取**。

### 2026-09-15 晚 · S5.1（记忆层第一条闭环）

- **`utils/redis.py` 落地**（`复`，63 行语义照抄）：惰性连接 + 连不上只 warning、读写静默返回 `None`/`False`。t2 把 `settings.redis.port` 指向死端口 6399 实测：`ConnectionError` 被吞，不抛。这是「没起容器也能跑自测」的前提。
- **`memory/brain_memory.py` 落地**（`复改` 345 → 168 行）：`history`/`knowledge`/`historical_summary` + 整体 JSON 存**单个** redis key + dirty 才写 + `DEFAULT_MAX_TOKENS=1500` 分窗滚动摘要 + 滑动窗口重叠。四条零调用者分支判 `弃`（三个 `MetaGPTLLM` 兜底、`get_title`、`is_related`/`rewrite`），`memory/summarizing.py`(17 行，与 BrainMemory 正重复) **`git rm`**。
- **RoleZero 工作记忆接线**（本步的真实缺口，不是行数缺口）：实测 `_think` 此前每轮只发 `[System, Human]` **两条**消息，`_act` 拿到的工具结果**从不回喂**，而 `CMD_PROMPT` 反复要求 "review the conversation history" → 跨轮失忆。现在 `Memory` 做窗口（`memory_k` 默认取 `settings.memory_overflow_size`，**该字段第一次有读者**），溢出交 `BrainMemory` 摘要落 Redis，key = `BRAIN_MEMORY:default:{会话目录}/{角色名}`（复用 `CURRENT_PROJECT`，与 `session_root` 同一接缝）。t7 断言打在 FakeLLM 收到的 messages 上：`已写入 prd.md` 确实出现在下一轮 payload。
- **P0（实测，非推测）**：runner 不传 `agents` 时兜底走 `default_team`（TeamLeader/Alice/Bob），**三个名字无一在 `team_graph.SOP` 目标里** → LangGraph 只打一行 `Ignoring unknown node name PM`，**整场会话零次 LLM 调用**就算跑完（`prepare_project` + `ainvoke` 实测 `fake.calls == 0`）。这就是「八个自测全绿而 Web 会话其实什么都没干」的又一个真实路径洞。修法：兜底改 `_default_agents()` → `classic_team`，并补 `classic_team` 缺的第 5 个角色 `Architect`（docstring 自称五角色、`WriteDesign` 的 `sent_from` 也写着 `"Architect"`，实际只建了四个）。修后同一冒烟 `LLM_CALLS=3`。**S3(b) 新增 t10** 双向钉住「兜底角色名覆盖 SOP 目标」+「default_team 与经典线名字互斥」，`default_team` 降为 S6 动态范式的备选组队（已写明不参与默认路由）。
- 顺带修一处一调即炸的接缝：`BrainMemory._get_summary` 原本照源传 `stream=False`，而 `provider/fake.FakeLLM.aask` 签名不收 `stream` → 自测路径必 `TypeError`。本仓 `LLMGateway.aask` 默认即 `stream=False`，去掉该关键字两边都通。
- 门禁基线：**九个不花钱的自测脚本全 exit 0** —— s1(12) / **s2(13)** / **s3a(13)** / **s3b(10)** / s4(35) / **s5_memory_rag(18)** + test_p1 / test_roles_registry / test_e2e_classic_line。
- **S5.2 已落地**（R9 检索层，详见 `施工2` 的 S5.2 落地状态）：`document_store/qdrant_store.py` 四形态实测在真集合上可见，`longterm`/`exp_store`/`knowledge` 三件改接，`storage/benchmark/s5_hitrate.json` 记下 `hit@1 2/4→4/4`、`mean rank 1.75→1.0`。**S5 只剩 S5.3 `exp_pool` 闭环**；`brain` 的会话级 resume 仍等 S7。

### 2026-09-15 深夜 · 真模型首跑（`.env` 已配 qwen3.8-flash + 本机 Qdrant 容器）

**「真实模型至今没端到端跑过一次」这句话从今天起不成立了。** 用 `tests/manual_real_e2e.py`（花真钱，不进九件套门禁）连跑 8 场真实会话，把 FakeLLM 全绿也照不出来的东西一批批炸出来，全部当场修掉并钉进不花钱的门禁：

| 现形 | 根因 | 修法与断言 |
|---|---|---|
| token 记账恒 0 | `cfg.stream` 默认 True → `ChatOpenAI(streaming=True)` 把用量只写进 `usage_metadata`，`add_usage` 读的是 `response_metadata.token_usage` | 标准口径优先、legacy 兜底；S2 t13 |
| 动态范式每轮思考不进账 | `structured()` 直接 `with_structured_output().ainvoke`，**绕开 `ainvoke` 这个唯一记账出口** | 内部改 `include_raw=True` 自记（顺带修好 `list` 字段解析成 `[', ']`）；S2 t13 打桩钉住 |
| 流式仍记 0 | 聚合只取末块 metadata，而 usage 常在中间块（末块是 None） | 边收边留最后一个带 usage 的块；S2 t13 |
| 被截断的调用不记账 | 抛异常路径不走记账；`LengthFinishReasonError` 的 usage 挂在 `ChatCompletion.usage` 上 | `_account` 移到 raise 之前并支持两种形态 |
| 长产出必然截断 | `.env` 写的是 `LLM__MAX_TOKENS`（复数），字段实为 `max_token`（单数，照源）→ 被 `extra="ignore"` 静默丢掉；thinking 模型的 reasoning 也算在预算里 | 实测 4096/8192 截断、**16384 通**；`.env` 已改单数 |
| 截断后整场会话崩掉 | `_fix_unclosed` 只补 `]`，「整棵对象少 `}`」「断在字符串里」两种最常见截断等于白放 | 按括号栈补齐；S2 t13 三种形态逐个钉 |
| `KeyError: 'filename'` | `TaskList.task_list: list[dict]` 等于没契约，真模型回了缺 filename 的条目 | 改具名 `TaskItem`（`filename` 必填非空）；S3(a) t13 |
| `PermissionError [Errno 13] 'workspace/x/src'` | `filename=""` 时路径塌成目录本身，`write_text` 打到目录上 | `_checked` 一处判定：**写拒、读软退 None**（`DebugError` 靠它走「缺少修复上下文」）；S3(a) t13 |
| 会话 `finished` 却零代码 | 真模型回了 `task_list=[]` → 零条 Send → 路由不报错地跑到收场 | `WriteTasks` 空清单就地抛并带上设计文档片段；S3(a) t13 |

**仍未闭合（都有实测证据，按优先级）**：
1. ~~**runner 的用量快照不合流**~~ —— **当晚闭合**（提交 `ed8e6e8` + `242d6a8`）：`on_chat_model_end` 每笔合流 store/落盘/SSE 三头；重启 resume 从落盘快照**续算**不覆盖；零用量漏账从"静默记 0"变成 `add_usage` warning（有 warning 才有可 grep 的账差）。新门禁 `tests/s8_runner_meter.py`（4 组）把"传给图的账本必须就是 runner 手上那个实例"钉成双向断言。冒烟复跑实证：跑动中 sessions.json 里的 cost 已在落且非零。那复跑当场又炸出**第十处**：`APIError: Model output became abnormal while generating a JSON response for response_format`——qwen MaaS **服务端**abort 掉 JSON 模式生成（这段错误文本不在任何本地包里），同一请求重发即成；`_acall` 原本只重 Timeout/ConnectionError/OSError，瞬态错直接吹掉整场会话。修法有个继承链坑：openai 的 `APIStatusError`（4xx/5xx，重了烧钱）是 **`APIError` 的子类**，判据必须 `isinstance(APIError) and not isinstance(APIStatusError)`（s2 t14 用真 SDK 异常类族两头钉）。
2. ~~**embedding 端点没配**~~ —— **2026-09-16 闭合**（`0a62e4c`）：本机 ollama 的 bge-m3（`:11434/v1`，实测 1024 维）已写进 `.env`；`gateway.embeddings()` 必须 `check_embedding_ctx_length=False`（langchain 默认发 tiktoken token-id 数组，ollama 只收字符串，直接 400）。真语义路径由 s5 t25 门禁常驻验（探活式，不在线就跳）。**reranker 仍离线**（`bge-reranker-v2-m3` 未部署，精排照旧降级 + warning）。
3. ~~**`qwen3.8-flash` 不在 `TOKEN_COSTS`**~~ —— **2026-09-16 闭合**（用户报价：输入 0.8 / 输出 2.7 元/百万 token）：表内注明该行是**人民币口径**（其余行美元，前端 "$" 符号是展示层遗留，不跨币种换算——归 S8 一并清）。缓存命中价（输入 0.1）未入账：MaaS 不回传 `cache_read` 字段，归 S9 计费口径对齐。
4. **LangGraph 会打 `Deserializing unregistered type codeharness.schema.Message from checkpoint`**，并声明"未来版本将拦截"——checkpointer 的 msgpack 白名单要显式配（S7）。
5. `structured` 的 `include_raw` 路径每次调用会打一条 pydantic 序列化 `UserWarning`（噪声，未影响结果）。


### 2026-09-15 深夜二段 · 计量合流闭合 + S5.3 经验池闭环（提交 `ed8e6e8` + `242d6a8`）

- **计量链残尾收掉**（上面「仍未闭合 #1」的详情）。附带修掉一处同族坑：`utils/redis.py` 的 async client 绑死在建它的 event loop 上，换 loop 后读写静默变 None（降级语义吞掉 `Event loop is closed`）——`_connect` 现在认 `get_running_loop()` 变化重建。
- **S5 三片就此全闭**：`exp_pool/` schema/serializers/manager/decorator 四件落地 + `@exp_cache` 真接线 `RoleZero.llm_cached_think`（命中=零模型调用，池默认关，`EXP_POOL__*` 开）。源 990 行的 chroma/bm25 与 LLM judge/ranker 按判定不复制，排序换成 Redis 命中计数（键=点 id=uuid5(tag,req)，三处同一派生式）。详见 `施工2` 的 S5.3 落地状态。
- 门禁基线更新：**十个不花钱脚本全 exit 0** —— s1(12) / **s2(14)** / s3a(13) / **s3b(11)** / s4(35) / **s5_memory_rag(24)** / **s8_runner_meter(6)** + test_p1 / test_roles_registry / test_e2e_classic_line。
- hit-rate 表复测会漂（HNSW 近似检索 + IDF 实时统计，4/4→3/4）：方向性结论仍成立，但 S9 要可复现数字得钉 ef/exact——口径记在 `施工2` S5.2 末。

### 2026-09-15 深夜三段 · 真模型首次完整跑通（第十一处闭合，提交 `5f6225f`）

复跑（`real_e2e_d`）在 Engineer 阶段又现形一处，且**根因不在报错点**：`ValueError: 非法产物文件名 ''（子目录 src）`。往上查穿的实是——`classic_team` 组队**没给任何 Agent 传 watch**，而 `Agent._observe` 默认只订阅 `UserRequirement`：SOP 把消息路由到了角色，角色却在观察层把跨角色消息整条丢掉，退化成拿空记忆干活。这场实测的直接后果：`design.md`/`tasks.json` 是模型**对着空需求编出来的通用套话**（node_modules/package.json，tinycli 根本不需要），Engineer 拿空 filename 烧掉一次真钱后被产物仓写拒炸掉会话。e2e 自测没抓到是因为它手写 `watch={"WriteTasks"}`——**生产组队与测试组队两套装配各长各的**，S3b t10 的「两套表互洽」教训第三次应验，这次是三张表（SOP 路由 × watch 订阅 × 产物契约）。

- 修法（三处）：`classic_team` 每角色的 watch 对齐 SOP 入边；`WriteCode` 空 filename **软失败**（不进模型不烧钱、不抛、错误回喂自愈）；`DebugError` 回流消息补 `filename=code_filename` 对齐消费方键名。
- 门禁：**s3b t11** 双向钉——SOP 每个 cause_by 必须 ∈ 目标角色 watch、watch 不许订阅 SOP 外孤儿 tag、`Engineer._observe` 行为级收到 `WriteTasks`、WriteCode 空上下文 FakeLLM 零调用。
- 🏁 **首战完整跑通**（`real_e2e_e`，21:51–21:58）：PRD→Design→Tasks→Engineer 写出 `src/tinycli/main.py`（argparse，`--version` 真打 `0.1.0`，按真需求只此一文件）→QA WriteTest→END，`finished` 无错。产物内容跟着需求走（不再编 node_modules），watch 接线生效的直接证据。
- 计量合流实战实证：跑动中 cost 逐笔涨（0→471/6002→1764/13388→终 2823/31856），失败/完成快照都在 sessions.json。
- ⚠ 新现形（当场证据）：
  1. ~~**`manual_real_e2e` 打完 FINAL 后挂住不退出**~~ —— **当晚闭合**（提交 `8eded33`）。根因**不在 app shutdown**：`/api/sessions/{sid}/events` 是给浏览器 EventSource 的 **SSE 无界流**（history 之后 `while True` 转 live + keepalive），冒烟脚本拿普通 GET 读它=等一个永不结束的响应；且 starlette TestClient 的 transport 用 `portal.call` **把应用跑到底才返回响应**，对无限流连"读到一半断开"都做不到（探针实测：3 条 data 全 yield 后 `iter_lines()` 一条都收不到，纯挂）。修法：新增有界回放口 **`GET /events/history?after=`**（返回 JSON，事后审计/S9 采集/断线补历史都走这条，SSE 只留给付费浏览器连接）——脚本消费它。复现验证：修复后 events 56 条回放、tree 正常、进程秒退。顺带补上早该接的第二处不对称：lifespan 停机调 `checkpoint.close_all()` + `LogBridge.remove()`（`close_all` 的 docstring 原文就写着"server 应在 lifespan 关闭时调用它"，一直没接线；复现实测 lifespan 退出后留着一条 aiosqlite worker 线程）。门禁 s8 t5/t6 各钉一头。
  2. **产物混头**（未修）：`main.py` 首行是 `## tinycli/main.py`——`_parse_code` 取了围栏体但把 markdown 小节头也带进文件（WriteCode 输出格式是 `## file name\n```python…```），纯外观但会让 `python main.py` 直接 SyntaxError，S6 的 fixture 该钉"产物必须可执行"。
  3. **真模型的 `prompt_tokens` 报得极小**（本场终值 2823，对五角色的完整 system+context 不合理，未修）：qwen MaaS 大概率按**缓存口径**报（隐式前缀缓存命中不计），token 账与计费口径要在 S9 双跑前对齐清楚。

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

### 2026-09-16 · 挂死收口实证 + 真模型第十二处（提交 `8eded33` + `a6cb4b6`）

- 挂死修复过真机：冒烟第四场 `SMOKE_EXIT=0`，FINAL 后 events 有界回放 286 条（status 7 / Thought 256 / Docs 18 / log 4 / error 1）、文件树打印、进程秒退——上一段「当晚闭合」里"复现验证"那句现在有了真模型证据。
- **第十二处现形并闭合**（`a6cb4b6`）：这场的会话本体 `failed` 在一个新漂移上——真模型这回给的是 `filename='/main.py'`（带前导斜杠的绝对路径）。产物仓 `_checked` 按契约**写拒是对的**（e005884 的判决没变），错的是那个 `ValueError` 一路吹穿 team graph，把已完成角色与花掉的 1512/7322 token 全部陪葬——**经典线 `Agent._act` 缺 RoleZero 早已立规的 self-heal 收口**。修法：Action 抛异常 → `[错误]` 消息回喂记忆走下一轮；`GraphInterrupt` 是唯一必须照抛的（interrupt/resume 靠它）。门禁 s3b t12 双条钉死（普通异常回喂进记忆 / GraphInterrupt 穿透）。
- 门禁基线：**十个不花钱脚本全 exit 0**，s3b 升至 12 组、s8_runner_meter 升至 6 组。

## ⚠ 三个仓库级陷阱（都已实际发生）

1. **`.gitignore` 最后一行 `docs/` 仍未删**（2026-09-14 复测：`git ls-files docs` 为空）。全套文档至今**零版本控制**——本轮全部改判只存在于工作区，一次误 `clean` 就蒸发。**开工第一件事仍是删这行并提交 docs/。**
2. **只在真实路径上才炸的洞，FakeLLM 主线路径照不出来**。这已经是第三次：先是 `debug_error.py` 用了未 import 的 `Document`；第二次是 `stream_usage`——五条自测全绿，而真模型流式一秒账都记不上；第三次是 runner 兜底组队的角色名与 `SOP` 目标名对不上，九个自测全绿而真实会话一次 LLM 都没调（2026-09-15 晚实测并已修，S3b t10 钉住）。**立规矩：每条 FakeLLM 门禁都要写明"断言打在回放上，还是打在 `_build()` 的构造参数上"；凡是"真模型才会有的字段"，必须有一条打在构造参数上的断言。** 结构性的同理：凡是「两套表必须互相自洽」的地方（角色名 ↔ SOP 目标名、工具名 ↔ profile 选择名），要有一条双向断言。
3. **本仓库存在并发写入者**。2026-09-14 20:12:57 出现提交 `9a765ba`（23 文件 / 2,632 行，含那个 0 字节 `actions/action.py`），当时有审查会话正在读同一工作区，根目录的一次性脚本 `_neutralize_batch1.py`、`_revert.py` 同时消失。多个会话同仓工作时，"改前 `git status`、改后立刻小口分批提交"不是仪式，是防丢工。

## 还债清单（按真实缺口排，不按行数）

1. **计量与上报这条链是断的**（`stream_usage` + runner 双账本 + 前端 `max_budget` 取错字段）—— 先修，否则 S9 的度量拿不到可信数字。
2. **`repo_parser.py` 63 行** vs 源 1,023：类图/序列图是前端与 RAG 的共同数据源，全项目最大的真实缺口。
3. ~~**记忆三条闭环，已闭一条**~~ —— **三条全闭**（S5.1 工作记忆 2026-09-15 晚 / S5.2 ltm 构造点+召回进 prompt 当晚 / S5.3 经验池 `@exp_cache` 真接线 `RoleZero.llm_cached_think` + Redis 命中计数排序，s5 门禁累计 24 组，2026-09-15 深夜）。开关默认关（照源），t24 钉"命中零模型调用/关池透传"。行数看着够，能力可以其实不存在——照旧按接线计，不按行数计。
4. **`gitignore_parser` 依赖未声明**：S1 交付的 `tree.py` 目前 import 不动。
5. ~~`cost.py` 62 vs 源 149~~ —— **划掉**：预算强制作废后，这个"行数差"不再是债（见判定表 §零）。

## 开工顺序建议

**第 0 步（比 S9.1 更早）：把预算强制删干净、把计量链修通。** 具体是：删 `budget_guard` 节点与 `TeamState.budget_used` 与 `settings.max_budget`；`CostManager` 去掉 `max_budget/total_budget/check_budget/is_within_budget/update_budget` 与三处 `NoMoneyException`；`gateway._build()` 补 `stream_usage=True`；runner 的 `CostManager` 一路传进 `prepare_project`（消灭双账本）；`runner.py:151` 的快照字段改回真值；前端删预算输入与假计费页。理由：这条链不通，S9 拿不到任何可信数字，而它现在全是 0。

**然后才是 S9.1 的双跑基线**：在源项目用真实模型跑一条经典线，把 **token / 轮次 / LLM 调用次数 / 产物快照**存下来（成本同表记录，只作观测不作判据）。源随时能跑，自己这边会越改越远——**没有这份基线，三个月后你无法回答"我到底升级了什么"**。

之后 S1 → S2 → S3。S9 其余部分和 S7 的切换放最后，S8 终验插在 S7 之前。
