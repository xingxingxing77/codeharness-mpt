"""S1 门禁：const + schema + document 的数据骨架自检。全部零成本、零联网。

覆盖 docs/施工1-地基与运行时-S1-S3.md 规定的 8 条断言，另加 3 条本次改动的回归点：
- `MessageQueue` 不许复燃（C7 判定：本栈只有 LangGraph 状态与 Redis LIST 两条消息通道）
- RunCodeContext 字段名对齐源 working_directory
- 两种同名 Document 不串（schema.Document vs document.Document）

跑法：
  cd /e/Codeharness && PYTHONPATH=/e/Codeharness PYTHONIOENCODING=utf-8 F:/anaconda/python.exe tests/s1_schema.py
"""
import asyncio
import json
import tempfile
from pathlib import Path

from pydantic import BaseModel

from codeharness.const import (
    MESSAGE_ROUTE_TO_ALL,
    MESSAGE_ROUTE_TO_NONE,
    MESSAGE_ROUTE_TO_SELF,
    REQUIREMENT_FILENAME,
    USER_REQUIREMENT,
    DocName,
    RepoName,
    RequirementTag,
)
from codeharness.schema import (
    AIMessage,
    BaseContext,
    CodePlanAndChangeContext,
    CodeSummarizeContext,
    CodingContext,
    Command,
    Document,
    Documents,
    Message,
    Plan,
    Resource,
    RunCodeContext,
    RunCodeResult,
    SerializationMixin,
    SimpleMessage,
    SystemMessage,
    Task,
    TaskResult,
    TeamState,
    TestingContext,
    UserMessage,
)


def _fail(msg: str):
    raise AssertionError(msg)


# ---------- 1. dump/load 逐字段一致（send_to 的 set<->list 最容易丢） ----------
def t1_roundtrip():
    m = Message(content="做个2048", sent_from="Alice", send_to={"Bob", "Mike"},
                cause_by=RequirementTag.WRITE_PRD, metadata={"k": "v"},
                instruct_content={"prd": "x"}, instruct_schema="WritePRD")
    back = Message.load(m.dump())
    for f in ("id", "content", "role", "cause_by", "sent_from", "instruct_content", "instruct_schema", "metadata"):
        if getattr(back, f) != getattr(m, f):
            _fail(f"1. 字段 {f} 往返不一致: {getattr(back, f)!r} != {getattr(m, f)!r}")
    if back.send_to != {"Bob", "Mike"}:
        _fail(f"1. send_to 往返后退化成 {type(back.send_to).__name__}: {back.send_to!r}")
    if back.id != m.id:
        _fail("1. id 未保留（load 里必须显式回写）")


# ---------- 2. instruct_content 两态：dict 与 BaseModel 都要能进、能出 ----------
class PRD(BaseModel):
    project_name: str = ""
    languages: str = "English"


def t2_instruct_two_ways():
    from_dict = Message(content="c", instruct_content={"project_name": "g2048"}, instruct_schema="PRD")
    from_model = Message(content="c", instruct_content=PRD(project_name="g2048"), instruct_schema="PRD")
    # dict 路径原样透传；BaseModel 路径按全字段（含默认值）归一
    if from_dict.instruct_content != {"project_name": "g2048"}:
        _fail(f"2. dict 路径被改写了: {from_dict.instruct_content!r}")
    if from_model.instruct_content != {"project_name": "g2048", "languages": "English"}:
        _fail(f"2. BaseModel 路径没归一成 dict: {from_model.instruct_content!r}")
    rt = Message.load(from_model.dump())
    if rt.instruct_content != {"project_name": "g2048", "languages": "English"}:
        _fail(f"2. BaseModel 路径序列化后丢了默认字段: {rt.instruct_content!r}")
    if rt.instruct_schema != "PRD":
        _fail(f"2. instruct_schema 没随消息往返: {rt.instruct_schema!r}")
    # create_instruct_value（源 :396）应能造出可用模型实例
    dyn = Message.create_instruct_value({"a": 1, "b": "s"}, class_name="DMTest")
    if not isinstance(dyn, BaseModel) or dyn.a != 1:
        _fail("2. create_instruct_value 未返回可用 BaseModel 实例")


# ---------- 3. cause_by：为空才落 USER_REQUIREMENT，显式给出绝不覆盖 ----------
def t3_cause_by():
    if Message(content="c").cause_by != USER_REQUIREMENT:
        _fail("3. 空 cause_by 没落到 USER_REQUIREMENT")
    for given in (RequirementTag.RUN_CODE, Task(task_id="T1")):
        expect = given if isinstance(given, str) else "Task"
        got = Message(content="c", cause_by=given).cause_by
        if got != expect:
            _fail(f"3. 显式 cause_by 被改写: {got!r} != {expect!r}")
    m = Message(content="c")
    m.cause_by = Task                                    # 走 __setattr__（源 :307）
    if m.cause_by != "Task":
        _fail(f"3. __setattr__ 没把类对象归一成类名: {m.cause_by!r}")
    m.send_to = MESSAGE_ROUTE_TO_SELF
    if m.send_to != {MESSAGE_ROUTE_TO_SELF}:
        _fail(f"3. __setattr__ 没把 send_to 归一成 set: {m.send_to!r}")


# ---------- 4. 三个消息子类的 role 与 agent 元数据 ----------
def t4_subclasses():
    if (UserMessage("x").role, SystemMessage("x").role, AIMessage("x").role) != ("user", "system", "assistant"):
        _fail("4. UserMessage/SystemMessage/AIMessage 的 role 不对")
    if UserMessage("x", role="assistant").role != "user":
        _fail("4. 子类应吞掉外部传入的 role（源 :425 kwargs.pop）")
    a = AIMessage("x").with_agent("Bob")
    if a.agent != "Bob" or a.metadata.get("agent") != "Bob":
        _fail(f"4. with_agent/agent 不通: {a.agent!r} / {a.metadata!r}")
    if not (AIMessage("x").is_ai_message() and UserMessage("y").is_user_message()):
        _fail("4. is_ai_message/is_user_message 判定错")


# ---------- 5. Plan：拓扑序确定 + 游标推进 + 下游级联 reset ----------
def t5_plan():
    def mk(tid, deps):
        return Task(task_id=tid, dependent_task_ids=deps, instruction=f"i-{tid}", assignee="Alex")

    p = Plan(goal="g")
    p.add_tasks([mk("T3", ["T1", "T2"]), mk("T1", []), mk("T2", ["T1"])])
    order = [t.task_id for t in p.tasks]
    if order != ["T1", "T2", "T3"]:
        _fail(f"5. 拓扑排序不稳定: {order}")
    if p.current_task_id != "T1" or p.current_task.task_id != "T1":
        _fail(f"5. 当前游标不在首个未完成任务: {p.current_task_id!r}")
    p.finish_current_task()
    if p.current_task_id != "T2":
        _fail(f"5. finish_current_task 没推进游标: {p.current_task_id!r}")
    # 重置 T1 必须级联重置依赖它的 T2/T3
    p.reset_task("T1")
    if any(t.is_finished for t in p.tasks if t.task_id in ("T2", "T3")):
        _fail("5. reset_task 没级联重置下游任务")
    p.finish_all_tasks()
    if not p.is_plan_finished() or len(p.get_finished_tasks()) != 3:
        _fail("5. finish_all_tasks/is_plan_finished/get_finished_tasks 不一致")
    # append 依赖未知任务必须断言失败（源 :628）
    try:
        p.append_task("T9", ["T-ghost"], "i", "Alex")
    except AssertionError:
        pass
    else:
        _fail("5. append_task 未拒绝未知依赖")
    # add_tasks 重规划要保住 (task_id, instruction) 相同的前缀
    p2 = Plan(goal="g")
    p2.add_tasks([mk("T1", []), mk("T2", ["T1"])])
    p2.tasks[0].is_finished = True
    p2.add_tasks([mk("T1", []), mk("T2", ["T1"]), mk("T3", ["T2"])])
    if [t.task_id for t in p2.tasks] != ["T1", "T2", "T3"] or not p2.tasks[0].is_finished:
        _fail("5. add_tasks 重规划丢掉了已完成前缀")


# ---------- 6. 路由常量：值与源一致，且全项目无内联字面量 ----------
def t6_route_constants():
    if (MESSAGE_ROUTE_TO_ALL, MESSAGE_ROUTE_TO_NONE, MESSAGE_ROUTE_TO_SELF) != ("<all>", "<none>", "<self>"):
        _fail("6. 路由 tag 值与源 const.py:83-85 不一致")
    if Message(content="c").send_to != {MESSAGE_ROUTE_TO_ALL}:
        _fail("6. Message.send_to 默认值不是 <all> 常量")
    root = Path(__file__).resolve().parent.parent / "codeharness"
    offenders = []
    for py in root.rglob("*.py"):
        if py.name == "const.py":
            continue
        text = py.read_text(encoding="utf-8", errors="replace")
        for lit in ('"<all>"', '"<none>"', '"<self>"'):
            if lit in text:
                offenders.append(f"{py.name}:{lit}")
    if offenders:
        _fail(f"6. 存在内联路由字面量，应收口到 const: {offenders}")


# ---------- 7. RepoName 单一真源：值必须等于源 *_FILE_REPO ----------
def t7_repo_single_source():
    from codeharness import const
    pairs = {RepoName.DOCS: const.DOCS_FILE_REPO, RepoName.PRD: const.PRDS_FILE_REPO,
             RepoName.TESTS: const.TEST_CODES_FILE_REPO, RepoName.TEST_OUTPUTS: const.TEST_OUTPUTS_FILE_REPO,
             RepoName.RESOURCES: const.RESOURCES_FILE_REPO}
    for got, want in pairs.items():
        if got != want:
            _fail(f"7. RepoName 值 {got!r} 与源 *_FILE_REPO {want!r} 脱钩")
    if REQUIREMENT_FILENAME != "requirement.txt" or DocName.REQUIREMENT != "requirement.md":
        _fail("7. 输入需求文件名与产物需求文件名混淆了（源是 .txt，新栈产物是 .md）")


# ---------- 8. 本次修复的回归点 ----------
def t8_regressions():
    # 8a. `MessageQueue` 不复燃（C7 判定后删除，见 schema.py:506 的注释）。
    #     它曾是"有 schema 无生产写入者/读者"的又一件——留着的代价不是 70 行代码，
    #     是下一个人以为"进程内消息队列已经有了"，于是真通道永远不接。
    import codeharness.schema as sch
    if hasattr(sch, "MessageQueue"):
        _fail("8a. MessageQueue 长回来了——要么接线（判据见 PLAN §2 C7）要么删干净，别拿半截当资产")
    # 两台实体的行为平价与并发判据在 s7（那里有 Redis），这里只钉"别多第三条通道"

    # 8b. 插话通道的接口平价：进程内与 Redis 两台同名同签名（route 只认这一套）
    from codeharness.runtime import ChatQueue
    q = ChatQueue()
    q.enqueue("追问 A", "PM")
    q.enqueue("追问 B")
    if q.drain() != [("追问 A", "PM"), ("追问 B", "")] or q.drain() != []:
        _fail("8b. ChatQueue 的 drain 语义变了（route 的插话分支按这个形状写）")

    # 8c. 上下文字段名与源逐字对齐
    if "working_directory" not in RunCodeContext.model_fields:
        _fail(f"8c. RunCodeContext 缺源字段 working_directory，现有: {list(RunCodeContext.model_fields)}")
    for name in ("mode", "test_code", "additional_python_paths", "output"):
        if name not in RunCodeContext.model_fields:
            _fail(f"8c. RunCodeContext 缺源字段 {name}")
    if {"summary", "stdout", "stderr", "return_code"} - set(RunCodeResult.model_fields):
        _fail("8c. RunCodeResult 字段与源+新增不符")
    for cls, fields in ((CodingContext, {"filename", "design_doc", "task_doc", "code_doc", "code_plan_and_change_doc"}),
                        (TestingContext, {"filename", "code_doc", "test_doc"}),
                        (CodePlanAndChangeContext, {"requirement", "issue", "prd_filename", "design_filename", "task_filename"})):
        missing = fields - set(cls.model_fields)
        if missing:
            _fail(f"8c. {cls.__name__} 缺字段 {missing}")

    # 8d. 两种同名 Document 不许串
    from codeharness.document import Document as RepoDocument, DocumentStatus, IndexableDocument, Repo, RepoMetadata
    if {"root_path", "filename"} - set(Document.model_fields):
        _fail("8d. schema.Document 丢了产物字段")
    if {"path", "status", "reviews", "author"} - set(RepoDocument.model_fields):
        _fail("8d. document.Document 丢了文档仓字段")
    if RepoDocument is Document:
        _fail("8d. 两个同名 Document 变成了同一个类")

    # 8e. BaseContext.loads / SerializationMixin 落盘往返
    tmp = Path(tempfile.mkdtemp())
    d = Document(root_path="docs", filename=DocName.DESIGN, content="# 设计")
    ctx = TestingContext(filename="game.js", code_doc=d)
    payload = json.dumps({"filename": "game.js", "code_doc": d.model_dump()}, ensure_ascii=False)
    if ctx.loads(payload).code_doc.content != "# 设计":
        _fail("8e. BaseContext.loads 还原不出嵌套 Document")
    if ctx.loads("not json") is not None:
        _fail("8e. BaseContext.loads 对坏输入应当返回 None 而不是抛")
    rc = RunCodeResult(summary="ok", stdout="1 passed", stderr="", return_code=0)
    written = rc.serialize(file_path=str(tmp / "RunCodeResult.json"))
    back = RunCodeResult.deserialize(written)
    if back is None or back.model_dump() != rc.model_dump():
        _fail(f"8e. SerializationMixin 落盘往返不一致: {back!r}")
    _ = (IndexableDocument, Repo, RepoMetadata, DocumentStatus)


# ---------- 9. parse_resources：验证从源逐字搬来的 prompt 通路 ----------
def t9_parse_resources():
    from codeharness.provider.fake import FakeLLM

    payload = {"resources": [{"resource_type": "url", "value": "https://x", "description": "竞品"}], "reason": "r"}
    fenced = "```json\n" + json.dumps(payload) + "\n```"
    llm = FakeLLM(responses=[fenced])
    m = Message(content="做个类似 https://x 的游戏")
    got = asyncio.run(m.parse_resources(llm, key_descriptions={"game_name": "the game name"}))
    if not isinstance(got.get("resources"), list) or not isinstance(got["resources"][0], Resource):
        _fail(f"9. parse_resources 没把 resources 转成 Resource: {got!r}")
    if got["resources"][0].value != "https://x":
        _fail("9. Resource 字段解析错")
    if not llm.calls:
        _fail("9. parse_resources 没走到 LLM")
    if got.get("reason") != "r":
        _fail(f"9. 自定义 key 丢了: {got!r}")
    # 裸 JSON（无围栏）走 parse_code 的回退分支，也要能解
    got2 = asyncio.run(m.parse_resources(FakeLLM(responses=[json.dumps(payload)])))
    if got2.get("resources", [None])[0].resource_type != "url":
        _fail(f"9. 无围栏回退分支解析失败: {got2!r}")


# ---------- 10. 新栈自有：TeamState / Command ----------
def t10_platform_types():
    if TeamState().round != 0 or TeamState(debug_rounds=2).debug_rounds != 2:
        _fail("10. TeamState 默认值不对")
    if Command(command_name="RunCommand", args={"c": "ls"}).args["c"] != "ls":
        _fail("10. Command.args 不对")
    if Documents.from_iterable([d := Document(filename="a.md", content="1")]).docs["a.md"] is not d:
        _fail("10. Documents.from_iterable 键应当是 filename")
    if Document.load.__name__ != "load":
        _fail("10. Document.load 签名漂移")


# ---------- 11. document.py：落盘编码配对 + Repo 分派 + eda 降级 ----------
def t11_document_repo():
    from codeharness.document import Document as RepoDocument, IndexableDocument, Repo

    tmp = Path(tempfile.mkdtemp())
    # 11a. 源 document.py:80 的 read_text() 不带编码，中文 Windows 上读自己 utf-8 写的文件会炸
    d = RepoDocument.from_text("# 标题 中文", path=tmp / "a.md")
    d.to_path()
    if RepoDocument.from_path(tmp / "a.md").content != "# 标题 中文":
        _fail("11a. Document from_path/to_path 编码没配对（源 :80 的 bug 回来了）")

    # 11b. Repo 按后缀三分类
    r = Repo(path=tmp, name=tmp.name)
    r.set("readme.md", "你好"); r.set("game.js", "let a=1"); r.set("x.json", "{}")
    if (len(r.docs), len(r.codes), len(r.assets)) != (1, 1, 1):
        _fail(f"11b. Repo 分类错: docs={len(r.docs)} codes={len(r.codes)} assets={len(r.assets)}")
    if r.get("game.js").content != "let a=1" or r.get_text_documents().__len__() != 2:
        _fail("11b. Repo.get / get_text_documents 不对")
    # 11c. eda()：RepoParser.generate_symbols 尚未落地，必须降级返回空 symbols 而不是抛
    ed = r.eda()
    if ed.n_docs != 3 or ed.n_chars <= 0 or ed.symbols != []:
        _fail(f"11c. eda() 降级分支异常: {ed.model_dump()}")

    # 11d. IndexableDocument 的 DataFrame 分支
    (tmp / "t.csv").write_text("content,metadata\nrow1,m1\nrow2,m2", encoding="utf-8")
    docs, metas = IndexableDocument.from_path(tmp / "t.csv", content_col="content", meta_col="metadata").get_docs_and_metadatas()
    if list(docs) != ["row1", "row2"] or metas[0] != {"metadata": "m1"}:
        _fail(f"11d. IndexableDocument df 分支: {docs} {metas}")
    try:
        IndexableDocument.from_path(tmp / "t.csv", content_col="nope")
    except ValueError:
        pass
    else:
        _fail("11d. validate_cols 未对缺失内容列报错")


# ---------- 12. BaseSerialization 多态序列化（一度被误删，此处锁死） ----------
def t12_polymorphic_serialization():
    from pydantic import ValidationError

    from codeharness.base.base_serialization import BaseSerialization

    if not issubclass(SerializationMixin, BaseSerialization):
        _fail("12a. SerializationMixin 没继承 BaseSerialization——多态标签又丢了")
    if not issubclass(BaseContext, BaseSerialization):
        _fail("12b. BaseContext 未经由 SerializationMixin 拿到多态能力，六上下文都会丢子类字段")

    # 12c. dump 必须带类型标签
    dumped = CodingContext(filename="a.js").model_dump()
    if dumped.get("__module_class_name") != f"{CodingContext.__module__}.CodingContext":
        _fail(f"12c. dump 缺 __module_class_name 标签: {dumped.keys()}")

    # 12d. 按基类 validate 子类数据 → 必须还原成真实子类（pydantic 原生不会这么做）
    class SpecialContext(CodingContext):
        extra_note: str = ""

    tagged = SpecialContext(filename="b.js", extra_note="n").model_dump()
    restored = CodingContext.model_validate(tagged)
    if type(restored) is not SpecialContext:
        _fail(f"12d. 多态还原失败，退化成 {type(restored).__name__}，子类字段 extra_note 丢了")
    if restored.extra_note != "n":
        _fail("12d. 子类字段没保住")

    # 12e. 自身往返必须无损（标签要在 validate 时剥掉，否则 extra=forbid 会自炸）
    src = RunCodeResult(summary="s")
    if RunCodeResult.model_validate(src.model_dump()) != src:
        _fail("12e. model_validate(model_dump()) 往返不等——__module_class_name 没被剥掉")

    # 12f. extra="forbid" 必须生效：非法键当场报错，不再静默丢弃
    try:
        CodingContext.model_validate({"filename": "c.js", "instruction": "不该出现的键"})
    except ValidationError:
        pass
    else:
        _fail("12f. extra=forbid 失效——脏字段又被静默吞了")

    # 12g. 未注册的类名要显式报错（读旧栈/metagpt 产物的 JSON 时属于危险场景，不能假装成功）
    bogus = dict(dumped, __module_class_name="metagpt.schema.CodingContext")
    try:
        CodingContext.model_validate(bogus)
    except TypeError as e:
        if "metagpt.schema.CodingContext" not in str(e):
            _fail(f"12g. TypeError 信息没带上类名: {e}")
    else:
        _fail("12g. 未注册的 __module_class_name 竟然通过了")


def main():
    checks = [t1_roundtrip, t2_instruct_two_ways, t3_cause_by, t4_subclasses, t5_plan,
              t6_route_constants, t7_repo_single_source, t8_regressions,
              t9_parse_resources, t10_platform_types, t11_document_repo, t12_polymorphic_serialization]
    for c in checks:
        c()
        print(f"  ok  {c.__name__}")
    print(f"\nS1 门禁全部通过：{len(checks)} 组断言（消息往返 / 两态 instruct / cause_by 不覆盖 / "
          f"Plan 拓扑与级联 / 路由常量收口 / RepoName 单源 / 插话通道 drain 语义与不复燃 / 上下文对齐源字段 / "
          f"双同名 Document / parse_resources / 平台自有类型 / document 编码配对与 eda 降级 / "
          f"BaseSerialization 多态与 forbid）")


if __name__ == "__main__":
    main()
