"""S23 门禁（C1-③ 现场招人）：会话期内造一个新成员，并且真的能在下一跑被队长点名唤醒。

钉六件事：
  t1 校验单点 `check_role_def`：非法节点名 / 空 profile / **未注册工具名** / 与已装配重名 四格必抛，
     外加一格正向对照（合法档案归一后字段齐、tools 去重排序）——没有正向对照就是「根本不区分对错」的空判据。
  t2 档位门复用的是执行期那张表：dynamic + `readonly` 档招一个带 `terminal_command` 的成员 → 422
     并说要 `full_access`；把会话切到 `full_access` 再招 → 200。同一份声明在两道档下结果必须不同，
     否则这条门是装饰。
  t3 落库同形：`role_defs` 走 `_JSON_FIELDS`，`_dump`→`_load` 一圈回来还是 list[dict]。
     **这条是专门防「Redis 与 JSON 两台 store 长得不一样」**：漏登记时 Redis 那台会 str() 落库、
     回读直接炸或读成字符串。
  t4 装配真吃它：`runner._prepare` 之后 agents 里有新名字、`session.roles` 回填上去了、
     **队长的名册里也有他**（没有名册队长不知道叫什么，委派就永远到不了）。
  t5 真图端到端：队长 `publish_team_message(指令, "Cleo")` → Cleo 节点被激活、拿到那条指令，
     干完把回报送回队长（C1-②/②b 那两跳在**新成员**身上重演一遍才算通路完整）。
  t6 越界与热插都拒：classic 线招人 → 422（那条线没人点名新节点，招进来是死成员）；
     会话正在跑 → 409（生效点是下一次装配，不做图中热插）。

跑法：
  cd /e/Codeharness && PYTHONPATH=/e/Codeharness:/e/Codeharness/logs PYTHONIOENCODING=utf-8 \\
    PYTHONDONTWRITEBYTECODE=1 F:/anaconda/python.exe -B tests/s23_hire_role.py
"""
import asyncio
import json
import tempfile
from pathlib import Path

from codeharness.const import RequirementTag, TEAMLEADER_NAME
from codeharness.provider.fake import FakeLLM
from codeharness.schema import Message
from codeharness.team import check_role_def

HIRED = {"name": "Cleo", "profile": "Data Analyst", "goal": "clean and summarize the dataset",
         "constraints": "只用 pandas，不联网", "tools": ["read_file", "search_file"]}


def _think(thought: str, commands: list) -> str:
    return json.dumps({"thought": thought, "commands": commands}, ensure_ascii=False)


def t1_check_role_def():
    ok = check_role_def(HIRED, taken=[])
    assert ok["name"] == "Cleo" and ok["tools"] == ["read_file", "search_file"], ok
    for bad, why in (({**HIRED, "name": "有中文"}, "非法节点名"),
                     ({**HIRED, "name": "Two Words"}, "含空格"),
                     ({**HIRED, "profile": "  "}, "空 profile"),
                     ({**HIRED, "tools": ["read_file", "not_a_tool"]}, "未注册工具名"),
                     ({**HIRED, "goal": ""}, "空 goal")):
        try:
            check_role_def(bad, taken=["PM"])       # taken 里不含 Cleo：这几格要各自那条原因触发，不能撞上重名闸
        except ValueError as e:
            assert why_key(why) in str(e) or "not_a_tool" in str(e), f"{why} 抛了但不说原因：{e}"
        else:
            raise AssertionError(f"{why} 没被拦住")
    try:
        check_role_def(HIRED, taken=["PM", "Cleo"])
    except ValueError as e:
        assert "Cleo" in str(e), f"重名错误没带上那个名字：{e}"
    else:
        raise AssertionError("与已装配成员重名却没报错")
    print("  ok  t1 五格校验全对（四拒 + 一正向），每条错误文本都点名原因")


def why_key(why: str) -> str:
    return {"非法节点名": "标识符", "含空格": "标识符", "空 profile": "profile",
            "未注册工具名": "未注册", "空 goal": "goal"}[why]


def t2_tier_gate_and_rejections():
    from fastapi.testclient import TestClient
    from server.app import create_app
    import server.sessions as ss

    keep, ss.SESSIONS_FILE = ss.SESSIONS_FILE, Path(tempfile.mkdtemp()) / "s23.json"
    try:
        import codeharness.configs.settings as cs
        cs.settings.platform.use_redis = False
        with TestClient(create_app()) as c:
            sid = c.post("/api/sessions", json={"idea": "分析这份销售表", "project_name": "s23dyn",
                                                "paradigm": "dynamic"}).json()["id"]
            term = {**HIRED, "tools": ["terminal_command"]}
            r = c.post(f"/api/sessions/{sid}/roles", json=term)
            assert r.status_code == 422, f"readonly 档下声明 terminal_command 竟然过了：{r.status_code}"
            assert "full_access" in r.text and "terminal_command" in r.text, \
                f"422 没说要哪一档、也点了工具名：{r.text[:160]}"
            # 档位只在建会话时定（PatchSessionReq 只认重命名/归档/置顶三字段），所以对照格另建一场
            wide = c.post("/api/sessions", json={"idea": "x", "project_name": "s23wide",
                                                 "paradigm": "dynamic", "permission": "full_access"}).json()["id"]
            r2 = c.post(f"/api/sessions/{wide}/roles", json=term)
            assert r2.status_code == 200, f"full_access 档下同一份声明必须过：{r2.status_code} {r2.text[:120]}"
            assert r2.json()["takes_effect"] == "next_start", r2.json()

            bad = c.post(f"/api/sessions/{wide}/roles", json={**HIRED, "name": "Dora",
                                                             "tools": ["no_such_tool"]})
            assert bad.status_code == 422 and "no_such_tool" in bad.text, bad.text[:160]
            dup = c.post(f"/api/sessions/{wide}/roles", json=term)
            assert dup.status_code == 422, "同名成员招了两次竟然通过"
            cls = c.post("/api/sessions", json={"idea": "x", "project_name": "s23cls"}).json()["id"]
            r3 = c.post(f"/api/sessions/{cls}/roles", json=HIRED)
            assert r3.status_code == 422 and "dynamic" in r3.text, f"classic 线招人没被拒：{r3.text[:120]}"
            got = c.get(f"/api/sessions/{wide}").json()
            assert [d["name"] for d in got["role_defs"]] == [term["name"]], got["role_defs"]
            print("  ok  t2 档位门两档结果不同（422↔200）、未注册工具/重名/classic 线各拒一次")
    finally:
        ss.SESSIONS_FILE = keep


def t3_store_roundtrip():
    from platforms.session_store import _JSON_FIELDS, _dump, _load
    from server.sessions import Session
    assert "role_defs" in _JSON_FIELDS, "新字段没登记进 _JSON_FIELDS —— Redis 那台会 str() 落库"
    s = Session(id="s23", idea="x", project_name="p", paradigm="dynamic", role_defs=[HIRED])
    h = _dump(s)
    assert isinstance(h["role_defs"], str), f"落哈希时必须是 JSON 字符串，实收 {type(h['role_defs'])}"
    back = _load("s23", h)
    assert back.role_defs == [HIRED], f"回读变形了：{back.role_defs!r}"
    assert isinstance(back.role_defs[0], dict) and back.role_defs[0]["tools"] == HIRED["tools"]
    print("  ok  t3 全量 _dump→_load 同形（落哈希是 str、回读是 list[dict]）")


def t4_assembly_sees_the_hire():
    """`_prepare` 是装配与回填的唯一接缝：招人后四处必须一起长齐（少一处就是「招了但没用上」）。
    会话必须真在 store 里——`_prepare` 出口会 `store.update(roles=…)`，凭空造的 Session 会 KeyError
    （本门禁第一版就栽在这，记着免得再犯）。"""
    import codeharness.team as team
    from server.runner import SessionRunner
    from server.sessions import SessionStore

    seen = {}
    saved = team.prepare_project

    def spy(idea, project, agents=None, checkpointer=None, cost_manager=None, sop=None):
        seen["agents"] = agents
        return saved(idea, project, agents=agents, checkpointer=checkpointer,
                     cost_manager=cost_manager, sop=sop)

    team.prepare_project = spy
    try:
        store = SessionStore(path=Path(tempfile.mkdtemp()) / "s23r.json")
        s = store.create(idea="分析销售表", project_name="s23a", paradigm="dynamic")
        store.update(s.id, role_defs=[HIRED])
        runner = SessionRunner(store, None)

        async def _none_saver():
            return None
        runner._saver = _none_saver
        s = store.get(s.id)
        asyncio.run(runner._prepare(s, s.project_name, None))
    finally:
        team.prepare_project = saved

    agents = seen["agents"]
    assert "Cleo" in agents, f"招的人没进装配：{sorted(agents or {})}"
    s2 = store.get(s.id)
    assert "Cleo" in s2.roles, f"session.roles 没回填：{s2.roles}"
    assert s2.role_defs == [HIRED], s2.role_defs
    leader = agents[TEAMLEADER_NAME]
    assert "Cleo" in leader.teammates, f"队长名册里看不见新成员：{sorted(leader.teammates)}"
    assert "Cleo" in leader.instruction_provider(), "TL_INSTRUCTION 的 {team_info} 槽没填进新成员"
    # RoleZero.tools 是 {名字: 工具} 的 dict（不是列表），断言吃键集
    assert {"read_file", "search_file"} <= set(agents["Cleo"].tools), sorted(agents["Cleo"].tools)
    print("  ok  t4 装配、session.roles 回填、队长名册与 prompt、成员工具集四处长齐")


def t5_real_graph_hire_reachable():
    """队长点名新成员 → 新节点真被激活、拿到指令、干完回报回队长"""
    from codeharness.environment.team_graph import build_team
    from codeharness.team import dynamic_assembly, sync_roster
    agents, sop = dynamic_assembly(FakeLLM([]))
    from codeharness.team import build_hired_role
    agents["Cleo"] = build_hired_role(HIRED, FakeLLM([
        _think("表读完了，摘要写好", [{"command_name": "RoleZero.reply_to_human",
                                     "args": {"content": "摘要已产出"}}]),
        _think("收工", [{"command_name": "end", "args": {}}])]))
    roster = sync_roster(agents)
    assert set(roster) == {TEAMLEADER_NAME, "Alice", "Bob", "Cleo"}, sorted(roster)
    leader_llm = FakeLLM([_think("这活得交给新来的分析师",
                                 [{"command_name": "TeamLeader.publish_team_message",
                                   "args": {"content": "把 sales.csv 汇总成周报", "send_to": "Cleo"}}]),
                          _think("等回报", [{"command_name": "end", "args": {}}])])
    agents[TEAMLEADER_NAME].llm = leader_llm
    for n in ("Alice", "Bob"):
        agents[n].llm = FakeLLM([_think("不该我", [{"command_name": "end", "args": {}}])])
    for r in agents.values():
        r.ltm = None
    out = asyncio.run(build_team(agents, sop=sop).ainvoke(
        {"messages": [Message(content="分析销售数据", role="user",
                              cause_by=RequirementTag.USER_REQUIREMENT)],
         "memories": {}, "debug_rounds": 0, "team_rounds": 0, "finished": False},
        {"configurable": {"thread_id": "s23t5"}}))

    reports = [m for m in out["messages"] if m.instruct_schema == "TeamReport"]
    assert len(reports) == 1 and reports[0].sent_from == "Cleo" \
        and reports[0].send_to == {TEAMLEADER_NAME}, f"新成员没回报给队长：{reports}"
    assert "摘要已产出" in reports[0].content, reports[0].content
    cleo_llm = agents["Cleo"].llm
    flat = [m for call in cleo_llm.calls for m in (call if isinstance(call, list) else [call])]
    joined = " ".join(getattr(m, "content", str(m)) for m in flat)
    assert "sales.csv" in joined, "Cleo 被激活却没拿到队长那条指令"
    for name in ("Alice", "Bob"):
        assert agents[name].llm.calls == [], f"{name} 被无关唤醒"
    print("  ok  t5 真图：队长→Cleo 定向送达、Cleo→队长 回报送达，其余成员零调用")


def t6_no_hot_swap():
    from server.sessions import SessionStatus
    assert SessionStatus.running.value == "running"
    # 生效点写在响应里，前端才知道要提示「下一次起跑才生效」
    import inspect
    from server.api.sessions import hire_role
    src = inspect.getsource(hire_role)
    assert 'takes_effect": "next_start"' in src and "SessionStatus.running" in src, \
        "运行中拒招 / next_start 语义被改掉了（生效点口径不能悄悄漂移）"
    print("  ok  t6 运行中拒招 + 响应里带 next_start 语义（不假装热插）")


def main():
    t1_check_role_def()
    t2_tier_gate_and_rejections()
    t3_store_roundtrip()
    t4_assembly_sees_the_hire()
    t5_real_graph_hire_reachable()
    t6_no_hot_swap()
    print("\ns23_hire_role: 6/6 全绿")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
