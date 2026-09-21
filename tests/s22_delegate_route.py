"""S22 门禁（C1-② 委派载体）：队长的任务真的落到成员节点上，而且只落到该落的那个人身上。

钉五件事：
  t1 拆包 + 定向：`_wire_delegation` 把聚合件拆成「一人一条」，route 按**载荷自己的** send_to 收窄。
     不收窄就是 cartesian：两名成员会互相收到对方的任务（本文件最容易写错的一格）。
  t2 真图端到端：`dynamic_assembly` 三角色 + 真 `build_team`，队长脚本调
     `TeamLeader.publish_team_message(指令, "Alice")` → Alice 节点被激活、那条指令进了她的模型请求、
     Bob 一次模型调用都没发生（订阅式路由的本意就是每轮只唤醒相关角色）；同场再跑一次「一次点名两名」，
     断言两人各自只看到自己的指令。
  t3 模型写错名字不赔整场：发给不存在的 "Charlie" → 命令回 `[已拒绝]`、零条委派消息、图正常收口；
     同形状正向对照（发给 Alice 必须真激活）证明这条判据不是「根本不触发」（B9 自愈同一条回喂路）。
  t4 没名册不委派：registry 直建的单人队长（`teammates` 空）→ `[已忽略]`，既不抛也不投。
  t5 收窄只作用于装配器产出的载荷：原始消息即使具名，SOP 目标仍一并收到——钉住「我只改了拆包后的
     寻址」，经典线 `RunCode`→`{"Engineer"}` 那一路行为不许变。

跑法：
  cd /e/Codeharness && PYTHONPATH=/e/Codeharness:/e/Codeharness/logs PYTHONIOENCODING=utf-8 \\
    PYTHONDONTWRITEBYTECODE=1 F:/anaconda/python.exe -B tests/s22_delegate_route.py
"""
import asyncio
import json

from codeharness.const import RequirementTag, TEAMLEADER_NAME
from codeharness.environment.team_graph import (UnknownRecipient, _wire_delegation, build_team,
                                                make_route)
from codeharness.provider.fake import FakeLLM
from codeharness.schema import Message

INSTRUCTION = "写一个 PRD：命令行待办工具，语言 Python"


def _think(thought: str, commands: list) -> str:
    return json.dumps({"thought": thought, "commands": commands}, ensure_ascii=False)


def _publish(content: str, send_to: str) -> dict:
    return {"command_name": "TeamLeader.publish_team_message", "args": {"content": content,
                                                                        "send_to": send_to}}


def _delegation(members: dict) -> Message:
    """队长节点收口时产出的那种聚合件（as_node 的产出形状，这里手工等价，便于纯 route 层判定）"""
    return Message(content="\n".join(members.values()), role="user",
                   cause_by=RequirementTag.RUN_COMMAND, sent_from=TEAMLEADER_NAME,
                   send_to=set(members),
                   instruct_content={"delegations": [{"member": m, "instruction": c}
                                                     for m, c in members.items()]},
                   instruct_schema="TeamDelegation")


def _run_route(route, msgs):
    return route({"messages": msgs, "memories": {}, "round": 0, "debug_rounds": 0, "finished": False})


def t1_split_and_target():
    agents = {TEAMLEADER_NAME: object(), "Alice": object(), "Bob": object()}
    stats = []
    route = make_route({RequirementTag.USER_REQUIREMENT: [TEAMLEADER_NAME]}, agents, stats=stats)
    agg = _delegation({"Alice": "写 PRD", "Bob": "写系统设计"})
    sends = _run_route(route, [agg])

    assert len(sends) == 2, f"两名成员该各一条 Send，实际 {len(sends)}"
    assert {s.node for s in sends} == {"Alice", "Bob"}, sorted({s.node for s in sends})
    got = {s.node: s.arg["_inbox"][0] for s in sends}
    assert got["Alice"].content == "写 PRD", f"Alice 拿到了别人的任务：{got['Alice'].content!r}"
    assert got["Bob"].content == "写系统设计", f"Bob 拿到了别人的任务：{got['Bob'].content!r}"
    assert got["Alice"].sent_from == TEAMLEADER_NAME and got["Alice"].cause_by == RequirementTag.RUN_COMMAND
    assert stats[-1]["activated"] == 2, f"stats 没记到两条激活：{stats[-1]}"
    assert _wire_delegation(agg, {})[0] is not agg, "聚合件没被拆包"
    plain = Message(content="普通产出", cause_by=RequirementTag.RUN_COMMAND, sent_from="Alice")
    assert _wire_delegation(plain, {}) == [plain], "非委派的 RunCommand 消息被改写了"

    ghost = Message(content="x", role="user", cause_by=RequirementTag.RUN_COMMAND,
                    sent_from=TEAMLEADER_NAME, send_to={"Ghost"},
                    instruct_content={"delegations": [{"member": "Ghost", "instruction": "x"}]},
                    instruct_schema="TeamDelegation")
    try:
        _run_route(route, [ghost])
    except UnknownRecipient as e:
        assert "Ghost" in str(e)
    else:
        raise AssertionError("聚合件指名不存在的成员却没抛——C1-① 那道闸被拆包绕过了")
    print("  ok  t1 聚合件拆成一人一条、各投各的；非委派消息原样透传；具名幽灵仍抛 UnknownRecipient")


def t2_real_graph():
    from codeharness.team import dynamic_assembly
    agents, sop = dynamic_assembly(FakeLLM([]))
    leader_llm = FakeLLM([_think("交给产品经理", [_publish(INSTRUCTION, "Alice")]),
                          _think("等回报", [{"command_name": "end", "args": {}}])])
    alice_llm = FakeLLM([_think("PRD 写完了",
                                [{"command_name": "RoleZero.reply_to_human",
                                  "args": {"content": "PRD 已写入产物仓"}},
                                 {"command_name": "end", "args": {}}])])
    bob_llm = FakeLLM([_think("不该轮到我", [{"command_name": "end", "args": {}}])])
    for name, llm in ((TEAMLEADER_NAME, leader_llm), ("Alice", alice_llm), ("Bob", bob_llm)):
        agents[name].llm = llm
        agents[name].ltm = None              # 门禁不连 qdrant（s3b t14 同款隔离）
    stats = []
    graph = build_team(agents, sop=sop, stats=stats)
    out = asyncio.run(graph.ainvoke(
        {"messages": [Message(content="做一个命令行待办工具", role="user",
                              cause_by=RequirementTag.USER_REQUIREMENT)],
         "memories": {}, "round": 0, "debug_rounds": 0, "finished": False},
        {"configurable": {"thread_id": "s22t2"}}))

    aggs = [m for m in out["messages"] if m.instruct_schema == "TeamDelegation"]
    assert len(aggs) == 1, f"黑板上该有一条委派聚合件，实际 {len(aggs)}"
    assert aggs[0].send_to == {"Alice"}, f"聚合件收件人不该扩散：{aggs[0].send_to}"
    assert aggs[0].instruct_content["delegations"][0]["instruction"] == INSTRUCTION

    flat = [m for call in alice_llm.calls for m in (call if isinstance(call, list) else [call])]
    assert any(INSTRUCTION in getattr(m, "content", str(m)) for m in flat), \
        "Alice 被激活了却没拿到队长那条指令——委派在半路丢了"
    assert bob_llm.calls == [], f"Bob 不该被唤醒，实际调了 {len(bob_llm.calls)} 次"
    delegated = [s for s in stats if s["cause_by"] == RequirementTag.RUN_COMMAND]
    woke = [s for s in delegated if s["activated"]]
    assert len(woke) == 1 and woke[0]["activated"] == 1, \
        f"整场只该有一次、且只唤醒一个节点的激活：{stats}"
    assert out["messages"][-1].content.endswith("PRD 已写入产物仓"), \
        f"会话没收在 Alice 的回报上：{out['messages'][-1].content!r}"
    print(f"  ok  t2 真图：队长→Alice 激活 1 个节点、指令原文进了她的请求；Bob 零调用；stats={stats}")

    # 一次点名两名：outbox 聚合 → 拆包 → 两条 Send，各自拿到自己的指令（源「create all tasks at once」）
    agents2, sop2 = dynamic_assembly(FakeLLM([]))
    agents2[TEAMLEADER_NAME].llm = FakeLLM([
        _think("同时铺开", [_publish("写 PRD", "Alice"), _publish("写系统设计", "Bob")]),
        _think("结束", [{"command_name": "end", "args": {}}])])
    a2, b2 = FakeLLM([_think("结束", [{"command_name": "end", "args": {}}])]), \
        FakeLLM([_think("结束", [{"command_name": "end", "args": {}}])])
    agents2["Alice"].llm, agents2["Bob"].llm = a2, b2
    for r in agents2.values():
        r.ltm = None
    asyncio.run(build_team(agents2, sop=sop2).ainvoke(
        {"messages": [Message(content="需求", role="user",
                              cause_by=RequirementTag.USER_REQUIREMENT)],
         "memories": {}, "round": 0, "debug_rounds": 0, "finished": False},
        {"configurable": {"thread_id": "s22t2b"}}))

    def _task_of(llm):
        flat = [m for call in llm.calls for m in (call if isinstance(call, list) else [call])]
        return " ".join(getattr(m, "content", str(m)) for m in flat)

    assert "写 PRD" in _task_of(a2) and "写系统设计" not in _task_of(a2), _task_of(a2)[:200]
    assert "写系统设计" in _task_of(b2) and "写 PRD" not in _task_of(b2), _task_of(b2)[:200]
    print("  ok  t2b 一次点名两名：Alice 只看到 PRD、Bob 只看到系统设计（没互相串任务）")


def _leader_only(send_to: str):
    """跑一场只委派一次的小会话，回 (图产出, 队长角色, Alice 调用数, Bob 调用数)"""
    from codeharness.team import dynamic_assembly
    agents, sop = dynamic_assembly(FakeLLM([]))
    leader_llm = FakeLLM([_think("委派", [_publish(INSTRUCTION, send_to)]),
                          _think("结束", [{"command_name": "end", "args": {}}])])
    alice_llm = FakeLLM([_think("结束", [{"command_name": "end", "args": {}}])])
    bob_llm = FakeLLM([_think("结束", [{"command_name": "end", "args": {}}])])
    for name, llm in ((TEAMLEADER_NAME, leader_llm), ("Alice", alice_llm), ("Bob", bob_llm)):
        agents[name].llm = llm
        agents[name].ltm = None
    graph = build_team(agents, sop=sop)
    out = asyncio.run(graph.ainvoke(
        {"messages": [Message(content="需求", role="user",
                              cause_by=RequirementTag.USER_REQUIREMENT)],
         "memories": {}, "round": 0, "debug_rounds": 0, "finished": False},
        {"configurable": {"thread_id": f"s22t3-{send_to}"}}))
    return out, agents[TEAMLEADER_NAME], len(alice_llm.calls), len(bob_llm.calls)


def t3_wrong_name_heals():
    out, leader, alice_calls, bob_calls = _leader_only("Charlie")
    assert not [m for m in out["messages"] if m.instruct_schema == "TeamDelegation"], \
        "不存在的成员还是发出去了一条委派"
    assert alice_calls == 0 and bob_calls == 0, "错名字把无关成员唤醒了"
    # 拒绝文本必须回喂到队长的可见上下文里（_observe 收 results），否则模型下一轮不知道自己被拒了
    seen = " ".join(m.content for m in leader.memory.get(leader.memory_k))
    assert "已拒绝" in seen and "Charlie" in seen, f"拒绝没进队长记忆，自愈回路断了：{seen[:200]}"
    print("  ok  t3 反证：发给不存在的成员 → 零条委派、零唤醒、图正常收口、拒绝文本回了模型上下文")

    out2, leader2, alice_calls2, _ = _leader_only("Alice")      # 阳性对照：同形状必须真激活
    assert alice_calls2 > 0, "正向对照都没激活 Alice——这条判据根本不区分对错，白测"
    assert any(m.instruct_schema == "TeamDelegation" for m in out2["messages"])
    assert "已拒绝" not in " ".join(m.content for m in leader2.memory.get(leader2.memory_k))
    print(f"  ok  t3 阳性对照：同一个脚本换个对的名字，Alice 真被激活（calls={alice_calls2}）")


def t4_no_roster_refuses():
    from codeharness.roles.registry import build_role
    leader = build_role("TeamLeader", FakeLLM([]))
    assert leader.teammates == {}, "registry 直建的角色不该有名册"
    out = leader._publish_team_message({"content": "x", "send_to": "Alice"})
    assert "已忽略" in out, f"没名册却当真去委派了：{out}"
    assert leader._outbox == [], f"outbox 被写脏了：{leader._outbox}"
    leader.teammates = {"Alice": "Product Manager, write a PRD"}
    assert "已委派" in leader._publish_team_message({"content": "x", "send_to": "Alice"})
    assert leader._outbox == [("x", "Alice")], leader._outbox
    assert "已拒绝" in leader._publish_team_message({"content": "x", "send_to": "Charlie"})
    assert leader._outbox == [("x", "Alice")], "被拒的那条也进了 outbox"
    assert "Alice: Product Manager" in leader.team_info(), leader.team_info()
    print("  ok  t4 无名册直接忽略；有名册后对的进 outbox、错的被拒且不进")


def t5_original_message_not_narrowed():
    """拆包后的载荷才允许自带收件人；原始消息仍按 SOP∪具名全发（经典线 RunCode→Engineer 不受影响）。"""
    agents = {"Engineer": object(), "PM": object()}
    route = make_route({"FixBug": ["Engineer", "PM"]}, agents)
    msg = Message(content="修这个 bug", cause_by="FixBug", sent_from="QA", send_to={"Engineer"})
    sends = _run_route(route, [msg])
    assert {s.node for s in sends} == {"Engineer", "PM"}, \
        f"原始消息被按 send_to 收窄了，SOP 目标丢了：{sorted({s.node for s in sends})}"
    print("  ok  t5 原始消息不收窄（SOP 目标照旧一并收到）——收窄只在拆包后的载荷上")


def main():
    t1_split_and_target()
    t2_real_graph()
    t3_wrong_name_heals()
    t4_no_roster_refuses()
    t5_original_message_not_narrowed()
    print("\ns22_delegate_route: 5/5 全绿")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
