"""S4 门禁：工具注册表（R8）、per-session 边界、上报接缝、沙箱执行、联网全 mock。

跑法：
  cd /e/Codeharness && PYTHONPATH=/e/Codeharness PYTHONIOENCODING=utf-8 F:/anaconda/python.exe tests/s4_tools.py

断言打在哪（docs 陷阱 #2 要求自证）：全部打在真实实现上——真起子进程、真写临时目录、
真调 `_safe()`。唯一被替换的是搜索引擎的 HTTP 层（t10/t11 喂 canned HTML/JSON 给真解析函数），
因为门禁必须零外网。工具层不碰 LLM，所以这里没有 FakeLLM 回放。
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
from codeharness.runtime import CURRENT_PROJECT, REPORT_SINK, session_root
from codeharness.schema import RunCodeContext
from codeharness.tools import (REGISTRY, _safe, execute_shell_async, read_file,
                               search_internet, tool_registry, write_file)
from codeharness.tools import search_engine as se
from codeharness.tools.tool_registry import TOOL_REGISTRY, register_tool
from codeharness.tools.libs.editor import Editor
from codeharness.tools.libs.linter import Linter
from codeharness.tools.libs.terminal import Terminal, close_terminal, current_terminal
from codeharness.tools.sandbox import run_context, run_proc, run_python_code
from codeharness.utils._config_compat import get_env_default

BASE = Path(tempfile.mkdtemp(prefix="s4gate_"))
WS = BASE / "ws"
WS.mkdir()
settings.workspace_root = str(WS)
CURRENT_PROJECT.set("s4_session")
ROOT = session_root()          # 工具层的文件/命令都以此为界（per-session）
PY = f'"{sys.executable}"'


def t1_registry_items_are_langchain_tools():
    names = [t.name for t in REGISTRY]
    assert len(names) == len(set(names)) == 5, names
    for t in REGISTRY:
        assert t.description and t.args, t.name
        assert t.func or t.coroutine, f"{t.name} 解析不到 callable"


def t2_sibling_prefix_escape():
    # 回归：str.startswith 会把兄弟目录判成区内（"s4_session" 是 "s4_session_evil" 的前缀）
    assert _safe("../s4_session_evil/x.txt") is None
    out = asyncio.run(write_file.ainvoke({"path": "../s4_session_evil/x.txt", "content": "pwn"}))
    assert out == "拒绝：路径越界", out
    assert not (BASE / "s4_session_evil").exists()


def t3_parent_and_absolute_escape():
    assert _safe("../../etc/passwd") is None
    assert _safe(str(ROOT.parent / "outside.txt")) is None
    assert _safe("ok/inside.txt") is not None


def t4_write_read_roundtrip_creates_dirs():
    body = "x" * 3000
    out = asyncio.run(write_file.ainvoke({"path": "a/b/c.txt", "content": body}))
    assert "已写入" in out and (ROOT / "a" / "b" / "c.txt").read_text(encoding="utf-8") == body
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


def t8_shell_runs_in_session_root():
    out = asyncio.run(execute_shell_async.ainvoke({"command": f"{PY} -c \"import os;print(os.getcwd())\""}))
    assert Path(out.strip()).resolve() == ROOT, out


def t9_shell_output_truncated():
    out = asyncio.run(execute_shell_async.ainvoke(
        {"command": f"{PY} -c \"print('z'*30000)\""}))
    assert len(out) <= 10000, len(out)


class _Resp:
    """够用的 requests.Response 替身：只被 .raise_for_status()/.text/.json() 读到。"""

    def __init__(self, text="", payload=None):
        self._text, self._payload = text, payload or {}

    def raise_for_status(self):
        pass

    @property
    def text(self):
        return self._text

    def json(self):
        return self._payload


class _FakeHttp:
    """替换 `search_engine.requests`（只这一件命名空间，不去动全局 requests 模块）。"""

    def __init__(self, resp=None, exc=None):
        self.calls, self._resp, self._exc = [], resp, exc

    def post(self, url, **kw):
        self.calls.append((url, kw))
        if self._exc:
            raise self._exc
        return self._resp


def t10_search_parses_ddg_html_with_zero_network():
    # 真解析函数 + canned HTML：零外网也能钉住选择器与 l/?uddg= 还原
    html = "".join(
        f'<div class="result"><a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fx.io%2F{i}">t{i}</a>'
        f'<a class="result__snippet">{"s" * 2000}</a></div>' for i in range(9))
    fake, orig = _FakeHttp(_Resp(text=html)), se.requests
    se.requests = fake
    try:
        out = asyncio.run(search_internet.ainvoke({"query": "ponytail"}))
    finally:
        se.requests = orig
    assert fake.calls[0][0] == se.DDG_URL and fake.calls[0][1]["data"] == {"q": "ponytail"}, fake.calls[0]
    assert "https://x.io/0" in out and len(out) == 8000, len(out)


def t11_search_routes_to_serper_when_key_set():
    orig_key, orig_http = settings.search.serper_api_key, se.requests
    payload = {"organic": [{"title": "t", "link": "https://x.io", "snippet": "s"}]}
    fake = _FakeHttp(_Resp(payload=payload))
    settings.search.serper_api_key = "k"
    se.requests = fake
    try:
        assert se.engine() == "serper"
        out = asyncio.run(search_internet.ainvoke({"query": "q"}))
    finally:
        se.requests, settings.search.serper_api_key = orig_http, orig_key
    assert fake.calls[0][0] == se.SERPER_URL and fake.calls[0][1]["headers"]["X-API-KEY"] == "k", fake.calls[0]
    assert fake.calls[0][1]["json"] == {"q": "q", "num": 8}, fake.calls[0][1]
    assert "https://x.io" in out, out


def t11b_search_degrades_on_failure():
    orig = se.requests
    se.requests = _FakeHttp(exc=RuntimeError("rate limited"))
    try:
        out = asyncio.run(search_internet.ainvoke({"query": "q"}))
    finally:
        se.requests = orig
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
                         working_directory=str(ROOT / "a" / "b"))
    r = asyncio.run(run_context(ctx))
    assert Path(r.stdout.strip()) == ROOT / "a" / "b", r.stdout
    nonzero = asyncio.run(run_context(
        RunCodeContext(command=[sys.executable, "-c", "raise SystemExit(3)"], working_directory=str(ROOT))))
    assert nonzero.return_code == 3, nonzero


def t14_default_workdir_and_scratch_stay_inside():
    asyncio.run(run_python_code("print('s')"))
    scratch = ROOT / "scratch"
    assert scratch.exists() and all(p.is_relative_to(ROOT) for p in scratch.glob("run_*.py"))


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


def t30_session_dirs_are_mutually_invisible():
    """per-session 隔离（S4 判 `新`）：会话各写各的，互相读不到；脏目录名退回默认桶。"""
    tok = CURRENT_PROJECT.set("s4_b")
    try:
        asyncio.run(write_file.ainvoke({"path": "only_b.txt", "content": "b"}))
        assert (WS / "s4_b" / "only_b.txt").exists()
        assert read_file.invoke({"path": "only_b.txt"}) == "b"
    finally:
        CURRENT_PROJECT.reset(tok)
    assert "文件不存在" in read_file.invoke({"path": "only_b.txt"}), "B 会话的文件不该出现在 A 的读口"
    assert (ROOT / "a" / "b" / "c.txt").exists()                      # t4 写在 A 里，仍在
    assert not (WS / "s4_b" / "a").exists(), "A 的目录树不该被 B 看见"
    # 目录名可能是 LLM 产出的 project_name：越出 workspace_root 就退回默认桶，而不是在仓库里建目录
    clamped = session_root("../../escape_me")
    assert clamped == (WS / "project").resolve(), clamped
    assert clamped.is_relative_to(WS) and not (BASE.parent / "escape_me").exists()


def t31_timeout_keeps_partial_output():
    """超时最要紧的是把已经打出来的东西留住——wait_for(communicate()) 被取消会丢管道数据（实测）。"""
    r = asyncio.run(run_python_code("print('partial_out')\nimport time; time.sleep(20)", timeout=3))
    assert r.return_code == -1 and "partial_out" in r.stdout and "timeout" in r.stderr, r
    out = asyncio.run(execute_shell_async.ainvoke(
        {"command": f"{PY} -c \"print('shell_partial');import time;time.sleep(20)\"", "timeout": 3}))
    assert "shell_partial" in out and "timeout" in out, out


def t32_timeout_kills_whole_process_tree():
    """Windows 上活进程会把工作目录句柄攥住：rmtree 删不掉 = 树没杀干净（实测过只杀父进程的坑）。"""
    probe = ROOT / "tree_probe"
    probe.mkdir(exist_ok=True)
    (probe / "spawn.py").write_text(
        "import subprocess, sys, time\n"
        "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'])\n"
        "time.sleep(120)\n", encoding="utf-8")
    asyncio.run(run_proc([sys.executable, "spawn.py"], cwd=probe, timeout=2))
    for _ in range(10):
        shutil.rmtree(probe, ignore_errors=True)
        if not probe.exists():
            return
        time.sleep(0.2)
    raise AssertionError("进程树没杀干净：probe 目录仍被句柄锁住")


def t33_terminal_registry_is_per_session():
    """源 TERMINAL 是全进程单例：B 会话会在 A 的 cwd 里执行、还读到 A 的输出队列。"""
    a = current_terminal()
    tok = CURRENT_PROJECT.set("s4_term_b")
    try:
        b = current_terminal()
        assert b is not a and current_terminal() is b
        asyncio.run(close_terminal("s4_term_b"))
        assert current_terminal() is not b, "关掉的壳必须重新登记，不能留在表里复用"
    finally:
        CURRENT_PROJECT.reset(tok)
    assert current_terminal() is a


def t34_additional_python_paths_reach_child():
    """RunCodeContext.additional_python_paths 此前被静默忽略（字段有、没人读）。"""
    r = asyncio.run(run_context(RunCodeContext(
        command=[sys.executable, "-c", "import os;print(os.environ.get('PYTHONPATH',''))"],
        working_directory=str(ROOT), additional_python_paths=[str(ROOT / "libs")])))
    assert r.stdout.strip().startswith(str(ROOT / "libs")), r.stdout


def main():
    checks = [t1_registry_items_are_langchain_tools, t2_sibling_prefix_escape,
              t3_parent_and_absolute_escape, t4_write_read_roundtrip_creates_dirs,
              t5_missing_path_and_no_sink_do_not_raise, t6_editor_block_reaches_sink,
              t7_tool_output_log_slot_fires, t8_shell_runs_in_session_root,
              t9_shell_output_truncated, t10_search_parses_ddg_html_with_zero_network,
              t11_search_routes_to_serper_when_key_set, t11b_search_degrades_on_failure,
              t12_sandbox_runs_and_reports_exit_code,
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
              t29_env_reader_matches_its_only_call_site,
              t30_session_dirs_are_mutually_invisible,
              t31_timeout_keeps_partial_output,
              t32_timeout_kills_whole_process_tree,
              t33_terminal_registry_is_per_session,
              t34_additional_python_paths_reach_child]
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
          f"未知 key 告警跳过/漏 @tool 不登记/SweAgent 取法）+ 越界防护 3 组（兄弟会话目录前缀回归/"
          f"父目录与绝对路径/scratch 收口）+ 接缝 3 组（无 sink 不抛 / editor 块达 sink / 工具日志槽）"
          f"+ shell 2 组 + 搜索 3 组（canned HTML 真解析/配 key 走 serper/失败降级，全程零外网）"
          f"+ 沙箱 3 组（退出码/超时/工作目录）+ Terminal 5 组（跨命令保态/挂死超时后 shell 自愈/"
          f"死壳报错不空转/禁行命令替换跳过/daemon 输出进队列）+ linter 3 组（Python 报错带行号/"
          f"干净文件放行/非 Python 照源不校验）+ Editor._lint_file 真消费者 1 组 + env 读口签名 1 组"
          f"+ per-session 隔离 5 组（会话目录互不可见+脏名退回/超时留输出/杀整棵进程树/"
          f"Terminal 按会话登记且可关/additional_python_paths 进 PYTHONPATH）")


if __name__ == "__main__":
    main()
