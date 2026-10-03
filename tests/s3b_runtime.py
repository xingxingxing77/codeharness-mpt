"""S3(b) 门禁：编排路由（R3）、持久化断点（R4a）、interrupt/resume（R5）。

跑法：
  cd /e/Codeharness && PYTHONPATH=/e/Codeharness PYTHONIOENCODING=utf-8 F:/anaconda/python.exe tests/s3b_runtime.py
"""
import asyncio
import operator
import tempfile
from pathlib import Path
from typing import Annotated, TypedDict

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt
from pydantic import BaseModel

from codeharness.base.action import BaseAction
from codeharness.const import MESSAGE_ROUTE_TO_ALL, MESSAGE_ROUTE_TO_SELF, RequirementTag
from codeharness.environment.checkpoint import close_all, default_checkpoint_path, make_checkpointer
from codeharness.environment.team_graph import UnknownRecipient, SOP, TeamState, build_team, make_route
from codeharness.roles.agent import Agent
from codeharness.runtime import CHAT_SINK, CURRENT_PROJECT, REPORT_SINK
from codeharness.schema import Message


def _fail(m):
    raise AssertionError(m)


class Rec(BaseModel):
    done: str = ""


class Mark(BaseAction):
    """记录自己被调用过，并产出带 cause_by 的消息"""
    output_schema = Rec

    async def run(self, msg: Message) -> Message:
        CALLS.append(self.name)
        return Message(content=f"{self.name} ok", role="assistant", cause_by=self.name,
                       sent_from=self.name)


CALLS: list[str] = []


class _StubLLM:
    """够用的结构化桩：返回一个空模型，只为让 Action 跑完不触网。"""

    def __init__(self):
        self.cost_manager = type("CM", (), {"get_costs": lambda s: None})()

    async def aask(self, prompt, system_msgs=None, tag="", **kw):
        return "text"

    def structured(self, schema):
        outer = self

        class _S:
            async def ainvoke(self, prompt, **kw):
                return schema()
        return _S()


class _Wrap:
    def __init__(self, name):
        self.name = name
        self.hits = 0

    def as_node(self, name):
        async def _run(state: dict):
            self.hits += 1
            return {"messages": [Message(content=f"{name} did", cause_by="none", sent_from=name)]}
        return name, _run


# ---------- 1. BY_ORDER 双 Action 必须跑完两个（off-by-one 回归） ----------
def t1_by_order_runs_all_actions():
    class A1(Mark): pass
    class A2(Mark): pass
    llm = _StubLLM()
    a1, a2 = A1(llm=llm), A2(llm=llm)
    CALLS.clear()
    agent = Agent({"name": "E", "profile": "Engineer", "goal": "g"}, [a1, a2], llm,
                  react_mode="BY_ORDER", max_loops=5, watch={RequirementTag.USER_REQUIREMENT})
    out = asyncio.run(agent.build().ainvoke({
        "name": "E", "inbox": [Message(content="开工", cause_by=RequirementTag.USER_REQUIREMENT)],
        "memory": [], "action_cursor": -1, "chosen": "", "loops": 0, "output": []}))
    if CALLS != ["A1", "A2"]:
        _fail(f"1. BY_ORDER 未跑完全部 Action（游标 off-by-one 回归）: {CALLS}")
    if len(out["output"]) < 2:
        _fail(f"1. 输出条数不足: {len(out['output'])}")


# ---------- 2. 精准激活：一轮只唤醒订阅者，不是全员 ----------
def t2_precise_activation():
    stats: list = []
    wraps = {name: _Wrap(name) for name in ("PM", "Architect", "Engineer", "QA")}
    agents = {n: w for n, w in wraps.items()}
    route = make_route(SOP, agents, wiring={}, stats=stats)
    state = TeamState(messages=[Message(content="prd", cause_by=RequirementTag.WRITE_PRD,
                                        sent_from="PM", send_to={MESSAGE_ROUTE_TO_ALL})],
                      memories={}, round=0, debug_rounds=0, finished=False)
    sends = route(state)
    if stats[0]["roles"] != 4 or stats[0]["activated"] != 1:
        _fail(f"2. 精准激活失败：4 个角色却激活 {stats[0]['activated']} 个（应 1）")
    if [s.node for s in sends] != ["Architect"]:
        _fail(f"2. 订阅表路由目标错: {[s.node for s in sends]}")


# ---------- 3. 显式 send_to 指名可路由 ----------
def t3_explicit_send_to():
    wraps = {n: _Wrap(n) for n in ("PM", "QA", "Engineer")}
    route = make_route({}, wraps, wiring={})            # 订阅表空，只靠指名
    state = TeamState(messages=[Message(content="c", cause_by="UnregisteredTag", sent_from="PM",
                                        send_to={"QA"})],
                      memories={}, round=0, debug_rounds=0, finished=False)
    sends = route(state)
    if [s.node for s in sends] != ["QA"]:
        _fail(f"3. 显式指名的 send_to 未被路由: {sends}")


# ---------- 3b. 插话指名不存在的角色：丢那一条 + 留痕，但不把整场打成 failed ----------
def t3b_chat_to_unknown_role_is_dropped():
    """委派与插话**不同判**（用户 2026-09-21 定的档位）：具名委派指错人当场抛（见 t4），
    插话指错人只丢那一条并留痕——为一根错名字赔上整场已烧的用量不划算，
    且端点早已按 `session.roles` 422 过，能走到这里只剩跨 worker 陈旧队列与 ext_api 直投。"""
    from codeharness.runtime import CHAT_SINK, ChatQueue
    wraps = {n: _Wrap(n) for n in ("PM", "QA")}
    stats = []
    route = make_route({}, wraps, wiring={}, stats=stats)
    q = ChatQueue()
    q.enqueue("给幽灵的插话", "Ghost")
    q.enqueue("给 QA 的插话", "QA")
    tok = CHAT_SINK.set(q)
    state = TeamState(messages=[Message(content="c", cause_by="UnregisteredTag", sent_from="PM",
                                        send_to=set())],
                      memories={}, round=0, debug_rounds=0, finished=False)
    try:
        sends = route(state)
    finally:
        CHAT_SINK.reset(tok)
    nodes = [s.node for s in sends] if sends != END else []
    if nodes != ["QA"]:
        _fail(f"3b. 插话路由不对（Ghost 那条该丢、QA 那条该送达）：{nodes}")
    drops = [x for x in stats if x.get("cause_by") == "chat-dropped"]
    if not drops or drops[0]["roles"] != "Ghost":
        _fail(f"3b. 丢弃没留痕（stats 里没有 chat-dropped/Ghost，就是静默丢）：{stats}")
    if q.drain():
        _fail("3b. 两条插话没被消费干净（丢一条也要把它从队列里取走，否则每轮重投）")


# ---------- 4. <self> 目标不存在：当场抛，绝不发 Send（C1-d 改判） ----------
def t4_self_to_unknown_node():
    """旧行为是「静默落到 END」——那条静默正是 docs 记的 `Ignoring unknown node name`
    + 整场零 LLM 调用。现在必须抛 UnknownRecipient，且错误里要点名那个不存在的收件人。"""
    route = make_route({}, {"PM": _Wrap("PM")}, wiring={})
    state = TeamState(messages=[Message(content="c", cause_by="t", sent_from="Ghost",
                                        send_to={MESSAGE_ROUTE_TO_SELF})],
                      memories={}, round=0, debug_rounds=0, finished=False)
    try:
        got = route(state)
    except UnknownRecipient as e:
        if "Ghost" not in str(e):
            _fail(f"4. 抛是抛了，但没点名那个不存在的收件人：{e}")
        return
    _fail(f"4. <self> 指向不存在的节点却只回 {got!r}——静默丢就是「整场零模型调用」那条老路")


# ---------- 5. 订阅关系可证伪：改 cause_by 后下游必须收不到 ----------
def t5_subscribe_is_falsifiable():
    route = make_route(SOP, {n: _Wrap(n) for n in ("PM", "Architect")}, wiring={})
    base = dict(memories={}, round=0, debug_rounds=0, finished=False)
    hit = route({**base, "messages": [Message(content="c", cause_by=RequirementTag.WRITE_PRD,
                                             sent_from="PM", send_to={MESSAGE_ROUTE_TO_ALL})]})
    miss = route({**base, "messages": [Message(content="c", cause_by="RenamedTag",
                                               sent_from="PM", send_to={MESSAGE_ROUTE_TO_ALL})]})
    if hit is END:
        _fail("5. 正常 tag 却没路由到订阅者")
    if miss is not END:
        _fail("5. 改掉 cause_by 后仍然路由——订阅关系是假的（全员轮询特征）")


# ---------- 6. 设计决定：默认 <all> 不做广播 ----------
def t6_all_is_not_broadcast():
    route = make_route({}, {n: _Wrap(n) for n in ("A", "B", "C")}, wiring={})
    state = TeamState(messages=[Message(content="c", cause_by="t", sent_from="A")],   # 默认 send_to=<all>
                      memories={}, round=0, debug_rounds=0, finished=False)
    if route(state) is END:
        return
    _fail("6. <all> 被当成广播了——每个动作都会唤醒全部角色，毁掉精准激活（这是刻意的设计决定）")


# ---------- 7. checkpointer 落盘：新实例能读到同一条断点 ----------
def t7_checkpointer_persists():
    tmp = Path(tempfile.mkdtemp())
    path = default_checkpoint_path(tmp)
    cfg = {"configurable": {"thread_id": "t7"}}

    class St(TypedDict):
        n: Annotated[int, operator.add]

    async def _go():
        s1 = await make_checkpointer(path)                    # 工厂要运行中的事件循环
        if type(s1).__name__ != "AsyncSqliteSaver":
            _fail(f"7. 未拿到异步文件型 saver（runner 走 astream_events，同步型会抛 "
                  f"NotImplementedError）：{type(s1).__name__}")
        g = StateGraph(St)
        g.add_node("a", lambda s: {"n": 1})
        g.add_edge(START, "a")
        g.add_edge("a", END)
        await g.compile(checkpointer=s1).ainvoke({"n": 0}, cfg)
        if not path.exists():
            _fail("7. checkpoint 库文件没生成，谈不上持久化")
        # 工厂按路径复用连接，再取一次必是同一对象，测不出跨进程语义；用全新连接读
        import aiosqlite
        from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
        async with aiosqlite.connect(str(path)) as conn:
            tup = await AsyncSqliteSaver(conn).aget_tuple(cfg)
        return tup.checkpoint["channel_values"]["n"] if tup else None

    got = asyncio.run(_go())
    if got != 1:
        _fail(f"7. 全新连接读不到落盘断点（持久化无效）: {got}")


# ---------- 8. interrupt 暂停后，换 graph 实例也能 resume ----------
def t8_interrupt_resume_across_restart():
    tmp = Path(tempfile.mkdtemp())
    path = default_checkpoint_path(tmp)
    cfg = {"configurable": {"thread_id": "t8"}}

    class St(TypedDict):
        asked: str
        answer: str

    def ask_node(s):
        # interrupt 靠抛 GraphInterrupt 暂停图；同时验证它不被 except Exception 吞掉
        return {"asked": "你选哪个方案？", "answer": interrupt({"question": "你选哪个方案？"})}

    def build(saver):
        g = StateGraph(St)
        g.add_node("ask", ask_node)
        g.add_edge(START, "ask")
        g.add_edge("ask", END)
        return g.compile(checkpointer=saver)

    async def _go():
        import aiosqlite
        from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
        got = await build(await make_checkpointer(path)).ainvoke({"asked": "", "answer": ""}, cfg)
        if "answer" not in got or got["answer"]:
            _fail(f"8. 图没有在 interrupt 处暂停: {got}")
        # 换一条全新连接 = 等价于进程重启；状态必须在库里，不在 graph 对象里
        async with aiosqlite.connect(str(path)) as conn:
            g2 = build(AsyncSqliteSaver(conn))
            st = await g2.aget_state(cfg)                     # ⚠ 不能用同步 get_state
            if not getattr(st, "next", None):
                _fail("8. 新连接看不到待恢复状态，resume 无从谈起")
            done = await g2.ainvoke(Command(resume="方案B"), cfg)
        return done

    done = asyncio.run(_go())
    if done.get("answer") != "方案B":
        _fail(f"8. 重启后 resume 未把人工回答接回图中: {done}")


# ---------- 9. 团队图默认内存、注入才落盘（内核自测无磁盘副作用） ----------
def t9_kernel_tests_leave_no_disk():
    import codeharness.environment.team_graph as tg
    from codeharness.configs.settings import settings
    before = set(Path(".").rglob("checkpoints.db"))
    graph = build_team({"PM": _Wrap("PM")}, sop={})
    after = set(Path(".").rglob("checkpoints.db"))
    if before != after:
        _fail("9. 默认构造就写了 checkpoint 文件，自测会产生磁盘副作用")
    if not hasattr(graph, "ainvoke"):
        _fail("9. build_team 没返回可执行图")


def t10_default_agents_cover_sop_targets():
    """兜底组队的角色名必须与 SOP 目标名一一对上。
    实测缺陷：runner 不传 agents 时走 default_team（TeamLeader/Alice/Bob），三个名字无一在 SOP 表内，
    LangGraph 只打一行 "Ignoring unknown node name PM"，整场会话零次 LLM 调用就算跑完。"""
    import codeharness.team as T
    from codeharness.provider.fake import FakeLLM
    keep, T._make_llm = T._make_llm, lambda cost_manager=None: FakeLLM(["{}"])
    try:
        agents = T._default_agents()
    finally:
        T._make_llm = keep
    targets = {n for names in SOP.values() for n in names}
    missing = targets - set(agents)
    if missing:
        _fail(f"10. 兜底组队缺 SOP 目标节点: {sorted(missing)}，图会静默丢 Send")
    overlap = targets & set(T.default_team(FakeLLM(["{}"])))
    if overlap:
        _fail(f"10. default_team 与经典线同名({sorted(overlap)})，换错兜底就测不出来了")


def t11_classic_team_watch_covers_sop():
    """经典组队的 watch 与 SOP 路由表必须双向自洽（真模型第十一处的根因门禁）。
    实测链：classic_team 没给 watch → `Agent._observe` 默认只订阅 UserRequirement →
    路由进来的消息整条被丢 → Architect/PMManager 拿空记忆让模型编造产物、
    Engineer 空 filename 烧一次真钱再被产物仓写拒吹掉整场会话。
    断言打在生产组队的表上，不是 e2e 手搭的那套——两套表各长各的，正是这个洞的成因。"""
    import codeharness.team as T
    from codeharness.provider.fake import FakeLLM
    from codeharness.actions.write_code import WriteCode
    from codeharness.const import RequirementTag
    keep, T._make_llm = T._make_llm, lambda cost_manager=None: FakeLLM(["{}"])
    try:
        agents = T._default_agents()
    finally:
        T._make_llm = keep
    for cause_by, targets in SOP.items():
        for t in targets:
            if t not in agents:
                _fail(f"11. SOP 路由目标 {t} 不在组队里（{cause_by}）")
            if cause_by not in agents[t].watch:
                _fail(f"11. {t} 的 watch={sorted(agents[t].watch)} 收不到路由它的 {cause_by}——"
                      f"消息会在 _observe 被静默丢掉")
    for name, ag in agents.items():
        orphans = {w for w in ag.watch if w not in SOP and w != RequirementTag.USER_REQUIREMENT}
        if orphans:
            _fail(f"11. {name} 订阅了 SOP 里不存在的 tag: {sorted(orphans)}")
    eng = agents["Engineer"]                      # 行为级：表配对了，消息真收得到
    got = asyncio.run(eng._observe({"inbox": [Message(content="写 main.py",
                                                      cause_by=RequirementTag.WRITE_TASKS)],
                                    "memory": []}))
    if not got["inbox"]:
        _fail("11. Engineer._observe 仍把 WriteTasks 丢出收件箱")
    fake = FakeLLM(["```python\nx=1\n```"])       # 软失败：空 filename 不进模型、不抛错
    soft = asyncio.run(WriteCode(llm=fake).run(Message(content="无上下文的触发")))
    if fake.calls or "filename" not in soft.content:
        _fail(f"11. WriteCode 空 filename 未软失败（llm.calls={len(fake.calls)}）: {soft.content!r}")


def t12_action_exception_feeds_back():
    """第十二处门禁：Action 抛错=错误消息回喂记忆（产物仓写拒是对的，吹掉整场会话不是）；
    GraphInterrupt 是唯一必须照抛的异常（interrupt/resume 机制靠它暂停图）。"""
    from langgraph.errors import GraphInterrupt
    from codeharness.roles.agent import Agent

    class Boom(BaseAction):
        output_schema = Rec
        async def run(self, msg: Message) -> Message:
            raise ValueError("非法产物文件名 '/main.py'")

    class BoomInterrupt(BaseAction):
        output_schema = Rec
        async def run(self, msg: Message) -> Message:
            raise GraphInterrupt()

    def st(name, act):
        return {"name": "E", "inbox": [Message(content="go", cause_by="WriteTasks")],
                "memory": [], "action_cursor": -1, "chosen": act, "loops": 1, "output": []}

    ag = Agent({"name": "E", "profile": "p", "goal": "g"},
               [Boom(llm=None), BoomInterrupt(llm=None)], None, max_loops=2)
    r = asyncio.run(ag._act(st("E", "Boom")))
    out = r["output"][0]
    if not out.content.startswith("[错误]") or "非法产物文件名" not in out.content or out.cause_by != "Boom":
        _fail(f"12. Action 异常未回喂自愈: {out.content!r}")
    if out not in r["memory"]:
        _fail("12. 错误消息没进记忆，下一轮想修都没得看")
    try:
        asyncio.run(ag._act(st("E", "BoomInterrupt")))
        _fail("12. GraphInterrupt 被吞了——interrupt/resume 会静默失效")
    except GraphInterrupt:
        pass


def t13_engineer_cr_wired_in_order():
    """接线台账 #2/#3 收口：装配顺序=业务顺序——Engineer 一场激活必须 写→评审→摘要 三件全跑；
    PM 前置 PrepareDocuments（源 product_manager.py:45-46 固定 SOP）。断言打在**生产组队**
    （classic_team）的动作输出上，并核 registry 同形态——"两套表各长各的"教训的三张表版
    （team × registry × e2e）互洽钉。"""
    import json
    import shutil
    import codeharness.runtime as rt
    import codeharness.team as T
    from codeharness.const import DocName, RepoName, RequirementTag
    from codeharness.document_store.artifact_store import ArtifactStore
    from codeharness.provider.fake import FakeLLM
    from codeharness.roles.registry import build_role
    from codeharness.runtime import CURRENT_PROJECT
    from codeharness.schema import Message

    PRD = {"language": "en_us", "programming_language": "python", "original_requirements": "x",
           "project_name": "p", "product_goals": ["g"], "user_stories": ["u"],
           "competitive_analysis": ["a"], "competitive_quadrant_chart": "q",
           "requirement_analysis": "ra", "requirement_pool": [["P0", "c"]],
           "ui_design_draft": "s", "anything_unclear": ""}
    llm = FakeLLM([json.dumps(PRD),                                     # PM: WritePRD
                   "```python\ndef add(a, b):\n    return a + b\n```",  # Eng: WriteCode
                   "## Code Review Result\nLGTM",                       # Eng: WriteCodeReview 首轮即过
                   "变更摘要：新增 main.py"])                            # Eng: SummarizeCode
    CURRENT_PROJECT.set("s3b_cr")
    store = ArtifactStore.active()
    shutil.rmtree(store.root, ignore_errors=True)

    def drive(agent, name, msg):
        return asyncio.run(agent.build().ainvoke(
            {"name": name, "inbox": [msg], "memory": [], "action_cursor": -1,
             "chosen": "", "loops": 0, "output": []}))
    try:
        agents = T.classic_team(llm)
        pm_out = drive(agents["PM"], "PM",
                       Message(content="做个加法库", cause_by=RequirementTag.USER_REQUIREMENT))
        assert [m.cause_by for m in pm_out["output"]] == ["PrepareDocuments", "WritePRD"], \
            [m.cause_by for m in pm_out["output"]]
        assert (store.root / RepoName.DOCS / DocName.REQUIREMENT).exists(), "requirements 无人落盘"

        eng_out = drive(agents["Engineer"], "Engineer",
                        Message(content="实现 add", role="user", cause_by=RequirementTag.WRITE_TASKS,
                                instruct_content={"filename": "main.py"}, instruct_schema="CodingContext"))
        causes = [m.cause_by for m in eng_out["output"]]
        assert causes == ["WriteCode", "WriteCodeReview", "SummarizeCode"], f"写后评审没通电: {causes}"
        assert (store.root / RepoName.SRC / "main.py").exists()
        review = eng_out["output"][1]
        assert review.instruct_content and review.instruct_content.get("review") == "LGTM", review

        reg_eng = build_role("Engineer", FakeLLM(["{}"]))
        assert set(reg_eng.actions) == set(agents["Engineer"].actions), "registry 与生产组队动作表分叉"
        assert reg_eng.react_mode == agents["Engineer"].react_mode == "BY_ORDER"
    finally:
        shutil.rmtree(rt.session_root("s3b_cr"), ignore_errors=True)
        rt.CURRENT_PROJECT.set("")


def t14_dynamic_paradigm_assembly():
    """S9.1 同范式对照的接线（台账 #19①）：dynamic_assembly 两张表（组队 × 路由）自洽 +
    runner._prepare 按 paradigm 分流。本仓动态形态=单 RoleZero 工具循环（无委派路由），
    需求只喂 TeamLeader——t10 的教训在这同样成立：路由表目标与组队名对不上就是零调用。"""
    from codeharness.provider.fake import FakeLLM
    from codeharness.team import dynamic_assembly
    from codeharness.roles.role_zero import RoleZero
    from codeharness.const import TEAMLEADER_NAME
    agents, sop = dynamic_assembly(FakeLLM([]))
    assert set(agents) == {TEAMLEADER_NAME, "Alice", "Bob"}, sorted(agents)
    assert all(isinstance(a, RoleZero) for a in agents.values())
    for tag, targets in sop.items():
        assert all(t in agents for t in targets), f"{tag} 路由到组队外的名字——零调用洞复发"
    assert sop[RequirementTag.USER_REQUIREMENT] == [TEAMLEADER_NAME]

    import codeharness.team as team
    from server.runner import SessionRunner
    import server.sessions as ss
    captured = {}

    def fake_prepare(idea, project, agents=None, checkpointer=None, cost_manager=None, sop=None):
        captured["agents"], captured["sop"] = agents, sop
        return object(), {}, None

    async def _none_saver():
        return None
    saved = team.prepare_project
    team.prepare_project = fake_prepare
    try:
        # 批次28（81acdc7）起 `_prepare` 会把装配出口的 roles/entry_role 回填进 store，
        # 生产里 store 由 app 装配注入、永不为 None → 测试替身得跟上，否则这里 `None.update` 直接炸。
        class _FakeStore:
            def __init__(self):
                self.writes = []

            def update(self, sid, **fields):
                self.writes.append((sid, fields))

        store = _FakeStore()
        r = SessionRunner(store, None)
        r._saver = _none_saver
        dyn = ss.Session(id="d1", idea="x", project_name="p", paradigm="dynamic")
        asyncio.run(r._prepare(dyn, "p", None))
        assert captured["agents"] and TEAMLEADER_NAME in captured["agents"], captured
        assert captured["sop"] is not None, "dynamic 没带路由表"
        cls = ss.Session(id="c1", idea="x", project_name="p")
        asyncio.run(r._prepare(cls, "p", None))
        # 旧断言是 `agents is None`（经典线走 prepare_project 的兜底组队），runner 已改成
        # **三条线都显式组队**（`server/runner.py:142-144`：兜底路径会另建一个不认
        # llm_override 的网关）→ 现在钉的是「classic 也显式组队、名字集等于 classic_team，但不带动态路由表」。
        from codeharness.team import classic_team
        assert captured["agents"] and set(captured["agents"]) == set(classic_team(FakeLLM([]))), \
            "classic 必须显式组队（否则会话的 llm_override 不生效）"
        assert captured["sop"] is None, "classic 不该带动态路由表"
        # 9.2 策略曲线第三腿：react=经典队形全员 REACT（路由表仍是经典 SOP，sop 传 None）
        from codeharness.roles.agent import Agent
        rct = ss.Session(id="r1", idea="x", project_name="p", paradigm="react")
        asyncio.run(r._prepare(rct, "p", None))
        assert captured["agents"] and all(
            isinstance(a, Agent) and a.react_mode == "REACT" for a in captured["agents"].values()), \
            "react 腿必须是经典队形×REACT 循环"
        assert captured["sop"] is None, "react 腿不改编排"
        assert any("roles" in f and "entry_role" in f for _, f in store.writes), \
            "装配出口没把 roles/entry_role 回填进 store（批次28 那半件必须被断言覆盖）"
        # 9.3 扩展线：sop 非空走模板装配，不再经 prepare_project（captured 不动）
        from codeharness.base.action import BaseAction
        from codeharness.roles.agent import Agent
        from codeharness.sop.templates import _EXT_TEMPLATES, SopTemplate, register_template

        class _Noop(BaseAction):
            async def run(self, msg):
                return msg

        register_template(SopTemplate(
            name="s3b_t14_sop", desc="门禁件",
            assemble=lambda llm: {"Solo": Agent({"name": "Solo", "profile": "p", "goal": "g"},
                                                [_Noop(llm=llm)], llm)},
            edges={RequirementTag.USER_REQUIREMENT: ["Solo"]}))
        try:
            captured.clear()
            sp = ss.Session(id="s1", idea="x", project_name="p", sop="s3b_t14_sop")
            team3, cfg3, init3 = asyncio.run(r._prepare(sp, "p", None))
            assert captured == {}, "sop 会话不该再走 prepare_project"
            assert team3 is not None and init3 is not None
            # ③（09-26 用户拍板「名字与唯一键解耦」）：装配键是**会话 id**，不是项目目录名。
            # ⚠ 这一格原先钉的是「thread_id 必须取会话项目名（防串台）」——那句判据的**前提**已被拍板
            # 改掉（项目名可重名，拿它当身份才是串台的来源），按 §4 第 3 条当场改判，不是把代码退回去。
            assert cfg3["configurable"]["thread_id"] == sp.id, \
                f"thread_id 必须是会话 id（③），实际 {cfg3['configurable']['thread_id']!r}（sid={sp.id}）"
            sp2 = ss.Session(id="s2b", idea="x", project_name="p", sop="s3b_t14_sop")
            _, cfg4, _ = asyncio.run(r._prepare(sp2, "p", None))
            assert cfg4["configurable"]["thread_id"] == "s2b" \
                and cfg4["configurable"]["thread_id"] != cfg3["configurable"]["thread_id"], \
                "同名两场（project_name 都是 'p'）拿到了同一个 thread_id —— 正是 ③ 要治的串台"
        finally:
            _EXT_TEMPLATES.pop("s3b_t14_sop", None)
    finally:
        team.prepare_project = saved

    # 行为级（第十七处常驻门禁）：任务文本必须出现在模型请求里——as_node 曾只设 _plan_goal
    # 不入 memory，think 的上下文里根本没有需求，真模型第一条思考=「没有具体用户需求」。
    import json as _json
    script = _json.dumps({"thought": "完成", "commands": [{"command_name": "end", "args": {}}]})
    leader_llm = FakeLLM([script])
    agents2, sop2 = dynamic_assembly(leader_llm)
    agents2[TEAMLEADER_NAME].ltm = None     # 行为检查只钉 inbox→memory→请求这一跳；ltm 召回打 qdrant（本机可无）
    # 走真路径（外层 build_team + thread_id）：内层图带 checkpointer，直调 as_node 没有父级 config 可继承
    gdyn = build_team(agents2, sop=sop2)
    idea = "实现一个命令行工具 tinycli"
    asyncio.run(gdyn.ainvoke(
        {"messages": [Message(content=idea, role="user", cause_by=RequirementTag.USER_REQUIREMENT)],
         "memories": {}, "debug_rounds": 0, "finished": False},
        {"configurable": {"thread_id": "t14dyn"}}))
    flat = [m for call in leader_llm.calls for m in (call if isinstance(call, list) else [call])]
    assert any(idea in getattr(m, "content", str(m)) for m in flat), \
        "任务文本没进模型请求——第十七处复发（inbox→memory 断链）"


def t15_no_dead_state_channels():
    """C2：`TeamState.docs` 是「看起来有其实没有」的假通道——三处 init 写 `{}` 后零读零写，
    文档交接实际全走磁盘 `ArtifactStore`（`actions/import_repo.py:121` 的 `save(subdir="docs")` 就是证据）。
    已连同三处写入与 msgpack 白名单里的 `Document`/`Documents` 一并删掉。

    必须留正向断言，不能指望运行期炸：**实测 LangGraph 对未知的状态键是静默丢弃**
    （删掉 TypedDict 字段后 `tests/s16_route_state.py` 五组仍全绿、exit 0），
    所以「有人又把 docs 写回 init」会以「写了没人读」的原病复发形态悄悄长回来。两层钉：
    ① 类型键集里不许有 docs；② 三个生产入口源码里不许再出现 "docs" 初值
    （源码文本级反向守卫，先例同 s20 t1 的 `assert "## 历史对话" not in content`）。"""
    from pathlib import Path
    import codeharness

    assert "docs" not in TeamState.__annotations__, \
        f"docs 假通道长回来了：TeamState 键集 {sorted(TeamState.__annotations__)}"
    # C14：`round` 同族（三处 init 写 0、全仓零读零写），C13 落了真游标 `seen` 之后它就纯剩死重，
    # 一起摘掉并钉同一层守卫。判据吃的是 `TeamState.__annotations__`，不看运行期——
    # LangGraph 对未知状态键静默丢弃，写者残留永远炸不出来（本函数开头那条实测）。
    assert "round" not in TeamState.__annotations__, \
        f"round 死键长回来了：TeamState 键集 {sorted(TeamState.__annotations__)}"
    assert "seen" in TeamState.__annotations__ and "undelivered" in TeamState.__annotations__, \
        "C13 的路由游标不见了（同超步多条产出会退回「只投最后一条」的老毛病）"
    root = Path(codeharness.__file__).parent
    for rel in ("team.py", "sop/builder.py", "environment/team_graph.py"):
        src = (root / rel).read_text(encoding="utf-8")
        assert '"docs"' not in src and "docs=" not in src, \
            f"{rel} 里又出现往图状态写 docs 的地方——产物通道是磁盘 ArtifactStore，不是 TeamState"
        assert '"round"' not in src, f"{rel} 里又出现往图状态写 round 初值的地方"
    # 游标刻意不进 init：跑完的会话再 start 时 messages 是追加，init 里带 seen=0 会把整条历史重投一遍
    for rel in ("team.py", "sop/builder.py"):
        src = (root / rel).read_text(encoding="utf-8")
        assert '"seen"' not in src and '"undelivered"' not in src, \
            f"{rel} 的 init 里写进了路由游标——那会让复用的 thread 把历史全量重投"
    print("  t15 docs/round 两个死键都没复燃、C13 游标在位且没被写进 init")


def t16_run_code_named_delivery():
    """批次4：全仓唯一生产级具名投递（run_code.py:97-109 QA 分诊）两分支的行为断言。
    ok→<self> 自环；fail 且复盘 'Send To: Engineer'→具名 Engineer。此前裸奔（s3b t3 用合成 QA）。"""
    import sys
    from codeharness.runtime import CURRENT_PROJECT
    from codeharness.provider.fake import FakeLLM
    from codeharness.actions.run_code import RunCode
    from codeharness.schema import Message, RunCodeContext

    CURRENT_PROJECT.set("s3b_delivery")
    # 分支①：命令成功（rc=0）→ <self>
    ok_ctx = RunCodeContext(command=[sys.executable, "-c", "pass"], code_filename="a", test_filename="t")
    msg_ok = Message(content="run", role="user", instruct_content=ok_ctx.model_dump(),
                     instruct_schema="RunCodeContext")
    r_ok = asyncio.run(RunCode(llm=FakeLLM(["## Send To:\nQaEngineer"])).run(msg_ok))
    assert MESSAGE_ROUTE_TO_SELF in r_ok.send_to, f"成功应自环 <self>，实际{r_ok.send_to}"
    assert r_ok.instruct_schema == "RunCodeContext", f"自环应带 RunCodeContext，实际{r_ok.instruct_schema}"

    # 分支②：命令失败（rc≠0）+ 复盘 'Send To: Engineer' → 具名投 Engineer
    fail_ctx = RunCodeContext(command=[sys.executable, "-c", "raise SystemExit(3)"],
                              code_filename="a", test_filename="t")
    msg_fail = Message(content="run", role="user", instruct_content=fail_ctx.model_dump(),
                       instruct_schema="RunCodeContext")
    r_fail = asyncio.run(RunCode(llm=FakeLLM(["## Send To: Engineer"])).run(msg_fail))
    assert r_fail.send_to == {"Engineer"}, f"失败+分诊 Engineer 应具名投 Engineer，实际{r_fail.send_to}"
    assert r_fail.instruct_schema == "CodingContext", f"具名投递应带 CodingContext，实际{r_fail.instruct_schema}"
    assert "filename" in (r_fail.instruct_content or {}), "CodingContext 必须有 filename（extra=forbid 校验）"


def t17_react_think_survives_a_broken_structured_reply():
    """C76（09-28 全量审查批）：REACT 档的 `_think` 必须扛住**解析不回来的 structured 回包**。

    现象：`roles/agent.py` 的 REACT 支裸调 `self.llm.structured(ActionChoice).ainvoke(...)`，且整个函数体
    没有 try；而网关在「严格解析失败 + `repair_to_model` 也救不回」时是 **raise**
    （`provider/gateway.py::_Wrapped.ainvoke`）⇒ 异常穿出角色子图，被 `server/runner.py` 的 `_fail` 判成
    **整场 failed**（经典线 = 默认范式，走的就是这条路），已完成的产物与已花的钱一起陪葬。对照
    `RoleZero._think` 有「structured 失败 → 纯文本重问 → `llm_repair_json` → 按 end 收口」整条链。
    修法：补同款兜底（真网关的 `aask` 吃 str/list，FakeLLM 只吃 str ⇒ 统一传 str）。

    三格（零花费、零外网，走真 Agent 子图）：
      ① 坏回包（非 JSON）**不抛**，终态 `chosen == "END"`（本轮收工，不是把整场打死）；
      ② 留了可 grep 的 `[agent-structured-fallback]` 警告（静默降级不许）；
      ③ 阳性对照：正常剧本**不走**兜底，`chosen` 就是脚本里那个动作名。
    """
    import io

    from codeharness.logs import logger
    from codeharness.provider.fake import FakeLLM      # s3b 里 FakeLLM/logger 都是局部导入（不在模块头）

    class _A1(BaseAction):
        async def run(self, msg):
            return msg

    class _A2(BaseAction):
        async def run(self, msg):
            return msg

    def _run(script):
        llm = FakeLLM(script)                       # 两个动作 ⇒ 不走「单动作直选」那条短路
        agent = Agent({"name": "T", "profile": "p", "goal": "g"}, [_A1(llm=llm), _A2(llm=llm)], llm)
        graph = agent.build()
        buf = io.StringIO()
        hid = logger.add(buf, format="{message}", level="WARNING")     # 与 s4/s17 同款抓法
        try:
            out = asyncio.run(graph.ainvoke({
                "name": "T", "inbox": [Message(content="干活", role="user")], "memory": [],
                "action_cursor": -1, "chosen": "", "loops": 0, "output": []}))
        finally:
            logger.remove(hid)
        return out, buf.getvalue()

    out_bad, log_bad = _run(["{不是 JSON", "END"])          # 第一发 structured 必校验失败
    assert out_bad["chosen"] == "END", \
        f"t17① 坏回包下没按 END 收口（修复前是抛穿节点 ⇒ 整场 failed）：chosen={out_bad['chosen']!r}"
    assert "[agent-structured-fallback]" in log_bad, \
        f"t17② 走了兜底却没留可 grep 的 warning：{log_bad[:200]!r}"

    out_ok, log_ok = _run(['{"thought": "选第一个", "action": "A1"}'])
    assert out_ok["chosen"] == "A1", f"t17③ 正常剧本没照选（阳性对照不成立）：{out_ok['chosen']!r}"
    assert "[agent-structured-fallback]" not in log_ok, \
        f"t17③ 正常回包也触发了兜底（判据在数错东西）：{log_ok[:200]!r}"
    print("  ok  t17 C76：坏回包 ⇒ 不抛且按 END 收口 + 留 [agent-structured-fallback] 警告；正常回包不走兜底")


def t18_log_file_sink_is_bounded():
    """C106（09-30 审查文档 §3 P1 第四件）：`logs.py` 导入期那颗文件 sink 必须有上界，且文件那本真在写。

    现证（09-30 本机，全部指回输出）：`logs/` 共 55 MB，`20260919.txt` 46,679,698 B / 208,573 行，
    而原形状 `_logger.add(METAGPT_ROOT / f"logs/{log_name}.txt", level=logfile_level)` **既无
    `rotation=` 也无 `retention=`** ⇒ 单本体积无上限。

    两处把审查文档的归因**修正**了（数字对、因果不对）：
    ① 09-19 那 46.7 MB 里 31.5 MB 出自**一个**调用点 `brain_memory:_get_summary:162`——99,934 行、
       **最长行只 315 B**、去重后**唯一内容 6 条**（「六轮压缩成一句」「短摘要」「sss…」＝FakeLLM 剧本串），
       峰值 **1,014 行/秒**、只落在 132 个不同秒里，同窗口另有 `Redis GET s5gate:<hex>` 这种**门禁专用键**
       ⇒ 那是**门禁跑批**打的，不是「每个角色的每轮」的业务量。上界照样要加，但受益方是长跑的机器。
    ② loguru 的 `retention` 管不到跨日文件（两次实测）：`retention=3` ⇒ 只留**本 sink 同名族**的
       3 份轮转 + 本体（同目录另一颗 sink 的 `day2.txt` 族一个不动）；`retention='7 days'` ⇒
       连 mtime 8 天前的 `20260922.txt` 都不删。⇒ 按天命名 + 这两个参数封的是**单日本体的顶**，
       不是目录总量。这条界写进 `logs.py` 的 ponytail 注释里，不当「已解决」收。

    顺带排除一条看着像缺陷的：文件 sink 没写 `encoding=`，但干净子进程实测
    （`locale.getpreferredencoding()=='cp936'`、`sys.flags.utf8_mode==0`）loguru 落的仍是 utf-8
    ⇒ 不是缺陷，不补参数（补了是噪声）。

    三格：
      ① AST 结构判：那颗**指向 `METAGPT_ROOT`** 的 `_logger.add` 必须带 `rotation=` 与 `retention=`
         （且 `level=` 不许顺手丢）；
      ② ①的仪器对照：同一判定喂「无参／只有 rotation／只有 stderr」三种形状，读数必须分别是
         「缺两参／缺 retention／认不出文件 sink」——否则 ① 是恒绿摆设；
      ③ 运行时判（不碰 loguru 私有属性）：把 `METAGPT_ROOT` 指向临时目录后调 `define_log_level()`，
         断言真落了一本、探针行写得进去、且那本**能按 utf-8 读回**（中文行不被写成 GBK）。
         ⚠ `define_log_level` 里那句 `_logger.remove()` 是**全局**动作 ⇒ 本格在 finally 里把
         `METAGPT_ROOT` 复位并重新 `define_log_level()`，把进程日志面还原回导入时的形状。
    """
    import ast
    import shutil

    import codeharness
    from codeharness import logs as L

    # 用 `codeharness.__file__` 定位源码，不用 `__file__` 的相对层数（本面 t15 同款取法，:572）：
    # 后者把判据钉死在 tests/ 目录里，工装单独跑一次就指到别处去（本轮实测 FileNotFoundError）。
    src = (Path(codeharness.__file__).parent / "logs.py").read_text(encoding="utf-8")

    def file_sink(text):
        """取「第一个实参指向 `METAGPT_ROOT`」的那颗 `_logger.add`，返回 (行号, 关键字集合)；没有就 None。

        ⚠ 匹配对象是**第一个实参**不是接收者：`_logger.add` 的接收者 unparse 出来是 `_logger.add`，
        拿它去找 `METAGPT_ROOT` 会永远找不到——本轮工装单验就是这么炸出这个洞的（判据恒红，
        比恒绿幸运，但同样是没牙的形状）。
        """
        found = []
        for n in ast.walk(ast.parse(text)):
            if not (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "add"):
                continue
            if not (n.args and "METAGPT_ROOT" in ast.unparse(n.args[0])):
                continue
            found.append((n.lineno, frozenset(k.arg for k in n.keywords)))
        assert len(found) <= 1, f"锚点不唯一（文件 sink 长出了第二颗？）：{found}"
        return found[0] if found else None

    # ---- ① 结构判 ----
    got = file_sink(src)
    assert got, "①靶子没了：`logs.py` 里找不到指向 `METAGPT_ROOT` 的 `_logger.add`"
    lineno, kw = got
    assert "rotation" in kw and "retention" in kw, \
        f"①文件 sink（:{lineno}）没有上界参数，实有关键字 {sorted(kw)}（单日可达几十 MB 的那道闸就在这）"
    assert "level" in kw, f"①连 `level=` 都丢了（那本日志的等级被改走样）：{sorted(kw)}"

    # ---- ② 仪器对照 ----
    shapes = {
        "无参": ('def define_log_level():\n    _logger.add(METAGPT_ROOT / "logs/a.txt", level="DEBUG")\n'),
        "只有rotation": ('def f():\n    _logger.add(METAGPT_ROOT / "x", level="DEBUG", rotation="10 MB")\n'),
        "只有stderr": 'def f():\n    _logger.add(sys.stderr, level="INFO")\n',
    }
    r_none = file_sink(shapes["无参"])
    assert r_none and not ({"rotation", "retention"} <= r_none[1]), \
        f"②仪器坏了：改前形状被判成「有上界」（①会恒绿），实得 {r_none}"
    r_half = file_sink(shapes["只有rotation"])
    assert r_half and "retention" not in r_half[1], \
        f"②仪器只盯 rotation：`retention` 丢了没人喊（半截闸），实得 {r_half}"
    assert file_sink(shapes["只有stderr"]) is None, \
        "②仪器把 stderr sink 当成文件 sink（靶子会漂，将来加第三颗 sink 就假绿）"

    # ---- ③ 运行时：临时根目录里真落一本、写得进、读回是 utf-8 ----
    tmp = Path(tempfile.mkdtemp(prefix="s3b_t18_"))
    keep_root = L.METAGPT_ROOT
    marker = "C106 探针：中文行 中文字"
    try:
        L.METAGPT_ROOT = tmp
        L.define_log_level(print_level="ERROR", logfile_level="DEBUG", name="t18probe")
        L.logger.debug(marker)
        # 路径里带一层 `logs/`（`METAGPT_ROOT / f"logs/{log_name}.txt"`），所以递归找而不是只看根目录
        # ——本轮工装单验第一次就因这个 glob 少一层而假报「没落本子」。
        books = sorted(tmp.rglob("*.txt"))
        assert books, f"③文件 sink 没落任何本子（临时根里有：{[p.name for p in tmp.iterdir()]}）"
        body = books[0].read_text(encoding="utf-8")   # 按 utf-8 读不动就炸——那正是这一格要量的
        assert marker in body, f"③落了本子却没有探针行：{body[:150]!r}"
        n_files = len(books)
    finally:
        L.METAGPT_ROOT = keep_root
        L.define_log_level()                          # `_logger.remove()` 是全局动作，必须复位
        shutil.rmtree(tmp, ignore_errors=True)
    assert not tmp.exists(), f"③清场没做成，{tmp} 还在"

    print(f"  ok  t18 C106：文件 sink（:{lineno}）带 rotation+retention，②的三种形状读数各对，"
          f"③临时根里落了 {n_files} 本且中文行按 utf-8 读回原样")


def t19_blocked_think_degrades_not_dies():
    """C154（C116 档位②）：供应侧拦停的那一发在 `_think` 里降级——**不发重问**（改前实际形状是
    structured 拦 → fallback 把同一内容原样重问 → 再拦 → 整场 failed；451 重发必再拦，与
    `gateway._retryable` 不重发 4xx 同一条理由）。降级成通告 thought + 零命令，走既有 end 契约
    ⇒ Worker 被拦=该角色优雅收工（图继续）、Leader 被拦=整场收进 finished，产出保留，
    `[session-failed]` 不再因此响。"""
    import httpx
    from openai import APIStatusError
    from codeharness.roles.role_zero import RoleZero
    from codeharness.provider.gateway import blocked_reason

    resp451 = httpx.Response(451, request=httpx.Request("POST", "http://127.0.0.1:1/v1/chat/completions"))
    blocked = APIStatusError(
        "Error code: 451 - {'error': {'message': 'The content you provided or machine outputted is blocked.',"
        " 'type': 'censorship_blocked'}}",
        response=resp451,
        body={"error": {"message": "The content you provided or machine outputted is blocked.",
                        "type": "censorship_blocked"}})

    class BlockedLLM:
        def __init__(self):
            self.aask_calls = 0

        def structured(self, cls):
            class _B:
                async def ainvoke(self, msgs, **kw):
                    raise blocked
            return _B()

        async def aask(self, *a, **kw):
            self.aask_calls += 1
            raise AssertionError("fallback 重问不该发生（451 同款内容重发必再拦）")

    async def go():
        role = RoleZero({"name": "Alice", "profile": "Product Manager", "goal": "g"},
                        [], BlockedLLM(), brain=None)
        s = {"task": "写个 prd", "history": [], "respond_language": "中文", "finished": False,
             "act_cursor": 0, "pending_ask": False}
        out = await role._think(s)
        entry = out["history"][-1]
        assert "拦停" in entry["thought"] and "保留" in entry["thought"], entry["thought"]
        assert entry["commands"] == [{"command_name": "end", "args": {}}], entry["commands"]
        assert role.llm.aask_calls == 0                        # 没走 repair 重问
        # 反向对照：普通 400（我们自己把调用写坏）不算拦停——repair 管线的地盘不许被误判抢走
        resp400 = httpx.Response(400, request=httpx.Request("POST", "http://127.0.0.1:1/v1/chat/completions"))
        bad = APIStatusError("Error code: 400 - invalid_request_error", response=resp400, body=None)
        assert blocked_reason(bad) is None

    asyncio.run(go())
    print("  ok  t19 C154：拦停 think 降级成通告+end（零重问、零炸场），普通 400 不误判为拦停")


def main():
    checks = [t1_by_order_runs_all_actions, t2_precise_activation, t3_explicit_send_to,
              t3b_chat_to_unknown_role_is_dropped,
              t4_self_to_unknown_node, t5_subscribe_is_falsifiable, t6_all_is_not_broadcast,
              t7_checkpointer_persists, t8_interrupt_resume_across_restart,
              t9_kernel_tests_leave_no_disk, t10_default_agents_cover_sop_targets,
              t11_classic_team_watch_covers_sop, t12_action_exception_feeds_back,
              t13_engineer_cr_wired_in_order, t14_dynamic_paradigm_assembly,
              t15_no_dead_state_channels, t16_run_code_named_delivery,
              t17_react_think_survives_a_broken_structured_reply,
              t18_log_file_sink_is_bounded,
              t19_blocked_think_degrades_not_dies]
    try:
        for c in checks:
            c()
            print(f"  ok  {c.__name__}")
        # 收尾必须关连接：否则 aiosqlite 后台线程在解释器退出时报 Event loop is closed，
        # 表现为"断言全过但退出码 1"，这样的门禁不可信。放在成功宣言之前，关不掉就不许宣布通过。
        asyncio.run(close_all())
    finally:
        # **异常路径同样要关**：中途抛错时若不关，非守护的 aiosqlite 线程会把解释器吊住永不退出，
        # 于是「一格断言失败」看起来像「整套门禁挂死」（实测 2026-09-21：t15 抛 NameError 后进程活了
        # 6.5 分钟；上一轮那条「s3b 管道跑 26 分钟不返回」的悬案就是这个，不是 grep 缓冲）。
        # 两处各调一次是安全的：`close_all()` 先把 _cache 取空再逐个关（checkpoint.py:102-104），
        # 第二次看到的是空表——幂等。别"顺手"删掉 try 里那次，否则又回到关不掉也宣布通过。
        asyncio.run(close_all())
    print(f"\nS3(b) 门禁通过：{len(checks)} 组 —— R3 路由 5 组（BY_ORDER 全跑完/精准激活/显式指名/"
          f"<self> 目标校验/订阅可证伪）+ R4a 持久化 1 组 + R5 interrupt-resume 跨实例 1 组 + "
          f"设计决定 1 组（<all> 不广播）+ 自测无磁盘副作用 1 组 + 兜底组队与 SOP 目标名自洽 1 组 + "
          f"watch 与 SOP 双向自洽含 WriteCode 软失败 1 组 + Action 异常回喂自愈含 GraphInterrupt 照抛 1 组 + "
          f"写→评审→摘要生产装配 1 组 + C2 假通道不复燃守卫 1 组（TeamState 无 docs 键 + 三处初值源码无写入）+ "
          f"生产级具名投递两分支 1 组 + REACT 档坏回包兜底 1 组（C76）+ C106 日志上界 1 组（t18：`logs.py` 那颗文件 sink 必须带 rotation/retention，三种形状对照各对，临时根里真落一本且中文行按 utf-8 读回）")


if __name__ == "__main__":
    main()
