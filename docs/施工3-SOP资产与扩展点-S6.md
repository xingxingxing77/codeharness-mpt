# 施工 3 · SOP 资产与扩展点（S6）

> 这一步把源项目最有价值的东西搬进来:**prompt 与业务流**。同时定义平台的扩展点——源项目做不到的"不改内核就加角色"。
> 前置:S3 运行时 + S4 工具 + S5 记忆检索都已绿。

## 产出文件
```
codeharness/prompts/*                     # 16 件,逐字
codeharness/actions/*                     # 分 6 批
codeharness/roles/registry.py             # profile 注册表
codeharness/sop/{templates.py, builder.py}  # N7 新增
codeharness/ext_api/{roles.py, actions.py, tools.py}  # N2 新增
tests/s6_sop.py
```

## 批 1 · prompts 全量复制（`复`，1,478 行，零风险）

`metagpt/prompts/` 16 件一字不改搬:
`di/role_zero.py` 267 · `di/swe_agent.py` 246 · `product_manager.py` 175 · `di/write_analysis_code.py` 123 · `di/architect.py` 115 · `di/engineer2.py` 104 · `summarize.py` 92 · `task_type.py` 66 · `sales.py` 65 · `di/team_leader.py` 63 · `tutorial_assistant.py` 45 · `invoice_ocr.py` 44 · `metagpt_sample.py` 40 · `di/data_analyst.py` 26 · `__init__.py` 7 · `generate_skill.md` 74(资产)

**这是全项目唯一"不许优化"的地方。** 判断标准:与源文件逐字 diff 为空(可脚本化)。改一个 prompt 字,所有基于源 prompt 的对拍测试同时失效。

## 批 2 · Action 长尾（`改`，源保留 prompt 与契约）

| 批 | 源 | 行 | 要点 |
|---|---|---|---|
| 2a SOP 文档系 | `write_prd` 325 · `design_api` 279 · `write_code_review` 315 · `write_code` 228 · `project_management` 201 · `write_code_plan_and_change_an` 240 · `write_test` 70 · `run_code` 173 · `debug_error` 77 · `prepare_documents` 90 | ~1,760 | **你现项目这些文件普遍只有源的 1/3 到 1/4,先还债再铺新**:`write_prd` 82→325、`design_api` 48→279、`run_code` 29→173、`project_management` 26→201 |
| 2b 搜索研究 | `research` 343 · `search_enhanced_qa` 292 · `search_and_summarize` 147 · `generate_questions` 25 · `analyze_requirements` 76 | ~880 | 联网部分走 S4 的 tool 注册表,**不在 Action 里直接发 HTTP** |
| 2c 代码理解 | `import_repo` 226 · `rebuild_class_view` 235 · `rebuild_sequence_view` 605 · `summarize_code` 123 · `extract_readme` 123 | ~1,310 | **两个硬前置**：① `repo_parser.py`（你现项目 63 行 vs 源 1,023）；② `utils/{graph_repository,di_graph_repository,visual_graph_repo}.py` 724 行 —— SPO 三元组知识图谱存储，这四个 Action 全靠它落 `graph_repo/*.json`。**该三件套我原先误判为 `弃`，自查后已改判 `改`**（详见判定表 §一 末第二条纪律） |
| 2d `di/` | `write_plan` 88 · `write_analysis_code` 74 · `execute_nb_code` 328 · `ask_review` 62 | ~550 | `execute_nb_code` 走 `sandbox`;无 kernel 环境测试标 optional skip |
| 2e 垂直 SOP 模板 | `requirement_analysis/` 12 件 | ~1,400 | **平台视角:这不是必须内核,是一个 SOP 模板**。批 5 做,且可以先只做 `write_trd` + `evaluate_trd` 打通模板机制 |
| 2f 交互类 | `talk_action` 168 · `skill_action` 113 · `invoice_ocr` 189 · `write_docstring` 218 · `write_teaching_plan` 191 · `write_tutorial` 65 · `prepare_interview` 25 | ~950 | OCR/TTS 类外部服务判 `弃`,落点可省 |

**每个 Action 的统一改造动作**(五处,逐文件重复):
1. `self.llm.aask(...)` → `gateway.aask(...)`;结构化输出 → R2 的逐字段 validator
2. prompt 常量段**保留源文本**,只把 `Config.xxx` 取值换成 `settings.xxx`
3. 落盘 → `ArtifactStore`;产物文件名/目录一律引 `const.DocName` / `const.RepoName`,**禁止内联字面量**(不一致 = 静默查不到文件)
4. **Reporter 类名必须齐备**：源 Action 按 `DocsReporter`/`TaskReporter`/`EditorReporter` 等**类名**调用（共 12 个，见 R6）。你的 `report.py` 目前只有 `docs_block`/`task_block` 这类上下文管理器 —— 逐字复制前要么补齐类名，要么统一改调用点，**两者选一但必须显式**
5. **路由 tag 一律走 `schema._tag`，不要调 `utils.common.any_to_str`**：后者返回 `module.Class` 全限定名（common.py:390），会让逐字复制过来的 `watch` 订阅与新栈短名 tag 静默失配

⚠ 另有一处**必须先定再写**：`write_prd.py:280` / `design_api.py:233` 调 `mermaid_to_file`，渲染走服务端还是前端见 `施工4` 的取舍节——它会改变这两段的落盘代码，别等写完再改。

## 批 3 · 角色（`改`：不搬类样板）

源 `roles/` 22 个文件 3,485 行里,大部分是"profile + actions 列表 + watch"三样。新栈:**2 引擎 + registry profile**,类样板由引擎吸收。

- 18 个角色 → `registry.py` 里每个一条 profile 字典(源逐字)+ actions + watch,`build_role(name, llm)` 构造。**✅ 你已做到,这块是对的**
- 两个例外要当 `重` 认真做:`di/role_zero.py` 491(引擎语义的实际定义处)、`engineer.py` 513(SOP 线最复杂的角色)
- 若要保持源 import 路径可用,补薄类(`class Engineer(Agent): ...` <20 行)即可,**不要求**

## 批 4 · N3 执行策略可插拔（`新`）

同一个 profile 选不同执行方式,这是源没有的:
```
profile = {name, profile, goal, actions, watch, strategy}
strategy ∈ {"sop"（顺序流水线）, "role_zero"（自主命令循环）, "react"（按需挑 Action）}
```
源里这三者分别是 `Role`+`Team`、`RoleZero`、`Agent(REACT)` 三套类;新栈收成运行时一个字段。**接口在 S3 已定,这里对外。**

## 批 5 · N7 SOP 模板 + N2 扩展点（`新`，平台的价值所在）

**N7 SOP 模板**：源把组队写死在 `software_company.py`(157)。平台要把它变成可存取的模板对象:
```python
SopTemplate(name, roles=[profile_ref...], edges=[(cause_by_tag, to_role)...], init_message)
```
→ 2b/2e 那些垂直流程(trd/framework/requirement)都以**模板**形式注册,而不是新增角色类。这样批 2e 从"移植 12 个文件"变成"写 1 个模板"。

**N2 扩展点三件套**：让使用者不改内核造新东西。
- `ext_api/roles.py`:`register_role(profile)` —— profile 三样齐全即可用,内部走 `registry`
- `ext_api/actions.py`:`register_action(cls)` —— 继承 `BaseAction`,自动获得 R2 validator
- `ext_api/tools.py`:`register_tool(fn, schema)` —— 复用 S4 的注册表
- **验收标准(必须能演示)**:不修改任何内核文件,用一个脚本注册"新角色 + 新 Action + 新 Tool",跑通一条完整会话。**这条能过,才叫平台;过不了,S6 只是搬代码。**

## 门禁 `tests/s6_sop.py`
1. 每个新增 Action 恰好一个 FakeLLM fixture,断言 `instruct_content` 字段集合与源 schema 一致 ✅(2a 已加者)
2. `build_role(每个源角色名)` 不抛,profile 与源字符串相等 —— 未落(批3)
3. 经典线端到端:需求→PRD→设计→任务→代码→测试→沙箱真跑 pytest(**你现有 `test_e2e_classic_line.py` 就是这条,保持绿**) ✅全程绿
4. prompt 逐字 diff 为空 ✅(t1/t4,AST 顶层字符串常量比对)
5. **N2 演示脚本**:外部注册一个新角色并跑通 —— 未落(批5)
6. **度量**:2a 加厚前后,同一 idea 的 SOP 完成率与 **token** 对比(FakeLLM 下看结构完整性即可,真模型对比留 S9；成本只记录不作判据)

## S6 落地状态（2026-09-16，门禁 `tests/s6_sop.py` 11 组，提交 `89283e9`+`02cfc04`+`599e836`）

**批 1 ✅ 全量**：16 件 prompt 按源目录结构复制（`prompts/` + `prompts/di/`）。**重写只允许打在 import 语句的物理行上**（AST 定位）——第一版按行首正则误伤过 prompt 正文的示例代码、全局 replace 又改坏 `generate_skill.md` 与 `REFLECTION_SYSTEM_MSG`，两处都被门禁当场抓出。t1 的口径因此不是"文件文本 diff"而是**每个模块级字符串常量与源的 AST 相等**（比中 54 项；import 前缀本来必须变，逐字性的对象是字符串）。依赖件：`strategy/task_type.py` 逐字复制；`tools/libs/data_preprocess.py` 只带 prompt 需要的 `get_column_info`（源全件拖 sklearn+tool_registry，工具注册面在 S4 的 REGISTRY）。运行时改件 `prompts/role_zero.py`（S3 期本地化）**保留不动**——逐字资产在 `prompts/di/role_zero.py`，是否并回归批 3。

**批 2a ✅ 先还最欠的四件**（加厚按业务分支齐否计，不凑行数）：
- `WritePRD` 82→147：源三情形全落（bugfix / 新建 / 增量 REFINED）；判定 instruction 逐字取 `write_prd_an.py:171-186` 进 field description（structured 吃 JSON schema，模型看到的就是源那句）；象限图按**方案 C** 落 `resources/competitive_analysis.mmd`。
- `WriteDesign` 48→105：字段集改为与源 NODES 一致——**project_name 移出**（源注释已把项目名生成移交 WritePRD）、补 `anything_unclear`；新建/REFINED 双 prompt 逐字（`design_api_an.py:12-95`）；产物三件：`design.json`（机器真源，=源 system_design.json 的等价）+ `design.md`（人读）+ 两图 `.mmd`。
- `RunCode` 29→94：补上源整段缺失的 **LLM 复盘段**（PROMPT_TEMPLATE/TEMPLATE_CONTEXT 逐字，File To Rewrite/Send To 进存档与消息）；`ok` 判据仍 return_code，"Ran N tests OK" 死正则不复活；复盘失败只降级——模型挂了不能吞掉已跑完的测试结果。
- `WriteTasks` 40→112：schema 补齐源三键（`required_packages`/`shared_knowledge`）；增量合并照源 `_update_tasks:134`（NEW_REQ_TEMPLATE 逐字）；**`requirements.txt` 聚合落地**（源 `_update_requirements:154`，此前本仓完全没有这件——QA 的依赖声明真源）。
- 门禁 t3–t7：每 Action 一个 FakeLLM 剧本 + 模板逐字比对；`test_e2e_classic_line` 全程保持绿（加厚没打断下游）。

**显式拍板（批2 第 4/5 条要求的"两者选一但必须显式"）**：Reporter 走**改调用点**——全部用 `docs_block/task_block` 上下文管理器，不回补 `DocsReporter` 类名形态；路由 tag 全短名（`schema._tag` 制，`any_to_str` 零出现）。
**判 `推迟` 的源面（有理由，不是漏）**：三件的 `_execute_api`（任意路径出口与 per-session 边界冲突，N2 需要时按会话内口径重写）；`RunCode` mode=text 的 in-process exec（沙箱纪律：执行只走子进程）；多 PRD 文件循环与 git changed_files 记账（ProjectRepo 制，本仓单 PRD 会话制无对应物）。
**批 2a 后六件 ✅（提交 `599e836`）**：
- `WriteCode`：源 :52-64 的**三路上下文**补齐——上一轮跑测 stderr（按源命名 `test_{code_filename}.json` 从 test_outputs 捞）、`code_summary` 复盘存档、bugfix 工单（**消费即删**，源 :163 防冲突）；此前三路全传空串=修复回路失明。`get_codes` 抽成 `build_code_context` 与评审共用（源本就同源，review 调的就是它）。模板恢复源文本（含源 `quoto` 笔误与 js 示例段——逐字优先于"顺手改对"）。
- `WriteCodeReview`：重建为源 `run` 的 **k 轮 评审→LBTM 就地重写→复审**（新配置 `code_validate_k_times` 照源默认 1）；四段 prompt 常量由构建脚本自源**逐字节摘取**——第一版手抄漏了 FORMAT_EXAMPLE 整段、REWRITE 少 js 分支，教训入档：**逐字件必须脚本搬不手抄**。解析不出代码保上版不写空；每轮即落盘（源由 Role 收尾存，本件改即存，偏离已注明）。
- `PrepareDocuments`：补发 `PrepareDocumentsOutput`（project_path/requirements_filename/prd_filenames，源 :76）；git 初始化与 `config.update_via_cli` 属源 CLI 立项制不搬。
- `SummarizeCode`：换成源 PROMPT_TEMPLATE/FORMAT_EXAMPLE 逐字，上下文从产物仓取 design.json+tasks.json+全部 src 带围栏（`get_markdown_code_block_type` 复用）；源的 tenacity 重试不在此重复（gateway._acall 统一收口）。
- `WriteTest`/`DebugError`：逐段比对判定**已在源语义水位**——write_test 的缺口全在源 Role 侧，debug_error 的"Ran N tests OK"死正则判弃不复活、复盘摘要已由 RunCode 承接。加厚按业务分支计，不为行数凑代码。
- 门禁 t8–t11：PrepareDocuments 三键、WriteCode 三路上下文+工单消费即删+排除自身、Review k 轮行为、**批 2a 全部 prompt 常量与源逐字节同段存在（14 段）**。
**批 2c 类图半边 ✅（同日，提交 `001d586`+`e04947d`，s6 门禁 14 组）**：
- `repo_parser.py` 整件照源搬（1086 行；CodeChunk 面保留，两面一文件——消费方不同，拆文件才是混职责），`schema.py` UML 三件套**逐字节从源摘取**。
- **Windows 路径校准是这片的真风险**（判 `改` 的核心）：源 `_repair_namespaces`/`_create_path_mapping` 假设 POSIX 正斜杠路径——Windows 反斜杠不折叠时 pyreverse 的包名全被误裁成空串（t13 实测 pkg=''），盘符前导 `/` 补进键空间后 mapping 键与前导点根又差一字符（切片错位），两处都必须在同一函数里对齐、返回给 `_diff_path` 的 package_root 还要剥回平台形态。三次踩坑全部当场复现当场修，t13/t14 双向钉住。
- `graph_repository`(394)/`di_graph_repository`(168)/`visual_graph_repo`(162) 三件套照源落地（判 `改`=只改 import 前缀），networkx 3.4.2 在场；t12 钉 SPO 三条件过滤 + JSON 往返相等。
- `RebuildClassView` Action：pyreverse 类面 + AST 文件面（`_diff_path`/`_align_root` 对齐同一根）→ SPO 图 → `resources/data_api_design/class_view.class_diagram.mmd`（方案 C 的 .mmd 真源）+ `docs/graph_repo/class_view.json`；aiofiles 直写换 asyncio.to_thread（本仓零 aiofiles 依赖）。
**剩余三件判词（2c 内自洽收口，不是烂尾）**：`rebuild_sequence_view`(605) 等类图数据被 RAG/前端真实消费过再搬（全仓最大单件，源侧测试也最薄，先验证数据形状再动）；`import_repo`(226) **并入 N2 批 5**（它耦合 git_clone/GitRepository/archive——全是判"被 ArtifactStore 覆盖"的件，平台的外部仓库导入该是扩展点工具不是会话 Action）；`extract_readme`(123) 归 2b 搜索研究批。三件去向已在判定表"三个判定必须自洽"的约束下显式登记。
**未动**：2b–2f 其余、批 3–5。

**批 2b ✅ 搜索研究（同日，提交 `bf5c7ff`，s6 门禁 16 组）**：
- `Research`：stub 重写为源三件套的合成管线（源把三件拆开是给它的 Role 循环用的，本仓一个 Action 顺序走完）——关键词→搜索→拆解子问题 / 子问题→搜索→LLM 排序→逐结果摘要 / 汇总报告。八段 prompt 常量**构建脚本自源逐字节摘取**；`reduce_message_length` 与源 `:343` 的语言后缀 system 照留；`Not relevant.` 过滤语义照留。**联网只发生在 `search_internet` 工具**（t15 把工具替身挂在接缝上，七次模型调用的腿序就是契约）；playwright 浏览腿判「推迟」，摘要以搜索快照文本降级顶上——管线不砍，取材深度受限如实注明。
- `SearchAndSummarize`：自造 prompt 换成源 SYSTEM/PROMPT 逐字（sales/food 三常量入 t11 推迟豁免表），对话历史按源走。
- **t11 换了比对法**：从「值当子串搜源文件文本」改成 **AST 取源顶层常量值比对**——前者对带 `\` 续行的常量是错的（`CONDUCT_RESEARCH_PROMPT` 当场假失败），且值比对才是真正的「逐字」。
- 三只长尾小件不搬，判定表 §一 自查追加行已登记（两只源自身孤儿 + 一只并入浏览器推迟面）。
