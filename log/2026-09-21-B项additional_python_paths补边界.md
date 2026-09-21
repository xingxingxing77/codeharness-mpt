# 2026-09-21 17:2x · B 项：`additional_python_paths` 补上会话边界（执行面第二处）

治理 §3 余账第 4 条，S3 那轮收尾时自己登记的同族洞：「同一个 `RunCodeContext` 的
`additional_python_paths` 也只 `Path(p).resolve()` 就塞进子进程 `PYTHONPATH`」。
两轮没人动（PLAN 里没有这一行，今天才开账为 F-B）。

## 段 1 · 现象

`RunCodeContext` 的两个字段整份来自 LLM 产出的结构化输出（`actions/run_code.py` 把
`msg.instruct_content` 直接构造进 context）。S3 关掉了 `working_directory` 那半，
`_env_with_paths()` 里这一行仍然敞开：

```python
paths = [str(Path(p).resolve()) for p in ctx.additional_python_paths]   # sandbox.py:84
```

后果不是"跑到会话外"而是**import 劫持**：把一个目录塞进 `PYTHONPATH` 前端，它里面的
`os.py`/`json.py`/任何同名包就会 shadow 标准库与第三方包，子进程里跑的每一行代码都建立在
被替换的模块上。会话边界（文件工具 `_boundary.safe_session_path`、S3 的执行 cwd）在这条面上
没有对应物。

## 段 2 · 排查过程

1. 先确认这字段除了 LLM 没有别的生产者（免得收紧把合法用法关掉）：
   `grep -rn "additional_python_paths" --include=*.py . | grep -v "^./tests/"` →
   只有 `schema.py:618` 的字段定义（默认空列表）+ `sandbox.py` 自己 + `actions/run_code.py`
   的注释指路。**没有任何生产侧写入点**，收紧零误伤。
2. 确认 `safe_session_path` 能直接复用：它对绝对路径也对——`(root / path).resolve()` 在
   `path` 为绝对路径时按 pathlib 语义取 `path` 本身，再由 `is_relative_to(root)` 裁决；
   界内绝对路径（`t34` 用的正是 `str(ROOT / "libs")`）照旧放行。
3. 落点选择：判据写在 `_env_with_paths` 里而不是 `run_code.py` 构造处——后者在别人在途的
   29 件 `codeharness/actions/*.py` 里（未提交的 `BaseAction` 改名），不能碰；
   而且 `run_context` 是执行唯一出口，管在这里对所有调用者生效。

## 段 3 · 根因

同 S3：字段整份信任 LLM 输出，执行面缺一条包含性判据。区别是**修法不能照抄**——
`working_directory` 越界有"退回会话根"这个自然替代，`PYTHONPATH` 没有：把 `/etc` 换成会话根
等于凭空多出一条 import 路径，比不加还糟。所以这一处是**丢弃 + 继承环境**，不是退回。

## 段 4 · 解决方案

1. `codeharness/tools/sandbox.py::_env_with_paths()`：逐条过 `safe_session_path`，越界的丢；
   全丢光则 `return None`（走继承环境那条既有分支）。docstring 写明为什么不照抄 S3 的退回语义，
   以及"丢掉之后子进程 import 失败会照常从 stderr 回到角色眼前，是响的不是静默降级"。
2. 门禁 `tests/s4_tools.py`：新增 `t34b_additional_python_paths_clamped_to_session`，
   三格（越界绝对/workspace_root 外/解释器目录/`../` 相对 → 解析后不得出现在子进程 PYTHONPATH；
   越界**夹在合法值里**时合法那条仍在、越界那条仍不在；全越界 → 等于继承的 `PYTHONPATH`）。
   `t34`（界内必到）当阳性对照，两条合起来才有区分力。
3. 账面回写：`docs/施工2-能力层-S4-S5.md:41` 那句"现在真进子进程 PYTHONPATH"补上边界口径
   （它只写了"进得去"，没写"不进得去"，正是这条洞能在两篇文档里活下来的原因）。

## 段 5 · 解决后效果（读数）

- `s4_tools` **41 组 exit 0**（原 40 + t34b），`log/fixB_s4.out`。
- **反向验证**：把 `sandbox.safe_session_path` 临时换成修复前那行（`lambda p: Path(p).resolve()`）
  再跑 t34b → `REVERSE=CAUGHT: 越界路径 '…\escape_site'（解析后 …）进了子进程 PYTHONPATH`。
  判据抓得住复发，不是空转断言。
- 按 §0.3 门禁表（碰了 `codeharness/tools/**`）：`s7_platform` **13/13 双配置**、
  `s8_frontend_contract` **12/12**，`log/fixB_s7_platform.out`、`log/fixB_s8_frontend_contract.out`。

**未验边界**：没有构造"真发生 import 劫持"的端到端读数（造一个 shadow 标准库的目录跑真会话）。
断言打在**子进程自己打印的 `PYTHONPATH`** 上（实测读数，不是构造参数），
但"越界路径进去了会怎样"这一步没验——收紧之后那条路径已不可达。
