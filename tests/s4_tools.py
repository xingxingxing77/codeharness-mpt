"""S4 门禁：工具注册表（R8）、路径越界防护、上报接缝、沙箱执行、联网全 mock。

跑法：
  cd /e/Codeharness && PYTHONPATH=/e/Codeharness PYTHONIOENCODING=utf-8 F:/anaconda/python.exe tests/s4_tools.py

断言打在哪（docs 陷阱 #2 要求自证）：全部打在真实实现上——真起子进程、真写临时目录、
真调 `_safe()`。唯一被替换的是 DuckDuckGo（t10/t11 打在注入的 stub 上），因为门禁必须零外网。
工具层不碰 LLM，所以这里没有 FakeLLM 回放。
"""
import asyncio
import importlib
import shutil
import sys
import tempfile
from pathlib import Path

from codeharness.configs.settings import settings
from codeharness.logs import set_tool_output_logfunc
from codeharness.report import BlockType
from codeharness.runtime import REPORT_SINK
from codeharness.schema import RunCodeContext
from codeharness.tools import (REGISTRY, _root, _safe, execute_shell_async, read_file,
                               search_internet, write_file)
from codeharness.tools.sandbox import run_context, run_python_code

BASE = Path(tempfile.mkdtemp(prefix="s4gate_"))
WS = BASE / "ws"
WS.mkdir()
settings.workspace_root = str(WS)
PY = f'"{sys.executable}"'


def t1_registry_items_are_langchain_tools():
    names = [t.name for t in REGISTRY]
    assert len(names) == len(set(names)) == 4, names
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


def main():
    checks = [t1_registry_items_are_langchain_tools, t2_sibling_prefix_escape,
              t3_parent_and_absolute_escape, t4_write_read_roundtrip_creates_dirs,
              t5_missing_path_and_no_sink_do_not_raise, t6_editor_block_reaches_sink,
              t7_tool_output_log_slot_fires, t8_shell_runs_in_workspace_root,
              t9_shell_output_truncated, t10_search_uses_stub_and_truncates,
              t11_search_degrades_on_failure, t12_sandbox_runs_and_reports_exit_code,
              t13_run_context_honors_working_directory,
              t14_default_workdir_and_scratch_stay_inside]
    for c in checks:
        c()
        print(f"  ok  {c.__name__}")
    shutil.rmtree(BASE, ignore_errors=True)
    assert not BASE.exists()
    print(f"\nS4 门禁通过：{len(checks)} 组 —— 注册表 1 组 + 越界防护 3 组（兄弟目录前缀回归/父目录与绝对路径/"
          f"scratch 收口）+ 接缝 3 组（无 sink 不抛 / editor 块达 sink / 工具日志槽）+ shell 2 组 + "
          f"搜索 2 组（零外网 stub 与降级）+ 沙箱 3 组（退出码/超时/工作目录）")


if __name__ == "__main__":
    main()
