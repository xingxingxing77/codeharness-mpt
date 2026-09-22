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


EXPECTED_TOOLS = {          # 注册面全名册：新工具必须写明"是谁、为何在"才准入
    "write_file", "read_file",                          # S4 文件对（带会话边界）
    "execute_shell_async", "terminal_command",          # shell 对（沙箱/保态终端）
    "search_internet",                                  # 联网唯一出口
    "open_file", "goto_line", "scroll_down", "scroll_up", "create_file",
    "edit_file_by_replace", "insert_content_at_line", "append_file",
    "search_dir", "search_file", "find_file",           # Editor 命令面 11 只（接线台账 #7）
    "git_create_pull", "git_create_issue",              # gh CLI 版 git 对（台账 #8）
}


def t1_registry_items_are_langchain_tools():
    names = [t.name for t in REGISTRY]
    assert len(names) == len(set(names)), f"重名登记: {names}"
    assert set(names) == EXPECTED_TOOLS, \
        f"注册面漂移\n缺: {EXPECTED_TOOLS - set(names)}\n多: {set(names) - EXPECTED_TOOLS}"
    for t in REGISTRY:
        assert t.description, t.name
        assert t.args is not None, t.name          # scroll_down 这类零参工具 args={}，形态断言不是真值断言
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


def t13b_run_context_clamps_outside_working_directory():
    """S3：working_directory 整字段来自 LLM 产出。越界必须退回会话根，且不得把目录建到会话外。"""
    cwd_of = [sys.executable, "-c", "import os;print(os.getcwd())"]
    for bad in (str(BASE / "escape_abs"),               # 绝对路径，会话根的父目录之下
                str(WS.parent / "escape_up"),           # 直接跳到 workspace_root 外面
                str(Path(sys.executable).parent),       # 解释器自己的目录（最像"合理"的越界）
                "../escape_rel"):                        # 相对跳一层
        r = asyncio.run(run_context(RunCodeContext(command=cwd_of, working_directory=bad)))
        got = Path(r.stdout.strip())
        assert got == ROOT, f"越界 working_directory={bad!r} 应落回会话根 {ROOT}，实际 {got}"
    for outside in (BASE / "escape_abs", WS.parent / "escape_up", WS / "escape_rel"):
        assert not outside.exists(), f"mkdir 把目录建到了会话外：{outside}"
    rel = asyncio.run(run_context(RunCodeContext(command=cwd_of, working_directory="tests")))
    assert Path(rel.stdout.strip()) == ROOT / "tests", f"界内相对路径应落会话根下，实际 {rel.stdout.strip()}"


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
    assert {t.name for t in REGISTRY} == set(TOOL_REGISTRY.tools) == EXPECTED_TOOLS
    assert sorted(TOOL_REGISTRY.tags()) == ["edit", "file", "git", "terminal", "web"]


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


def t34b_additional_python_paths_clamped_to_session():
    """F-B：`additional_python_paths` 与 `working_directory` 是执行面上挨着的两个 LLM 字段，
    S3 那轮只关了一个（`sandbox.py:84` 原样 `Path(p).resolve()` 就进子进程 PYTHONPATH，
    外部目录能 shadow 标准库/第三方包）。越界一律丢弃；界内必到由 t34 当阳性对照。"""
    probe = [sys.executable, "-c", "import os;print(os.environ.get('PYTHONPATH',''))"]
    outside_abs = WS.parent / "escape_site"                 # workspace_root 之外
    outside_abs.mkdir(parents=True, exist_ok=True)
    (outside_abs / "hollow.py").write_text("print('imported from outside')\n", encoding="utf-8")
    inside = ROOT / "libs"
    inside.mkdir(parents=True, exist_ok=True)

    def run(paths):
        return asyncio.run(run_context(RunCodeContext(
            command=probe, working_directory=str(ROOT),
            additional_python_paths=paths))).stdout.strip()

    for bad in (str(outside_abs), "../escape_rel", str(Path(sys.executable).parent)):
        resolved = str((ROOT / bad).resolve())
        got = run([bad])
        assert resolved not in got, f"越界路径 {bad!r}（解析后 {resolved!r}）进了子进程 PYTHONPATH：{got!r}"

    mixed = run([str(inside), str(outside_abs)])            # 越界夹在合法值里也不许过
    assert mixed.startswith(str(inside)), f"混合入参把界内那条也误伤了：{mixed!r}"
    assert "escape_site" not in mixed, f"越界那条混在合法值里就漏过：{mixed!r}"
    assert run([str(outside_abs)]) == os.environ.get("PYTHONPATH", ""), \
        "全部越界时应退回继承环境（不凭空多一条 import 路径）"


SOURCE_EDITOR_COMMANDS = [  # 源 roles/di/role_zero.py:147-167 默认装配 `Editor.*` 十四法
    "append_file", "create_file", "edit_file_by_replace", "find_file", "goto_line",
    "insert_content_at_line", "open_file", "read", "scroll_down", "scroll_up",
    "search_dir", "search_file", "similarity_search", "write"]


def t35_source_editor_assembly_covered():
    """台账 #7/#9：源默认装配的每只 Editor 命令在本仓都要有去向——11 只同名登记；
    read/write 由带边界的 read_file/write_file 覆盖（Editor 版绝对路径直解，多挂=多一条旁路）；
    similarity_search 随 index_repo 判弃，方法体必须已从 editor.py 摘除。"""
    reg = TOOL_REGISTRY.tools
    for cmd in SOURCE_EDITOR_COMMANDS:
        if cmd in ("read", "write"):
            assert ("read_file" if cmd == "read" else "write_file") in reg, f"{cmd} 无覆盖落点"
        elif cmd == "similarity_search":
            body = (Path(__file__).resolve().parents[1] / "codeharness" / "tools" / "libs" /
                    "editor.py").read_text(encoding="utf-8")
            assert "def similarity_search" not in body, "判弃方法没摘除=悬空 import 的定时炸弹（台账 #9）"
        else:
            assert cmd in reg, f"源默认装配命令 {cmd} 未登记"
    assert "edit" in TOOL_REGISTRY.by_tag and "git" in TOOL_REGISTRY.by_tag


def t36_editor_tools_roundtrip_and_boundary():
    """工具面必须真干活：create→append→replace→open/search 闭环 + 每个收路径的入口拒越界。"""
    from codeharness.tools.libs.editor_tools import close_editor
    close_editor()
    reg = TOOL_REGISTRY.tools
    asyncio.run(reg["create_file"].ainvoke({"filename": "blank.py"}))     # create 只验存在（源件写入起始换行，行号语义交 write_file 铺底）
    assert (ROOT / "blank.py").exists()
    asyncio.run(write_file.ainvoke({"path": "mod.py", "content": "a = 1\nb = 2\n"}))
    reg["edit_file_by_replace"].invoke(
        {"file_name": "mod.py", "first_replaced_line_number": 1, "first_replaced_line_content": "a = 1",
         "last_replaced_line_number": 1, "last_replaced_line_content": "a = 1", "new_content": "a = 0"})
    reg["insert_content_at_line"].invoke({"file_name": "mod.py", "line_number": 3, "insert_content": "c = 3"})
    reg["append_file"].invoke({"file_name": "mod.py", "content": "d = 4\n"})
    text = (ROOT / "mod.py").read_text(encoding="utf-8")
    assert "a = 0" in text and "b = 2" in text and "c = 3" in text and text.rstrip().endswith("d = 4"), \
        f"编辑命令没全部落盘:\n{text}"
    assert "a = 0" in reg["open_file"].invoke({"path": "mod.py", "line_number": 1})
    assert "a = 0" in reg["goto_line"].invoke({"line_number": 1})
    assert "a = 0" in reg["search_file"].invoke({"search_term": "a = 0", "file_path": "mod.py"})
    assert "mod.py" in reg["search_dir"].invoke({"search_term": "b = 2"})
    assert "mod.py" in reg["find_file"].invoke({"file_name": "mod.py"})
    escapes = {
        "open_file": {"path": "../../../Windows/win.ini"},
        "create_file": {"filename": "../s4_escape/x.py"},
        "append_file": {"file_name": "../s4_escape/x.py", "content": "c"},
        "insert_content_at_line": {"file_name": "../s4_escape/x.py", "line_number": 1, "insert_content": "c"},
        "edit_file_by_replace": {"file_name": "../s4_escape/x.py", "first_replaced_line_number": 1,
                                 "first_replaced_line_content": "x", "last_replaced_line_number": 1,
                                 "last_replaced_line_content": "x", "new_content": "n"},
        "search_dir": {"search_term": "t", "dir_path": "../"},
        "find_file": {"file_name": "passwd", "dir_path": "/"},
        "search_file": {"search_term": "t", "file_path": str(WS.parent / "outside.py")},
    }
    for name, args in escapes.items():
        out = asyncio.run(reg[name].ainvoke(args))   # create_file 只有协程实现，统一走 ainvoke
        assert "越界" in str(out), f"{name} 没拦住 {args}: {out!r}"
    assert not (WS.parent / "s4_escape").exists() and not (WS.parent / "outside.py").exists()
    close_editor()


def t37_git_tools_degrade_without_gh():
    """git 对已登记且参数面可用；gh 不在场必须返回降级文案而不是抛（工具面给模型的是可读字符串）。"""
    import codeharness.tools.libs.git as G

    async def missing_exe(*a, **k):
        raise FileNotFoundError("gh")
    keep, G.run_proc = G.run_proc, missing_exe
    try:
        out = asyncio.run(TOOL_REGISTRY.tools["git_create_pull"].ainvoke(
            {"base": "main", "head": "feat", "base_repo_name": "u/r"}))
        assert "gh CLI" in out, out
        out2 = asyncio.run(TOOL_REGISTRY.tools["git_create_issue"].ainvoke({"repo_name": "u/r", "title": "t"}))
        assert "gh CLI" in out2, out2
    finally:
        G.run_proc = keep


def t38_no_dangling_metagpt_imports():
    """代码里不得残留 `from metagpt`（git.py 两函数与 editor.similarity_search 曾是'一接线就
    ModuleNotFoundError'的死复制件，本检查保证同类问题不再静默回归，台账 #8/#9 的机器版）。
    ⚠ prompts/ 豁免：write_analysis_code.py / metagpt_sample.py / generate_skill.md 是 s6 t1 逐字保护的源
    prompt 资产，且该门禁除"常量与源逐字相等"外还断言**顶层常量键集相等**——往这些模块里加法拼接一个新常量
    同样当场红（`C9_PROBE_CALIBRATION` 探针，2026-09-21 实测）。其中诱导模型 `from metagpt.tools.libs...`
    的文本不在此断言范围内：2026-09-21 C9 取证 = 三件资产生产路径**零消费者**，今日不构成运行时隐患；
    「谁接进生产谁同批在消费处加法拼接校准常量」这条重开条件记在对照3 §结论-2。"""
    import re as _re
    root = Path(__file__).resolve().parents[1] / "codeharness"
    hits = []
    for p in sorted(root.rglob("*.py")):
        if "prompts" in p.parts:
            continue
        for i, ln in enumerate(p.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            if _re.match(r"\s*(from|import)\s+metagpt\b", ln):
                hits.append(f"{p.relative_to(root.parent)}:{i}")
    assert not hits, f"供体包悬空 import: {hits}"


# ---------------- C6：工具召回（默认休眠；名册长过 min_tools 才开始裁 prompt） ----------------
def _c6_capture_warn(fn):
    """只收 WARNING 及以上：兜底路径全靠 warning 说话，收全级别会让任何一条旧告警都算通过。"""
    import io

    from codeharness.logs import logger
    buf = io.StringIO()
    hid = logger.add(buf, format="{message}", level="WARNING")
    try:
        got = fn()
        return buf.getvalue(), got
    finally:
        logger.remove(hid)


def _c6_tools():
    return {t.name: t for t in TOOL_REGISTRY.all()}


async def _c6_select(min_tools, topk=6, recall_topk=12, use_llm=False, llm=None, query="写入文件",
                     tools=None, semantic=None):
    from codeharness.configs.settings import settings as S
    from codeharness.tools.tool_recall import select_for_prompt
    keep = (S.tool_recall.min_tools, S.tool_recall.topk, S.tool_recall.recall_topk, S.tool_recall.use_llm,
            S.tool_recall.semantic)
    S.tool_recall.min_tools, S.tool_recall.topk = min_tools, topk
    S.tool_recall.recall_topk, S.tool_recall.use_llm = recall_topk, use_llm
    if semantic is not None:
        S.tool_recall.semantic = semantic
    try:
        return await select_for_prompt(tools if tools is not None else _c6_tools(), query, llm=llm)
    finally:
        (S.tool_recall.min_tools, S.tool_recall.topk, S.tool_recall.recall_topk,
         S.tool_recall.use_llm, S.tool_recall.semantic) = keep


def t39_recall_dormant_on_today_roster():
    """C6 的默认档必须是「什么都不做」：今天名册 18 只 ≤ `min_tools=30` ⇒ 返回**同一个对象**。

    判 identity 不判相等：`role_zero.py` 那个 `json.dumps` 吃的是 dict 的键序与内容，
    返回一个「等值的新 dict」也算通过，但键序漂了 prompt 就变了——休眠档要的是逐字不变。
    阳性对照（同一格内）：把阈值调到 1，必须换回一个更小的集合，否则这格是恒真的废话。
    """
    from codeharness.configs.settings import settings as S
    assert S.tool_recall.min_tools >= len(TOOL_REGISTRY.all()) > 1, \
        f"默认阈值 {S.tool_recall.min_tools} 已低于现名册 {len(TOOL_REGISTRY.all())} 只：休眠前提没了，生产 prompt 会被裁"
    tools = _c6_tools()
    same = asyncio.run(_c6_select(S.tool_recall.min_tools, query="把内容写进 note.txt", tools=tools))
    assert same is tools, "默认档竟然返回了别的对象——C6 的『今天不生效』是设计前提，不是巧合"
    active = asyncio.run(_c6_select(1, topk=6, query="把内容写进 note.txt", tools=tools, semantic=False))
    assert active is not tools and 0 < len(active) < len(tools), \
        f"阈值降到 1 仍没裁（{len(active)} 只）：召回整条路是死的，上面那格就成了自证"
    assert set(active) <= set(tools), "裁出来的子集出现了名册外的工具"


def t40_recall_falls_back_to_full_and_warns():
    """两条兜底都要留可 grep 的话（源是直接 `return []`，prompt 里一个命令都不剩）。

    ① 零命中（query 与任何工具名/描述都无词面重叠）→ 回全量 + 「零命中」。**09-22 描述规范化之后这一格的输入只能人造**：中文按单字切分，加了关键词档之后「今天天气怎么样」都能命中 3 只（`今天/天气/怎么样` 撞上 `报错怎么解`/`里面写了什么`/`叫什么名字`），真口语 query 已近乎不可能零命中 ⇒ 这格测的是**兜底分支的形状**，不是真实分布；真实分布上还有用的是下面那格薄命中。
    ② 薄命中（粗筛只凑到 1 只，实测「跑一下 pytest 看结果」就这样）→ 低于下限 `min(topk,3)` → 回全量 + 「判为不可用」。
       这一格是 C6 唯一真正救回命中率的机制：词法腿裁错时，代价由兜底承担而不是由会话承担。
    """
    tools = _c6_tools()
    # 两格都显式关语义腿：钉的是**词法腿**的兜底。语义腿一开，「今天天气」也会被 dense 补出命中，
    # 那就不是零命中场景了（融合读数在 t44、降级路径在 t45）。
    w1, got1 = _c6_capture_warn(lambda: asyncio.run(
        _c6_select(1, query="饕餮魍魉虪龘", tools=tools, semantic=False)))   # 人造串，理由见下
    assert got1 is tools and "零命中" in w1, f"①失效：{len(got1)} 只 / 日志 {w1[-160:]!r}"
    # ② 的输入也换人造串了，而且换的理由值得留档：旧输入「跑一下 pytest 看结果」当年粗筛只有 1 只
    #    （<3 的下限，所以靠这格兜底），而 09-22 描述规范化之后它粗筛涨到 12 只 ⇒ **那条原始 miss 是被
    #    关键词补上的，不是被兜底救的**（这格从"救它"变成"救别的"）。薄命中现在用「鹬蚌相争渔翁得利」
    #    （实测粗筛 2 只）——它测的仍是同一个下限分支，只是不再是真实分布里的句子。
    w2, got2 = _c6_capture_warn(lambda: asyncio.run(
        _c6_select(1, query="鹬蚌相争渔翁得利", tools=tools, semantic=False)))
    assert got2 is tools and "判为不可用" in w2, \
        f"②失效：薄命中没兜住（给了 {len(got2)} 只），模型会被裁到只剩终端以外的工具：{w2[-160:]!r}"


def t41_rank_leg_degrades_without_losing_the_run():
    """精排腿（`use_llm=True`）三种回法：正常选、幻觉名、不是 JSON——后两种都必须退回粗筛且**不抛**。

    降级纪律照 `memory/longterm.py:_rerank`：精排是可选能力，不可用就原序，绝不把会话带崩。
    默认 `use_llm=False` 的理由与 `RerankerConfig.base_url=""` 同一条：开一级 = 每轮 think 多发一次调用
    （本机实测一句 ¥0.12–0.27），不能默认替用户烧。
    """
    from codeharness.provider.fake import FakeLLM

    q = "把内容写进 note.txt 再读回来核对"
    good = FakeLLM(['["write_file", "read_file"]'])
    got = asyncio.run(_c6_select(1, topk=6, use_llm=True, llm=good, query=q, semantic=False))
    assert {"write_file", "read_file"} <= set(got) and len(got) <= 6, f"精排正常路径失配：{sorted(got)}"

    halluc = FakeLLM(['["make_coffee", "teleport"]'])
    w, got2 = _c6_capture_warn(lambda: asyncio.run(_c6_select(1, topk=6, use_llm=True, llm=halluc, query=q, semantic=False)))
    assert len(got2) >= 3 and "没有一个在候选里" in w, \
        f"幻觉名那格失配：给了 {sorted(got2)} / 日志 {w[-160:]!r}（必须退粗筛原序并留话）"

    junk = FakeLLM(["抱歉，我不确定该用哪个工具。"])
    w3, got3 = _c6_capture_warn(lambda: asyncio.run(_c6_select(1, topk=6, use_llm=True, llm=junk, query=q, semantic=False)))
    assert len(got3) >= 3 and "精排不可用" in w3, f"非 JSON 回法失配：{len(got3)} 只 / {w3[-160:]!r}"


def t42_recall_coverage_is_pinned_at_measured_value():
    """把**实测覆盖率**钉成回归守卫（14 条标注 query，top-6）。这不是「判据达标」，是现值留档。

    PLAN §4 C6 的判据原文是「工具数超阈值时 prompt 里工具面变小且**命中率不降**」。**读数历史**：
      词法腿曾 13/14 覆盖、真裁小 12/14（`3dbb506`）→ 融合 13/13（`1a09f83`）→ 常驻集 14/14（`7c2e802`）
      → **09-22 描述规范化后词法腿单跑也 14/14**（`t3a` 那批改动）。
    为什么这格曾经故意断 13 而不是断达标数：它的用处是「谁改坏了切分/兜底，当场看得见」，
    在现值不是 14 的时候写 14 就是拿门禁自证。现在现值真是 14，于是这格的性质变了——**它只能防退化，
    不能证明「命中率不降」**：这批题从写判据起就在，描述里的关键词也就是照着它们加的 ⇒ 已用集饱和。
    判据 🟡 的唯一现役证据是 **t47**（生成于描述定稿之后、没参与任何调参的那批）。
    """
    CASES = [("把这段内容写入 note.txt", {"write_file"}),
             ("append 一行日志到 app.log", {"append_file"}),
             ("新建一个 config.yaml", {"create_file"}),
             ("读一下 src/main.py 现在写的什么", {"read_file"}),
             ("把那一行改掉，替换成新的实现", {"edit_file_by_replace"}),
             ("在 workspace 里搜 login 出现在哪些文件", {"search_file", "search_dir"}),
             ("找出所有叫 handler.py 的文件", {"find_file"}),
             ("打开 src/app.py 并跳到第 40 行", {"open_file", "goto_line"}),
             ("跑一下 pytest 看结果", {"terminal_command", "execute_shell_async"}),
             ("down 一屏看看后面的内容", {"scroll_down"}),
             ("把这个分支推上去开 PR", {"git_create_pull"}),
             ("给这个 bug 开一个 issue", {"git_create_issue"}),
             ("search internet for langchain astream_events docs", {"search_internet"}),
             ("在第 12 行后面插入一行 import os", {"insert_content_at_line"})]
    full = trimmed = 0
    missed = []
    for q, want in CASES:
        got = asyncio.run(_c6_select(1, topk=6, query=q, semantic=False))   # 只钉词法腿，语义腿现值在 t44
        if want <= set(got):
            full += 1
            if len(got) < len(_c6_tools()):
                trimmed += 1
        else:
            missed.append((q[:20], sorted(want - set(got))))
    assert full == 14, f"词法腿覆盖率漂了：现值应 14/14，实际 {full}/14，miss={missed}"
    assert trimmed == 14, f"「裁了还中」的格数漂了：现值应 14/14，实际 {trimmed}"
    print(f"     覆盖率读数：完全覆盖 {full}/14、其中真裁小 {trimmed}/14、miss={missed}")
    # 14/14 不是「达标」——是**已用集饱和**：这批题从写判据起就摆在这儿，加关键词当然会抬高它。
    # 所以本格自 09-22 描述规范化起**失去判别力**，真读数看 t47（未参与调参的那批）。


def t43_roster_unchanged_by_c6():
    """C6 不许往名册里塞工具：召回是「少给模型看」的一层，不是新能力面。

    为什么单独一格：把阈值凑过去的最省事写法就是多注册几只假工具，而 `EXPECTED_TOOLS`
    是登记制守卫（t1）——真有人这么干，那一格会红，但这格把「为什么不许」写在现场。
    """
    assert set(TOOL_REGISTRY.tools) == EXPECTED_TOOLS and len(EXPECTED_TOOLS) == 18, \
        f"名册变了：{sorted(set(TOOL_REGISTRY.tools) ^ EXPECTED_TOOLS)}（C6 只裁 prompt，不加工具）"


def _c6_dense_alive() -> bool:
    """语义腿**真的活着**吗——判据不是「端口有响应」，而是「dense 排得出名次」。

    现场教训（2026-09-22）：C 盘模型删掉后 `OLLAMA_MODELS` 只在命令行里设，托盘 App 会立刻重拉一个
    不带该变量的 serve 抢回 11434 —— 端口照样 200、`_dense_rank` 静默退词法腿，t44 于是量出
    「13 覆盖 / 12 真裁中」这种**词法腿的数**却写着「bge-m3 在线」。这就是 §0 点名的假绿。
    """
    from codeharness.provider.gateway import LLMGateway
    from codeharness.tools.tool_recall import _dense_rank
    docs = {t.name: f"{t.name}: {t.description or ''}" for t in TOOL_REGISTRY.all()}
    return bool(asyncio.run(_dense_rank("把内容写进文件再跑一遍测试", docs, 6, LLMGateway.embeddings())))


def t44_hybrid_coverage_when_embedding_live():
    """语义腿在线时，融合粗筛的现值钉在 **14/14 且 14 格全是真裁小**（常驻集不参与裁剪）（离线则显式跳过）。

    与 t42 的分工：t42 是常跑的词法腿现值（13 覆盖 / 12 真裁中），这格是加腿之后的增量读数。
    跳过纪律照 `s5_memory_rag::t25`：语义质量这件事不能拿假 embedding 冒充——`HashEmbeddings` 是
    bag-of-chars（`provider/fake.py:56` 自己写着「只看得见字符重叠」），拿它跑出来的「命中」是假阳性。
    ~~现值仍差的那一格是 `'跑一下 pytest 看结果'`~~ → 那格由 `7c2e802` 的**常驻集**接走（终端/读写
    四件不参与裁剪），本格断言随之从 13/13 改钉 14/14。**这条 docstring 当时只改了半截**（断言改了、
    正文没改），09-22 描述规范化那轮一起补上。
    """
    import httpx

    from codeharness.configs.settings import settings as S
    try:
        up = httpx.get(f"{S.embedding.base_url}/models", timeout=3).status_code < 400
    except Exception:
        up = False
    if not up or not _c6_dense_alive():
        print(f"     skip t44（语义腿没活着：{S.embedding.base_url}）——融合读数不拿词法腿的数冒充")
        return
    CASES = [("把这段内容写入 note.txt", {"write_file"}), ("append 一行日志到 app.log", {"append_file"}),
             ("新建一个 config.yaml", {"create_file"}), ("读一下 src/main.py 现在写的什么", {"read_file"}),
             ("把那一行改掉，替换成新的实现", {"edit_file_by_replace"}),
             ("在 workspace 里搜 login 出现在哪些文件", {"search_file", "search_dir"}),
             ("找出所有叫 handler.py 的文件", {"find_file"}),
             ("打开 src/app.py 并跳到第 40 行", {"open_file", "goto_line"}),
             ("跑一下 pytest 看结果", {"terminal_command", "execute_shell_async"}),
             ("down 一屏看看后面的内容", {"scroll_down"}),
             ("把这个分支推上去开 PR", {"git_create_pull"}),
             ("给这个 bug 开一个 issue", {"git_create_issue"}),
             ("search internet for langchain astream_events docs", {"search_internet"}),
             ("在第 12 行后面插入一行 import os", {"insert_content_at_line"})]
    full = trimmed = 0
    missed = []
    for q, want in CASES:
        got = asyncio.run(_c6_select(1, topk=6, query=q, semantic=True))
        if want <= set(got):
            full += 1
            if len(got) < len(_c6_tools()):
                trimmed += 1
        else:
            missed.append((q[:20], sorted(want - set(got))))
    assert (full, trimmed) == (14, 14), f"融合+常驻集现值漂了：应 14/14，实际 覆盖 {full}、真裁小 {trimmed}，miss={missed}"
    print(f"     融合读数（bge-m3 在线）：覆盖 {full}/14、真裁小 {trimmed}/14、miss={missed}")


def t45_semantic_leg_offline_degrades_to_lexical():
    """语义腿连不上时必须**当场退回词法腿**并留一行可 grep 的话，不许抛也不许静默变全量。

    死端口那档是本仓既有的造法（不停共享服务）。这一格常跑、不依赖服务在线——它钉的是
    「服务挂了的那条会话」的实际走向：t44 跳过的时候，全仓就没有任何判据覆盖过降级路径。
    """
    from codeharness.configs.settings import settings as S
    keep = S.embedding.base_url
    S.embedding.base_url = "http://127.0.0.1:1/v1"          # 与容器停着同一个 ConnectError，且不打扰别人
    try:
        w, got = _c6_capture_warn(lambda: asyncio.run(
            _c6_select(1, topk=6, query="down 一屏看看后面的内容", semantic=True)))
    finally:
        S.embedding.base_url = keep
    # 上限是 `topk + 常驻集`（6+4）：降级只该少一条腿，不该连带把常驻那四件也裁没或放大
    assert 4 <= len(got) <= 10, f"降级后工具集规模异常（{len(got)} 只）：{sorted(got)}"
    assert "语义腿不可用" in w, f"降级没留话（日志 {w[-160:]!r}）——运维无从知道这一跳只跑了一条腿"


PIN_T47 = 22   # 09-22 22:3x 现测（22/22）。钉了之后不许为转绿去改描述；要动描述就得再换一批新题
HELD_OUT = [("看看现在登录逻辑是怎么写的", {"read_file"}),
            ("把这份报告保存到磁盘上", {"write_file"}),
            ("列出目录里所有的 py 文件", {"search_file", "find_file", "search_dir"}),
            ("翻到文件末尾再多看两行", {"scroll_down"}),
            ("把刚才那个改动提交并推到远端", {"git_create_pull"}),
            ("开个单子追踪这个崩溃", {"git_create_issue"}),
            ("在第 30 行下面加一行注释", {"insert_content_at_line"}),
            ("查一下官方文档里这个 API 的用法", {"search_internet"})]


def t46_held_out_phrasings_show_the_real_rate():
    """**held-out 8 条**：措辞没参与过任何调参、也没写进任何判据或工具描述——防答题的那格。

    t42/t44 钉的「已用 14 条」写判据时就在那儿，抬 `topk`、补描述都能把绿凑出来；这格用没被碰过的口语
    措辞量同一套召回（常驻集 + 融合，任一即中即算可用）。现值 **6/8**，漏的两条都落在 git（「把刚才那个
    改动提交并推到远端」「开个单子追踪这个崩溃」）⇒ 中文口语到英文工具名/描述那层隔阂没被任何 tune 消掉。
    ~~**C6 记 🟡 的依据就是这格**~~ → **09-22 描述规范化时我没能守住这条**：那两条 miss 的说法（git 的
    「推到远端」「开个单子」）被我写进了 `git_create_pull`/`git_create_issue` 的关键词档，这 8 条从此
    就是答案的一部分 ⇒ **本格降级为已用集**，它从 6/8 涨到 8/8 只能算「答了熟悉的题」，不再当证据。
    真 held-out 交给 **t47**（那批由「没见过描述的子代理」生成、生成时机在描述定稿之后）。
    """
    if not _c6_dense_alive():
        print("     skip t46（语义腿没活着）——held-out 不拿词法字符重叠冒充语义读数")
        return
    hit = 0
    missed = []
    for q, want in HELD_OUT:
        got = set(asyncio.run(_c6_select(1, topk=6, query=q)))
        hit += bool(got & want)
        if not got & want:
            missed.append(q[:14])
    assert hit == 8, f"这批题现值漂了：应 8/8，实际 {hit}/8，漏={missed}"
    print(f"     旧 held-out 读数：任一即中 {hit}/8 —— **已降级为已用集**（见 docstring），涨不算证据")


HELD_OUT_2 = [
    # **第二批 held-out（09-22 22:2x）**：由一个「只见工具名与参数签名、没读过任何工具描述」的子代理生成，
    # 生成时机在 18 条描述**定稿之后** —— 它在因果上不可能参与调参，这是它比 HELD_OUT 更硬的地方。
    # 约束记在案：本格的数一旦量出来就不许回头改描述去救它；改描述只能救 t42/t44/t46 那些已用集。
    ("下面这段季度总结我已经拟好了，你原样落到 docs/summary_2025Q3.md 里，之前那份不要了", {"write_file"}),
    ("utils/text_util.py 这个文件我压根没碰过，你先整个过一遍，然后跟我说里面那个 truncate 到底咋实现的", {"read_file"}),
    ("执行一下 npm run build，给它三分钟，超了就直接掐掉别陪它耗", {"execute_shell_async"}),
    ("cd 到 backend 那边，用 llm 这个 conda 环境把 uvicorn 拉起来，回头我还要在同一个命令行里接着敲别的", {"terminal_command"}),
    ("FastAPI 现在是不是不太推荐 on_startup 了？网上帮我捞几篇说 lifespan 的帖子，看看社区咋用的", {"search_internet"}),
    ("payment_service.py，把第 88 行前后那块逻辑调出来给我瞧瞧", {"open_file"}),
    ("文件不用重新开，光标直接挪到 233 行，我说的空指针就在那一片", {"goto_line"}),
    ("视图往下挪挪，上面那些 import 和类头我已经扫过了", {"scroll_down"}),
    ("唉不对，回退一点，开头那个类到底继承的啥我没看清", {"scroll_up"}),
    ("src/components 底下给我起个空壳，名字叫 PriceTag.vue，里面写什么我自己来", {"create_file"}),
    ("order_service.py 从 45 行到 52 行那一坨 if elif 太啰嗦，整块换成下面这个字典分发的写法", {"edit_file_by_replace"}),
    ("Dockerfile 第 9 行那个位置塞两段 ENV 进去，后面原有的东西一行都别给我动", {"insert_content_at_line"}),
    ("每轮压测跑完，把带时间戳的这一行记到 benchmarks/run.log 尾巴上，前面积攒的一条都不能丢", {"append_file"}),
    ("全项目范围扫一遍，到底哪些地方调了 decode_token，一个都别漏", {"search_dir"}),
    ("app.js 这个文件里头 setTimeout 都在哪几行，我就想知道总共埋了几处", {"search_file"}),
    ("有个 settings.ini 我死活想不起来丢哪儿去了，你帮我瞅瞅它到底躲在哪个目录", {"find_file"}),
    ("feature/rate-limit 这活儿干完了，往 main 合，标题写「新增令牌桶限流中间件」，描述里交代清楚依赖 Redis 计数，仓库是 our-org/gateway", {"git_create_pull"}),
    ("redis 一挂客户端就死循环重试，CPU 直接打满。这事得让上游知道，去 our-org/cache-client 那边记一笔，复现步骤我贴在下面", {"git_create_issue"}),
    ("先把 conda 的 py311 环境切过来，然后 pip list 看看装了没，等会儿我还要接着往下装包，你别每次给我换个新窗口", {"terminal_command"}),
    ("这一页全是些没用的样板代码，往下走走，直接给我看实现那部分", {"scroll_down"}),
    ("别的先不管，那条 stacktrace 指的 47 行，让我先瞅见那一行写的啥", {"goto_line", "open_file"}),
    ("这项目里是不是满地都留着 print 啊，我想知道到底有几个文件带这种调试垃圾", {"search_dir"}),
]


def t47_new_held_out_after_desc_normalization():
    """**第二批 held-out（22 条）**：描述规范化之后唯一的真读数，钉的是**现测值**。

    与 t46 的分工（不写清就会被当成同一件事）：t46 那 8 条**已不再是 held-out**——规范化时把那两条
    miss 的说法（「推到远端」「开个单子」）写进了 git 工具的关键词档，这些措辞从此就是答案的一部分，
    它涨只能算「答了熟悉的题」。所以判据里**唯一的 held-out 证据是这格**。
    跳过纪律照 t46：dense 不活着就不量，不拿词法腿的数冒充「融合 + 常驻集」的读数。
    """
    if not _c6_dense_alive():
        print("     skip t47（语义腿没活着）——新 held-out 也不拿字符重叠冒充语义读数")
        return
    hit = 0
    missed = []
    for q, want in HELD_OUT_2:
        got = set(asyncio.run(_c6_select(1, topk=6, query=q)))
        hit += bool(got & want)
        if not got & want:
            missed.append((q[:16], sorted(want)))
    assert hit == PIN_T47, f"新 held-out 现值漂了：应 {PIN_T47}/22，实际 {hit}/22，漏={missed}"
    print(f"     新 held-out 读数：任一即中 {hit}/22、漏={missed}（描述规范化后的唯一真读数）")



def main():
    checks = [t1_registry_items_are_langchain_tools, t2_sibling_prefix_escape,
              t3_parent_and_absolute_escape, t4_write_read_roundtrip_creates_dirs,
              t5_missing_path_and_no_sink_do_not_raise, t6_editor_block_reaches_sink,
              t7_tool_output_log_slot_fires, t8_shell_runs_in_session_root,
              t9_shell_output_truncated, t10_search_parses_ddg_html_with_zero_network,
              t11_search_routes_to_serper_when_key_set, t11b_search_degrades_on_failure,
              t12_sandbox_runs_and_reports_exit_code,
              t13_run_context_honors_working_directory,
              t13b_run_context_clamps_outside_working_directory,
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
              t34_additional_python_paths_reach_child,
              t34b_additional_python_paths_clamped_to_session,
              t35_source_editor_assembly_covered, t36_editor_tools_roundtrip_and_boundary,
              t37_git_tools_degrade_without_gh, t38_no_dangling_metagpt_imports,
              t39_recall_dormant_on_today_roster, t40_recall_falls_back_to_full_and_warns,
              t41_rank_leg_degrades_without_losing_the_run,
              t42_recall_coverage_is_pinned_at_measured_value, t43_roster_unchanged_by_c6,
              t44_hybrid_coverage_when_embedding_live, t45_semantic_leg_offline_degrades_to_lexical,
              t46_held_out_phrasings_show_the_real_rate,
              t47_new_held_out_after_desc_normalization]
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
    print(f"\nS4 门禁通过：{len(checks)} 组 —— 注册表 6 组（全名册 EXPECTED_TOOLS 18 只登记/名字与 tag 并集去重/"
          f"未知 key 告警跳过/漏 @tool 不登记/SweAgent 取法）+ 越界防护 3 组（兄弟会话目录前缀回归/"
          f"父目录与绝对路径/scratch 收口）+ 接缝 3 组（无 sink 不抛 / editor 块达 sink / 工具日志槽）"
          f"+ shell 2 组 + 搜索 3 组（canned HTML 真解析/配 key 走 serper/失败降级，全程零外网）"
          f"+ 沙箱 3 组（退出码/超时/工作目录）+ Terminal 5 组（跨命令保态/挂死超时后 shell 自愈/"
          f"死壳报错不空转/禁行命令替换跳过/daemon 输出进队列）+ linter 3 组（Python 报错带行号/"
          f"干净文件放行/非 Python 照源不校验）+ Editor._lint_file 真消费者 1 组 + env 读口签名 1 组"
          f"+ per-session 隔离 5 组（会话目录互不可见+脏名退回/超时留输出/杀整棵进程树/"
          f"Terminal 按会话登记且可关/additional_python_paths 进 PYTHONPATH）"
          f"+ 接线批 B3 4 组（源 Editor 装配覆盖/编辑闭环+八入口拒越界/git 无 gh 降级/全仓无 metagpt 悬空 import）"
          f"+ C6 工具召回 {sum(1 for c in checks if c.__name__.split('_', 1)[0] in {'t39', 't40', 't41', 't42', 't43', 't44', 't45', 't46', 't47'})} 组"
          f"（t39 默认档不裁/t40 两条兜底各留告警/t41 精排不抛/t42 词法腿现值/t43 名册不涨/"
          f"t44 融合+常驻现值（离线显式跳过）/t45 死端口退词法并留话/t46 旧 held-out（09-22 起降级为已用集）/"
          f"t47 新 held-out）**各格现值只印在自己的输出行里，这里不复述**——这行手抄过两次数、漂了两次）")


if __name__ == "__main__":
    main()
