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
    "search_knowledge_base",                            # C24：知识库检索从「每轮预取」变成模型可主动调
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
    # `retrieval` 是 C24 加的第三只 tag（知识库检索从「每轮预取」变成模型可主动调的工具）
    assert sorted(TOOL_REGISTRY.tags()) == ["edit", "file", "git", "retrieval", "terminal", "web"]
    assert [t.name for t in TOOL_REGISTRY.select("retrieval")] == ["search_knowledge_base"], \
        "tag=retrieval 选出来的不是那件检索工具"


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
    """C6 的默认档必须是「什么都不做」：今天名册 19 只 ≤ `min_tools=30` ⇒ 返回**同一个对象**。

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
    18→19 那一次是 C24 的 `search_knowledge_base`（新能力面，走 t1 登记），不是 C6 凑阈值。
    """
    assert set(TOOL_REGISTRY.tools) == EXPECTED_TOOLS and len(EXPECTED_TOOLS) == 19, \
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
    from codeharness.configs.settings import settings as S
    # 探活只用 `_c6_dense_alive()`（判据是「dense 排得出名次」）。**别再拿裸 GET `/models` 当第一道门**：
    # 09-29 现证 `httpx.get(f"{base_url}/models")` **不带 Authorization**，百炼那台直接回 401
    # （`{"error":{"message":"You didn't provide an API key…"}}`），于是 `up` 恒 False、这格从加守卫起
    # **一次都没真跑过**——而同一次运行里 `_c6_dense_alive()` 连测三次全 True、`t47` 正常出 22/22。
    # 「401」不是「腿没活着」的证据，它只是「我没带钥匙」（与 §6 那条「`GET /models` 200 不证档位」互为反面）。
    # 前端那侧的目录口（`server/api/models.py:31`）是发了 Bearer 的，只有这里漏了。
    if not _c6_dense_alive():
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
    # 打印必须点名是哪台模型（§6 第 28 条）：这行原本写死「bge-m3 在线」，而 09-24 之后生产端点是
    # 百炼 `qwen3.7-text-embedding`——名字错了的读数行，比没有读数更难查。
    print(f"     融合读数（语义腿活着，模型 = {S.embedding.model}）："
          f"覆盖 {full}/14、真裁小 {trimmed}/14、miss={missed}")


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



def t48_editor_read_path_is_locale_independent():
    """T5（09-26 全量审查留账；本轮复核时**当场改判了一条**）。

    `editor.py` 的读路径原先全是裸 `open()`（编码跟 locale 走），而同一文件的写路径（`write()`、
    `:682`）与 `read_file` 固定 utf-8 ⇒ 读自己刚写的中文文件在非 UTF-8 启动的进程里会
    `UnicodeDecodeError: 'gbk' codec ...`（实测命中 `editor.py:216`）。

    ⚠ 留账里写的「本机 ACP=936 所以读不了」**不准确**：本机 `GetACP()=936`、`locale.getencoding()`
    也是 cp936 都不假，但**门禁进程默认 `sys.flags.utf8_mode=1`，`open()` 的默认编码就是 utf-8**
    ⇒ 默认跑法下**复现不出来**。必须 `PYTHONUTF8=0` 才现形，而编码是**进程启动时定的**、同进程里
    改不了，所以 ① 只能起子进程——这也正是真部署的形状。

    两格：
      ① **功能（真读数）**：`PYTHONUTF8=0` 的子进程里「写一份含中文的文件 → 编辑器读窗口」，
         退出码必须 0 且真读到中文；修复前退出码 1、stderr 上是那句 UnicodeDecodeError。
      ② **结构（不靠环境）**：AST 断言 `editor.py` 里每个 `open()` 都带 `encoding=`。
         ① 只在非 UTF-8 模式的进程里有牙，② 才是任何环境下的常驻守卫（新增裸 open 的读路径即红）。
    """
    import subprocess as _sp

    code = (
        "import pathlib\n"
        "from codeharness.runtime import session_root\n"
        "from codeharness.tools.libs.editor import Editor\n"
        "root = session_root()\n"
        "p = pathlib.Path(root) / 's4_cn_probe.py'\n"
        "p.write_text('# 中文注释：知识库\\nx = 1\\n', encoding='utf-8')\n"
        "out = Editor(working_dir=pathlib.Path(root))._print_window(p, 1, 5)\n"
        "assert '知识库' in out, repr(out[:80])\n"
        "print('EDITOR_READ_OK')\n"
    )
    env = {**os.environ, "PYTHONUTF8": "0"}          # 关掉 UTF-8 模式：open() 默认编码变回 cp936
    r = _sp.run([sys.executable, "-B", "-c", code], cwd=str(ROOT), env=env,
                capture_output=True, text=True, encoding="utf-8", errors="replace")
    assert r.returncode == 0 and "EDITOR_READ_OK" in (r.stdout or ""), \
        f"t48① PYTHONUTF8=0 下编辑器读 utf-8 中文失败（exit={r.returncode}）：" \
        f"{(r.stderr or r.stdout or '')[-300:]}"

    import ast
    src = (Path(__file__).resolve().parents[1] / "codeharness" / "tools" / "libs"
           / "editor.py").read_text(encoding="utf-8")
    naked = [n.lineno for n in ast.walk(ast.parse(src))
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
             and n.func.attr == "open" and "encoding" not in [k.arg for k in n.keywords]]
    assert not naked, f"t48② editor.py 还有不带 encoding 的 open()（读路径跟 locale 走）：行 {naked}"


def t49_cancel_reaps_child_and_pumps():
    """T6（09-26 全量审查留账）：`run_proc` 被**外部取消**时原先不收尸——两个 `except` 只管超时。
    会话 stop 走的正是 `task.cancel()`（`server/runner.py` 取消跑图任务 → `roles/*._act` → 这里），
    `CancelledError` 会直接穿过这个函数：子进程继续跑、还攥着继承去的管道，两条 pump 任务永远悬着。

    两格都是**确定性**读数（不看调度运气），子进程与 pump 任务用 spy 抓：
      ① 取消之后两条 pump 任务全部收场（`done()`）——修复前恒为假；
      ② 子进程真被判死（`returncode is not None`）——修复前恒为假（那个子进程要睡 30 秒）。
    """
    import asyncio as _aio

    async def case():
        real_exec, real_ct = _aio.create_subprocess_exec, _aio.create_task
        procs, tasks = [], []

        async def exec_spy(*a, **kw):
            p = await real_exec(*a, **kw)
            procs.append(p)
            return p

        def ct_spy(coro, **kw):
            t = real_ct(coro, **kw)
            tasks.append(t)
            return t

        _aio.create_subprocess_exec, _aio.create_task = exec_spy, ct_spy
        try:
            task = real_ct(run_proc([sys.executable, "-c", "import time; time.sleep(30)"], cwd=ROOT))
            for _ in range(100):                    # 子进程没起来就没法断言，等它现身
                if procs:
                    break
                await _aio.sleep(0.05)
            assert procs, "子进程没起来——这一格会变成空转"
            pumps = [t for t in tasks if t is not task]
            assert len(pumps) == 2, f"pump 任务数不对：{len(pumps)}"
            await _aio.sleep(0.3)                   # 让两条 pump 真挂到 read 上
            task.cancel()
            try:
                await task
            except _aio.CancelledError:
                pass
            else:
                raise AssertionError("run_proc 被取消却没抛 CancelledError")
            assert all(t.done() for t in pumps), \
                f"t49① 取消之后 pump 任务还悬着（没收尸）：{[t.done() for t in pumps]}"
            assert procs[0].returncode is not None, \
                "t49② 取消之后子进程还活着（那个要睡 30s 的）——没收尸"
        finally:
            _aio.create_subprocess_exec, _aio.create_task = real_exec, real_ct
            for p in procs:
                if p.returncode is None:
                    p.kill()

    _aio.run(case())


def t50_tool_state_is_keyed_by_session_not_project_name():
    """③（09-26 用户拍板「名字与唯一键解耦」）：常驻 shell 与 Editor 的登记键是**会话 id**，不是项目目录名。

    原先两者都 `key = CURRENT_PROJECT.get()`（`terminal.py:178`、`editor_tools.py:21`），而
    `CURRENT_PROJECT` 是**产物目录名**、同用户重名合法 ⇒ 同用户两场同名会话共用一支 `cmd.exe`
    （B 在 A 的 cwd 里执行、读到 A 的输出队列）与同一个 Editor（current_file / 行窗串台）。

    两格：
      ① 同名两场（`CURRENT_PROJECT` 相同、`CURRENT_SESSION` 不同）⇒ 拿到**不同**的 shell 与 editor，
         登记表的键是 sid；且 `close_terminal(sid1)` / `close_editor(sid1)` 只动自己那一支；
      ② 不在会话里（`CURRENT_SESSION` 空）⇒ 退回项目目录名——脚本 / 单测 / 批处理的旧行为逐字不变。
    """
    from codeharness.runtime import CURRENT_PROJECT, CURRENT_SESSION
    from codeharness.tools.libs.editor_tools import _EDITORS, close_editor, current_editor
    from codeharness.tools.libs.terminal import _TERMINALS, close_terminal, current_terminal

    for k in ("s50sid1", "s50sid2", "s50_project_name"):        # 清基线，别让残留污染读数
        _TERMINALS.pop(k, None)
        _EDITORS.pop(k, None)
    tp = CURRENT_PROJECT.set("s50_project_name")
    try:
        ts1 = CURRENT_SESSION.set("s50sid1")
        try:
            t_a, e_a = current_terminal(), current_editor()
        finally:
            CURRENT_SESSION.reset(ts1)
        ts2 = CURRENT_SESSION.set("s50sid2")
        try:
            t_b, e_b = current_terminal(), current_editor()
        finally:
            CURRENT_SESSION.reset(ts2)
        assert t_a is not t_b, "t50① 同名两场共用了一支常驻 shell（③ 未落地）"
        assert e_a is not e_b, "t50① 同名两场共用了一个 Editor（③ 未落地）"
        assert {"s50sid1", "s50sid2"} <= set(_TERMINALS), f"shell 没按 sid 登记：{sorted(_TERMINALS)}"
        assert {"s50sid1", "s50sid2"} <= set(_EDITORS), f"editor 没按 sid 登记：{sorted(_EDITORS)}"
        asyncio.run(close_terminal("s50sid1"))
        close_editor("s50sid1")
        assert "s50sid1" not in _TERMINALS and "s50sid2" in _TERMINALS, \
            f"t50① 收壳串到别人那一支：{sorted(_TERMINALS)}"
        assert "s50sid1" not in _EDITORS and "s50sid2" in _EDITORS, \
            f"t50① 销账串到别人那一支：{sorted(_EDITORS)}"
        ts3 = CURRENT_SESSION.set("")                          # ② 不在会话里
        try:
            current_terminal()
            current_editor()
        finally:
            CURRENT_SESSION.reset(ts3)
        assert "s50_project_name" in _TERMINALS and "s50_project_name" in _EDITORS, \
            f"t50② 不在会话里时没退回项目目录名（脚本/单测的旧行为断了）：{sorted(_TERMINALS)}"
    finally:
        for k in ("s50sid1", "s50sid2", "s50_project_name"):
            _TERMINALS.pop(k, None)
            _EDITORS.pop(k, None)
        CURRENT_PROJECT.reset(tp)


def t51_write_path_pins_encoding_and_newline():
    """C73/C74（09-28 全量审查批）：工具层的**写路径**必须同时钉住 `encoding` 与 `newline`。

    修前读数（探针 `E:/tmp/ch_probe_editor_bytes.py`）：`Editor.edit_file_by_replace` 改第 2 行 ⇒
    整份文件 `crlf=3 / 裸 lf=0`——文本模式默认把 `\\n` 翻译成 `os.linesep`，而这条路径是
    `readlines()` 全读 + 临时文件整份写回 + `shutil.move` 顶掉原件（`editor.py:568/595`），
    所以「改一行」=全文件 diff；写进 Linux 容器的 `#!/bin/bash\\r` 直接跑不起来。
    同族第二条：那两处 `NamedTemporaryFile("w")` 与 `Editor.write` 的 `open(...,"w")` **连 encoding
    都没有**（C55 的 AST 判据只扫 `.open()` 这一个 attr），`PYTHONUTF8=0` 的进程里会把内容按
    locale(cp936) 写出去——症状延后到下次 utf-8 读时才以 `UnicodeDecodeError` 现形。

    三格：
      ① **行尾（本机今天就有牙）**：真 `Editor.edit_file_by_replace` 改一份 **LF** 文件的中间行 ⇒
         编辑后**零个** `\\r\\n`、`\\n` 个数不变、内容文本一致（修复前 `crlf=3/裸 lf=0`）。
      ② **编码（只在非 UTF-8 进程里有牙）**：`PYTHONUTF8=0` 子进程里编辑含中文的 LF 文件 ⇒
         文件仍按 utf-8 解得回中文且仍是 LF（真部署的形状，与 t48① 同款做法）。
      ③ **结构（任何环境下的常驻守卫）**：AST 断言 `editor.py` 与 `tools/__init__.py` 的每个
         **文本写**调用（`open(...,"w"/"a")`、`NamedTemporaryFile`、`write_text`）都同时带
         `encoding=` 与 `newline=`。
    """
    import ast
    import subprocess as _sp
    import tempfile

    from codeharness.tools.libs.editor import Editor

    # ---- ① 行尾：真编辑器改一行，整份文件的行尾不许变 ----
    d = Path(tempfile.mkdtemp(prefix="s4_t51_"))
    f = d / "t.py"
    f.write_bytes("第一行中文\n第二行中文\n第三行中文\n".encode("utf-8"))     # 纯 LF
    ed = Editor(working_dir=str(d))
    ed.open_file("t.py")
    ed.edit_file_by_replace("t.py", 2, "第二行中文", 2, "第二行中文", "第二行改过了")
    raw = f.read_bytes()
    crlf, lf = raw.count(b"\r\n"), raw.count(b"\n") - raw.count(b"\r\n")
    assert (crlf, lf) == (0, 3), \
        f"t51① 编辑后行尾被改写：crlf={crlf} 裸lf={lf}（C73 复发：整份文件被换成 CRLF）字节={raw!r}"
    assert raw.decode("utf-8") == "第一行中文\n第二行改过了\n第三行中文\n", f"t51① 内容不对：{raw!r}"

    # ---- ② 非 UTF-8 模式进程：写出去还得是 utf-8（C74 的激活条件） ----
    code = (
        "import pathlib, tempfile\n"
        "from codeharness.tools.libs.editor import Editor\n"
        "d = pathlib.Path(tempfile.mkdtemp(prefix='s4_t51_sub_'))\n"
        "f = d / 'z.py'\n"
        "f.write_bytes('甲\\n乙\\n'.encode('utf-8'))\n"
        "ed = Editor(working_dir=str(d)); ed.open_file('z.py')\n"
        "ed.edit_file_by_replace('z.py', 1, '甲', 1, '甲', '甲改了')\n"
        "raw = f.read_bytes()\n"
        "assert raw.decode('utf-8') == '甲改了\\n乙\\n', raw\n"
        "assert b'\\r\\n' not in raw, raw\n"
        "print('WRITE_BYTES_OK')\n"
    )
    env = {**os.environ, "PYTHONUTF8": "0"}          # 关掉 UTF-8 模式：open() 默认编码变回 cp936
    r = _sp.run([sys.executable, "-B", "-c", code], cwd=str(ROOT), env=env,
                capture_output=True, text=True, encoding="utf-8", errors="replace")
    assert r.returncode == 0 and "WRITE_BYTES_OK" in (r.stdout or ""), \
        f"t51② PYTHONUTF8=0 下写路径没钉住编码/行尾（exit={r.returncode}）：" \
        f"{(r.stderr or r.stdout or '')[-300:]}"

    # ---- ③ 结构：三类文本写调用都要带 encoding + newline（C55 那条只扫 .open()） ----
    def bad_calls(src: str) -> list:
        out = []
        for n in ast.walk(ast.parse(src)):
            if not isinstance(n, ast.Call):
                continue
            f_ = n.func
            name = f_.attr if isinstance(f_, ast.Attribute) else (f_.id if isinstance(f_, ast.Name) else "")
            kw = {k.arg for k in n.keywords}
            args = [a.value for a in n.args if isinstance(a, ast.Constant)]
            mode = next((a for a in args if isinstance(a, str) and any(c in a for c in "wa")), "")
            is_text_write = (
                (name == "open" and bool(mode) and "b" not in mode)
                or name == "write_text"
                or name == "NamedTemporaryFile"
            )
            if is_text_write and not {"encoding", "newline"} <= kw:
                out.append((n.lineno, name, sorted(kw)))
        return out

    root = Path(__file__).resolve().parents[1] / "codeharness" / "tools"
    for rel in ("libs/editor.py", "__init__.py"):
        bad = bad_calls((root / rel).read_text(encoding="utf-8"))
        assert not bad, f"t51③ {rel} 还有没同时钉住 encoding/newline 的文本写调用（行,调用,已有keys）：{bad}"

    print(f"  ok  t51 C73/C74：编辑器改一行的字节读数 crlf={crlf} 裸lf={lf}（修复前 3/0）、"
          f"PYTHONUTF8=0 子进程写出的仍是 utf-8+LF、两文件的文本写调用全带 encoding+newline")


def t52_action_tree_is_importable_and_tier_names_have_a_home():
    """删掉 `requirement_analysis/requirement/pic2txt.py` 这座孤岛之后，把「为什么删」变成常驻不变量。

    起因（09-29 普查，R7 §1.5 的副产品）：`pic2txt.py` 四处坏引用——`:18` 从 `utils.text` 导
    `encode_image`（真身在 `utils/common.py:831`）⇒ **模块级 ImportError**；`:21` 的装饰器
    `register_tool` 早被 R8 摘掉却还在用；`:22` 的基类 `Action` 从没 import；`:96` 调
    `self._aask(prompt, images=...)` 而 `base/action.py:65` 的 `_aask` 根本没有 `images` 通道
    （`provider/gateway.py:11` 明写「源 `aask` 的 `images` 参数未搬」）。它**全仓零消费者**
    （`requirement/__init__.py` 是空的、父包只导 `evaluate_action`）⇒ 所以从没炸过，
    但 `tools/_approval.py` 的分级表里躺着 `"Pic2Txt"` 这个名字，谁哪天把那批 action 挂进注册表，
    就炸在 import 上。拍板口径：删岛（补多模态通道属另一件，本仓按能力覆盖验收、不追源文件数）。

    两格都打在**整棵 actions 树**上，不是只盯被删那一处：
      ① actions 树里**每个模块都必须 import 得起来**（接线雷区清零）；**仪器对照**：采集器必须
         真数到 ≥30 个模块——数到 0 既可能是「全修好了」也可能是采集器坏了，后者更常见。
      ② `ACTION_TIER` 里**每个名字都必须对应一个真能 import 到的 Action 类**；**仪器对照**：
         同一个判定函数必须认得出 `"WritePRD"` 有归宿、认得出 `"NoSuchActionXYZ"` 没有——
         缺了这条，「一个都没缺」这句结论可能是恒绿的空函数给的。
    """
    import importlib
    import pkgutil

    import codeharness.actions as actions_pkg

    from codeharness.tools._approval import ACTION_TIER

    mods, fails, classes = 0, [], set()
    for m in pkgutil.walk_packages(actions_pkg.__path__, actions_pkg.__name__ + "."):
        mods += 1
        try:
            mod = importlib.import_module(m.name)
        except Exception as e:                              # 不许静默跳过：这一条就是这个格存在的理由
            fails.append((m.name, f"{type(e).__name__}: {str(e)[:80]}"))
            continue
        for n in dir(mod):
            o = getattr(mod, n, None)
            if isinstance(o, type) and getattr(o, "__module__", "") == m.name:
                classes.add(n)

    assert mods >= 30, f"①仪器坏了：actions 树只数到 {mods} 个模块（现值应 ≥30），采集没跑起来谈不上「零失败」"
    assert not fails, f"①失效：actions 树里有 import 不起来的模块（接线雷区）：{fails}"

    def homeless(names):
        return sorted(n for n in names if n not in classes)

    assert homeless({"WritePRD"}) == [], "②仪器坏了：真存在的 Action 被判成无归宿（判定函数恒真）"
    assert homeless({"NoSuchActionXYZ"}) == ["NoSuchActionXYZ"], \
        "②仪器坏了：假名字没被判成无归宿（判定函数恒假，整个 ② 会恒绿）"
    loose = homeless(ACTION_TIER.keys())
    assert not loose, f"②失效：分级表里这些名字没有可达的 Action 类（免审/分级给了不存在的东西）：{loose}"

    print(f"  ok  t52 actions 树 {mods} 个模块全部可 import、分级表 {len(ACTION_TIER)} 个名字个个有归宿")


def t53_editor_generic_except_never_overwrites_source():
    """P0-1（09-30 审查批）：`Editor._edit_file_impl` 的泛捕获**不许**拿 `temp_backup_file` 盖回源文件。

    形状：那份备份只在 `enable_auto_lint` 那半支里被写过，而该字段默认关、全仓无处置真
    ⇒ 走到泛捕获时它是一个**从没写入过**的 `NamedTemporaryFile`，那次 `shutil.move(备份, 源)`
    就是把源文件清成 0 字节。而 `:601` 的原子替换要么还没发生（源文件本就完好，不需要「恢复」），
    要么已经发生（本方法手里没有任何原文可恢复）⇒ 两种情况都只能说实话，不能覆写。

    现网到达与否（先量后动）：本机 `workspace/storage/checkpoints.db`（24.8 MB）按字节 grep，
    `ERROR_GUIDANCE` 里最早那句 "This is how your edit would have looked if applied" = 0 次，
    而**同库同法**的对照能数到成功回执 44 次 / 工具名 941 次 ⇒ 有牙的干净阴性，不是无样本。
    所以这一支按「修形状」记账，不是救火。

    本轮探针另外量到两条，第一条已在本轮改掉（① 能跑到断言就是它的读数），第二条由 ⑤ 钉住：
      · 泛捕获自己会先炸：`_get_indentation_info(content, start or len(lines))` 拿**原文件**的行号去索引
        **待写入**的 `content`（append 时后者只有 1 行）⇒ `IndexError`，真错被换成一句假错。已夹住
        ——① 能跑到断言就是这条的读数（夹之前它连回执都发不出去）。
      · `enable_auto_lint=True` 那半支在本机**原本**走不到回滚：`_print_window(Path(temp_backup_file.name))`
        去读一个仍开着的临时文件 ⇒ `PermissionError`（动作序列现证在台账），被 `except IOError` 接走，
        于是编辑留在盘上、回执只说「文件处理出错」。同一段工装还量到：Windows 上 move 那个句柄是**通的**、只有按路径读它不通 ⇒ 本轮改成「先回滚，再从已回滚的源文件打『编辑前』窗口」。

    五格：
      ① 主刀：异常发生在原子替换**之前** ⇒ 源文件逐字不变 + 回执说清「未应用」，并且不冒充
         lint 支那句「你的编辑引入了语法错误」。修前这里该是 0 字节。
      ② `applied` 那半：替换**之后**才炸 ⇒ 文件保留编辑后内容 + 回执如实说「已应用、本方法不留原文」。
      ③ 阳性对照：lint 关的正常 append 仍成功（好路没被修坏，也保证 ①② 不是恒真）。
      ④ AST 双向：泛捕获支里 `move(备份→源)` = 0 处，`enable_auto_lint` 那半支里 = 1 处
         （一个恒假的判定函数会被后半条抓住，所以这格自带仪器对照）。
      ⑤ 回滚走通：lint 开且出现**新**错 ⇒ 函数正常返回、源文件被还原成原文逐字、回执同时含「编辑后」与「编辑前」两个窗口的内容。
    """
    import ast
    import shutil
    import tempfile
    from typing import ClassVar

    from codeharness.tools.libs.editor import Editor

    ORIG = "第一行\n第二行\n第三行\n".encode("utf-8")
    ADDED = "追加的一行\n".encode("utf-8")

    class _BoomBefore(Editor):
        """炸在原子替换**之前**：任何不属于 LineNumberError/FileNotFoundError/OSError/ValueError 的内部错误。"""

        @staticmethod
        def _append_impl(lines, content):
            raise RuntimeError("t53 注入：原子替换之前的内部错误")

    class _BoomAfterLint(Editor):
        """lint 开着，且炸在原子替换**之后**（第一次 `_lint_file` 正常返回新错误，第二次抛）。"""

        n: ClassVar[int] = 0

        def _lint_file(self, file_path):
            _BoomAfterLint.n += 1
            if _BoomAfterLint.n == 1:
                return ("E0 broke\nE1 NEW broke", 2)
            raise RuntimeError("t53 注入：原子替换之后的内部错误")

    def _fresh():
        d = Path(tempfile.mkdtemp(prefix="s4_t53_"))
        (d / "t.py").write_bytes(ORIG)
        return d

    def _append(ed):
        ed.open_file("t.py")
        try:
            return "returned", ed.append_file("t.py", "追加的一行\n")
        except Exception as e:
            return "raised", f"{type(e).__name__}: {e}"

    def backup_moves(node):
        """`node` 这段子树里，`shutil.move(temp_backup_file…, src_abs_path…)` 的行号。"""
        return [m.lineno for m in ast.walk(node)
                if isinstance(m, ast.Call) and ast.unparse(m.func) == "shutil.move"
                and "temp_backup_file" in ast.unparse(m.args[0])]

    dirs = []
    try:
        # ---- ① 主刀：修前这一步会把源文件清成 0 字节 ----
        d = _fresh()
        dirs.append(d)
        kind, msg = _append(_BoomBefore(working_dir=str(d)))
        now = (d / "t.py").read_bytes()
        assert kind == "raised", f"①仪器坏了：注入的异常没炸出来（{kind}）⇒ 这一格会恒绿"
        assert now == ORIG, f"①失效：替换之前就炸，源文件该逐字不变，实得 {len(now)} 字节 {now[:40]!r}"
        assert "NOT been applied" in msg, f"①回执没说清文件此刻是什么状态：{msg[:200]!r}"
        assert "RuntimeError: t53 注入" in msg, f"①真错被换成别的话了：{msg[:200]!r}"
        assert "introduced new syntax error" not in msg, \
            f"①泛捕获在冒充 lint 支的话术（这一支跟语法错误无关）：{msg[:200]!r}"

        # ---- ② applied 那半：文件已经被替换过，本方法没有原文可恢复 ----
        d = _fresh()
        dirs.append(d)
        _BoomAfterLint.n = 0
        kind, msg = _append(_BoomAfterLint(working_dir=str(d), enable_auto_lint=True))
        now = (d / "t.py").read_bytes()
        assert kind == "raised", f"②仪器坏了：替换之后的异常没炸出来（{kind}）"
        assert now == ORIG + ADDED, \
            f"②编辑已随 `:601` 落定，文件该保留**编辑后**内容（0 字节＝那步覆写又回来了），实得 {now[:60]!r}"
        assert "already applied" in msg and "keeps no copy" in msg, \
            f"②没说实话：此刻文件是改过的，却说「未应用」会把人引去重试同一笔编辑：{msg[:220]!r}"

        # ---- ③ 阳性对照：正常 append ----
        d = _fresh()
        dirs.append(d)
        kind, msg = _append(Editor(working_dir=str(d)))
        now = (d / "t.py").read_bytes()
        assert kind == "returned" and now == ORIG + ADDED, \
            f"③正常 append 被修坏了（结局 {kind}、字节 {len(now)}）：{msg[:160]!r}"

        # ---- ⑤ lint 那半支的「按设计回滚」在本机走得到（09-30 现证它原本走不到） ----
        class _LintFails(Editor):
            n: ClassVar[int] = 0

            def _lint_file(self, file_path):
                _LintFails.n += 1
                return ("E0 broke\nE1 NEW broke", 2) if _LintFails.n > 1 else ("E0 broke", 1)

        d = _fresh()
        dirs.append(d)
        _LintFails.n = 0
        ed = _LintFails(working_dir=str(d), enable_auto_lint=True)
        ed.open_file("t.py")
        try:
            kind, msg = "returned", ed.append_file("t.py", "追加的一行\n")
        except Exception as e:
            kind, msg = "raised", f"{type(e).__name__}: {e}"
        now = (d / "t.py").read_bytes()
        assert kind == "returned", \
            f"⑤回滚那一步没走通、异常冒出来了（修前就是这句 PermissionError 读仍开着的备份）：{msg[:200]!r}"
        assert now == ORIG, f"⑤lint 报错后源文件该被回滚成**原文**，实得 {len(now)} 字节 {now[:60]!r}"
        assert "Correct your edit code" in msg, f"⑤没走 lint 支的回执（这一支的话术该是它独有的）：{msg[:200]!r}"
        assert "追加的一行" in msg and "001|第一行" in msg, \
            f"⑤两个窗口该各是「编辑后」与「编辑前」的内容，实得：{msg[:260]!r}"

        # ---- ④ AST 双向（泛捕获 0 处 / lint 支 1 处） ----
        src = (Path(__file__).resolve().parents[1] / "codeharness" / "tools" / "libs" / "editor.py"
               ).read_text(encoding="utf-8")
        fn = next(n for n in ast.walk(ast.parse(src))
                  if isinstance(n, ast.FunctionDef) and n.name == "_edit_file_impl")
        generic = [x for h in fn.body if isinstance(h, ast.Try) for x in h.handlers
                   if x.type is not None and ast.unparse(x.type) == "Exception"][0]
        lint_ifs = [x for x in ast.walk(fn) if isinstance(x, ast.If) and "enable_auto_lint" in ast.unparse(x.test)]
        got_generic, got_lint = backup_moves(generic), [m for x in lint_ifs for m in backup_moves(x)]
        assert not got_generic, f"④那步覆写又回来了（泛捕获里的 备份→源 move）：行 {got_generic}"
        assert len(got_lint) == 1, \
            f"④lint 那半支是 Linux 上唯一的真回滚，不许被顺手删掉，也不许判定函数恒假：实得 {got_lint}"
    finally:
        for d in dirs:
            shutil.rmtree(d, ignore_errors=True)
        left = [str(d) for d in dirs if d.exists()]
        assert not left, f"④t53 清场没做成，留下 {left}"

    print(f"  ok  t53 P0-1：泛捕获不再拿恒空备份盖回源文件（①替换前炸→文件逐字不变、②替换后炸→保留编辑后内容"
          f"并如实说『已应用』、③正常 append 仍成功、④AST 泛捕获 0 处 / lint 支 {got_lint} 处）")


def t54_pdf_docx_read_path_no_dead_island():
    """P1（09-30 审查文档 §3）：`utils/file.py` 的 pdf/docx 读取链——岛删掉，两态必居其一。

    改前那一支叠了**三个**缺陷，前两个把第三个盖得死死的（本轮探针 `E:/tmp/p1a/probe_read.py` 才量齐）：
      ① 第一跳 `_omniparse_read_file` import 的 `codeharness.utils.omniparse_client` **不在仓里**
         （`_config_compat.py:6` 写着 `omniparse=None`，源那份按排除清单没搬）⇒ 真递一份 pdf/docx 进来就是
         `ModuleNotFoundError`，后面那个 `llama_index` 兜底**从来没走到过**（本机也没装 `llama_index`）；
      ② `from codeharness.utils import read_docx` 拿到的是**子模块**（`utils/__init__.py` 是空的、没再导出）
         ⇒ 就算过了 ①，也是 `TypeError: 'module' object is not callable`（这条是本轮量出来的新读数）；
      ③ 旧那句 `"\n".join(read_docx(...))` 是**把字符串按字符拼行**（`read_docx` 返回的已是整体 str）
         ⇒ 过了 ①②，读出来的 docx 也会变成「一个字一行」。
    修法：岛整块删（47 行，只被它自己用的卫星 import 一并走）；pdf **复用知识库摄取那条路**
    （`document.py` 的 `_require_reader` + `read_data`，`OPTIONAL_READERS` 声明的是 `pypdf`，
    缺件抛人话 `ReaderUnavailable`）；docx 保持 python-docx 那件（本机装得动、且它自带人话 ImportError）——
    **不**统一到 docx2txt：那是摄取侧的声明，换过去等于把一条现在真读得动的路改坏。

    五格：
      ① docx 在本机是**读出**态 ⇒ 逐字两段、`splitlines()` 恰好 2 行（对着 ②③ 那两个缺陷）；
      ② pdf **两态必居其一**（结构不变量，同 C26① 的判法）：装了 `pypdf` 就要读出那行 ASCII，
         没装就必须抛 `ReaderUnavailable` 且消息点名 `pypdf`——**不许**第三种（Python 原文、静默回空都算坏）；
      ③ 常驻守卫：`codeharness/utils/*.py` 里的 import（AST 走全树，**函数体内的惰性 import 也算**）
         不许指向仓里不存在的模块（岛不许从 vendor 拷回来）；
         判定函数自带阳性对照（喂一个故意写错的假名必须被抓到，否则这格恒绿）；
      ④ `file.py` 里那个 `read_docx` 名字必须是**函数**不是模块（把 ② 那个缺陷常驻钉住）；
      ⑤ 阳性对照：`.md`/`.txt` 那支不受本次改动影响。
    """
    import ast
    import importlib.util
    import tempfile
    from types import ModuleType

    from codeharness.document import ReaderUnavailable, reader_available
    from codeharness.utils import file as filemod
    from codeharness.utils.file import File

    tmp = Path(tempfile.mkdtemp(prefix="s4_t54_"))
    try:
        # ---- ① 真 .docx：两段正文，不许一字一行 ----
        import docx
        d = docx.Document()
        d.add_paragraph("第一段：周报正文")
        d.add_paragraph("第二段：图表说明")
        p_docx = tmp / "probe.docx"
        d.save(str(p_docx))
        try:
            got = asyncio.run(File.read_text_file(p_docx))
        except Exception as e:            # ②③那族缺陷的现形方式就是「在这里抛」，要红在这句上而不是裸 traceback
            raise AssertionError(f"① docx 读取抛了：{type(e).__name__}: {str(e)[:120]}") from e
        assert got == "第一段：周报正文\n第二段：图表说明", \
            f"① docx 读出的内容不对（本机装了 python-docx，这一格该走「读出」态）：{got!r}"
        assert len(got.splitlines()) == 2, \
            f"① docx 被拆成 {len(got.splitlines())} 行（旧那句 `\"\\n\".join(str)` 就是一字一行）：{got[:40]!r}"

        # ---- ② 真 .pdf：两态必居其一 ----
        stream = b"BT /F1 11 Tf 1 0 0 1 60 760 Tm (PDF probe line one) Tj ET"
        objs = [b"<< /Type /Catalog /Pages 2 0 R >>",
                b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
                b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Contents 4 0 R"
                b" /Resources << /Font << /F1 5 0 R >> >> >>",
                ("<< /Length " + str(len(stream)) + " >>\nstream\n").encode() + stream + b"\nendstream",
                b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>"]
        out = bytearray(b"%PDF-1.4\n")
        offs = []
        for n, body in enumerate(objs, 1):
            offs.append(len(out))
            out += f"{n} 0 obj\n".encode() + body + b"\nendobj\n"
        xref = len(out)
        out += f"xref\n0 {len(objs) + 1}\n".encode() + b"0000000000 65535 f \n"
        for o in offs:
            out += f"{o:010d} 00000 n \n".encode()
        out += (f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n").encode()
        p_pdf = tmp / "probe.pdf"
        p_pdf.write_bytes(bytes(out))
        have = reader_available(".pdf")
        if have:
            txt = asyncio.run(File.read_text_file(p_pdf))
            assert txt and "PDF probe line one" in txt, \
                f"② 这台机器装了 pypdf，却读不出那行 ASCII（白名单反过来说谎了）：{txt!r}"
        else:
            try:
                asyncio.run(File.read_text_file(p_pdf))
                raise AssertionError("② 缺组件的机器上 pdf 竟然安静地返回了——C26 禁的第三种")
            except AssertionError:
                raise
            except ReaderUnavailable as e:
                msg = str(e)
                assert "pypdf" in msg, f"② 拒收那句没点名缺哪个组件：{msg!r}"
                for bad in ("ModuleNotFoundError", "omniparse_client", "llama_index", "Traceback"):
                    assert bad not in msg, f"② 拒收那句里混进了 Python 原文（{bad}）：{msg!r}"
            except Exception as e:                    # 第三种「换了个报错」也算坏（变异刀的落点要能指名）
                raise AssertionError(
                    f"② 缺组件时该抛 ReaderUnavailable（人话），实得 {type(e).__name__}: {str(e)[:120]}") from e

        # ---- ③ 模块级 import 不许指向仓里不存在的模块 ----
        def dead_repo_imports(src: str) -> list:
            dead = []
            for n in ast.walk(ast.parse(src)):
                if isinstance(n, ast.ImportFrom) and (n.module or "").startswith("codeharness."):
                    if importlib.util.find_spec(n.module) is None:
                        dead.append((n.lineno, n.module))
                elif isinstance(n, ast.Import):
                    for al in n.names:
                        if al.name.startswith("codeharness.") and importlib.util.find_spec(al.name) is None:
                            dead.append((n.lineno, al.name))
            return dead

        probe = dead_repo_imports("from codeharness.utils.definitely_not_here import x\n")
        assert probe == [(1, "codeharness.utils.definitely_not_here")], \
            f"③仪器坏了：假模块名没被抓出来（这格会恒绿），实得 {probe}"
        root = Path(__file__).resolve().parents[1] / "codeharness" / "utils"
        offenders = {p.name: dead_repo_imports(p.read_text(encoding="utf-8"))
                     for p in sorted(root.glob("*.py"))}
        offenders = {k: v for k, v in offenders.items() if v}
        assert not offenders, f"③ 这些 import（含函数体内的惰性 import）指向仓里不存在的东西：{offenders}"

        # ---- ④ read_docx 是函数，不是子模块 ----
        rd = getattr(filemod, "read_docx", None)
        assert rd is not None and not isinstance(rd, ModuleType) and callable(rd), \
            f"④ `file.py` 里的 read_docx 不是函数（旧写法拿到的是子模块 ⇒ 调用即 TypeError）：{rd!r}"

        # ---- ⑤ 阳性对照：文本那支没被动过 ----
        p_md = tmp / "note.md"
        p_md.write_text("# 标题\n正文一行\n", encoding="utf-8")
        got5 = asyncio.run(File.read_text_file(p_md))
        assert got5 == "# 标题\n正文一行\n", f"⑤ .md 读路径被改坏了：{got5!r}"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        for _ in range(10):
            if not tmp.exists():
                break
            time.sleep(0.2)
        assert not tmp.exists(), f"④t54 清场没做成，{tmp} 还在"

    print(f"  ok  t54 P1：pdf/docx 读链的死岛删净（docx 读出两段不多不少一字一行=否、"
          f"pdf 本机走「缺组件人话」那态={not have}、utils 模块级 import 零死引用、read_docx 是函数、.md 支不受影响")


def t55_bugfix_ticket_survives_model_failure():
    """C102（09-30 审查文档 §3）：BUGFIX 工单不许在模型调用**之前**被删——同 C97 那一族。

    现证（改前两处锚点本轮 18:5x 逐字回读）：`actions/write_code.py:93` 读到工单、`:96` 当场
    `unlink(missing_ok=True)`（注释写着源 :163「防止冲突」），而模型调用是 `:114` 那句
    `rsp = await self._aask(prompt)`（`:110-113` 是 prompt 的 format 实参行；审查文档的 `:96`/`:114`
    两处都对得上；我草稿里先前写 `:113` 是自己少数一行，已按现值改回）
    ⇒ `_aask` 一失败（超时/端点挂/落盘 raise）那份工单就永久没了：下次重试没有工单，
    `write_code_plan_and_change.py:146` 的 `issue_doc` 也读不到。「防止冲突」（源 :163）要的是
    **本次消费掉**，不是**本次机会烧掉**。

    修法：删除挪到「产物已落盘」之后（`s6_sop.py:338` 那句 `assert not ...exists()` 仍是正向断言，
    语义一字不改），失败那支加一句可 grep 的 warning 点名「工单未消费、仍留在 DOCS」，异常照抛不吞。

    四格：
      ① 失败路 ⇒ 工单**还在** + 异常照抛 + warning 那句有；
      ② 成功路 ⇒ 产物落盘、工单被消费掉、且 prompt 里**带过**工单文本（防「删了但从没用过」这种假消费）；
      ③ AST 顺序守卫：钉的是**两次调用的先后**——`run` 里 `unlink(BUGFIX…)` 那次 Call 的行号必须大于
         `await self._aask(...)` 那次 Await 的行号（不钉具体某一行，否则仪器刀会因为选点而假绿）；
         且判定函数自带阳性对照（把 unlink 人为挪回 `_aask` 之前，判定必须翻红，否则这格是恒绿摆设）；
      ④ 无工单的成功路（阳性对照）⇒ 不炸、产物照样落盘、且不产生那句 warning。
    """
    import ast
    import io
    import tempfile

    from codeharness.actions.write_code import WriteCode
    from codeharness.configs.settings import settings
    from codeharness.const import BUGFIX_FILENAME, DocName, RepoName
    from codeharness.document_store.artifact_store import ArtifactStore
    from codeharness.logs import logger as _real_logger
    from codeharness.provider.fake import FakeLLM
    from codeharness.runtime import CURRENT_PROJECT
    from codeharness.schema import Document, Message
    import codeharness.actions.write_code as wc

    class _Boom(WriteCode):
        """模型那一发失败：`_aask` 是 BaseAction 的方法，子类覆盖最省事（不碰真实网关）。"""

        async def _aask(self, prompt, stream=True, **kw):
            raise RuntimeError("t55 注入：模型端点挂了")

    class _Rec:
        def __init__(self):
            self.warnings = []

        def warning(self, m):
            self.warnings.append(str(m))

        def __getattr__(self, name):
            fn = getattr(_real_logger, name)

            def wrap(*a, **k):
                return fn(*a, **k)
            return wrap

    base = Path(tempfile.mkdtemp(prefix="s4_t55_"))
    keep_ws, keep_proj = settings.workspace_root, CURRENT_PROJECT.get()
    settings.workspace_root = str(base)
    CURRENT_PROJECT.set("t55_proj")
    saved_logger, wc.logger = wc.logger, _Rec()

    def _trigger():
        return Message(content="写 main.py", role="user",
                       instruct_content={"filename": "main.py"}, instruct_schema="CodingContext")

    try:
        store = ArtifactStore.active()

        # ---- ① 失败路：工单必须还在 ----
        asyncio.run(store.save(RepoName.DOCS, Document(filename=BUGFIX_FILENAME, content="程序启动就崩")))
        ticket = store.root / RepoName.DOCS / BUGFIX_FILENAME
        assert ticket.exists(), "①夹具坏了：工单没写进去（这一格会恒真）"
        try:
            asyncio.run(_Boom(llm=FakeLLM(["不该被调用"])).run(_trigger()))
            raise AssertionError("①模型失败被吞掉了（那就不叫失败路）")
        except AssertionError:
            raise
        except RuntimeError as e:
            assert "t55 注入" in str(e), f"①抛的不是注入的那发：{e}"
        assert ticket.exists(), \
            "①失效：模型失败之后工单没了——下次重试没有工单，PlanAndChange 也读不到 issue_doc"
        assert any("BUGFIX" in w and "未消费" in w for w in wc.logger.warnings), \
            f"①失败没留可 grep 的一声（C97 同族要求）：{wc.logger.warnings}"

        # ---- ② 成功路：消费掉，且真的用过 ----
        wc.logger.warnings.clear()
        asyncio.run(store.save(RepoName.DOCS, Document(filename=BUGFIX_FILENAME, content="崩溃在 main 的 --version")))
        llm = FakeLLM(["```python\nprint('v2')\n```"])
        out = asyncio.run(WriteCode(llm=llm).run(_trigger()))
        assert "已写 main.py" in out.content, f"②产物没落盘：{out.content[:80]}"
        assert (store.root / RepoName.SRC / "main.py").exists(), "②src 里找不到 main.py"
        assert not (store.root / RepoName.DOCS / BUGFIX_FILENAME).exists(), \
            "②成功之后工单还留着——「防止冲突」那半语义丢了（`s6_sop.py:338` 同一条）"
        assert "崩溃在 main 的 --version" in str(llm.calls[0]), \
            "②工单从没进过 prompt，那删除就是「销毁证据」而不是「消费」"

        # ---- ③ AST 顺序守卫 + 自带阳性对照 ----
        src = (Path(__file__).resolve().parents[1] / "codeharness" / "actions" / "write_code.py"
               ).read_text(encoding="utf-8")

        def ticket_order(text):
            """返回 (工单 unlink 行号, _aask 行号)；缺一处就报错，不返回「看起来没问题」。"""
            u = [n.lineno for n in ast.walk(ast.parse(text))
                 if isinstance(n, ast.Call) and "BUGFIX_FILENAME" in ast.unparse(n) and "unlink" in ast.unparse(n.func)]
            a = [n.lineno for n in ast.walk(ast.parse(text))
                 if isinstance(n, ast.Await) and "_aask" in ast.unparse(n.value.func)]
            assert len(u) == 1 and len(a) == 1, f"锚点行不唯一：unlink@{u} aask@{a}"
            return u[0], a[0]

        u, a = ticket_order(src)
        assert u > a, f"③工单的 unlink@{u} 排在模型调用 aask@{a} 之前＝证据先没，判据该红"
        # 阳性对照：同一判定喂「先删后调」的形状，必须翻红（否则上面那条 u>a 是恒绿的摆设）。
        # 这里**不**对真源码做文字搬移——刀切出来的形状若非合法 Python，红在 IndentationError
        # 只说明刀坏了，说明不了判定有牙。合成源只要能让 ticket_order 数出两个行号就够。
        synthetic = (
            "async def run(self):\n"
            "    (store.root / RepoName.DOCS / BUGFIX_FILENAME).unlink(missing_ok=True)\n"
            "    rsp = await self._aask(prompt)\n"
        )
        su, sa = ticket_order(synthetic)
        assert su < sa, f"③仪器坏了：『先删后调』这一形状没被同一判定抓出（{su} vs {sa}）"

        # ---- ④ 无工单的成功路 ----
        wc.logger.warnings.clear()
        llm4 = FakeLLM(["```python\nprint('no-ticket')\n```"])
        out4 = asyncio.run(WriteCode(llm=llm4).run(_trigger()))
        assert "已写 main.py" in out4.content, f"④没有工单时写码路被改坏：{out4.content[:80]}"
        assert not any("未消费" in w for w in wc.logger.warnings), \
            f"④没有工单也去喊那一声（warning 该只在真放弃工单时出现）：{wc.logger.warnings}"
    finally:
        wc.logger = saved_logger
        settings.workspace_root = keep_ws
        CURRENT_PROJECT.set(keep_proj)
        shutil.rmtree(base, ignore_errors=True)
        for _ in range(10):
            if not base.exists():
                break
            time.sleep(0.2)
        assert not base.exists(), f"t55 清场没做成，{base} 还在"

    print(f"  ok  t55 C102：失败路工单仍在 DOCS 且留了 warning、成功路消费掉且 prompt 里用过它、"
          f"AST 顺序 unlink@{u} > aask@{a}（仪器对照会翻红）、无工单路不喊冤")


def t56_model_supplied_path_keys_are_gated():
    """C105（09-30 审查文档 §3）：模型可控的**路径键**必须进审批判据——`_PATH_KEYS` 漏一个键＝免审开一道口子。

    现场坐实（本轮 19:0x 直调真函数，零门禁零 db15）：
        writes_inside_workspace({'readme_path': 'C:/Windows/win.ini', ...}) -> True
    ⇒ `_approval.py:89`「`workspace_write` 且路径越界才升级到 full 审批」那句**永不触发**。
    三处同形（都挂在 `workspace_write` 档）：`edge_actions.py:24`（`readme_path` 真 `read_text` 任意文件）、
    `rebuild_class_view.py:31`（`repo_path` 真当 `RepoParser` 的 `base_directory` 扫任意目录）、
    `invoice_ocr.py:24`（`image_path` 只交给 `ocr_provider`，本机未注入 ⇒ 当下不可利用，仍按同族登记）；
    另有第四处 `import_repo.py:54` 的 `repo_path`，但 ImportRepo 在 `full_access` 档（本来就要审批）⇒ 一并登记只为「键表完整」。
    形状扫描实测：**改前 4 处调用点／3 个键名**，补齐之后**零漏网**（探测器与数字同一份源码，不凭记忆）。
    ⚠ 探测器第一版有洞：`(msg.instruct_content or {}).get("image_path")` 这种「BoolOp 套 get」它认不出接收者，
    当场漏掉 `invoice_ocr`/`rebuild_class_view` 两处 ⇒ 判据会假绿。现在先 `while isinstance(node, ast.BoolOp)`
    剥壳再取 `Attribute.attr`/`Name.id`，并把这三种接收者形态（裸名 / 属性 / BoolOp）都写进仪器对照。

    根因不是「少列了一个名字」，是 `writes_inside_workspace` 的**默认放行**：认不出的键一律按
    「不带路径的写类件」放过（那句 `return True`）。所以两处都要动：① 把三个键补进表；
    ② 判据按形状扫，下一棒再加新键名而忘了登记就当场红。

    三格：
      ① 判定函数三档都要对：越界路径键 ⇒ False（要升级）；会话根内的路径 ⇒ True；
         **完全不带路径键 ⇒ True**（这条是阳性对照——一刀切成「一律升级」会把 WritePRD 那类全打掉）；
      ② 形状扫描：`actions/*.py` 里凡是 `fic.get("<键>")` / `(msg.instruct_content or {}).get("<键>")`
         且键名以 `_path` 结尾的，必须出现在 `_PATH_KEYS` 里；
      ③ ②的仪器对照：同样判定喂一段含未登记键的假源码必须**抓到**（否则 ② 是恒绿摆设）。
    """
    import ast

    from codeharness.runtime import CURRENT_PROJECT, CURRENT_SESSION
    from codeharness.tools._approval import ACTION_TIER, _PATH_KEYS, writes_inside_workspace as wiw

    # ---- ① 三档 ----
    tp = CURRENT_PROJECT.set("s56_proj")
    ts = CURRENT_SESSION.set("s56sid")
    try:
        assert wiw({"readme_path": "C:/Windows/win.ini"}) is False, \
            "①`readme_path` 指到系统文件还判「在工作区内」——那 `:89` 的升级永不发生"
        assert wiw({"repo_path": "a/b"}) is True, "①会话根内的相对路径被误判越界（会把正常动作全送去审批）"
        assert wiw({}) is True, "①不带路径键的写类件该按表里定的档走（阳性对照，别一刀切）"
        assert wiw({"path": "../outside.py"}) is False, "①既有键 `path` 的越界判定被改坏了（t2/t3 同族）"
        for name in ("ExtractReadMe", "RebuildClassView", "InvoiceOCR"):
            assert ACTION_TIER[name] == "workspace_write", \
                f"①这三件的档位不是 workspace_write（那这条判据的靶子变了）：{name}={ACTION_TIER[name]}"
    finally:
        CURRENT_SESSION.reset(ts)
        CURRENT_PROJECT.reset(tp)

    # ---- ②③ 形状扫描 + 仪器对照 ----
    def receiver_name(node):
        """把 `fic` / `msg.instruct_content` / `(msg.instruct_content or {})` 三种接收者归约成一个名字。

        ⚠ 不剥 BoolOp 就认不出 `(x or {}).get("image_path")` 这一形——探测器第一版正是这样漏掉
        `invoice_ocr`/`rebuild_class_view` 两处，让 ② 变成假绿的。
        """
        while isinstance(node, ast.BoolOp):
            node = node.values[0]
        if isinstance(node, ast.Attribute):
            return node.attr
        if isinstance(node, ast.Name):
            return node.id
        return None

    def unregistered_path_keys(src: str) -> list:
        """取 `fic.get("x_path")` / `(msg.instruct_content or {}).get("x_path")` / `params.get(...)` 这类模型可控键。"""
        out = []
        for n in ast.walk(ast.parse(src)):
            if not (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "get"):
                continue
            if not (n.args and isinstance(n.args[0], ast.Constant) and isinstance(n.args[0].value, str)):
                continue
            base = receiver_name(n.func.value)
            key = n.args[0].value
            if base in ("fic", "instruct_content", "params") and key.endswith("_path") and key not in _PATH_KEYS:
                out.append((n.lineno, key))
        return out

    ctl = [unregistered_path_keys('fic = {}\nx = fic.get("evil_path")\n'),
           unregistered_path_keys('msg = 1\nx = (msg.instruct_content or {}).get("deep_path")\n'),
           unregistered_path_keys('params = {}\nx = params.get("wide_path")\n')]
    assert [c[0][1] for c in ctl] == ["evil_path", "deep_path", "wide_path"], \
        f"③仪器坏了：三种接收者形态没被全抓出来（②会恒绿），实得 {ctl}"
    assert unregistered_path_keys('fic = {}\nx = fic.get("readme_path")\n') == [], \
        "③仪器反了：已登记的键也被判成漏网（②会恒红）"
    root = Path(__file__).resolve().parents[1] / "codeharness" / "actions"
    gaps = {p.name: unregistered_path_keys(p.read_text(encoding="utf-8")) for p in sorted(root.glob("*.py"))}
    gaps = {k: v for k, v in gaps.items() if v}
    assert not gaps, f"②这些模型可控的路径键没进 `_PATH_KEYS`（＝免审越界读/扫）：{gaps}"
    print("  ok  t56 C105：readme_path/repo_path/image_path 已纳进审批判据（越界→升级、根内→放行、"
          "无路径键→按档走三档各对），形状扫描抓到器内假键、放过已登记键，全 actions/ 零漏网")


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
              t47_new_held_out_after_desc_normalization,
              t48_editor_read_path_is_locale_independent, t49_cancel_reaps_child_and_pumps,
              t50_tool_state_is_keyed_by_session_not_project_name,
              t51_write_path_pins_encoding_and_newline,
              t52_action_tree_is_importable_and_tier_names_have_a_home,
              t53_editor_generic_except_never_overwrites_source,
              t54_pdf_docx_read_path_no_dead_island,
              t55_bugfix_ticket_survives_model_failure,
              t56_model_supplied_path_keys_are_gated]
    from _gatecov import run_all, verdict
    skipped, silent = run_all(checks, ok_line=True)
    for _ in range(20):
        shutil.rmtree(BASE, ignore_errors=True)   # Windows：刚被 taskkill 的句柄要几百毫秒才释放
        if not BASE.exists():
            break
        time.sleep(0.25)
    leftovers = [str(p.relative_to(BASE)) for p in BASE.rglob("*")] if BASE.exists() else []
    assert not BASE.exists(), f"自测留下了句柄或文件: {leftovers[:8]}"
    print(f"\nS4 门禁通过：{len(checks)} 组 —— 注册表 6 组（全名册 EXPECTED_TOOLS 19 只登记/名字与 tag 并集去重/"
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
          f"t47 新 held-out）**各格现值只印在自己的输出行里，这里不复述**——这行手抄过两次数、漂了两次）"
          f" + C73/C74 写路径 3 组（t51：编辑一行不许改整份文件行尾/PYTHONUTF8=0 子进程写出仍 utf-8+LF/两类文件的文本写调用都带 encoding+newline） + P0-1 编辑器异常恢复 1 组（t53：泛捕获不许拿恒空备份盖回源文件/applied 两半各说实话/AST 双向钉住 lint 支那处唯一合法回滚还在） + P1 读链死岛 1 组（t54：pdf/docx 不再先跳不存在的 omniparse_client、缺组件走人话那态、utils 模块级 import 零死引用、read_docx 是函数不是模块） + P1 工单先删后调 1 组（t55：模型失败路工单必须还在且留 warning、成功路才消费、AST 顺序 unlink 排在 aask 之后并自带翻红对照） + C105 审批路径键 1 组（t56：模型可控的 `readme_path`/`repo_path`/`image_path` 必须纳进 `_PATH_KEYS`，越界→升级、根内→放行、无路径键→按档三档各对，形状扫描按三种接收者形态自证仪器）")
    print(f"S4 覆盖率：{len(checks)} 组里真判 {len(checks) - len(skipped) - len(silent)} 组"
          + verdict(skipped, silent))


if __name__ == "__main__":
    main()
