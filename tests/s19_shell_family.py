"""S19 门禁：阶段三 shell 系三条（B4 阻塞壳、B5 单引号注入、B6 pyreverse 三处）。

  t1 B4：`shell_execute` 期间事件循环必须照跳（修复前阻塞调用跑在循环线程上，一次最长把整个
     服务冻 timeout 秒）。同一判据用**修复前的形状**（直接在协程里 `subprocess.run`）取对照读数
     → 对照组必须一跳不跳，否则这条判据不成立。顺带钉 timeout 仍抛 `TimeoutExpired`（语义没被 to_thread 吞掉）。
  t2 B4：`tree(run_command=True)` 在可执行文件不在场时给降级文案而不是抛。本机 Windows 实测就是
     真分支（系统自带 tree 是 .com，shell=False 解析不到 → 修复前直接 FileNotFoundError 出环）。
  t3 B5：`get_mime_type` 必须把 filename 当**一个 argv**（list → shell=False）。对照组用修复前的
     模板（f-string 拼单引号 + shell=True）打 shell 内建 `echo` → payload 真被执行成独立一行。
     不打到 `file` 上：本机 PATH 里有 Git 带的 GNU `file`，它会把文件名原样打回 stdout，
     拿 stdout 找标记会被它的报错骗过（本轮实测踩过）。顺带钉扩展名快速路径没被改动影响。
  t4 B6：`rebuild_class_views` 含空格路径必须真跑通（真 pyreverse）；非零退出要抛含 stderr 的
     ValueError（用替换 subprocess 的缝注入 rc=3）。对照组用修复前的 `f"pyreverse {path} -o dot"`
     + `shell=True` + `check=True` 打同一个含空格路径 → 实测 rc=1 且抛的是 CalledProcessError，
     正好同时证否两件事：路径被 shell 拆词、以及紧随的 `if result.returncode != 0` 是死代码。
  t5 F-C（治理 §3 第 3 条）：B6 换来的另一半——可执行文件不在场时 shell=False 抛裸
     FileNotFoundError，调用方零 `except`。归一成 ValueError，且只咬 FileNotFoundError。

跑法：
  cd /e/Codeharness && PYTHONPATH=/e/Codeharness:/e/Codeharness/logs PYTHONIOENCODING=utf-8 \\
    PYTHONDONTWRITEBYTECODE=1 F:/anaconda/python.exe tests/s19_shell_family.py
"""
import asyncio
import subprocess
import sys
import tempfile
from pathlib import Path

import codeharness.repo_parser as R
import codeharness.tools.libs.shell as shell_module
from codeharness.tools.libs.shell import shell_execute
from codeharness.utils.common import get_mime_type
from codeharness.utils.tree import tree

SLEEP_SRC = "import time; time.sleep(2)"
CLS_SRC = ("class Board:\n    def reset(self):\n        pass\n\n\n"
           "class Game:\n    def __init__(self):\n        self.board = Board()\n"
           "    def move(self, d: str) -> bool:\n        return True\n")


async def _tick_during(coro_factory, seconds=2.0):
    """在等一个 shell 调用的同时数事件循环跳了多少次（50ms 一跳）。"""
    ticks = 0
    stop = asyncio.Event()

    async def ticker():
        nonlocal ticks
        while not stop.is_set():
            await asyncio.sleep(0.05)
            ticks += 1

    task = asyncio.create_task(ticker())
    await coro_factory()
    stop.set()
    await task
    return ticks


def t1_loop_stays_responsive():
    async def blocking_run():                     # 修复前的形状：协程里直接跑阻塞调用
        subprocess.run([sys.executable, "-c", SLEEP_SRC], capture_output=True)

    ctrl = asyncio.run(_tick_during(blocking_run))
    assert ctrl == 0, f"对照组竟然跳了 {ctrl} 次，这条判据分辨不出阻塞（不成立）"
    got = asyncio.run(_tick_during(lambda: shell_execute([sys.executable, "-c", SLEEP_SRC])))
    assert got >= 10, f"shell_execute 期间事件循环只跳了 {got} 次（2s / 50ms 应约 40 次）——还在循环线程上阻塞"
    try:
        asyncio.run(shell_execute([sys.executable, "-c", "import time; time.sleep(5)"], timeout=1))
        raise AssertionError("timeout 不再抛 TimeoutExpired——to_thread 改坏了语义")
    except subprocess.TimeoutExpired:
        pass
    print(f"  t1 对照组（修复前形状）ticks=0；shell_execute ticks={got}（循环不被冻）；timeout 仍抛 TimeoutExpired")


def t2_tree_degrades_instead_of_raising():
    import codeharness.utils.tree as T               # tree.py 顶层 import 了名字，补丁要打在这一侧
    probe = str(Path(__file__).resolve().parent.parent / "codeharness" / "tools")
    out = asyncio.run(tree(probe, run_command=True))
    assert out.strip(), "tree 返回空串：既没跑通也没降级"
    keep = T.shell_execute

    async def boom(*a, **kw):
        raise FileNotFoundError(2, "No such file or directory: 'tree'")

    T.shell_execute = boom
    try:
        msg = asyncio.run(tree(probe, run_command=True))
    finally:
        T.shell_execute = keep
    assert msg.startswith("[tree 命令不可用"), f"降级文案没生效，实际 {msg[:60]!r}"
    branch = "降级文案" if out.startswith("[tree 命令不可用") else "真跑通"
    print(f"  t2 tree(run_command=True) 不抛（本机不补丁就走={branch}）；注入 FileNotFoundError → {msg[:24]}…")


def t3_mime_type_passes_filename_as_one_argv():
    marker = "PWNED-here"
    payload = f"x' & echo {marker} & rem"      # `'` 闭合外层引号；& 在 cmd 与 POSIX sh 里都是命令分隔符
    # 对照：修复前那套模板（f-string 拼进单引号 + shell=True）。这里用 echo 而不是 file，
    # 因为本机 PATH 上真有 GNU `file`（Git 带的），它会把文件名原样打回 stdout——拿 stdout
    # 找 marker 判「有没有执行」会被它的报错骗过（本轮实测就这么假红过一次）。
    old_out, _, _ = asyncio.run(shell_execute(f"echo '{payload}'"))
    old_lines = [l.strip() for l in old_out.splitlines()]
    assert marker in old_lines, \
        f"对照组里 payload 没被执行成独立一行，这条判据不成立（shell 语义与预期不符）：{old_out!r}"

    seen = {}
    real = shell_module.shell_execute

    async def spy(command, *a, **kw):
        seen["command"] = command
        return await real(command, *a, **kw)

    shell_module.shell_execute = spy          # common.py 在函数体内 import，打这一侧才生效
    try:
        asyncio.run(get_mime_type(Path(payload), force_read=True))
    finally:
        shell_module.shell_execute = real
    assert isinstance(seen["command"], list), f"又退回字符串+shell=True：{seen['command']!r}"
    assert seen["command"] == ["file", "--mime-type", payload], f"argv 形状不对：{seen['command']!r}"
    # 快速路径（按扩展名猜中就不进 shell）不得被改动影响
    assert asyncio.run(get_mime_type(Path("docs/a.json"))) == "application/json", "扩展名快速路径变了"
    print(f"  t3 对照组同一个模板真把 payload 执行成两条命令（stdout 出现独立一行 {marker}）；"
          f"改 list 后 argv=={seen['command']}（shell=False，无 shell 可解释元字符），扩展名快速路径不变")


def _spaced_pkg():
    root = Path(tempfile.mkdtemp(prefix="s19_pkg_")) / "pkg with space"
    pkg = root / "sub pkg"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "game.py").write_text(CLS_SRC, encoding="utf-8")
    return pkg


def t4_rebuild_class_views_spaced_and_failure():
    pkg = _spaced_pkg()
    views, rels, package_root = asyncio.run(R.RepoParser(base_directory=pkg).rebuild_class_views(path=pkg))
    assert any(c.name == "Game" for c in views), f"含空格路径没产出类视图：{[c.name for c in views]}"
    # 真失败注入：非零退出必须变成带 stderr 文本的 ValueError（而不是 CalledProcessError 或死代码）
    class _Stub:
        def run(self, argv, **kw):
            return subprocess.CompletedProcess(argv, 3, "", "pyreverse: bogus interpreter")

    keep = R.subprocess
    R.subprocess = _Stub()
    try:
        asyncio.run(R.RepoParser(base_directory=pkg).rebuild_class_views(path=pkg))
        raise AssertionError("非零退出竟然没抛")
    except ValueError as e:
        assert "pyreverse: bogus interpreter" in str(e), f"ValueError 里没带上 stderr：{e}"
    finally:
        R.subprocess = keep
    # 对照：修复前的命令形态打同一个含空格路径
    ctl = pkg / "__dot__ctl"
    ctl.mkdir()
    try:
        subprocess.run(f"pyreverse {str(pkg)} -o dot", shell=True, check=True, cwd=str(ctl),
                       capture_output=True, text=True)
        raise AssertionError("对照组竟然跑通了——含空格路径这条判据不成立")
    except subprocess.CalledProcessError as e:
        ctl_rc = e.returncode
        assert not (ctl / "classes.dot").exists(), "拆词后竟然也产出了 classes.dot"
    print(f"  t4 含空格路径修复后真产出 {[c.name for c in views]}；rc=3 抛 ValueError 含 stderr；"
          f"对照组（旧 f-string+check=True）实测 rc={ctl_rc} 且不产出 classes.dot（旧 returncode 判断是死代码）")


def t5_missing_pyreverse_normalizes_to_value_error():
    """F-C：B6 把 argv 交回 shell=False 之后，**可执行文件不在场**从「shell 回 rc=127 /
    CalledProcessError」变成裸 `FileNotFoundError` 出环，而唯一调用方
    `actions/rebuild_class_view.py` 全文零 `except`——它在图里跑时只被 `agent.py:201`
    那个泛 `except Exception` 兜住，于是"没装 pylint"会走成一次自愈重跑而不是可读的动作失败。

    三格：① 前提（异常类型从哪来）用**真** shell=False 打一个不存在的命令，必须是
    FileNotFoundError；② 修复后同一形状在共享出口归一成 ValueError 且消息说人话；
    ③ 收紧只咬 FileNotFoundError——换成 PermissionError 必须原样出环（否则这条 guard
    就成了 `except Exception`，把别的 OSError 静默改成"没装 pylint"）。"""
    pkg = _spaced_pkg()

    class _Raises:
        def __init__(self, exc):
            self.exc = exc

        def run(self, argv, **kw):
            raise self.exc

    # ① 前提
    try:
        subprocess.run(["pyreverse-not-installed-here", str(pkg), "-o", "dot"],
                       shell=False, capture_output=True, text=True)
        raise AssertionError("①前提不成立：shell=False 打不存在的命令竟然没抛 FileNotFoundError")
    except FileNotFoundError:
        pass

    # ② 归一
    keep = R.subprocess
    R.subprocess = _Raises(FileNotFoundError(2, "系统找不到指定的文件", "pyreverse"))
    try:
        asyncio.run(R.RepoParser(base_directory=pkg).rebuild_class_views(path=pkg))
        raise AssertionError("②失效：pyreverse 不在场竟然没抛")
    except ValueError as e:
        assert "pyreverse 不在 PATH" in str(e), f"②消息没说是缺可执行文件：{e}"
    finally:
        R.subprocess = keep

    # ③ 不放宽
    R.subprocess = _Raises(PermissionError(13, "另一个程序正在使用此文件"))
    try:
        asyncio.run(R.RepoParser(base_directory=pkg).rebuild_class_views(path=pkg))
        raise AssertionError("③失效：别的 OSError 被一起吞成了 ValueError")
    except PermissionError:
        pass
    finally:
        R.subprocess = keep
    print("  t5 前提（shell=False 打不存在的命令）实测 FileNotFoundError；修复后归一成 "
          "ValueError 含「pyreverse 不在 PATH」；PermissionError 原样出环（guard 没放宽成 except Exception）")


def main():
    print("=" * 60)
    print("S19: shell 系三条（B4 阻塞壳 / B5 注入 / B6 pyreverse）")
    print("=" * 60)
    fns = (t1_loop_stays_responsive, t2_tree_degrades_instead_of_raising,
           t3_mime_type_passes_filename_as_one_argv, t4_rebuild_class_views_spaced_and_failure,
           t5_missing_pyreverse_normalizes_to_value_error)
    fails = []
    try:
        for fn in fns:
            try:
                fn()
            except AssertionError as e:
                fails.append(f"{fn.__name__}: {e}")
                print(f"  ❌ {fn.__name__}：{e}")
    except Exception as e:
        print(f"\n❌ 异常：{type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()
        return 1
    if fails:
        print(f"\n❌ 失败 {len(fails)}/{len(fns)} 条")
        return 1
    print("\n" + "=" * 60 + f"\n✅ 全部通过 ({len(fns)}/{len(fns)} 全绿，编号见 fns)\n" + "=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
