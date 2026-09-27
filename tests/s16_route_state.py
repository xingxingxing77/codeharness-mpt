"""B1 门禁：`debug_rounds` 刹车的写入口必须在节点里，条件边函数写 state 不持久化。

跑法：
  cd /e/Codeharness && PYTHONPATH=/e/Codeharness:/e/Codeharness/logs PYTHONIOENCODING=utf-8 \\
      PYTHONDONTWRITEBYTECODE=1 F:/anaconda/python.exe tests/s16_route_state.py

langgraph 版本 = **1.2.11**（t1 实测的语义属于这个版本，升级后 t1 会红，那是有意义的信号）。

三条刹车语义 + 两条 B9 回喂：
  t1 语义实验（总文档 §B1 的 20 行验证实验转正）：条件边里写 state 丢弃 vs 节点里写提交；
  t2 真图 QA `<self>` 自环（RUN_CODE）：三轮内刹车生效，不是打到 recursion_limit；
  t3 真图 DEBUG_ERROR 广播回路（QA↔Engineer）：同上。
  t4 真图 Action 抛错（B9）：错误消息回到 `<self>` → 角色被再激活，3 轮内收尾
     （修复前只激活 1 次就散会；只改 send_to 不放宽刹车则激活 12 次打到 recursion_limit）；
  t5 真图审批拒绝（B9）：被拒动作一次都不执行，角色照样被再激活并走同一道闸。
t2/t3 在 B1 修复前是红的（`debug_rounds` 恒 0 → 循环到 recursion_limit 抛 GraphRecursionError），
这正是总文档 B1 说的「刹车可能从未生效」的行为级读数。
"""
import asyncio
import importlib.metadata as md
from typing import TypedDict

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.errors import GraphRecursionError
from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel

from codeharness.base.action import BaseAction
from codeharness.const import (MESSAGE_ROUTE_TO_ALL, MESSAGE_ROUTE_TO_NONE, MESSAGE_ROUTE_TO_SELF,
                                RequirementTag)
from codeharness.environment.team_graph import build_team
from codeharness.roles.agent import Agent
from codeharness.schema import Message

LANGGRAPH_VERSION = md.version("langgraph")


class N(TypedDict):
    n: int
    seen: int


# ---------- 1. 语义实验：条件边写 state 丢不丢 ----------
def t1_conditional_edge_write_is_dropped():
    """A 节点返回 {} + 条件边内 `state["n"] += 1` → 值不进通道；节点返回值写同名键 → 进通道。

    第二轮起输入必须是 `{}`（续跑同一 thread）：再传 `{"n": 0}` 会把通道重置，
    那时「不持久化」和「被输入覆盖」读数完全一样，是个假阴性陷阱。"""
    async def node_a(state: N):
        return {}

    async def node_b(state: N):
        return {"seen": state.get("n", 0)}     # 走正规提交口，读到什么就是什么

    def edge_route(state: N):
        state["n"] = state.get("n", 0) + 1     # ⚠ 条件边函数内赋值
        return "b"

    g = StateGraph(N)
    g.add_node("a", node_a)
    g.add_node("b", node_b)
    g.add_edge(START, "a")
    g.add_conditional_edges("a", edge_route)
    g.add_edge("b", END)
    app = g.compile(checkpointer=InMemorySaver())
    cfg = {"configurable": {"thread_id": "s16-route-write"}}

    async def _run():
        seen = []
        for i in (1, 2, 3):
            out = await app.ainvoke({"n": 0, "seen": 0} if i == 1 else {}, cfg)
            seen.append(out["seen"])
        return seen

    seen = asyncio.run(_run())
    assert seen == [0, 0, 0], \
        f"t1 前提变了：条件边内写 state 现在会持久化（langgraph {LANGGRAPH_VERSION}）→ {seen}；" \
        f"若确实改了语义，team_graph 的注释与 B9 的计数口径要一起复审"

    # 对照组：同样的图，写入挪到节点返回值 → 必须累计（否则上面那个「恒 0」证明不了任何事）
    async def node_a_write(state: N):
        return {"n": state.get("n", 0) + 1}    # 正规提交口

    g2 = StateGraph(N)
    g2.add_node("a", node_a_write)
    g2.add_node("b", node_b)
    g2.add_edge(START, "a")
    g2.add_conditional_edges("a", lambda s: "b")
    g2.add_edge("b", END)
    app2 = g2.compile(checkpointer=InMemorySaver())
    cfg2 = {"configurable": {"thread_id": "s16-node-write"}}

    async def _run2():
        seen = []
        for i in (1, 2, 3):
            out = await app2.ainvoke({"n": 0, "seen": 0} if i == 1 else {}, cfg2)
            seen.append(out["seen"])
        return seen

    ctrl = asyncio.run(_run2())
    assert ctrl == [1, 2, 3], f"t1 对照组坏了（节点写入也不累计？）→ {ctrl}"


class _Emitter:
    """每次被激活就往黑板投一条固定形状的消息，并计一次数（= QA/Engineer 的最小替身）"""

    def __init__(self, cause_by, send_to):
        self.cause_by = cause_by
        self.send_to = send_to
        self.hits = 0

    def as_node(self, name):
        async def _run(state: dict):
            self.hits += 1
            return {"messages": [Message(content=f"{name} 第 {self.hits} 次", role="assistant",
                                         cause_by=self.cause_by, sent_from=name, send_to=self.send_to)]}
        return name, _run


def _init(msg: Message):
    return {"messages": [msg], "memories": {},
            "debug_rounds": 0, "finished": False}


def _ainvoke(graph, init, thread):
    """刹车本该在 3 轮内生效，所以 12 步的 recursion_limit 足够把「刹车失效」读成 GraphRecursionError，
    而不是让进程挂到默认 25 步再撞出个含糊的超时。"""
    return asyncio.run(graph.ainvoke(init, {"configurable": {"thread_id": thread},
                                            "recursion_limit": 12}))


# ---------- 2. 真图：QA `<self>` 自环（RUN_CODE）三轮内刹车 ----------
def t2_self_loop_brake_fires():
    qa = _Emitter(RequirementTag.RUN_CODE, {MESSAGE_ROUTE_TO_SELF})
    graph = build_team({"QA": qa})
    msg = Message(content="跑测试", role="assistant", cause_by=RequirementTag.RUN_CODE,
                  sent_from="QA", send_to={MESSAGE_ROUTE_TO_SELF})
    try:
        out = _ainvoke(graph, _init(msg), "s16-self-loop")
    except GraphRecursionError as e:
        raise AssertionError(
            f"t2 刹车未生效：QA 自环打到 recursion_limit 才停（debug_rounds 从未累加）→ {e}") from e
    assert out["debug_rounds"] == 3, f"t2 debug_rounds 应为 3（每轮 router 加一），实际 {out['debug_rounds']}"
    assert qa.hits == 2, f"t2 第 3 次进 router 就该 END，QA 实际被激活 {qa.hits} 次"


# ---------- 3. 真图：DEBUG_ERROR 广播回路（QA↔Engineer）刹车 ----------
def t3_debug_error_broadcast_brake():
    engineer = _Emitter(RequirementTag.DEBUG_ERROR, {MESSAGE_ROUTE_TO_ALL})
    graph = build_team({"Engineer": engineer})
    msg = Message(content="QA 报缺陷", role="assistant", cause_by=RequirementTag.DEBUG_ERROR,
                  sent_from="QA", send_to={MESSAGE_ROUTE_TO_ALL})
    try:
        out = _ainvoke(graph, _init(msg), "s16-debug-loop")
    except GraphRecursionError as e:
        raise AssertionError(
            f"t3 刹车未生效：DEBUG_ERROR 回路打到 recursion_limit 才停 → {e}") from e
    assert out["debug_rounds"] == 3, f"t3 debug_rounds 应为 3，实际 {out['debug_rounds']}"
    assert engineer.hits == 2, f"t3 第 3 轮该 END，Engineer 实际被激活 {engineer.hits} 次"


# ---------- 4/5. B9：自愈回喂（抛错 / 审批拒绝）必须有人接，且仍在 3 轮闸内 ----------
class _Rec(BaseModel):
    done: str = ""


class _Boom(BaseAction):
    """每次都抛错的 Action（真模型输出漂移→产物仓写拒的最小替身）"""
    output_schema = _Rec

    async def run(self, msg: Message) -> Message:
        HITS.append("run")
        raise ValueError("非法产物文件名 '/main.py'")


HITS: list[str] = []


def _boom_agent():
    HITS.clear()
    ag = Agent({"name": "E", "profile": "p", "goal": "g"}, [_Boom(llm=None)], None,
               react_mode="BY_ORDER", max_loops=2, watch={RequirementTag.USER_REQUIREMENT})
    graph = build_team({"E": ag}, sop={RequirementTag.USER_REQUIREMENT: ["E"]})
    msg = Message(content="开工", role="user", cause_by=RequirementTag.USER_REQUIREMENT,
                  sent_from="user")
    return graph, _init(msg)


def t4_action_error_reactivates_role():
    """B9 验收：mock Action 抛错 → 角色节点被 Send 第二次（回喂有订阅者），且 3 轮内收尾。

    修复前：错误消息 `send_to=<all>` 在 route 里刻意不广播 + `cause_by=action.name` 不在 SOP
    → 无订阅者 → 角色只激活 1 次就散会（会话以 "run completed" 收口，自愈轮从未发生）。
    中间态（只改 send_to=<self>、刹车仍挑 cause_by）：激活 12 次打到 recursion_limit，
    读数见 log/b9_probe.py —— 所以这道闸必须不挑 cause_by。"""
    graph, init = _boom_agent()
    try:
        out = _ainvoke(graph, init, "s16-b9-error")
    except GraphRecursionError as e:
        raise AssertionError(f"t4 回喂自环没被刹住，执行 {len(HITS)} 次 → {e}") from e
    assert len(HITS) == 3, f"t4 角色应被激活 3 次（1 次原发 + 2 次自愈）后刹车，实际 {len(HITS)}"
    assert out["debug_rounds"] == 3, f"t4 debug_rounds 应为 3，实际 {out['debug_rounds']}"


def t5_approval_reject_does_not_run_action():
    """审批拒绝路径：动作一次都不能执行，但角色要被再激活（有换动作的机会），同样 3 轮收尾。"""
    from codeharness.runtime import APPROVAL_IO
    from codeharness.tools import _approval

    graph, init = _boom_agent()
    saved = _approval.gate_decide
    _approval.gate_decide = lambda *a, **k: ("rejected", None)
    tok = APPROVAL_IO.set(object())                    # 装了待批通道（没装时 gate fail-open 放行）
    try:
        out = _ainvoke(graph, init, "s16-b9-reject")
    except GraphRecursionError as e:
        raise AssertionError(f"t5 拒绝回喂自环没被刹住 → {e}") from e
    finally:
        _approval.gate_decide = saved
        APPROVAL_IO.reset(tok)
    assert HITS == [], f"t5 被拒的动作照跑了：{HITS}"
    assert out["debug_rounds"] == 3, f"t5 拒绝回喂应走同一道闸（3 轮），实际 {out['debug_rounds']}"
    last = out["messages"][-1]
    assert last.content.startswith("[已拒绝]"), f"t5 末条不是拒绝消息：{last.content[:40]!r}"
    assert MESSAGE_ROUTE_TO_SELF in last.send_to, "t5 拒绝消息没回到 <self>，下一轮没人接"


def _unwrap_unknown(exc: BaseException):
    """langgraph 会把条件边里的异常包一层，这里沿 __cause__/__context__ 找回 UnknownRecipient。"""
    seen = []
    while exc is not None and id(exc) not in seen:
        seen.append(id(exc))
        if type(exc).__name__ == "UnknownRecipient":
            return str(exc)
        exc = exc.__cause__ or exc.__context__
    return None


def t6_unknown_recipient_raises():
    """C1-d：收件人名字对不上装配时必须**当场抛**，不许静默丢。

    静默的代价早就被实测记进文档：LangGraph 对未知节点只打一行
    `Ignoring unknown node name PM`，然后**整场零次 LLM 调用**（会话看起来"正常跑完"，
    什么产物都没有）。五格：①具名投给不存在的角色；②SOP 订阅表指向不存在的角色；
    ③`<self>` 的 sent_from 不在册；④**阳性对照**：合法具名投递照常送达（证明不是一律抛）；
    ⑤`<all>` 不炸——它是路由标记不是角色名，把它当错误会把所有默认值消息全炸掉。"""
    def expect_unknown(thread, msg, agents, sop=None):
        # sop 传 None 用默认 SOP；传 {} 是"没有订阅表"——两者语义不同，别拿真值判断合并
        graph = build_team(agents, sop=sop if sop is not None else None)
        try:
            _ainvoke(graph, _init(msg), thread)
        except Exception as e:
            got = _unwrap_unknown(e)
            assert got is not None, f"抛的不是 UnknownRecipient，而是 {type(e).__name__}: {e}"
            return got
        raise AssertionError("收件人不存在竟然没抛——又回到静默丢 + 整场零模型调用那条老路")

    # ①具名投递给不存在的角色（空 SOP 隔离这一条：默认表里 USER_REQUIREMENT→PM 会先命中）
    ghost = Message(content="派给幽灵", role="assistant", cause_by=RequirementTag.USER_REQUIREMENT,
                    sent_from="Leader", send_to={"Ghost"})
    got = expect_unknown("s16-ghost", ghost, {"Leader": _Emitter(RequirementTag.USER_REQUIREMENT, {"Ghost"})},
                         sop={})
    assert "Ghost" in got, f"①错误里没带上那个名字：{got}"

    # ②SOP 订阅表把 USER_REQUIREMENT 派给 PM，但装配里没有 PM（正是"整场零模型调用"那一格）
    got = expect_unknown("s16-sop",
                         Message(content="开工", role="user", cause_by=RequirementTag.USER_REQUIREMENT,
                                 sent_from="user", send_to=set()),
                         {"Boss": _Emitter(RequirementTag.USER_REQUIREMENT, set())},
                         sop={RequirementTag.USER_REQUIREMENT: ["PM"]})
    assert "PM" in got, f"②错误里没带上订阅表那个名字：{got}"

    got = expect_unknown("s16-self", Message(content="回喂", role="assistant",
                                             cause_by=RequirementTag.RUN_CODE,
                                             sent_from="Ghost", send_to={MESSAGE_ROUTE_TO_SELF}),
                         {"QA": _Emitter(RequirementTag.RUN_CODE, {MESSAGE_ROUTE_TO_SELF})})
    assert "Ghost" in got, f"③<self> 的 sent_from 不在册却没说清：{got}"

    b = _Emitter(RequirementTag.USER_REQUIREMENT, set())
    graph = build_team({"A": _Emitter(RequirementTag.USER_REQUIREMENT, {"B"}), "B": b}, sop={})
    out = _ainvoke(graph, _init(Message(content="正常指名", role="assistant",
                                        cause_by=RequirementTag.USER_REQUIREMENT,
                                        sent_from="A", send_to={"B"})), "s16-nominal")
    assert b.hits >= 1, f"④阳性对照失败：合法具名投递没送达，B 被激活 {b.hits} 次（判据怕是变成一律抛）"

    # ⑤`<all>` 是路由标记不是角色名：既不抛（否则所有默认值消息全炸），也不广播（本仓刻意不广播）
    graph = build_team({"A": _Emitter(RequirementTag.USER_REQUIREMENT, {MESSAGE_ROUTE_TO_ALL})}, sop={})
    out = _ainvoke(graph, _init(Message(content="默认值", role="assistant",
                                        cause_by=RequirementTag.USER_REQUIREMENT,
                                        sent_from="A", send_to={MESSAGE_ROUTE_TO_ALL})), "s16-all")
    assert len(out["messages"]) == 1, \
        f"⑤<all> 被当成广播了（应当谁都不唤醒）：{[m.content for m in out['messages']]}"
    assert out["messages"][0].content == "默认值", f"⑤读数不对：{out['messages'][0].content}"


def t7_superstep_batch_all_delivered():
    """C13：同一超步里多条产出**必须全投**。旧写法只看 `messages[-1]`，其余消息留在黑板上永不路由。
    真跑里这不是理论场景——A4 那批队长一轮攒 15 条委派 → 15 个并发 Send → 14 条回报没人收。"""

    class _TwoOut:
        """一个节点一轮产两条消息，分别指名两个目标"""
        def as_node(self, name):
            async def _run(state: dict):
                return {"messages": [
                    Message(content="给 X", role="user", cause_by=RequirementTag.RUN_COMMAND,
                            sent_from=name, send_to={"X"}),
                    Message(content="给 Y", role="user", cause_by=RequirementTag.RUN_COMMAND,
                            sent_from=name, send_to={"Y"})]}
            return name, _run

    class _Hub:
        """队长替身：一轮里把 N 件任务发给同一成员（一条聚合件 → 拆成 N 个并发 Send）"""
        def __init__(self, member: str, n: int):
            self.member, self.n = member, n

        def as_node(self, name):
            async def _run(state: dict):
                return {"messages": [Message(
                    content="派发中", role="user", cause_by=RequirementTag.RUN_COMMAND, sent_from=name,
                    send_to={self.member},
                    instruct_content={"delegations": [{"member": self.member,
                                                       "instruction": f"任务 {i}"} for i in range(self.n)]},
                    instruct_schema="TeamDelegation")]}
            return name, _run

    class _Member:
        """成员替身：每次被激活回一条 TeamReport 给队长"""
        def __init__(self, leader: str):
            self.leader, self.hits = leader, 0

        def as_node(self, name):
            async def _run(state: dict):
                self.hits += 1
                inbox = state.get("_inbox") or []
                return {"messages": [Message(content=f"完成-{inbox[-1].content if inbox else ''}",
                                             role="assistant", cause_by=RequirementTag.RUN_COMMAND,
                                             sent_from=name, send_to={self.leader},
                                             instruct_schema="TeamReport")]}
            return name, _run

    class _Sink:
        """收件方替身：记下每次收到的那条内容，然后不再往外投（避免多节点续跑）"""
        def __init__(self):
            self.seen = []

        def as_node(self, name):
            async def _run(state: dict):
                self.seen.append(((state.get("_inbox") or [Message(content="")])[-1]).content)
                return {"messages": [Message(content="收口", role="assistant",
                                             cause_by=RequirementTag.RUN_COMMAND, sent_from=name,
                                             send_to={MESSAGE_ROUTE_TO_NONE})]}
            return name, _run

    # 格①：同批两条、两个目标 → 都得投出去
    x, y = _Sink(), _Sink()
    g1 = build_team({"Hub": _TwoOut(), "X": x, "Y": y},
                    sop={RequirementTag.USER_REQUIREMENT: ["Hub"]})
    asyncio.run(g1.ainvoke(_init(Message(content="开工", role="user",
                                        cause_by=RequirementTag.USER_REQUIREMENT)),
                            {"configurable": {"thread_id": "s16-t7a"}}))
    assert x.seen == ["给 X"] and y.seen == ["给 Y"], f"同批两条只投出了一条：X={x.seen} Y={y.seen}"

    # 格②：聚合件拆成 3 条给同一成员 → 3 次并发激活的 3 条回报都要回到队长
    leader, member = _Sink(), _Member("Mike")
    g2 = build_team({"Hub": _Hub("Alice", 3), "Alice": member, "Mike": leader},
                    sop={RequirementTag.USER_REQUIREMENT: ["Hub"]})
    asyncio.run(g2.ainvoke(_init(Message(content="派三件", role="user",
                                         cause_by=RequirementTag.USER_REQUIREMENT)),
                            {"configurable": {"thread_id": "s16-t7b"}}))
    assert member.hits == 3, f"三条委派没都送达成员：Alice 被激活 {member.hits} 次"
    assert len(leader.seen) == 3, f"队长只收到 {len(leader.seen)} 条回报（旧写法只会收到 1 条）"
    assert sorted(leader.seen) == ["完成-任务 0", "完成-任务 1", "完成-任务 2"], leader.seen
    print(f"  ok  t7 同批全投：两条各投各的 + 三条并发激活的回报三条都回队长（{leader.seen}）")


def t8_memories_concurrent_no_last_write_wins():
    """T3/C66（用户拍 (a)：并集去重、保序）：同一成员同超步被多条 Send 激活时，memories **不许末写覆盖**。

    各激活返回「各自的新增」（Agent._run 形状），旧 reducer `{**a, **b}` 对同一角色的并发
    激活是末写覆盖 ⇒ 前 N-1 个激活的新增静默丢。安全性前提：图 state 里的 memory
    **只增不减**（agent.py:53 的溢写不裁 state 列表）⇒ 并集不会复活被删条目。
    ⚠ 探针实锤的边界（超出本格拍板范围，留账在分计划）：langgraph 1.2.11 的 `Send` arg
    **完全替换节点输入**——Send 路的节点 state 里只有 `_inbox`，Agent._run 的记忆播种恒空。
    本格只钉 **reducer 的提交正确性**（它同时是未来修好播种后的唯一防覆盖护栏）。
    两格：
      ① 并发（真图）：一条委派聚合件拆 3 条给同一成员，每次激活返回一条自己的新增
         ⇒ 终态该角色恰好 3 条（旧 reducer 只活最后 1 条）；
      ② reducer 直测：带快照的返回 = 顺序等价（快照+新增 ⊇ 旧值，替换语义保住）、
         重复条目不堆积（含不可哈希形状）、幂等；阳性对照 = 旧 `{**a,**b}` 形状确实丢条目。
    """
    class _Hub:
        def as_node(self, name):
            async def _run(state):
                return {"messages": [Message(
                    content="派发中", role="user", cause_by=RequirementTag.RUN_COMMAND, sent_from=name,
                    send_to={"Mem"},
                    instruct_content={"delegations": [{"member": "Mem", "instruction": f"任务 {i}"}
                                                      for i in range(3)]},
                    instruct_schema="TeamDelegation")]}
            return name, _run

    class _Mem:
        """照 Agent._run 的返回形状（agent.py:310）：交回本激活的记忆增量"""
        def __init__(self):
            self.seen = []

        def as_node(self, name):
            async def _run(state):
                inbox = state.get("_inbox") or []
                tag = inbox[-1].content if inbox else ""
                self.seen.append(tag)
                return {"messages": [Message(content=f"完成-{tag}", role="assistant",
                                             cause_by=RequirementTag.RUN_COMMAND, sent_from=name,
                                             send_to={MESSAGE_ROUTE_TO_NONE})],
                        "memories": {name: [f"记住-{tag}"]}}
            return name, _run

    m = _Mem()
    g = build_team({"Hub": _Hub(), "Mem": m},
                   sop={RequirementTag.USER_REQUIREMENT: ["Hub"]})
    out = _ainvoke(g, _init(Message(content="开工", role="user",
                                    cause_by=RequirementTag.USER_REQUIREMENT)), "s16-t8")
    assert len(m.seen) == 3, f"前置失配：成员没被激活 3 次（{m.seen}）"
    mems = (out.get("memories") or {}).get("Mem", [])
    assert sorted(mems) == [f"记住-任务 {i}" for i in range(3)], \
        f"并发激活的新增被末写覆盖丢了（T3）：{mems}"

    # ② reducer 直测：顺序等价 / 去重 / 幂等 / 旧形状的阳性对照
    from codeharness.environment.team_graph import merge_memories
    assert merge_memories({}, {"Seq": ["a"]}) == {"Seq": ["a"]}, "空侧合并失配"
    assert merge_memories({"Seq": ["a"]}, {"Seq": ["a", "b"]}) == {"Seq": ["a", "b"]}, \
        "返回带快照时不是顺序等价（快照+新增 ⊇ 旧值）——替换语义被破坏"
    assert merge_memories({"Seq": ["a", {"k": 1}]}, {"Seq": ["a", {"k": 1}, "b"]}) \
        == {"Seq": ["a", {"k": 1}, "b"]}, "重复条目堆积了（含不可哈希形状）"
    x = {"Seq": ["a", {"k": 1}]}
    assert merge_memories(x, x) == x, "幂等失败（重放场景会翻倍）"
    assert {**{"Seq": ["x"]}, **{"Seq": ["y"]}} == {"Seq": ["y"]}, \
        "阳性对照失效：旧 {**a,**b} 形状在这个例子里居然不丢条目"


def t9_send_input_carries_state():
    """① Send 输入形状（C69 拍板落地）：`Send` 的 arg **完全替换节点输入**（langgraph 1.2.11，
    t8 落账时的 keys 探针实锤）——早先 route() 只带 `{"_inbox": [m]}`，被 Send 的节点读不到
    `memories` 等通道 ⇒ `Agent._run` 的记忆播种恒空（「快照+新增」实际是「空快照+新增」）。
    修法：Send arg 带整份 state（`{**state, "_inbox": [m]}`——arg 不持久化，写回照旧走 reducer）。
      ① 播种生效：init 预置 `memories={"Mem": ["seed"]}`，委派拆 2 条给 Mem ⇒ 每次激活
         **读得到** seed、返回「seed+各自新增」⇒ 终态恰为 seed + 2 条新增（修复前 seed 丢失）；
      ② `<self>` 自环路同形状：回喂激活读得到播种并追加。
    """
    class _Hub:
        def as_node(self, name):
            async def _run(state):
                return {"messages": [Message(
                    content="派发中", role="user", cause_by=RequirementTag.RUN_COMMAND, sent_from=name,
                    send_to={"Mem"},
                    instruct_content={"delegations": [{"member": "Mem", "instruction": f"任务 {i}"}
                                                      for i in range(2)]},
                    instruct_schema="TeamDelegation")]}
            return name, _run

    class _Mem:
        """照 Agent._run 的形状（agent.py:307/310）：读播种快照、追加自己的新增、整列表交回。
        ⚠ 多带一条「快照长度=N」读数：并集 reducer 会掩盖「激活丢播种」（seed 已在通道里，
        激活丢播种只是没重复返回）——只有快照长度才区分得出来（修复后=1，回退后=0）。"""

        def __init__(self):
            self.seen = []

        def as_node(self, name):
            async def _run(state):
                inbox = state.get("_inbox") or []
                tag = inbox[-1].content if inbox else ""
                self.seen.append(tag)
                mem = list(state.get("memories", {}).get(name, []))
                return {"messages": [Message(content=f"完成-{tag}", role="assistant",
                                             cause_by=RequirementTag.RUN_COMMAND, sent_from=name,
                                             send_to={MESSAGE_ROUTE_TO_NONE})],
                        "memories": {name: mem + [f"记住-{tag}", f"快照长度={len(mem)}"]}}
            return name, _run

    m = _Mem()
    g = build_team({"Hub": _Hub(), "Mem": m},
                   sop={RequirementTag.USER_REQUIREMENT: ["Hub"]})
    init = _init(Message(content="开工", role="user", cause_by=RequirementTag.USER_REQUIREMENT))
    init["memories"] = {"Mem": ["seed"]}                 # 播种：外层 state 预置一条旧记忆
    out = _ainvoke(g, init, "s16-t9")
    mems = (out.get("memories") or {}).get("Mem", [])
    assert len(m.seen) == 2, f"前置失配：成员没被激活 2 次（{m.seen}）"
    assert "快照长度=0" not in mems and "快照长度=1" in mems, \
        f"t9① 播种失效：激活读不到外层 memories（Send arg 只带 _inbox 的旧形状回来了，快照长度=0）：{mems}"
    assert sorted(m for m in mems if m.startswith("记住")) == ["记住-任务 0", "记住-任务 1"], \
        f"t9① 新增没齐：{mems}"

    # ② <self> 自环路同形状
    class _SelfLoop:
        def __init__(self):
            self.turns = 0

        def as_node(self, name):
            async def _run(state):
                self.turns += 1
                mem = list(state.get("memories", {}).get(name, []))
                new = mem + [f"turn-{self.turns}"]
                nxt = (Message(content="再想一轮", role="assistant",
                               cause_by=RequirementTag.RUN_CODE, sent_from=name,
                               send_to={MESSAGE_ROUTE_TO_SELF})
                       if self.turns < 2 else
                       Message(content="收口", role="assistant",
                               cause_by=RequirementTag.RUN_COMMAND, sent_from=name,
                               send_to={MESSAGE_ROUTE_TO_NONE}))
                return {"messages": [nxt], "memories": {name: new}}
            return name, _run

    sl = _SelfLoop()
    g2 = build_team({"SL": sl}, sop={RequirementTag.USER_REQUIREMENT: ["SL"]})
    init2 = _init(Message(content="开始", role="user", cause_by=RequirementTag.USER_REQUIREMENT))
    init2["memories"] = {"SL": ["seed"]}
    out2 = _ainvoke(g2, init2, "s16-t9b")
    mems2 = (out2.get("memories") or {}).get("SL", [])
    assert mems2 == ["seed", "turn-1", "turn-2"], \
        f"t9② 自环路的播种也断了：{mems2}"
    print(f"  ok  t9 Send arg 带全量 state：委派播种生效（{mems}）、<self> 自环播种生效（{mems2}）")


def t10_plan_lives_in_outer_state():
    """C71（口径 a，用户拍）：RoleZero 的计划状态机住外层 TeamState 键 `plans`——每个激活
    从外层按名播种出**子图 state 副本**（RoleZeroState.plan）、收口按名写回；实例字段不参与
    （共享槽会被「新任务→plan=None」清掉并发激活正在用的副本，那是 C71 要治的病本身；
    试实施的「播种进实例」形状治不了它——副本必须随 state 走）。
      ① 续跑：continue 激活播种旧计划，Plan.* 在旧计划上推进（判据双读数：外层终态含旧任务
         + FakeLLM prompt 的 plan_status 渲染出旧任务字样——播种没发生时退化为 history 摘要）；
      ② 作废：新任务激活 ⇒ 写回 plans[name]=None（None 只来自新任务分支）；
      ③ 并发不互清（缺陷本身）：同一成员两条 Send 并发激活——A 新任务、B continue，两边剧本
         都是 [end]，谁先谁后不影响判据：B 的 prompt 必须看得到旧任务（副本播种），A 的
         prompt 必须看不到（本激活已作废）；
      ④ reducer 直测：按名覆盖 / None 作废 / 异名互不干扰 / 幂等；
      ⑤ 结构守卫（上一棒试实施的两个死坑）：`report_to = incoming.sent_from` 赋值必须恰一次
         且缩进深于 `elif task !=`（残留行留在 if/elif/else 之后 = is_report 激活也被覆盖成
         report_to=发件人 ⇒ 等回报↔收工死循环）；return 必须带 plans 写回键。
    """
    import inspect
    import json as _json

    from codeharness.provider.fake import FakeLLM
    from codeharness.roles.role_zero import RoleZero
    from codeharness.schema import Plan, Task
    from codeharness.environment.team_graph import merge_plans

    END_CMD = {"command_name": "end", "args": {}}

    def _think(thought, commands):
        return _json.dumps({"thought": thought, "commands": commands}, ensure_ascii=False)

    def _append(tid, instr):
        return {"command_name": "Plan.append_task",
                "args": {"task_id": tid, "dependent_task_ids": [],
                         "instruction": instr, "assignee": "Mem"}}

    def _member(script):
        return RoleZero({"name": "Mem", "profile": "p", "goal": "g"}, [], FakeLLM(script))

    def _flatten(rz):
        flat = [m for call in rz.llm.calls for m in (call if isinstance(call, list) else [call])]
        return [getattr(m, "content", str(m)) for m in flat]

    # ① 续跑：先立计划（外层写回），continue 激活播种后在旧计划上追加
    m1 = _member([_think("立计划", [_append("t1", "写出周报"), END_CMD]),
                  _think("续跑推进", [_append("t2", "画出图表"), END_CMD])])
    g1 = build_team({"Mem": m1}, sop={})
    cfg1 = {"configurable": {"thread_id": "s16-t10a"}}
    out1 = asyncio.run(g1.ainvoke(
        {"messages": [Message(content="任务甲", role="user", cause_by=RequirementTag.USER_REQUIREMENT,
                              sent_from="user", send_to={"Mem"})],
         "memories": {}, "debug_rounds": 0, "team_rounds": 0, "finished": False}, cfg1))
    p1 = out1["plans"]["Mem"]
    assert p1 and p1["tasks"][0]["task_id"] == "t1", f"①写回缺失：{p1}"
    out2 = asyncio.run(g1.ainvoke(
        {"messages": [Message(content="continue", role="assistant", cause_by=RequirementTag.RUN_COMMAND,
                              sent_from="Boss", send_to={"Mem"})]}, cfg1))
    ids = [t["task_id"] for t in out2["plans"]["Mem"]["tasks"]]
    assert ids == ["t1", "t2"], f"①播种失效：续跑激活看不到旧计划 t1（终态只剩 {ids}）"
    joined = " ".join(_flatten(m1))
    assert "写出周报" in joined, \
        "①播种失效：续跑激活的 prompt 里没有旧计划（plan_status 退化成 history 摘要）"

    # ② 作废：新任务激活 ⇒ plans[Mem] 写回 None
    m2 = _member([_think("立计划", [_append("t1", "写出周报"), END_CMD]),
                  _think("新任务来了", [END_CMD])])
    g2 = build_team({"Mem": m2}, sop={})
    cfg2 = {"configurable": {"thread_id": "s16-t10b"}}
    asyncio.run(g2.ainvoke(
        {"messages": [Message(content="任务甲", role="user", cause_by=RequirementTag.USER_REQUIREMENT,
                              sent_from="user", send_to={"Mem"})],
         "memories": {}, "debug_rounds": 0, "team_rounds": 0, "finished": False}, cfg2))
    out3 = asyncio.run(g2.ainvoke(
        {"messages": [Message(content="换个活：任务乙", role="user",
                              cause_by=RequirementTag.USER_REQUIREMENT, sent_from="user",
                              send_to={"Mem"})]}, cfg2))
    assert out3["plans"]["Mem"] is None, \
        f"②作废失效：新任务没把旧计划清掉：{out3['plans']['Mem']}"

    # ③ 并发不互清：委派聚合件拆 2 条给同一成员（s16 t7 形状），A 新任务 + B continue
    _p = Plan(goal="种子目标")
    _p.add_tasks([Task(task_id="s1", instruction="种子任务", assignee="Mem")])
    seed_plan = _p.model_dump()

    class _Hub:
        """只派发一次：回报回到 Hub 后不再派发（否则 委派↔回报 ping-pong 到 team_rounds 刹车）"""

        def __init__(self):
            self.fired = 0

        def as_node(self, name):
            async def _run(state):
                self.fired += 1
                if self.fired > 1:
                    return {"messages": []}
                return {"messages": [Message(
                    content="派发中", role="user", cause_by=RequirementTag.RUN_COMMAND, sent_from=name,
                    send_to={"Mem"},
                    instruct_content={"delegations": [{"member": "Mem", "instruction": "任务A"},
                                                      {"member": "Mem", "instruction": "continue"}]},
                    instruct_schema="TeamDelegation")]}
            return name, _run

    hub = _Hub()
    m3 = _member([_think("收尾", [END_CMD]), _think("收尾", [END_CMD])])
    g3 = build_team({"Hub": hub, "Mem": m3},
                    sop={RequirementTag.USER_REQUIREMENT: ["Hub"]})
    out4 = asyncio.run(g3.ainvoke(
        {"messages": [Message(content="开工", role="user", cause_by=RequirementTag.USER_REQUIREMENT)],
         "memories": {}, "debug_rounds": 0, "team_rounds": 0, "finished": False,
         "plans": {"Mem": seed_plan}},
        {"configurable": {"thread_id": "s16-t10c"}, "recursion_limit": 24}))
    # 判别按**调用组**（一次激活 = 一次 structured 请求）：激活任务不进 prompt（continue 不进记忆），
    # 唯一可靠的组级标记就是 "# Current Plan" 段本身——两次激活恰好一组带种子、一组作废。
    groups = [" ".join(getattr(m, "content", str(m))
                       for m in (call if isinstance(call, list) else [call]))
              for call in m3.llm.calls]
    prompts = [g for g in groups if "# Current Plan" in g]
    assert len(prompts) == 2, f"前置失配：两次激活各一次 think，实得 {len(prompts)} 组 prompt"
    assert sum("种子任务" in g for g in prompts) == 1, \
        "③播种失效：续跑激活的 prompt 里看不到旧计划（并发新任务激活把共享副本清了——C71 的病）"
    assert sum("no plan yet" in g for g in prompts) == 1, \
        "③作废失效：新任务激活的 prompt 里还渲染着旧计划"

    # ④ reducer 直测
    assert merge_plans({}, {"M": None}) == {"M": None}
    assert merge_plans({"M": {"goal": "g"}}, {"M": None}) == {"M": None}, "作废必须能覆盖旧计划"
    assert merge_plans({"M": None}, {"M": {"goal": "g"}}) == {"M": {"goal": "g"}}, \
        "续跑写回必须能覆盖 None"
    assert merge_plans({"A": {"goal": "1"}}, {"B": {"goal": "2"}}) \
        == {"A": {"goal": "1"}, "B": {"goal": "2"}}, "异名互不干扰失败"
    x = {"M": {"goal": "g"}}
    assert merge_plans(x, x) == x, "幂等失败（重放场景会变形）"

    # ⑤ 结构守卫（上一棒试实施的两个死坑）
    src = inspect.getsource(RoleZero.as_node)
    lines = src.splitlines()
    ind = lambda l: len(l) - len(l.lstrip())
    rep = [i for i, l in enumerate(lines) if "report_to = incoming.sent_from" in l]
    assert len(rep) == 1, "report_to 赋值不见了或多了（收口的回报路由被破坏）"
    elif_i = next(i for i, l in enumerate(lines) if l.strip().startswith('elif task != "continue"'))
    assert rep[0] > elif_i and ind(lines[rep[0]]) > ind(lines[elif_i]), \
        "report_to 赋值被移出 elif 分支（残留行形状：is_report 激活也被覆盖 report_to ⇒ 等回报↔收工死循环）"
    seed_i = next(i for i, l in enumerate(lines) if 'state.get("plans")' in l)
    isrep_i = next(i for i, l in enumerate(lines) if "is_report = " in l)
    assert isrep_i < seed_i < elif_i, "plan 种子必须读得到 is_report/task（分支前计算）"
    assert 'sub.get("plan")' in src and '"plans": {name:' in src, "C71 的 plans 写回键不见了"


def main():
    checks = [t1_conditional_edge_write_is_dropped, t2_self_loop_brake_fires,
              t3_debug_error_broadcast_brake, t4_action_error_reactivates_role,
              t5_approval_reject_does_not_run_action, t6_unknown_recipient_raises,
              t7_superstep_batch_all_delivered,
              t8_memories_concurrent_no_last_write_wins,
              t9_send_input_carries_state,
              t10_plan_lives_in_outer_state]
    for c in checks:
        c()
        if c is not t7_superstep_batch_all_delivered:      # t7 自己打了带读数的 ok
            print(f"  ok  {c.__name__}")
    print(f"\nS16 门禁通过：{len(checks)} 组（langgraph {LANGGRAPH_VERSION}）—— "
          f"条件边写 state 不持久化 1 组（含节点写入对照组）+ 真图刹车 2 组（<self> 自环 / DEBUG_ERROR 广播）"
          f"+ B9 自愈回喂 2 组（Action 抛错 / 审批拒绝，都要再激活角色且 3 轮内收尾）"
          f"+ C1 未知收件人当场抛 1 组（含合法指名与 <all> 两格对照）"
          f"+ C13 同超步多条产出全投递 1 组（两目标两条 + 一成员三条）"
          f"+ **T3 memories 并发不覆盖 1 组（C66：并集去重、顺序等价）**"
          f"+ **C71 计划住外层 plans 键 1 组（续跑播种/新任务作废/并发不互清/reducer/结构守卫）**")


if __name__ == "__main__":
    main()
