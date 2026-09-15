"""S4 门禁：工具注册表（R8）、路径越界防护、上报接缝、沙箱执行、联网全 mock。

跑法：
  cd /e/Codeharness && PYTHONPATH=/e/Codeharness PYTHONIOENCODING=utf-8 F:/anaconda/python.exe tests/s4_tools.py

断言打在哪（docs 陷阱 #2 要求自证）：全部打在真实实现上——真起子进程、真写临时目录、
真调 `_safe()`。唯一被替换的是 DuckDuckGo（t10/t11 打在注入的 stub 上），因为门禁必须零外网。
工具层不碰 LLM，所以这里没有 FakeLLM 回放。
"""
import asyncio
import importlib
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

from codeharness.configs.settings import settings
from codeharness.logs import set_tool_output_logfunc
from codeharness.report import BlockType
from codeharness.runtime import REPORT_SINK
from codeharness.schema import RunCodeContext
from codeharness.tools import (REGISTRY, _root, _safe, execute_shell_async, read_file,
                               search_internet, tool_registry, write_file)
from codeharness.tools.tool_registry import TOOL_REGISTRY, register_tool
from codeharness.tools.libs.editor import Editor
from codeharness.tools.libs.linter import Linter
from codeharness.tools.libs.terminal import Terminal
from codeharness.tools.sandbox import run_context, run_python_code
from codeharness.utils._config_compat import get_env_default

BASE = Path(tempfile.mkdtemp(prefix="s4gate_"))
WS = BASE / "ws"
WS.mkdir()
settings.workspace_root = str(WS)
PY = f'"{sys.executable}"'


def t1_registry_items_are_langchain_tools():
    names = [t.name for t in REGISTRY]
    assert len(names) == len(set(names)) == 5, names
    for t in REGISTRY:
        assert t.description and t.args, t.name
        assert t.func or t.coroutine, f"{t.name} 解析不到 callable"


def t2_sibling_prefix_escape():
    # 回归：str.startswith 会把兄弟目录 ws_probe 判成 ws 之内（"ws" 是 "ws_probe" 的前缀）
    assert _safe("../ws_probe/x.txt") is None
    out = asyncio.run(write_file.ainvoke({"path": "../ws_probe/x.txt", "content": "pwn"}))
    assert out == "拒绝：路径越界", out
    assert not (BASE / "ws_probe" / "x.txt").exists()


def t3_parent_and_absolute_escape():
    assert _safe("../../etc/passwd") is None
    assert _safe(str(WS.parent / "outside.txt")) is None
    assert _safe("ok/inside.txt") is not None


def t4_write_read_roundtrip_creates_dirs():
    body = "x" * 3000
    out = asyncio.run(write_file.ainvoke({"path": "a/b/c.txt", "content": body}))
    assert "已写入" in out and (WS / "a" / "b" / "c.txt").read_text(encoding="utf-8") == body
    assert read_file.invoke({"path": "a/b/c.txt"}) == body


def t5_missing_path_and_no_sink_do_not_raise():
    assert "文件不存在" in read_file.invoke({"path": "nope.txt"})
    assert REPORT_SINK.get() is None, "自测基线：未装报道桥时 _emit 必须静默丢弃而不是抛"
    assert "已写入" in asyncio.run(write_file.ainvoke({"path": "d.txt", "content": "1"}))


def t6_editor_block_reaches_sink():
    events = []

    async def go():
        tok = REPORT_SINK.set(lambda e: events.append(e))
        try:
            await write_file.ainvoke({"path": "e.txt", "content": "hello"})
        finally:
            REPORT_SINK.reset(tok)

    asyncio.run(go())
    got = {e["name"] for e in events}
    assert {"meta", "document"} <= got, got
    assert all(e["block"] == BlockType.EDITOR.value for e in events), {e["block"] for e in events}
    assert next(e for e in events if e["name"] == "meta")["value"]["filename"] == "e.txt"


def t7_tool_output_log_slot_fires():
    seen = []
    orig = getattr(importlib.import_module("codeharness.logs"), "_tool_output_log")
    set_tool_output_logfunc(lambda output, tool_name="": seen.append((output.name, output.value)))
    try:
        asyncio.run(write_file.ainvoke({"path": "f.txt", "content": "2"}))
    finally:
        set_tool_output_logfunc(orig)
    assert seen == [("write_file", "f.txt")], seen


def t8_shell_runs_in_workspace_root():
    out = asyncio.run(execute_shell_async.ainvoke({"command": f"{PY} -c \"import os;print(os.getcwd())\""}))
    assert Path(out.strip()).resolve() == _root(), out


def t9_shell_output_truncated():
    out = asyncio.run(execute_shell_async.ainvoke(
        {"command": f"{PY} -c \"print('z'*30000)\""}))
    assert len(out) <= 10000, len(out)


def t10_search_uses_stub_and_truncates():
    mod = importlib.import_module("langchain_community.tools.ddg_search.tool")
    calls, orig = [], mod.DuckDuckGoSearchRun

    class Stub:
        def run(self, query):
            calls.append(query)
            return "r" * 9000

    mod.DuckDuckGoSearchRun = Stub
    try:
        out = search_internet.invoke("ponytail")
    finally:
        mod.DuckDuckGoSearchRun = orig
    assert calls == ["ponytail"] and len(out) == 8000, (calls, len(out))


def t11_search_degrades_on_failure():
    mod = importlib.import_module("langchain_community.tools.ddg_search.tool")
    orig = mod.DuckDuckGoSearchRun

    class Boom:
        def run(self, query):
            raise RuntimeError("rate limited")

    mod.DuckDuckGoSearchRun = Boom
    try:
        out = search_internet.invoke("q")
    finally:
        mod.DuckDuckGoSearchRun = orig
    assert out.startswith("[搜索暂不可用") and "rate limited" in out, out


def t12_sandbox_runs_and_reports_exit_code():
    ok = asyncio.run(run_python_code("print(6*7)"))
    assert (ok.stdout.strip(), ok.return_code) == ("42", 0), ok
    bad = asyncio.run(run_python_code("def ("))
    assert bad.return_code != 0 and "SyntaxError" in bad.stderr, bad.return_code
    slow = asyncio.run(run_python_code("import time; time.sleep(20)", timeout=1))
    assert slow.return_code == -1 and "timeout" in slow.stderr, slow


def t13_run_context_honors_working_directory():
    ctx = RunCodeContext(command=[sys.executable, "-c", "import os;print(os.getcwd())"],
                         working_directory=str(WS / "a" / "b"))
    r = asyncio.run(run_context(ctx))
    assert Path(r.stdout.strip()) == WS / "a" / "b", r.stdout
    nonzero = asyncio.run(run_context(
        RunCodeContext(command=[sys.executable, "-c", "raise SystemExit(3)"], working_directory=str(WS))))
    assert nonzero.return_code == 3, nonzero


def t14_default_workdir_and_scratch_stay_inside():
    asyncio.run(run_python_code("print('s')"))
    scratch = _root() / "scratch"
    assert scratch.exists() and all(p.is_relative_to(_root()) for p in scratch.glob("run_*.py"))


def _capture_warnings():
    seen = []
    orig = tool_registry._warn
    tool_registry._warn = seen.append
    return seen, orig


def t15_registry_and_tools_share_one_source():
    assert {t.name for t in REGISTRY} == set(TOOL_REGISTRY.tools) == {
        "write_file", "read_file", "execute_shell_async", "search_internet", "terminal_command"}
    assert sorted(TOOL_REGISTRY.tags()) == ["file", "terminal", "web"]


def t16_select_unions_names_and_tags_without_duplicates():
    got = [t.name for t in TOOL_REGISTRY.select("write_file", "file")]
    assert sorted(got) == ["read_file", "write_file"] and got.count("write_file") == 1, got


def t17_unknown_key_warns_and_is_skipped():
    seen, orig = _capture_warnings()
    try:
        got = [t.name for t in TOOL_REGISTRY.select("no_such_tool", "terminal")]
    finally:
        tool_registry._warn = orig
    assert got == ["execute_shell_async", "terminal_command"], got
    assert len(seen) == 1 and "no_such_tool" in seen[0], seen


def t18_register_tool_requires_langchain_tool():
    seen, orig = _capture_warnings()
    try:
        @register_tool(tags=["bogus"])
        def not_a_tool(x):
            return x
    finally:
        tool_registry._warn = orig
    assert seen and "不是 LangChain tool" in seen[0], seen
    assert "bogus" not in TOOL_REGISTRY.tags() and not_a_tool.__name__ not in TOOL_REGISTRY.tools


def t19_swe_agent_tool_set_comes_from_tags():
    # roles/registry.py 的 SweAgent 取法：终端 + 文件，不许静默漂成全量工具
    assert {t.name for t in TOOL_REGISTRY.select("terminal", "file")} == {
        "execute_shell_async", "terminal_command", "write_file", "read_file"}


async def _in_shell(coro):
    """每个用例独立起壳、跑完必关：常驻 shell 漏关就是孤儿进程，门禁不许留。"""
    term = Terminal()
    try:
        return await coro(term)
    finally:
        await term.close()


def t20_terminal_keeps_state_across_commands():
    async def go(term):
        await term.run_command("mkdir state_probe")
        await term.run_command("cd state_probe")
        return await term.run_command(term.pwd_command)

    assert "state_probe" in asyncio.run(_in_shell(go))


def t21_hung_command_times_out_and_shell_self_heals():
    hang = "ping -n 30 127.0.0.1 >nul" if sys.platform.startswith("win") else "sleep 30"

    async def go(term):
        out = await term.run_command(hang, timeout=2)
        assert "[timeout after 2s]" in out, out
        assert term.process is None, "超时后必须丢掉这条 shell，否则下一条命令骑在挂死进程上"
        return await term.run_command(term.pwd_command)

    assert "timeout" not in asyncio.run(_in_shell(go))


def t22_shell_exit_reports_instead_of_spinning():
    # 源 :158 `if not output: continue` 在进程退出后变成空转死循环，web 进程里就是永久卡住
    async def go(term):
        out = await term.run_command("exit")
        assert "shell 已退出" in out, out
        assert term.process is None
        return await term.run_command(term.pwd_command)

    assert asyncio.run(_in_shell(go))  # 能自重建并拿到输出即通过


def t23_forbidden_command_is_skipped_not_run():
    async def go(term):
        return await term.run_command("echo before_out && npm run dev")

    out = asyncio.run(_in_shell(go))
    assert "Failed to execute npm run dev" in out and "Deployer" in out, out
    assert "before_out" in out, out


def t24_daemon_output_reaches_queue():
    async def go(term):
        assert await term.run_command("echo bg_marker_42", daemon=True) == ""
        got = ""
        for _ in range(20):
            await asyncio.sleep(0.25)
            got += await term.get_stdout_output()
            if "bg_marker_42" in got:
                break
        return got

    # 源 :103 起 daemon 任务时漏传 daemon，queue 恒空 → get_stdout_output 恒 ""
    assert "bg_marker_42" in asyncio.run(_in_shell(go))


def t25_linter_reports_python_syntax_error_with_line():
    p = WS / "broken.py"
    p.write_text("def f(:\n    return 1\n", encoding="utf-8")
    r = Linter(root=WS).lint(str(p))
    assert r and "SyntaxError" in r.text, r
    assert r.lines[0] == 1, r.lines


def t26_linter_passes_clean_python():
    p = WS / "clean.py"
    p.write_text("def f(x):\n    return x + 1\n", encoding="utf-8")
    assert Linter(root=WS).lint(str(p)) is None


def t27_linter_skips_non_python_like_source():
    # 源 languages 表把 js/css/sql 全指到 fake_lint（不校验），本处照抄该语义
    p = WS / "note.md"
    p.write_text("这不是代码 ((( 未闭合", encoding="utf-8")
    assert Linter(root=WS).lint(str(p)) is None


def t28_editor_lint_hook_works():
    """真消费者：editor.py:198 `_lint_file`。旧假垫片把 lint 声明成 async 而调用点不 await，
    返回的协程对象恒真 → 下一步取 .text 必 AttributeError。这条断言让它无处可藏。"""
    p = WS / "edit_target.py"
    p.write_text("import os\n\nif True:\nprint(1)\n", encoding="utf-8")
    err, line = Editor(working_dir=WS)._lint_file(p)
    assert err and err.startswith("ERRORS:\n"), err
    # 精确锁 4：真实错误行。Windows 盘符冒号曾让它退化成 1；flake8 在/不在两条分支都给 4
    assert line == 4, (line, err)
    p.write_text("import os\n\nprint(os.name)\n", encoding="utf-8")
    assert Editor(working_dir=WS)._lint_file(p) == (None, None)


def t29_env_reader_matches_its_only_call_site():
    """utils/file.py:154 写死了 await + key/app_name/default_value 三个关键字，签名一漂就是 TypeError。"""
    async def go():
        os.environ["OMNIPARSE__BASE_URL"] = "http://127.0.0.1:9331"
        try:
            hit = await get_env_default(key="base_url", app_name="OmniParse", default_value="")
            miss = await get_env_default(key="timeout", app_name="OmniParse", default_value="60")
        finally:
            del os.environ["OMNIPARSE__BASE_URL"]
        return hit, miss

    assert asyncio.run(go()) == ("http://127.0.0.1:9331", "60")


def main():
    checks = [t1_registry_items_are_langchain_tools, t2_sibling_prefix_escape,
              t3_parent_and_absolute_escape, t4_write_read_roundtrip_creates_dirs,
              t5_missing_path_and_no_sink_do_not_raise, t6_editor_block_reaches_sink,
              t7_tool_output_log_slot_fires, t8_shell_runs_in_workspace_root,
              t9_shell_output_truncated, t10_search_uses_stub_and_truncates,
              t11_search_degrades_on_failure, t12_sandbox_runs_and_reports_exit_code,
              t13_run_context_honors_working_directory,
              t14_default_workdir_and_scratch_stay_inside,
              t15_registry_and_tools_share_one_source,
              t16_select_unions_names_and_tags_without_duplicates,
              t17_unknown_key_warns_and_is_skipped,
              t18_register_tool_requires_langchain_tool,
              t19_swe_agent_tool_set_comes_from_tags,
              t20_terminal_keeps_state_across_commands,
              t21_hung_command_times_out_and_shell_self_heals,
              t22_shell_exit_reports_instead_of_spinning,
              t23_forbidden_command_is_skipped_not_run,
              t24_daemon_output_reaches_queue,
              t25_linter_reports_python_syntax_error_with_line,
              t26_linter_passes_clean_python,
              t27_linter_skips_non_python_like_source,
              t28_editor_lint_hook_works,
              t29_env_reader_matches_its_only_call_site]
    for c in checks:
        c()
        print(f"  ok  {c.__name__}")
    for _ in range(20):
        shutil.rmtree(BASE, ignore_errors=True)   # Windows：刚被 taskkill 的句柄要几百毫秒才释放
        if not BASE.exists():
            break
        time.sleep(0.25)
    leftovers = [str(p.relative_to(BASE)) for p in BASE.rglob("*")] if BASE.exists() else []
    assert not BASE.exists(), f"自测留下了句柄或文件: {leftovers[:8]}"
    print(f"\nS4 门禁通过：{len(checks)} 组 —— 注册表 6 组（五项工具登记/名字与 tag 并集去重/"
          f"未知 key 告警跳过/漏 @tool 不登记/SweAgent 取法）+ 越界防护 3 组（兄弟目录前缀回归/"
          f"父目录与绝对路径/scratch 收口）+ 接缝 3 组（无 sink 不抛 / editor 块达 sink / 工具日志槽）"
          f"+ shell 2 组 + 搜索 2 组（零外网 stub 与降级）+ 沙箱 3 组（退出码/超时/工作目录）"
          f"+ Terminal 5 组（跨命令保态/挂死超时后 shell 自愈/死壳报错不空转/禁行命令替换跳过/"
          f"daemon 输出进队列）+ linter 3 组（Python 报错带行号/干净文件放行/非 Python 照源不校验）"
          f"+ Editor._lint_file 真消费者 1 组（钉死旧假垫片的 async/sync 漂移）+ env 读口签名 1 组")


if __name__ == "__main__":
    main()
