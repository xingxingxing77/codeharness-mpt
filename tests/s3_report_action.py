"""S3(a) 门禁：Reporter 类族（R6）+ 字段级定向重试（R2）。

⚠ 本文件只覆盖 S3 中与预算改造不冲突的两块。R3（编排条件边）、R4a（checkpointer）、
R5（interrupt/resume）要等 `team_graph.py`/`team.py`/`runner.py` 上的预算清理落地后再接，
届时补 `tests/s3b_runtime.py`，不要以为 S3 已全绿。

跑法：
  cd /e/Codeharness && PYTHONPATH=/e/Codeharness PYTHONIOENCODING=utf-8 F:/anaconda/python.exe tests/s3_report_action.py
"""
import json
from pathlib import PurePath
from uuid import UUID

from pydantic import BaseModel

import codeharness.report as RP
from codeharness.base.action import BaseAction, _is_empty
from codeharness.provider.fake import FakeLLM
from codeharness.runtime import REPORT_SINK

import asyncio


def _fail(m):
    raise AssertionError(m)


def _collect():
    """把报道槽接到一个列表上，返回捕获到的事件。"""
    events = []
    REPORT_SINK.set(events.append)
    return events


# ================= R6：12 个 Reporter 类名与签名 =================
ALL_REPORTERS = ["ResourceReporter", "TerminalReporter", "BrowserReporter", "ServerReporter",
                 "ObjectReporter", "TaskReporter", "ThoughtReporter", "FileReporter",
                 "NotebookReporter", "DocsReporter", "EditorReporter", "GalleryReporter"]
EXPECT_BLOCK = {"TerminalReporter": "Terminal", "TaskReporter": "Task", "BrowserReporter": "Browser",
                "ServerReporter": "Browser-RT", "ObjectReporter": "Task", "ThoughtReporter": "Thought",
                "FileReporter": "Docs", "NotebookReporter": "Notebook", "DocsReporter": "Docs",
                "EditorReporter": "Editor", "GalleryReporter": "Gallery"}
NAME_DEFAULT = {"TerminalReporter": "cmd", "ServerReporter": "local_url", "ObjectReporter": "object",
                "TaskReporter": "object", "ThoughtReporter": "object", "FileReporter": "path",
                "GalleryReporter": "path", "BrowserReporter": "url"}


def t1_class_surface():
    missing = [n for n in ALL_REPORTERS if not hasattr(RP, n)]
    if missing:
        _fail(f"1. 缺 Reporter 类: {missing}")
    for cls, block in EXPECT_BLOCK.items():
        obj = getattr(RP, cls)()                 # 必须能免参构造（调用方写法是 XReporter()）
        if str(obj.block) != block and obj.block.value != block:
            _fail(f"1. {cls}.block = {obj.block!r} 应为 {block!r}")
    for name in ("report", "async_report", "_format_data", "set_report_fn", "set_async_report_fn",
                 "wait_llm_stream_report"):
        if not hasattr(RP.ResourceReporter, name):
            _fail(f"1. ResourceReporter 缺方法 {name}")
    for cls, default in NAME_DEFAULT.items():
        params = __import__("inspect").signature(getattr(RP, cls).report).parameters
        if "name" not in params or params["name"].default != default:
            _fail(f"1. {cls}.report 的 name 默认值应为 {default!r}，实为 {params.get('name')}")


def t2_blocktype_vocabulary():
    got = [b.value for b in RP.BlockType]
    # 第十值 `ToolCall` 是 09-23 有意扩的：只读类工具（read_file/grep/find_file/编辑器读）
    # 原本一个块都不发，对话流里"每步一行"无从谈起。扩词汇表的代价由两处兜住：
    # s8 t1 要求 `ChatNode/ToolCard` 里有 `type === 'ToolCall'` 分发分支（少一支就会整体降级灰块），
    # `frontend/scripts/check_tool_row.mjs` 钉住动词/摘要派生与图标名可在别名表取到。
    want = ["Terminal", "Task", "Browser", "Browser-RT", "Editor", "Gallery", "Notebook", "Docs",
            "Thought", "ToolCall"]
    if got != want:
        _fail(f"2. BlockType 词汇表被改动: {got} != {want}（前端会整体降级为灰色 GenericBlock）")
    if RP.END_MARKER_NAME != "end_marker":
        _fail("2. end_marker 名称变了，前端无法收块")


def t3_payload_shape():
    ev = _collect()
    rep = RP.TaskReporter()
    rep.report({"tasks": [], "current_task_id": "T1"}, "object")
    if len(ev) != 1:
        _fail(f"3. 未写入报道槽: {ev!r}")
    d = ev[0]
    if set(d) != {"block", "uuid", "name", "value", "role"}:
        _fail(f"3. 载荷字段与源不一致: {sorted(d)}")
    if d["block"] != "Task" or d["name"] != "object":
        _fail(f"3. block/name 错: {d['block']!r} {d['name']!r}")
    # 同一实例多次上报必须共用一个 uuid（前端按 uuid 归并成一个块）
    rep.report({"x": 1}, "object")
    if ev[1]["uuid"] != d["uuid"]:
        _fail("3. 同实例两次上报的 uuid 不一致，前端会裂成多个块")
    # 不同实例必须不同 uuid
    if RP.TaskReporter().uuid == rep.uuid:
        _fail("3. 不同实例的 uuid 撞了")
    if not isinstance(rep.uuid, UUID):
        _fail(f"3. uuid 字段类型应为 UUID（源如此），实为 {type(rep.uuid).__name__}")


def t4_path_absolute():
    ev = _collect()
    RP.EditorReporter().report("docs/design.md", "path")
    got = ev[-1]["value"]
    # 判「是不是绝对路径」用 Path.is_absolute：abspath 原样继承 cwd 的盘符大小写，
    # 本机不同启动器给出的 cwd 有 E:/e: 两种（WorkBuddy bash = 小写），按前缀判会假红
    if not PurePath(got).is_absolute():
        _fail(f"4. name=path 时未转绝对路径（前端文件树取不到）: {got!r}")
    data = RP.DocsReporter()._format_data("a/b.md", "path", None)
    if "extra" in data:
        _fail("4. extra=None 时不应出现 extra 键")
    data2 = RP.DocsReporter()._format_data({"k": 1}, "object", {"m": 1})
    if data2.get("extra") != {"m": 1}:
        _fail(f"4. extra 未带上: {data2}")


def t5_context_manager_and_hooks():
    ev = _collect()
    with RP.TaskReporter() as r:
        r.report({"a": 1}, "object")
    if ev[-1]["name"] != "end_marker":
        _fail(f"5. 同步 with 退出未发 end_marker: {ev[-1]}")

    async def _a():
        async with RP.DocsReporter() as d:
            await d.async_report("正文", "content")
    asyncio.run(_a())
    if ev[-1]["name"] != "end_marker":
        _fail("5. 异步 with 退出未发 end_marker")

    seen = []
    orig = RP.ObjectReporter._report
    RP.ObjectReporter.set_report_fn(lambda self, v, n, extra=None: seen.append((n, v)))
    try:
        RP.TaskReporter().report({"z": 1}, "object")
    finally:
        RP.ObjectReporter._report = orig
    if seen != [("object", {"z": 1})]:
        _fail(f"5. set_report_fn 未生效: {seen!r}")


def t6_llm_stream_bridge():
    """enable_llm_stream=True 时，日志流队列里的片段应作为 content 事件上报。"""
    from codeharness.logs import get_llm_stream_queue
    ev = _collect()

    async def _a():
        async with RP.ThoughtReporter(enable_llm_stream=True) as t:
            q = get_llm_stream_queue()
            await q.put("第一段")
            await q.put(None)
    asyncio.run(_a())
    names = [e["name"] for e in ev]
    if "content" not in names or ev[-1]["name"] != "end_marker":
        _fail(f"6. LLM 流未桥接到 content 事件: {names!r}")


# ================= R2：字段级定向重试 =================
class PRD(BaseModel):
    project_name: str = ""
    language: str = ""
    patterns: list = []
    reason: str = ""


class PRDAction(BaseAction):
    output_schema = PRD


def t7_retry_targets_only_missing():
    plays = [json.dumps({"project_name": "2048", "language": "Python"}),        # 缺 patterns/reason
             json.dumps({"patterns": ["grid", "merge"], "reason": "教学用"})]
    llm = FakeLLM(responses=plays)
    a = PRDAction(llm=llm, name="WritePRD")
    out = asyncio.run(a._structured("做个2048"))

    if out.project_name != "2048" or out.patterns != ["grid", "merge"]:     # 合并必须两边都在
        _fail(f"7. 合并结果不完整: {out.model_dump()}")
    if len(llm.calls) != 2:
        _fail(f"7. 应恰好 2 次调用（整结构 + 定向补问），实为 {len(llm.calls)} —— 退化成整份重发？")
    patch = llm.calls[-1]
    # 补问要分两段：只补缺的字段进 "Missing fields"，已填内容进 "Already filled"（防止模型重做）
    def section(text, title):
        rest = text.split(title, 1)
        if len(rest) < 2:
            return ""
        return rest[1].split("\n##", 1)[0]

    miss_blk = section(patch, "## Missing fields")
    if "patterns" not in miss_blk or "reason" not in miss_blk:
        _fail(f"7. 缺失字段没进补问清单:\n{patch}")
    if "project_name" in miss_blk or "language" in miss_blk:
        _fail(f"7. 已填字段混进了 Missing fields，等于整份重发:\n{miss_blk}")
    filled_blk = section(patch, "## Already filled")
    if "2048" not in filled_blk:
        _fail(f"7. 补问没带上已填内容，模型会重做已完成部分:\n{patch}")


def t8_no_retry_when_complete():
    llm = FakeLLM(responses=[json.dumps({"project_name": "x", "language": "y",
                                        "patterns": ["p"], "reason": "r"})])
    out = asyncio.run(PRDAction(llm=llm)._structured("p"))
    if len(llm.calls) != 1:
        _fail(f"8. 字段齐全时不该再问，实为 {len(llm.calls)} 次")
    if out.reason != "r":
        _fail("8. 结果被改写")


def t9_empty_semantics():
    # 空串/空列表/空白串算缺；0 与 False 不算缺（别让数字型字段被反复追问）
    for v in (None, "", "   ", [], {}, set()):
        if not _is_empty(v):
            _fail(f"9. {v!r} 应判为空")
    for v in (0, False, [0], {"a": ""}):
        if _is_empty(v):
            _fail(f"9. {v!r} 不应判为空")
    llm = FakeLLM(responses=[json.dumps({"project_name": "x", "language": "", "patterns": [], "reason": ""}),
                             json.dumps({"language": "Go"}),      # 第二轮只补上一个
                             json.dumps({"language": "Go"})])      # 第三轮仍缺 reason/patterns → 到上限止损
    a = PRDAction(llm=llm)
    a.max_field_retries = 2
    out = asyncio.run(a._structured("p"))
    if len(llm.calls) > 3:
        _fail(f"9. 未在重试上限处止损，调了 {len(llm.calls)} 次")
    if out.language != "Go":
        _fail(f"9. 部分补上的字段没合并进来: {out.model_dump()}")


def t10_merge_never_clobbers():
    """补丁里的空值不得覆盖已填内容。"""
    class One(BaseModel):
        a: str = ""
        b: str = ""

    class A(BaseAction):
        output_schema = One

    llm = FakeLLM(responses=[json.dumps({"a": "keep", "b": ""}), json.dumps({"a": "", "b": "new"})])
    out = asyncio.run(A(llm=llm)._structured("p"))
    if out.model_dump() != {"a": "keep", "b": "new"}:
        _fail(f"10. 合并把已填值冲掉了: {out.model_dump()}")


def t11_partial_schema_keys():
    keys = set(PRDAction._partial(PRD, ["patterns", "reason"]).model_fields)
    if keys != {"patterns", "reason"}:
        _fail(f"11. 部分模型键集不对: {keys}")
    if PRDAction._empty_fields(PRD, PRD(project_name="x")) != ["language", "patterns", "reason"]:
        _fail("11. 缺失字段列表不对或顺序不定")


def t12_plain_text_path_untouched():
    """output_schema 为 None 时必须仍是纯文本路径，不被重试逻辑影响。"""
    class Plain(BaseAction):
        pass
    llm = FakeLLM(responses=["just text"])
    if asyncio.run(Plain(llm=llm)._structured("p")) != "just text":
        _fail("12. 无 schema 的退化路径坏了")
    if asyncio.run(Plain(llm=FakeLLM(responses=["t"]))._aask("p")) != "t":
        _fail("12. _aask 坏了")


def t13_artifact_filename_gate():
    """真模型实测的两条崩法：任务条目缺 filename（装配处 KeyError）、filename 空串（写目录本身）。"""
    import shutil
    import tempfile
    from pathlib import Path
    from pydantic import ValidationError
    from codeharness.actions.project_management import TaskItem
    from codeharness.configs.settings import settings
    from codeharness.const import RepoName
    from codeharness.document_store.artifact_store import ArtifactStore
    from codeharness.runtime import CURRENT_PROJECT
    from codeharness.schema import Document

    base = Path(tempfile.mkdtemp(prefix="s3a_gate_"))
    keep_ws, keep_proj = settings.workspace_root, CURRENT_PROJECT.get()
    settings.workspace_root = str(base)
    CURRENT_PROJECT.set("gate_proj")
    try:
        store = ArtifactStore.active()
        # 边界是会话根：跳出到根内（src/../x.py）不算越界，跳出根与绝对路径才拒
        for bad in ("", "   ", "/etc/passwd", "C:\\Windows\\x.py", "../../outside.py", "..\\..\\outside2.py"):
            try:
                asyncio.run(store.save(RepoName.SRC, Document(filename=bad, content="x")))
                _fail(f"13. 非法产物文件名被放过了: {bad!r}")
            except ValueError as e:
                if "非法产物文件名" not in str(e):
                    _fail(f"13. 拒绝理由没说清，将来没人看得懂: {e}")
            except PermissionError:
                _fail(f"13. 又走成写目录本身了（空串的真实崩法）: {bad!r}")
        outside = [p for p in base.parent.glob("outside*.py") if not p.resolve().is_relative_to(base.resolve())]
        if outside or list(base.rglob("escape.py")):
            _fail(f"13. 越界文件名虽然报错但还是落了盘: {outside}")
        asyncio.run(store.save(RepoName.SRC, Document(filename="main.py", content="print(1)")))
        asyncio.run(store.save(RepoName.SRC, Document(filename="pkg/util.py", content="A = 1")))
        if not (store.root / "src" / "main.py").exists() or not (store.root / "src" / "pkg" / "util.py").exists():
            _fail("13. 合法名（含嵌套 pkg/util.py）写不下去，闸口管太宽")
        # 读侧要软退化：非法名 = 没有这个产物（DebugError 靠它走「缺少修复上下文」，不是崩）
        for bad in ("", "   ", "/etc/passwd", "../../outside.py"):
            if asyncio.run(store.get(RepoName.SRC, bad)) is not None:
                _fail(f"13. 非法名读侧没软退化成 None: {bad!r}")
        try:
            TaskItem(filename="")
            _fail("13. TaskItem 允许空 filename，问题会一路跑到写盘")
        except ValidationError:
            pass
        # 空 task_list = 零条 Send = 会话以 finished 收场却什么都没写（真模型实测的假成功）
        from codeharness.actions.project_management import WriteTasks
        from codeharness.provider.fake import FakeLLM
        from codeharness.schema import Message
        try:
            asyncio.run(WriteTasks(llm=FakeLLM(['{"task_list": []}'])).run(Message(content="拆不出东西的设计")))
            _fail("13. 空任务清单被放过了，会话会假成功收场")
        except ValueError as e:
            if "拆不出任何文件级任务" not in str(e):
                _fail(f"13. 空任务清单的报错没说清: {e}")
    finally:
        settings.workspace_root = keep_ws
        CURRENT_PROJECT.set(keep_proj)
        shutil.rmtree(base, ignore_errors=True)


def t14_tool_call_report():
    """`tool_call_report` 的发射形状（09-23 扩 BlockType 第十值时同批装的判据）。

    为什么必须有：只读类工具（read_file/grep/find_file/编辑器读）原本一个块都不发，
    "对话流里每步一行"因此只是愿望。扩了词汇表而没有发射判据，下次谁把它改回静默，
    界面上只是"少了几行"——没有任何测试会红，正是本仓反复踩的那类洞。
    """
    import asyncio
    from codeharness.runtime import REPORT_SINK
    from codeharness.report import tool_call_report, BlockType

    ev = []

    async def go():
        tok = REPORT_SINK.set(lambda e: ev.append(e))
        try:
            await tool_call_report("read_file", {"path": "a.py", "content": "第一行\n第二行"},
                                   "文件不存在: a.py")
            await tool_call_report("terminal_command", {"command": "ls -al"}, "", ok=False)
            await tool_call_report("search_dir", {"pattern": "x" * 300}, "3 matches")
        finally:
            REPORT_SINK.reset(tok)

    asyncio.run(go())
    names = [e["name"] for e in ev]
    if names != ["meta", "content", "end_marker"] * 3:
        _fail(f"14. 一次调用应发 meta/content/end_marker 三条，实为 {names}")
    if {e["block"] for e in ev} != {BlockType.TOOL_CALL.value}:
        _fail(f"14. 块类型不是 ToolCall：{sorted({e['block'] for e in ev})}")
    m1 = ev[0]["value"]
    if m1["tool"] != "read_file" or m1["type"] != "tool_call" or m1["ok"] is not True:
        _fail(f"14. meta 形状不对：{m1}")
    if "\n" in m1["args"]["content"]:
        _fail(f"14. 参数摘要吃了第二行（整份文件内容会灌进事件流）：{m1['args']['content']!r}")
    if ev[1]["value"] != "文件不存在: a.py":
        _fail(f"14. 结果首行没进正文：{ev[1]['value']!r}")
    if ev[3]["value"]["ok"] is not False or "已拒绝" not in ev[4]["value"]:
        _fail("14. 被拒的那一步发成了成功（界面上「没发生」与「被拦下」就分不出来）："
              f"{ev[3]['value']} / {ev[4]['value']!r}")
    long_args = ev[6]["value"]["args"]["pattern"]
    if len(long_args) > 121 or not long_args.endswith("…"):
        _fail(f"14. 超长参数没截断（事件流不该搬 300 字模式串）：{long_args!r}")


def t15_block_markdown_not_schema_dump():
    """块正文的口径：Docs/Task 那几块发**渲染后的 markdown**，不是 schema dump。

    09-28 用户报的「输出会带有一大块文本出现」有另一半在这里：`ChatNode.vue` 把 Docs 当
    markdown 正文渲染、Task 行也只是标题 + 文本，**前端全仓没有一处 `JSON.parse` 块正文**，
    而动作们塞进去的是 `model_dump_json()`（实测一跑：PRD 块 4965 字、Design 块 5741 字全是 JSON）。
    """
    import pathlib

    from codeharness.actions.project_management import TaskItem, TaskList
    from codeharness.actions.write_prd import PRDOutput
    from codeharness.report import block_markdown

    prd = PRDOutput(language="en", programming_language="Vite, React", original_requirements="一段需求",
                    project_name="demo", product_goals=["g1 长句子", "g2"],
                    requirement_pool=[["F001", "main.py", "登录"], ["F002", "api.py", "接口"]],
                    requirement_analysis="需求分析正文", anything_unclear="无")
    md = block_markdown(prd)
    assert not md.lstrip().startswith("{"), f"正文还是 JSON 开头：{md[:50]!r}"
    assert '"language"' not in md and "## requirement analysis" in md, md[:120]
    assert "- F001 | main.py | 登录" in md, "内层标量列表该一行一条（表形字段）"

    tasks = TaskList(task_list=[TaskItem(filename="src/main.jsx", task_id="T001", instruction="入口"),
                                TaskItem(filename="src/App.jsx", task_id="T002",
                                         dependent_task_ids=["T001"], instruction="正文段落")],
                     required_packages=["react"], shared_knowledge="只用静态页")
    tm = block_markdown(tasks)
    assert "{" not in tm and '"' not in tm, f"块正文里出现了 JSON 字符：{tm[:60]!r}"
    assert "\n  - task_id: T001" in tm, "一条任务该是一个子弹头 + 缩进字段（平铺会丢分组）"
    assert block_markdown(PRDOutput()).strip() == "## language\nen_us", "空 schema 只剩有默认值的字段"

    # 一族四处收齐 + 落盘侧的阳性对照：机器读的那几份仍逐字是 JSON。
    src = {f: pathlib.Path("codeharness/actions/" + f).read_text(encoding="utf-8")
           for f in ("write_prd.py", "design_api.py", "project_management.py")}
    leak = [f for f, s in src.items() if "rep.content(" in s and ".content(" in s
            and any(l.strip().startswith("await rep.content(") and "model_dump_json" in l
                    for l in s.splitlines())]
    assert not leak, f"往块正文里塞 schema dump 的形状复燃：{leak}"
    wp = src["write_prd.py"]
    assert "Document(filename=DocName.PRD, content=prd.model_dump_json())" in wp, \
        "阳性对照失守：机器要解析的 prd.json 不再是 JSON（下游按 JSON 读它）"
    assert "Document(filename=DocName.PRD_MD, content=block_markdown(prd))" in wp, \
        "prd.md 又落回 JSON 转储（它全仓零解析方，只给人读）"
    assert "content=design.model_dump_json()))" in src["design_api.py"], \
        "阳性对照失守：design.json 的落盘格式被动了"


def t16_task_block_opens_with_meta():
    """第七件：`task_block` 开块必须发一条 `meta`，那是它进落点表的**唯一入口**。

    `LIVE_BLOCKS` 里一直有 `Task`，可从前 `task_block` 只建 reporter 就 yield——报道槽那条通道上
    一个事件都不经过，于是里面那一笔 structured 调用的逐片只能落兜底行 `stream-{node}`，
    既不与清单同块、schema 的正字段名单也永远挂不上（翻译层拿不到那块是谁）。
    """
    from codeharness.actions.project_management import TaskList
    from codeharness.report import BlockType, task_block

    ev = _collect()

    async def _a():
        async with task_block(role="PM", prose=TaskList) as rep:
            await rep.content("清单正文")
        async with task_block(role="PM") as rep:          # 阳性对照：没声明名单的块照旧发 meta，但不带名单
            await rep.content("另一份")

    asyncio.run(_a())
    names = [e["name"] for e in ev]
    assert names == ["meta", "content", "end_marker"] * 2, f"Task 块的事件序列变了：{names}"
    assert {e["block"] for e in ev} == {BlockType.TASK.value}, f"发错块类型：{[e['block'] for e in ev]}"
    m0, m1 = ev[0]["value"], ev[3]["value"]
    assert m0["type"] == "tasks" and m0["prose_fields"] == list(TaskList.prose_fields), \
        f"名单没进开块那条 meta（翻译层就永远登不上这块）：{m0}"
    assert "prose_fields" not in m1, f"没声明名单却凭空多出名单：{m1}"


def t17_llm_stream_degrade_zero_loss():
    """C112（④）：LLM token 流队列有界 + 满档整段降级——**降级退化的是粒度不是正文**。
    判据承重句是「收到的 content 逐字拼回全部输入」：这条红了就是「把无界内存病换成了数据丢失病」。"""
    from codeharness.logs import (get_llm_stream_queue, log_llm_stream, MAX_LLM_STREAM_QUEUE,
                                  set_llm_stream_logfunc)
    orig_logfunc = _set_noop_stream_log()
    try:
        ev = _collect()
        box = {}

        async def _degrade():
            async with RP.ThoughtReporter(enable_llm_stream=True):
                q = get_llm_stream_queue()
                box["q"] = q
                assert q.maxsize == MAX_LLM_STREAM_QUEUE, \
                    f"C112 ① 队列没上界（maxsize={q.maxsize}）——无界就是这条件的病根"
                assert q._degraded is False and q._spill == [], "C112 建队时降级位/缓冲初值不对"
                # 同步 for 里没有 await ⇒ 消费者任务在这一段抢不到 CPU，前 cap 条 put 成功后必满、必降级
                for i in range(MAX_LLM_STREAM_QUEUE + 50):
                    log_llm_stream("p%05d" % i)
        asyncio.run(_degrade())                     # __aexit__ 把 _spill 合成一条排在 None 之前
        q = box["q"]
        assert q._degraded, "C112 ② 灌了 cap+50 片却没置降级位（说明没走真生产者或没界）"
        got = "".join(e["value"] for e in ev if e["name"] == "content")
        want = "".join("p%05d" % i for i in range(MAX_LLM_STREAM_QUEUE + 50))
        assert got == want, \
            f"C112 整段降级丢了字：期望 {len(want)} 字、收到 {len(got)} 字 ⇒ 正文断字（数据丢失），不是粒度退化"
        assert ev and ev[-1]["name"] == "end_marker", "C112 降级后没收尾 end_marker"

        # 阳性对照：远小于 cap 的逐片**不该**降级，且一条一片原样到齐（否则上面那条红可能只是恒真）
        ev2 = _collect()
        box2 = {}

        async def _normal():
            async with RP.ThoughtReporter(enable_llm_stream=True):
                box2["q"] = get_llm_stream_queue()
                for p in ("甲", "乙", "丙"):
                    log_llm_stream(p)
        asyncio.run(_normal())
        assert not box2["q"]._degraded, "C112 阳性对照破：3 片（远小于 cap）就误判降级"
        got2 = [e["value"] for e in ev2 if e["name"] == "content"]
        assert got2 == ["甲", "乙", "丙"], f"C112 cap 内逐片序列/粒度变了：{got2}"
    finally:
        set_llm_stream_logfunc(orig_logfunc)


def _set_noop_stream_log():
    """临时把 `_llm_stream_log` 静音（真生产者的旁路打印，与本判据无关），返回原函数以便复原。"""
    from codeharness.logs import set_llm_stream_logfunc, _llm_stream_log
    orig = _llm_stream_log
    set_llm_stream_logfunc(lambda *a, **k: None)
    return orig


def main():
    checks = [t1_class_surface, t2_blocktype_vocabulary, t3_payload_shape, t4_path_absolute,
              t5_context_manager_and_hooks, t6_llm_stream_bridge,
              t7_retry_targets_only_missing, t8_no_retry_when_complete, t9_empty_semantics,
              t10_merge_never_clobbers, t11_partial_schema_keys, t12_plain_text_path_untouched,
              t13_artifact_filename_gate, t14_tool_call_report,
              t15_block_markdown_not_schema_dump, t16_task_block_opens_with_meta,
              t17_llm_stream_degrade_zero_loss]
    for c in checks:
        c()
        print(f"  ok  {c.__name__}")
    print(f"\nS3(a) 门禁通过：{len(checks)} 组 —— R6 Reporter 类族 6 组（类名/词汇表/载荷/绝对路径/"
          f"上下文管理器与钩子/LLM 流桥接）+ R2 字段级定向重试 6 组（只补缺口/齐全不重发/空值语义/"
          f"合并不覆盖/部分模型键/纯文本退化）+ 产物仓文件名闸口与任务契约 1 组 + Task 块开块那条 meta 1 组")
    print("注意：S3 的 R3/R4a/R5（编排、checkpointer、interrupt）尚未做，勿据此认为 S3 已完成。")


if __name__ == "__main__":
    main()
