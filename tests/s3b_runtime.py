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
from codeharness.environment.team_graph import SOP, TeamState, build_team, make_route
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
                      memories={}, docs={}, round=0, debug_rounds=0, finished=False)
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
                      memories={}, docs={}, round=0, debug_rounds=0, finished=False)
    sends = route(state)
    if [s.node for s in sends] != ["QA"]:
        _fail(f"3. 显式指名的 send_to 未被路由: {sends}")


# ---------- 4. <self> 目标不存在时绝不能发 Send ----------
def t4_self_to_unknown_node():
    route = make_route({}, {"PM": _Wrap("PM")}, wiring={})
    state = TeamState(messages=[Message(content="c", cause_by="t", sent_from="Ghost",
                                        send_to={MESSAGE_ROUTE_TO_SELF})],
                      memories={}, docs={}, round=0, debug_rounds=0, finished=False)
    got = route(state)
    if got == END:
        return
    if any(getattr(s, "node", None) == "Ghost" for s in got):
        _fail("4. 向不存在的节点发 Send，LangGraph 会直接抛 Unknown node")


# ---------- 5. 订阅关系可证伪：改 cause_by 后下游必须收不到 ----------
def t5_subscribe_is_falsifiable():
    route = make_route(SOP, {n: _Wrap(n) for n in ("PM", "Architect")}, wiring={})
    base = dict(memories={}, docs={}, round=0, debug_rounds=0, finished=False)
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
                      memories={}, docs={}, round=0, debug_rounds=0, finished=False)
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


def main():
    checks = [t1_by_order_runs_all_actions, t2_precise_activation, t3_explicit_send_to,
              t4_self_to_unknown_node, t5_subscribe_is_falsifiable, t6_all_is_not_broadcast,
              t7_checkpointer_persists, t8_interrupt_resume_across_restart,
              t9_kernel_tests_leave_no_disk, t10_default_agents_cover_sop_targets,
              t11_classic_team_watch_covers_sop, t12_action_exception_feeds_back,
              t13_engineer_cr_wired_in_order]
    for c in checks:
        c()
        print(f"  ok  {c.__name__}")
    # 收尾必须关连接：否则 aiosqlite 后台线程在解释器退出时报 Event loop is closed，
    # 表现为"断言全过但退出码 1"，这样的门禁不可信。
    asyncio.run(close_all())
    print(f"\nS3(b) 门禁通过：{len(checks)} 组 —— R3 路由 5 组（BY_ORDER 全跑完/精准激活/显式指名/"
          f"<self> 目标校验/订阅可证伪）+ R4a 持久化 1 组 + R5 interrupt-resume 跨实例 1 组 + "
          f"设计决定 1 组（<all> 不广播）+ 自测无磁盘副作用 1 组 + 兜底组队与 SOP 目标名自洽 1 组 + "
          f"watch 与 SOP 双向自洽含 WriteCode 软失败 1 组 + Action 异常回喂自愈含 GraphInterrupt 照抛 1 组 + "
          f"写→评审→摘要生产装配 1 组")


if __name__ == "__main__":
    main()
