# 2026-09-21 17:4x · C 项：`pyreverse` 不在场不再抛裸 `FileNotFoundError` 出环

治理 §3 余账第 3 条（B6 那轮自己留下的行为差，登记未修）。

## 段 1 · 现象

B6 把 `rebuild_class_views()` 的命令从 `f"pyreverse {path} -o dot"` + `shell=True` 改成
argv list + `shell=False`。这一改关掉了注入面，但换来了一个副作用：**可执行文件不在场时
不再由 shell 回 rc=127，而是本地抛 `FileNotFoundError`**。当时那轮的登记原话：

> ⚠ 行为差一条：pyreverse 不在场时从旧的 rc=127/CalledProcessError 变成 `FileNotFoundError`
> （list + shell=False 没有 shell 可报错），调用方 `actions/rebuild_class_view.py` 不捕这个类型

调用方确实一个 `except` 都没有（本轮实测：文件里零 `except`）。

## 段 2 · 排查过程

1. 先确认「不捕」到底意味着什么。两条调用路径分开看：
   - **图内路径**（真会话）：`RebuildClassView.run` 抛出去会被 `codeharness/roles/agent.py:201`
     那个 `except Exception` 接住 → 走 B9 的自愈回喂（`send_to=<self>`）。也就是说
     「这台机器没装 pylint」会被当成一次动作失败**重跑三轮**（`debug_rounds` 刹车），
     而不是让人看懂的失败原因。
   - **直连路径**（CLI / ext_api）：异常裸出到调用者。
2. 确认这函数的既有契约：它已经用 `ValueError` 报「非零退出」（`:737-738`）和「缺 `__init__.py`」
   （`:728`）。所以归一到 `ValueError` 不是发明新口径，是**补齐同一条出口**。
3. 落点选择：治理登记时写的是「调用方不捕这个类型」，看着像该在调用方补 `except`。
   但 `codeharness/actions/rebuild_class_view.py` 在别人在途的 29 件里（未提交的
   `BaseAction` 改名），按 §0.8 不能碰；更重要的是**根因在共享出口**——`RepoParser` 还有
   ext_api 侧的直连调用者，在调用方补等于每处补一遍。

## 段 3 · 根因

`shell=True` 时代"命令不存在"这件事由 shell 汇报（rc=127 → 走 `returncode != 0` 分支 → ValueError）；
换成 `shell=False` 后同一事实改由 `Popen` 本地抛出，而那句 `if result.returncode != 0` 永远看不到它。
B6 改的时候只按「注入面 + 死代码 + 阻塞」三处判过，没把这条异常类型的迁移算进契约。

## 段 4 · 解决方案

1. `codeharness/repo_parser.py`：`asyncio.to_thread(subprocess.run, …)` 外面套
   `except FileNotFoundError` → `raise ValueError(f"pyreverse 不在 PATH（未安装 pylint？）：{exc}") from exc`。
   **只咬 `FileNotFoundError`**，别的 `OSError`（例如 Windows 上文件被占用）原样出环。
   注释里写清为什么不放在调用方。
2. 门禁 `tests/s19_shell_family.py::t5`（新增，S19 从 4 组到 **5 组**）三格：
   ① **前提格**——真 `shell=False` 打一个不存在的命令，必须抛 `FileNotFoundError`
   （不成立就说明这条判据在演一出空戏）；② 用 t4 同款 `R.subprocess` 注入缝抛
   `FileNotFoundError` → 必须收到 `ValueError` 且消息含「pyreverse 不在 PATH」；
   ③ **不放宽格**——同一缝注入 `PermissionError` 必须原样出环（挡住"顺手写成
   `except Exception`"的退化）。
3. 顺带修一处账面：s19 的收官打印把编号硬写成「t1–t4」而组数用的是 `len(fns)`，
   加格之后自相矛盾（实测打印过 `✅ 全部通过 (t1–t4 5/5 全绿)`）。改成不带硬编号。

## 段 5 · 解决后效果（读数）

- `s19_shell_family` **5/5 exit 0**、`s4_tools` **41 组 exit 0**、`s7_platform` **13/13 双配置**
  （改动面 `codeharness/**` 按 §0.3 表跑本层 + s7）。原始输出 `log/fixC_*.out`。
- 判据打在**真实 subprocess 行为**上（①）与**函数实际抛出的类型**上（②③），不是打在构造参数上。
- **未验边界**：没有真把 pylint 从 PATH 摘掉跑一次完整 `RebuildClassView` 动作（那要动本机环境，
  且①已经用真 shell=False 取到同一个异常类型）。图内那条「三轮自愈」的路径读数也没有——
  它属于 B9 的既有语义，本项只改异常类型，不改自愈行为。
- 本机 pyreverse 在场（t4 真跑出 `Board`/`Game`），所以修复前这条分支在本机**从未被走到**；
  在未装 pylint 的部署机上它才是日常路径。
