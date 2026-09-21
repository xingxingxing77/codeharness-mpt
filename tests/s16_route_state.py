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


def main():
    checks = [t1_conditional_edge_write_is_dropped, t2_self_loop_brake_fires,
              t3_debug_error_broadcast_brake, t4_action_error_reactivates_role,
              t5_approval_reject_does_not_run_action, t6_unknown_recipient_raises,
              t7_superstep_batch_all_delivered]
    for c in checks:
        c()
        if c is not t7_superstep_batch_all_delivered:      # t7 自己打了带读数的 ok
            print(f"  ok  {c.__name__}")
    print(f"\nS16 门禁通过：{len(checks)} 组（langgraph {LANGGRAPH_VERSION}）—— "
          f"条件边写 state 不持久化 1 组（含节点写入对照组）+ 真图刹车 2 组（<self> 自环 / DEBUG_ERROR 广播）"
          f"+ B9 自愈回喂 2 组（Action 抛错 / 审批拒绝，都要再激活角色且 3 轮内收尾）"
          f"+ C1 未知收件人当场抛 1 组（含合法指名与 <all> 两格对照）"
          f"+ C13 同超步多条产出全投递 1 组（两目标两条 + 一成员三条）")


if __name__ == "__main__":
    main()
