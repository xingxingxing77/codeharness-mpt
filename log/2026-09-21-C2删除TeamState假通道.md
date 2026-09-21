# 2026-09-21 C2 删 TeamState.docs 假通道 + 白名单同批摘（阶段开发驱动器，本会话第 2 项）

会话标记 `stage-dev-B2-closeout-20260921-0914`。同一会话第 2 项（第 1 项 B2 见 `2026-09-21-B2收口与SSE流尾游标竞态.md`）。
开工前按 §0.6 重验锚点，全部成立；未偏离 PLAN 给的判据。

---

## 1 · 现象

`TeamState.docs`（`codeharness/environment/team_graph.py:22`，注释写「filename -> Document（产物仓）」）
在图状态里挂了个文档通道的名分，但：

- 写侧只有三处初始化把 `"docs": {}` 塞进图初值：`codeharness/team.py:42`、`codeharness/team.py:61`、
  `codeharness/sop/builder.py:34`；
- 读侧**零处**；
- `checkpoint.py:23-24` 的 msgpack 白名单里还专门登记了 `Document` / `Documents`，注释 `:21` 自陈
  「docs 装 Document」——一个会让人按「文档经图传递」理解本仓的空声明。

PLAN §4 C2 的判据是二选一：接成真通道（有读者 + 有写者，门禁断言「存在读者」）**或删干净并回写判定表**。

## 2 · 排查过程（实际命令与输出）

**① 确认没有别的同名 `docs` 被误伤。**

```
grep -rn '"docs"\|docs=' --include=*.py codeharness/ server/ platforms/ tests/
```
命中的非通道项逐个排除：`codeharness/actions/import_repo.py:121` 是
`await self._artifact_store.save(subdir="docs", doc=doc)`（**磁盘产物仓，正是真通道**）、
`codeharness/const.py:99 DOCS_FILE_REPO = "docs"`（常量）、`codeharness/schema.py:119 Documents(docs=…)`
（**另一个类**的字段，PLAN 早已标为干扰项）、`document.py:236 n_docs=`（形参名）。
→ 图状态通道只有上面那三处写、零读，结论与 PLAN 一致。

**② 确认有两个同名 `TeamState`，别改错对象。**

```
grep -n "class TeamState" codeharness/environment/team_graph.py codeharness/schema.py
→ team_graph.py:19（TypedDict，生效的那份，含 docs:22）
→ schema.py:665（pydantic 版，字段只有 round/debug_rounds/finished，**根本没有 docs**）
```
照旧文字去改 `schema.py` 会改错对象——PLAN 的这条提醒成立。

**③ 白名单那两行到底还有没有人用？做实验而不是猜。**
先把 `TeamState` 的 `docs` 字段删掉（此时三处 init 还在写它），然后拿减配后的白名单跑整套 `s3b`：

```
# log/c2_probe_whitelist.py：把 _ALLOWED 减到只剩 Message 系，跑 s3b.main()，抓 langgraph 的 warning
S3(b) 门禁通过：15 组 …
DOC_WHITELIST_REMOVED_UNREGISTERED_WARNINGS: 0
```
→ 含真落断点的 t7/t8 全程**没有一条 unregistered 告警**，说明 `Document`/`Documents` 两个登记项
**确实只为那条假通道而存在**，可以同批摘掉。（这一条是实测，不是「注释这么说」。）

**④ 关键发现：删字段不会让残留写者报错。**
在「字段已删、init 还写着 `"docs": {}`」这个中间态跑 `s16_route_state`（它的 `_init()` 就是图输入）：

```
F:/anaconda/python.exe -B tests/s16_route_state.py   →   EXIT=0
S16 门禁通过：5 组
```
→ **LangGraph 对未知的状态键是静默丢弃**，不抛不警。这意味着「假通道删了但某处又开始写 `docs=`」
这种复发**运行期发现不了**，必须留正向断言。于是新增 `s3b t15`，并反过来验证它会红：

```
LAYER1=CAUGHT docs 假通道长回来了：TeamState 键集 ['debug_rounds','docs','finished','memori…
LAYER2=CAUGHT team.py 里又出现往图状态写 docs 的地方——产物通道是磁盘 ArtifactStore，不是 TeamState
```

## 3 · 根因

不是 bug 是**残留的假接线**：源项目（`software_company.py:44-66` + `document_store/`）确实把文档放环境里传，
本仓改成了「落盘 `ArtifactStore` + `CONTEXT_WIRING`（`team_graph.py:44-79`）按需装配进消息」这条真通道，
但 state 里那个 `docs` 键连同白名单登记项一起留了下来。危害不是占几行，是**误导**：
下一个想找「文档怎么传给下游角色」的人会在图状态里找，而答案在磁盘。

## 4 · 解决方案

删干净（判据的另一支），共 10 个文件：

| 面 | 改动 |
|---|---|
| `team_graph.py` | 删 `docs: dict` 字段 |
| `team.py:42,61` / `sop/builder.py:34` | 三处 `"docs": {}` 撤掉 |
| `checkpoint.py` | 白名单摘掉 `Document`/`Documents`，注释改成实测口径（含「摘后跑整套 s3b → 0 告警」这条依据） |
| `tests/s3b_runtime.py` | 新增 **t15**（两层：键集无 docs + 三处入口源码无 `docs` 写入）；6 处测试构造去掉 `docs={}` |
| `tests/s16_route_state.py` | `_init()` 去掉 `docs` |
| `tests/s7_platform.py` | t10 改口径：配好的 serde 对 Message 静默、对 Document **照旧告警**（反向那一格同时钉住「白名单没被放宽成什么都放行」与「Document 不该进图状态」） |
| `docs/判定-复制与重构清单.md` | 弃项表新增一条（含「删字段不会让残留写者报错」这条实测） |
| `docs/对照6` §结论-4/§三表/§四-A/§五、`docs/对照1` §三表 | 从「缺口/假通道」改写成「已处置 + 真通道在哪 + 谁钉住不复燃」 |

**顺带修掉一条自测自身的缺陷**（本轮实测踩到，见 §5「不是代码问题」下面单列）：
`s3b main()` 里任何一格在 `close_all()` 之前抛错 → 非守护的 aiosqlite 线程吊住解释器**永不退出**。
修法是把 `close_all()` 移进 `finally`（该函数先 `_cache.clear()` 再逐个关，幂等，所以成功路径双调用安全）。

## 5 · 解决后效果

**已修并实测**（终态读数，全部落盘 `log/c2_*.out`，退出码见 `log/c2_gates_summary.txt`）：

| 门禁 | 读数 |
|---|---|
| `s1_schema` | S1 全部通过：**12 组** |
| `s2_gateway` | S2 全部通过：**15 组** |
| `s3_report_action` | S3(a) 通过：**13 组** |
| `s3b_runtime` | S3(b) 通过：**16 组**（新增 t15 在内），`S3B_EXIT=0` |
| `s4_tools` | S4 通过：**40 组** |
| `s5_memory_rag` | S5 通过：**31 组**（含 2 处外部服务探活跳过） |
| `s6_sop` | S6 通过：**23 组** |
| `s16_route_state` | S16 通过：**5 组** |
| `s7_platform` | **13/13 全绿（双配置）**，含改写后的 t10 |

- 反向验证：`t15` 两层各自伪造复发都能红（上面 `LAYER1/LAYER2=CAUGHT` 原文），**不是空转门禁**。
- 白名单实验读数：`DOC_WHITELIST_REMOVED_UNREGISTERED_WARNINGS: 0`。
- **断言打在真实运行路径上**：`Document` 不再进图状态这条，判据打在「配好的 serde 遇到 Document 仍告警」
  这个反向信号上，而不是打在「构造参数看着对」上。

**已修未实测**：无（本项判据全在离线门禁里）。

**本轮自己引入并当场改掉的问题**（照实记）：
1. `t15` 一开始调了 `_ok(...)` —— 那是 `s7`/`s8` 的助手，`s3b` 的惯例是「断言过了就 return，由 `main()` 统一打印
   `ok <组名>`」→ `NameError`。是 `s3b` 自己把它抓出来的。
2. 跑该层门禁时把文件名写成 `s3_report_shape`（真名 `s3_report_action`）→ `EXIT=2`「can't open file」。
   **`ls tests/s*.py` 已经写在 PLAN §0.6 的每轮复测里，仍然写错**：下次直接抄列表，别凭记忆。

**不是代码问题（是工具/自测形态造成的假异常）**：
- **`s3b` 中途抛错 → 进程永不退出**：t15 的 `NameError` 之后 pid 还活了 **6.5 分钟**，`ps -W` 里 Windows
  PID 是 16656（`ps -W` 第一列是 cygwin PID，`taskkill` 要的是后者——第一次杀错没杀掉）。
  杀掉后循环才继续，`s3b_runtime EXIT=1` 落进汇总。
  已修（`close_all()` 进 `finally`）并实测：同样故意抛错，现在 **0.1 秒返回**
  （`log/c2_probe_exit.py` → `RETURNED_AFTER=0.1s`）。
  **这条顺带解释了上一轮 A5 留下的悬案**：`plan/handoff-2026-09-21-06.md` §③ 记的
  「同一条 `s3b_runtime` 命令 06:13 用管道跑 26 分钟不返回、06:44 改落盘跑 <1 分钟收口」
  ——机制不是管道缓冲，而是**当时 t14 抛错 → 解释器被吊住**；修完 t14 不抛错了，自然秒回。
  当时把怀疑方向记在「跑法（管道）」上，本轮证伪：管道只是让人**看不见**它吊住，不是它吊住的原因。

**相关 commit**：`e7f3892`（C2 主体 + 文档回写 + s3b 退出修复）。前一项目 `71bb22d`（B2）。
