"""S22 门禁（C1-②/②b 委派与回报）：队长的任务真的落到成员节点上，成员的回报真的回到队长身上。

钉十件事：
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
  t6 回报通路（②b）：Alice 的产出**定向回到派活给她的那个人**、队长的计划没被这条回报清掉、
     他能 `Plan.finish_current_task`、回报原文进了他第二跑的模型请求。她的「汇报」与「收尾」刻意分两轮
     ——旧写法（只读最后一轮 results）在这格必红，已做反向验证（content 掉成「收工」）。
  t7 委派↔回报死循环的刹车：队长无视回报反复重派 → `team_rounds` 到档干净散会，不烧到 recursion_limit
     把整场打成 failed。**对照组是判据的一部分**：同剧本把 `recursion_limit` 压到 12 必须真抛
     `GraphRecursionError`，否则「它停下来了」可能只是因为剧本根本不循环（本仓反复踩过的空转判据）。
  t8 C148 真图档：`_publish_plan_open` 读**真 build_team 落下的 `plans` dump** 数得对（2 条任务勾掉 1 条
     ⇒ `open=1/total=2`）。s17 t18 吃的是替身图的 values，而本仓因这颗函数的形状假设真炸过一次
     （遍历 dict 拿到键 ⇒ `AttributeError` 冒到 `_fail`、红在无关的 t7），所以形状必须由真图复核。
  t9 C190 车道真图档：角色生命周期三事件由**真图**数出来（run_id 整场共用、graph:step 在 start/end
     不同数 ⇒ 都不能当配对键），四格见函数文档。
  t10 C192 续跑档：`_resume` 那趟的插话队列必须在装配出口就存在并把空目标默认落进**在册**角色，
     只读回放那趟不建队（真模型活体照出来的静默丢消息，见函数文档）。

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
    return route({"messages": msgs, "memories": {}, "debug_rounds": 0, "finished": False})


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
         "memories": {}, "debug_rounds": 0, "finished": False},
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
    # ②b 起这条链是两跳：队长的委派唤醒 Alice 一次、Alice 的回报再唤醒队长一次——各只 1 个节点
    assert len(woke) == 2 and all(s["activated"] == 1 for s in woke), \
        f"两跳来回每跳都该只唤醒 1 个节点：{stats}"
    assert any(m.instruct_schema == "TeamReport" and m.send_to == {TEAMLEADER_NAME}
               for m in out["messages"]), "Alice 干完没向派活的队长回报（通路见 t6）"
    reports = [m for m in out["messages"] if m.instruct_schema == "TeamReport"]
    assert reports[0].content.endswith("PRD 已写入产物仓"), \
        f"回报内容不是她那句汇报：{reports[0].content!r}"
    print(f"  ok  t2 真图：队长→Alice 定向激活、指令原文进了她的请求、Alice 的回报回到队长；Bob 零调用；stats={stats}")

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
         "memories": {}, "debug_rounds": 0, "finished": False},
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
         "memories": {}, "debug_rounds": 0, "finished": False},
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
    # C69：_publish_team_message 攒进**调用方给的激活级 outbox**（不再挂实例——并发激活互偷）
    ob = []
    out = leader._publish_team_message({"content": "x", "send_to": "Alice"}, ob)
    assert "已忽略" in out, f"没名册却当真去委派了：{out}"
    assert ob == [], f"outbox 被写脏了：{ob}"
    leader.teammates = {"Alice": "Product Manager, write a PRD"}
    assert "已委派" in leader._publish_team_message({"content": "x", "send_to": "Alice"}, ob)
    assert ob == [("x", "Alice")], f"激活级 outbox 没写入：{ob}"
    assert "已拒绝" in leader._publish_team_message({"content": "x", "send_to": "Charlie"}, ob)
    assert ob == [("x", "Alice")], "被拒的那条也进了 outbox"
    assert "Alice: Product Manager" in leader.team_info(), leader.team_info()
    print("  ok  t4 无名册直接忽略；有名册后对的进激活级 outbox、错的被拒且不进")


def t5_original_message_not_narrowed():
    """拆包后的载荷才允许自带收件人；原始消息仍按 SOP∪具名全发（经典线 RunCode→Engineer 不受影响）。"""
    agents = {"Engineer": object(), "PM": object()}
    route = make_route({"FixBug": ["Engineer", "PM"]}, agents)
    msg = Message(content="修这个 bug", cause_by="FixBug", sent_from="QA", send_to={"Engineer"})
    sends = _run_route(route, [msg])
    assert {s.node for s in sends} == {"Engineer", "PM"}, \
        f"原始消息被按 send_to 收窄了，SOP 目标丢了：{sorted({s.node for s in sends})}"
    print("  ok  t5 原始消息不收窄（SOP 目标照旧一并收到）——收窄只在拆包后的载荷上")


def _team(leader_script, alice_script, bob_script=None):
    """真图 + 每角色独立剧本（三个 RoleZero 共用一个 llm 会把剧本吃串，所以各自换掉）"""
    from codeharness.const import TEAMLEADER_NAME
    from codeharness.team import dynamic_assembly
    agents, sop = dynamic_assembly(FakeLLM([]))
    llms = {TEAMLEADER_NAME: FakeLLM(leader_script), "Alice": FakeLLM(alice_script),
            "Bob": FakeLLM(bob_script or [_think("结束", [{"command_name": "end", "args": {}}])])}
    for name, llm in llms.items():
        agents[name].llm = llm
        agents[name].ltm = None
    return agents, sop, llms


PLAN_CMD = {"command_name": "Plan.append_task",
            "args": {"task_id": "t1", "dependent_task_ids": [], "instruction": "写一份 PRD",
                     "assignee": "Alice"}}
PUBLISH = _publish("照需求写一份 PRD，中文，落到产物仓", "Alice")
REPLY = {"command_name": "RoleZero.reply_to_human", "args": {"content": "PRD 已写入产物仓"}}
FINISH = {"command_name": "Plan.finish_current_task", "args": {}}
END = {"command_name": "end", "args": {}}


async def t6_report_path():
    """成员干完 → 回报进队长 → 队长的计划活着、能 finish_current_task（C1-②b 的全部意义）"""
    from codeharness.const import TEAMLEADER_NAME
    agents, sop, llms = _team(
        [_think("先立计划再派活", [PLAN_CMD, PUBLISH]), _think("等回报", [END]),
         _think("成员做完了，收口这一步", [FINISH, END])],
        # 汇报与收尾**分两轮**：只读最后一轮的旧写法会把她那句 PRD 蒸发成第二轮的「收工」，
        # 旧代码在这条断言上必红——这就是 t6 的判据所在
        [_think("写完了", [REPLY]), _think("收工", [END])],
        [_think("不该轮到我", [END])])
    stats = []
    graph = build_team(agents, sop=sop, stats=stats)
    out = await graph.ainvoke(
        {"messages": [Message(content="做一个命令行待办工具", role="user",
                              cause_by=RequirementTag.USER_REQUIREMENT)],
         "memories": {}, "debug_rounds": 0, "team_rounds": 0, "finished": False},
        {"configurable": {"thread_id": "s22t6"}})

    reports = [m for m in out["messages"] if m.instruct_schema == "TeamReport"]
    assert len(reports) == 1 and reports[0].send_to == {TEAMLEADER_NAME} \
        and reports[0].sent_from == "Alice", f"回报没投回队长：{[(r.sent_from, r.send_to) for r in reports]}"
    assert "PRD 已写入产物仓" in reports[0].content, \
        f"回报内容不是她那句汇报（跨轮次取 reply 的修法没生效）：{reports[0].content!r}"
    leader_plan = (out.get("plans") or {}).get(TEAMLEADER_NAME)
    assert leader_plan is not None and leader_plan.get("tasks"), \
        "回报把队长的计划清掉了（C71：外层 plans 没收到队长的写回——播种或写回断了）"
    assert leader_plan["tasks"][0]["is_finished"], \
        f"队长没能 finish_current_task：{leader_plan['tasks'][0]}"
    leader = agents[TEAMLEADER_NAME]
    assert not hasattr(leader, "_report_to"), \
        "C69 之后 _report_to 是激活级局部量（收口消费、无实例残留）——实例字段还在=改造没做完" \
        "（旧护栏钉的「回报不回投给成员」由此结构性保证）"
    flat = [m for call in llms[TEAMLEADER_NAME].calls for m in (call if isinstance(call, list) else [call])]
    joined = " ".join(getattr(m, "content", str(m)) for m in flat)
    assert "[Alice 的回报]" in joined, "回报没进队长的模型上下文"
    assert llms["Bob"].calls == [], f"Bob 被无关唤醒了 {len(llms['Bob'].calls)} 次"
    woke = [s for s in stats if s["activated"]]
    assert len(woke) == 3 and all(s["activated"] == 1 for s in woke), f"来回里的激活不精准：{stats}"
    print(f"  ok  t6 回报通路：Alice→队长定向送达、队长计划存活并 finish、"
          f"三次激各活 1 个节点、Bob 零调用（stats={stats}）")


async def t8_plan_open_reads_real_dump():
    """C148 真图档：`_publish_plan_open` 读的是**真状态机**，不是替身图的 values。

    为什么必须有这一格：s17 的 t18 吃替身图，而本会话刚因 `state.values["plans"]` 的形状假设真炸过一次
    ——第一版写 `for dump in ….get("plans") or {}`，遍历 dict 拿到的是**键**（角色名），
    `AttributeError: 'str' object has no attribute 'get'` 冒到 `_fail`，红出来却在无关的 t7。
    所以「dump 形状对不对」不能靠我自己造的字典证，得用真 `build_team` + 真剧本跑完一场再数。
    剧本：队长立两条任务（t1、t2）→ 派活 → Alice 回报 → 队长 `finish_current_task`（只勾得上 t1）
    ⇒ 真图里 `plans[队长]["tasks"]` 是 2 条、1 条完成；交给真 runner 应当数出 `open=1/total=2`。"""
    import tempfile
    from pathlib import Path

    from langgraph.checkpoint.memory import InMemorySaver
    from server.events import SessionEventBus
    from server.runner import SessionRunner
    from server.sessions import SessionStore

    plan2 = {"command_name": "Plan.append_task",
             "args": {"task_id": "t2", "dependent_task_ids": ["t1"], "instruction": "补上测试", "assignee": "Alice"}}
    agents, sop, _llms = _team(
        [_think("立两条任务再派活", [PLAN_CMD, plan2, PUBLISH]),
         _think("等回报", [END]),
         _think("第一条做完了，收口这一步", [FINISH, END])],
        [_think("写完了", [REPLY]), _think("收工", [END])],
        [_think("不该轮到我", [END])])
    stats = []
    # checkpointer 显式给：生产由 runner 注入 AsyncSqliteSaver，这里要的是「aget_state 读得到」这同一件事
    graph = build_team(agents, checkpointer=InMemorySaver(), sop=sop, stats=stats)
    cfg = {"configurable": {"thread_id": "s22t8"}}
    out = await graph.ainvoke(
        {"messages": [Message(content="做一个命令行待办工具", role="user",
                              cause_by=RequirementTag.USER_REQUIREMENT)],
         "memories": {}, "debug_rounds": 0, "team_rounds": 0, "finished": False}, cfg)

    dump = (out.get("plans") or {}).get(TEAMLEADER_NAME)
    assert dump and len(dump.get("tasks") or []) == 2, f"前置失配：真图里队长计划不是两条任务：{dump}"
    done = [t["task_id"] for t in dump["tasks"] if t["is_finished"]]
    assert done == ["t1"], f"前置失配：勾掉的不是 t1（剧本没跑对，后面数的就不是真账）：{dump['tasks']}"

    store = SessionStore(path=Path(tempfile.mkdtemp()) / "sessions.json")
    s = store.create("C148 真图档", project_name="s22t8")
    bus = SessionEventBus()
    runner = SessionRunner(store, bus)
    runner.graphs[s.id] = (graph, cfg)
    await runner._publish_plan_open(s.id)
    ev = [e for e in bus.history(s.id) if e.kind == "turn"]
    assert len(ev) == 1, f"真图收口该发且只发一条未完成提示，实际 {len(ev)} 条：{ev}"
    r = ev[0].value.get("reason") or {}
    assert (r.get("kind"), r.get("open"), r.get("total")) == ("plan-unfinished", 1, 2), \
        f"数的是真 dump 却数错了（形状假设又漂了？）：{r}"
    # 「全勾完不发」那一档的对照在 s17 t18②（同一颗函数，替身图与真图各证一次，不在这儿重复造状态）
    print(f"  ok  t8 真图档：真 build_team 的 plans dump 数出 open=1/total=2（剧本勾掉 t1），"
          f"替身图那格（s17 t18）的形状假设至此被真图复核过")


async def t7_pingpong_brake():
    """队长无视回报反复重派 → 必须靠 team_rounds 刹车散会，而不是烧到 recursion_limit 把整场打成 failed。
    剧本刻意只给一条回复：FakeLLM 耗尽后重复最后一条，于是「派活→收口」与「汇报→收口」每轮都重演，
    形成真·委派↔回报死循环。阳性对照：同一剧本把 recursion_limit 压到 12（刹车要 12 个来回≈24 超步才生效）
    → 必须真抛 GraphRecursionError，证明这条判据不是「本来就停得下来」。"""
    from langgraph.errors import GraphRecursionError
    agents, sop, llms = _team([_think("再派一次", [PUBLISH, END])], [_think("做完了", [REPLY, END])])
    graph = build_team(agents, sop=sop)
    init = {"messages": [Message(content="做个工具", role="user",
                                 cause_by=RequirementTag.USER_REQUIREMENT)],
            "memories": {}, "debug_rounds": 0, "team_rounds": 0, "finished": False}
    try:
        await graph.ainvoke(init, {"configurable": {"thread_id": "s22t7control"},
                                   "recursion_limit": 12})
    except GraphRecursionError:
        pass
    else:
        raise AssertionError("对照组没能把 recursion_limit 撞出来——这个剧本根本不循环，判据是空转")
    out = await graph.ainvoke(init, {"configurable": {"thread_id": "s22t7"}})
    assert out["team_rounds"] >= 12, f"刹车没到档就散了：team_rounds={out['team_rounds']}"
    cycles = sum(1 for m in out["messages"] if m.instruct_schema == "TeamReport")
    assert cycles >= 5, f"来回数对不上刹车档位：report×{cycles}"
    print(f"  ok  t7 死循环刹车：对照组真撞 recursion_limit、实验组 {out['team_rounds']} 档"
          f"（{cycles} 个委派-回报来回）后正常散会，会话没被打成 failed")


def t9_role_lanes():
    """C190（10-10）：角色生命周期事件（车道）必须由**真图**数出来，不能照参照系的形状写。

    为什么这一格非用真图不可：三条反直觉的事实全是现取的
    （`tests/manual_role_node_shape_probe.py`，真 `build_team` + 每角色独立 FakeLLM 剧本）——
      · 整场所有 `on_chain_*` 的 `run_id` 是**同一颗** ⇒ 拿它配对会把两次激活并成一条车道；
      · 角色本体的 `checkpoint_ns` 为空，内层 think/gate/act 的那颗 uuid 首段才是角色名；
      · `graph:step:N` 在 start 与 end 上不是同一个数 ⇒ 也配不上。
    本仓在这族「判据打在构造/合成事件上、生产里那条分支从不触发」上应验过两次
    （A4 那轮的 `on_interrupt`；`manual_stream_landing` 文件头那两次形状猜错）。所以形状先现取，
    再拿真图的事件源喂真 `_translate`，数总线上的东西。

    四格：① 每次激活恰好一条 `started` + 一条 `completed`，uuid 配对且不重复、ms 是算出来的；
         ② `phase` 只挂在**已开着的**车道上（不发孤立相位——前端会画出一条没主的行）；
         ③ 内层节点名不冒充角色（`think`/`act` 不该有自己的车道）；
         ④ 阳性对照·取消那条路：走不到 `on_chain_end`，散会清扫必须补一颗 `aborted` 收口
            （与 C172 的兜底行同族：不收口就是那个角色在界面上永远「在跑」）。
    ⑤（C193 加的一格）停在待人工处那一条**不是** aborted：补的是 `paused` + `park`，且不带 `ms`。
    """
    import tempfile
    from pathlib import Path

    from langgraph.checkpoint.memory import InMemorySaver
    from server.events import SessionEventBus
    from server.runner import SessionRunner
    from server.sessions import SessionStore

    agents, sop, _ = _team([_think("派活给 Alice", [PUBLISH]), _think("等回报", [END])],
                           [_think("PRD 写完了", [REPLY, END])])
    graph = build_team(agents, sop=sop, checkpointer=InMemorySaver())
    tmp = Path(tempfile.mkdtemp())
    store = SessionStore(path=tmp / "sessions.json")
    bus = SessionEventBus()
    runner = SessionRunner(store, bus)
    init = {"messages": [Message(content="做个命令行待办工具", role="user",
                                  cause_by=RequirementTag.USER_REQUIREMENT)],
            "memories": {}, "debug_rounds": 0, "team_rounds": 0, "finished": False}

    def _run(sid_tag, stop_after_started=False, park=""):
        s = store.create("车道判据", project_name=f"s22t9_{sid_tag}", paradigm="dynamic")
        cfg = {"configurable": {"thread_id": f"s22t9-{sid_tag}"}}
        # 真路径里这份名单是 `_prepare` 顺手缓存的（装配出口那一份，与 /chat 目标校验同源）。
        # 这里只借真图的事件源，不重跑装配：装配要建网关与 checkpointer，与「车道形状」无关。
        runner._node_names[s.id] = set(agents)

        async def _go():
            seen_started = 0
            async for ev in graph.astream_events(init, cfg, version="v2"):
                runner._translate(s.id, ev)
                if stop_after_started and ev.get("event") == "on_chain_start" \
                        and str((ev.get("metadata") or {}).get("langgraph_node") or "") in agents:
                    seen_started += 1
                    if seen_started:
                        return s.id          # 模拟「角色还在跑就被取消」：停在第一条车道开着的时候
            return s.id

        asyncio.run(_go())
        if stop_after_started:
            # 取消/异常那条路的清扫点（C172 同处）；`park` 非空＝停在待人工处那一条（C193）
            runner._forget(s.id, terminal=False, park=park)
        return s.id, [e for e in bus.history(s.id) if e.kind == "role"]

    sid, lanes = _run("full")
    started = [e for e in lanes if e.name == "started"]
    done = [e for e in lanes if e.name == "completed"]
    phases = [e for e in lanes if e.name == "phase"]
    assert started, "真图跑完一条车道都没有 ⇒ 发射点根本没通电（下面几条都是空转）"
    assert len({e.uuid for e in started}) == len(started), \
        f"车道 uuid 重复了（配对靠的就是它）：{[e.uuid for e in started]}"
    assert {e.uuid for e in started} == {e.uuid for e in done}, \
        f"开了没关 / 关了没开：started={[e.uuid for e in started]} completed={[e.uuid for e in done]}"
    assert all(e.role in agents for e in lanes), \
        f"车道角色不在册（内层节点名冒充了角色？）：{sorted({e.role for e in lanes})}"
    assert {e.value.get("phase") for e in started} | {e.value.get("phase") for e in phases} \
        <= {"observe", "think", "gate", "act"}, "相位取值漂了（前端词表对账在 s8 t32⑦）"
    opened = set()
    for e in lanes:
        if e.name == "started":
            opened.add(e.uuid)
        elif e.name == "phase":
            assert e.uuid in opened, f"孤立相位：车道 {e.uuid} 还没开就来了 phase（会画出没主的行）"
    assert sum(int(e.value.get("ms") or 0) for e in done) > 0, \
        f"ms 全是 0 = 根本没在算（写死的 0 比不报更坏）：{[e.value for e in done]}"
    # 栈要弹空（键可以留着、值是空栈）；`_lane_t0` 按 (sid, uuid) 存，必须一条不剩。
    # 上一版这里写成 `assert not runner._lane_open.get(sid)`——那查的是「键没了」，而实现只 pop
    # 弹栈不删键，于是判据红在一处不是缺陷的地方（假红也是红，记一笔免得下次再撞）。
    _stacks = runner._lane_open.get(sid) or {}
    assert all(not v for v in _stacks.values()), \
        f"跑完还留着在途车道 ⇒ 配对没弹干净，取消时会补错收口：{ {k: list(v) for k, v in _stacks.items()} }"
    assert not [k for k in runner._lane_t0 if k[0] == sid], "跑完还留着在途 t0 ⇒ 计时表漏扫"
    # 同一个角色被激活两次必须留下**两条**车道（LIFO 配对的正证；剧本里队长派活一次、收回报一次）
    per_role: dict = {}
    for e in started:
        per_role[e.role] = per_role.get(e.role, 0) + 1
    assert max(per_role.values()) >= 2, \
        f"这场真图里没有一个角色跑过两趟（{per_role}）⇒ 配对这条判据成了空转，别留假绿"

    _sid2, lanes2 = _run("cancel", stop_after_started=True)
    aborted = [e for e in lanes2 if e.name == "completed" and e.value.get("aborted")]
    assert lanes2 and aborted, \
        f"取消那条路必须留下 aborted 收口（否则界面上那个角色永远在跑）：{[(e.name, e.value) for e in lanes2]}"
    assert {e.uuid for e in aborted} <= {e.uuid for e in lanes2 if e.name == "started"}, \
        "aborted 收口对到了没开过的车道上"

    # ⑤ C193（10-10 真模型活体现证）：**停在待批/待答处**那一条收口必须是 `paused` 带 `park`，
    # 不是 aborted，也不许带 ms。那场跑到 `finished` 的会话 12 颗收口有 6 颗写着「没跑完」，
    # 就是因为 `_park` 也走 `_forget`，而这里只有一支 `aborted=True`。
    _sid3, lanes3 = _run("park", stop_after_started=True, park="approval")
    paused = [e for e in lanes3 if e.name == "paused"]
    assert paused, f"⑤失效：停在待人工处仍按散会收口 ⇒ 界面上就是那句假「没跑完」：{[(e.name, e.value) for e in lanes3]}"
    assert not [e for e in lanes3 if e.name == "completed" and e.value.get("aborted")], \
        "⑤失效：停车那一趟同时被写成 aborted——两种收尾必须分家，前端才选得对文案"
    assert all(e.value.get("park") == "approval" and "ms" not in e.value for e in paused), \
        f"⑤失效：paused 那颗形状漂了（`park` 才说得出「等你批准」；带 `ms` 就是把散场墙钟差当用时）：{[e.value for e in paused]}"
    print(f"  ok  t9 车道真图档：{len(started)} 条 started/completed 逐条配对、"
          f"{len(phases)} 条相位全挂在已开车道上、ms 合计 "
          f"{sum(int(e.value.get('ms') or 0) for e in done)}ms；取消档补了 {len(aborted)} 条 aborted、"
          f"停车档补了 {len(paused)} 条 paused（不带 ms）")


def t10_resume_has_a_queue_to_address():
    """C192（10-10 真模型活体照出来的一条静默丢消息）。

    现场：8795 隔离实例那场 classic（¥0.041、641 条事件）里那句**没写目标**的插话，日志逐字
    `插话指名投给不存在的角色 'Mike'，该条已丢弃（在册：['Architect', 'Engineer', 'PM',
    'PMManager', 'QA']）`——而同一场 `entry_role` 明明是 `PM`。根因是**顺序**：`_run` 先建队再
    `_prepare`（默认目标落得上），`_resume` 却先 `_ensure_graph`（走到装配出口）再在第 751 行建队，
    于是**停过一次待批再续跑**的每一场，队上留的是构造函数那个 `TEAMLEADER_NAME="Mike"`，
    而 classic/react 的队长叫 PM、不在 Mike 名下 ⇒ route 按「指名了不存在的角色」把这条丢掉。
    判法打在 route 自己那句条件上：`default_target` 必须在册（它查的就是 `recv not in agents`）。
    """
    import tempfile
    from pathlib import Path

    from codeharness.provider.cost import CostManager
    from codeharness.environment.team_graph import SOP
    import codeharness.team as team_mod
    from server.events import SessionEventBus
    from server.runner import SessionRunner
    from server.sessions import SessionStore

    real_llm = team_mod._make_llm
    team_mod._make_llm = lambda cm=None, override=None: FakeLLM([])
    tmp = Path(tempfile.mkdtemp())
    store = SessionStore(path=tmp / "sessions.json")
    runner = SessionRunner(store, SessionEventBus())

    async def none():
        return None
    runner._saver = none
    try:
        s = store.create("插话默认目标判据", project_name="s22_t10", paradigm="classic")
        assert s.id not in runner.chats, "前置失配：还没装配就已有队，这格测不到『队还没建』"

        async def go(session, persist):
            return await runner._prepare(session, session.project_name, CostManager(),
                                         persist_roles=persist)
        asyncio.run(go(s, True))

        chat = runner.chats.get(s.id)
        assert chat is not None, \
            "失效：`_resume` 那条顺序（先 _prepare 后建队）没被兜住——装配出口拿不到队，默认目标就留在 Mike"
        assert chat.default_target == s.entry_role, \
            f"默认目标与回填的 entry_role 不一致：{chat.default_target!r} vs {s.entry_role!r}"
        assert chat.default_target in s.roles, \
            f"①失效：空目标插话会被 route 丢掉（`recv not in agents`）——default_target={chat.default_target!r}" \
            f"、在册={s.roles}"
        assert chat.default_target != TEAMLEADER_NAME, \
            "①失效：默认目标还是构造函数那颗 Mike（classic 的队长是 PM）"
        # 装配自己算出来的那份也要对得上（这格不是自证：它读的是 route 用的同一张 SOP 表）
        assert s.entry_role in SOP[RequirementTag.USER_REQUIREMENT], \
            f"entry_role 不在 SOP 的 USER_REQUIREMENT 目标里：{s.entry_role!r}"

        # 阳性对照·只读回放不许建队（B7 那一列同判：两条 GET 不该留下写侧痕迹）
        s2 = store.create("只读回放不建队", project_name="s22_t10_ro", paradigm="classic")
        asyncio.run(go(s2, False))
        assert s2.id not in runner.chats, \
            "②失效：只读回放（persist_roles=False）也建了队——那两条 GET 从此有写侧痕迹"
        assert not store.get(s2.id).roles and not store.get(s2.id).entry_role, \
            "②失效：只读回放把 roles/entry_role 回写进库了"
        print(f"  ok  t10 续跑档：装配出口建队并把空目标默认落进在册角色（{chat.default_target}），"
              f"只读回放那趟依旧零痕迹")
    finally:
        team_mod._make_llm = real_llm
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)
        shutil.rmtree(Path("workspace") / "s22_t10", ignore_errors=True)
        shutil.rmtree(Path("workspace") / "s22_t10_ro", ignore_errors=True)


def main():
    t1_split_and_target()
    t2_real_graph()
    t3_wrong_name_heals()
    t4_no_roster_refuses()
    t5_original_message_not_narrowed()
    asyncio.run(t6_report_path())
    asyncio.run(t7_pingpong_brake())
    asyncio.run(t8_plan_open_reads_real_dump())
    t9_role_lanes()
    t10_resume_has_a_queue_to_address()
    print("\ns22_delegate_route: 10/10 全绿")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
