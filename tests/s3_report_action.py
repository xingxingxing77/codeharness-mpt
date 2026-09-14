"""S3(a) 门禁：Reporter 类族（R6）+ 字段级定向重试（R2）。

⚠ 本文件只覆盖 S3 中与预算改造不冲突的两块。R3（编排条件边）、R4a（checkpointer）、
R5（interrupt/resume）要等 `team_graph.py`/`team.py`/`runner.py` 上的预算清理落地后再接，
届时补 `tests/s3b_runtime.py`，不要以为 S3 已全绿。

跑法：
  cd /e/Codeharness && PYTHONPATH=/e/Codeharness PYTHONIOENCODING=utf-8 F:/anaconda/python.exe tests/s3_report_action.py
"""
import json
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
    want = ["Terminal", "Task", "Browser", "Browser-RT", "Editor", "Gallery", "Notebook", "Docs", "Thought"]
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
    if not got.startswith(("C:", "/", "E:")):
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


def main():
    checks = [t1_class_surface, t2_blocktype_vocabulary, t3_payload_shape, t4_path_absolute,
              t5_context_manager_and_hooks, t6_llm_stream_bridge,
              t7_retry_targets_only_missing, t8_no_retry_when_complete, t9_empty_semantics,
              t10_merge_never_clobbers, t11_partial_schema_keys, t12_plain_text_path_untouched]
    for c in checks:
        c()
        print(f"  ok  {c.__name__}")
    print(f"\nS3(a) 门禁通过：{len(checks)} 组 —— R6 Reporter 类族 6 组（类名/词汇表/载荷/绝对路径/"
          f"上下文管理器与钩子/LLM 流桥接）+ R2 字段级定向重试 6 组（只补缺口/齐全不重发/空值语义/"
          f"合并不覆盖/部分模型键/纯文本退化）")
    print("注意：S3 的 R3/R4a/R5（编排、checkpointer、interrupt）尚未做，勿据此认为 S3 已完成。")


if __name__ == "__main__":
    main()
