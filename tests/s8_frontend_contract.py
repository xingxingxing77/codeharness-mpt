"""S8 门禁（第二件）：前端 ↔ 后端契约（施工4「三处核对」的机器版）。

背景（docs 陷阱 #2 的教训族）：FakeLLM 全绿≠浏览器能跑——词汇表错一个词，
富块静默降级成灰色 GenericBlock，只在浏览器里暴露。所以把"核对"钉成断言：
  t1 九值 BlockType ↔ Timeline.vue 分发名逐一对上（错一个词=一类块永久灰色）。
  t2 事件信封：server Event 模型字段 == 前端 WEvent 字段；kind 词汇表后端全量 ⊆ 前端处理集。
  t3 路由：client.ts / sessions.ts 消费端写死的每个 /api 路径都在 FastAPI 路由表里
     （前端 404 这种"只有点开才炸"的洞归零）。
  t4 /graph 端点：节点与边取自真装配（_default_agents × watch），不是手绘——
     断言每个角色名成节点、每个 watch tag 成边；不存在的会话 404。
  t5 /workspace/file 响应形状：预览分发靠 ext 键（真浏览器第十五处——字段一直没回，
     markdown/image/.mmd 三类预览从未命中，路由门禁 t3 查不出"路由在但形状错"）。
  t7 直聊目标出自真装配：三线各自的路由目标必须在自己角色集里，/chat 对未知目标 422，
     前端 ComposerCard 不得再硬编码角色名（曾写着三个装配里不存在的名字→追问静默丢弃）。
  t8 工具审批（批次36）：判定表 fail-closed 且覆盖全量工具/Action、两张图真连 gate 节点、
     同一 approval_id 重放幂等、未批的动作不执行、permission 与 respond 端点的值域。
     全程不打模型——云端额度只剩几块钱，判定与端点都能离线验。
  t9 F2：req() 的 fetch 必须真带 AbortSignal.timeout（否则服务端挂起=前端永久 pending、按钮焊死）。
  t10 F3：respondApproval 的 catch 必须「快照回滚 + 服务端重取」两句都在
     （只回滚会复活别处已决议的卡，只重取则断网时卡片再也回不来）。

跑法：
  cd /e/Codeharness && PYTHONPATH=/e/Codeharness PYTHONIOENCODING=utf-8 F:/anaconda/python.exe tests/s8_frontend_contract.py
"""
import re
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FE = ROOT / "frontend" / "src"

# 后端事件 kind 的发出点：events.py 默认值 + 各 publish(kind=...)
_KIND_RE = re.compile(r'kind="(\w+)"|kind: str = "(\w+)"')
# 前端消费的 kind：applyEvent 的 if/else-if 链
_FE_KIND_RE = re.compile(r"ev\.kind === '(\w+)'")


def t1_blocktype_vocabulary():
    from codeharness.report import BlockType
    # 分发从 Timeline.vue 搬到了 conversation/：ChatNode 管 prose 与 User，
    # ToolCard 按 block 类型给展开卡。模板里写 b.type、脚本里写 b.value.type，
    # 所以匹配 type === '...' 而不是写死前缀。
    src = ''.join((FE / 'components' / 'conversation' / f).read_text(encoding='utf-8')
                  for f in ('ChatNode.vue', 'ToolCard.vue'))
    dispatched = set(re.findall(r"type === '([^']+)'", src))
    missing = {b.value for b in BlockType} - dispatched
    assert not missing, f"BlockType {missing} 在 ChatNode/ToolCard 无分发分支——块会静默降级灰色 GenericBlock"
    _ok("t1", f"九值 BlockType 词汇表 ↔ conversation 分发逐一对上（{len(dispatched)} 支）")


def t2_envelope_and_kinds():
    from server.events import Event
    types_ts = (FE / "types.ts").read_text(encoding="utf-8")
    iface = re.search(r"interface WEvent \{(.*?)\n\}", types_ts, re.S).group(1)
    fe_fields = {m.strip() for m in re.findall(r"^\s*(\w+)[?]?:", iface, re.M)}
    be_fields = set(Event.model_fields)
    assert fe_fields == be_fields, f"信封字段漂移 server={sorted(be_fields)} fe={sorted(fe_fields)}"

    server_srcs = [ROOT / "server" / p for p in
                   ("events.py", "runner.py")] + [ROOT / "server" / "api" / p
                                                  for p in ("sessions.py", "approvals.py")]
    kinds = set()
    for f in server_srcs:
        for m in _KIND_RE.finditer(f.read_text(encoding="utf-8")):
            kinds.add(m.group(1) or m.group(2))
    fe_src = (FE / "stores" / "sessions.ts").read_text(encoding="utf-8")
    handled = set(_FE_KIND_RE.findall(fe_src))
    assert kinds <= handled, f"后端发出 {sorted(kinds - handled)} 前端不处理"
    assert set(re.findall(r"kind == \"(\w+)\"", (ROOT / "codeharness" / "report.py").read_text(encoding="utf-8"))) == set()  # 报道走 sink 不标 kind，防误加第二通道
    _ok("t2", f"信封字段 server==frontend（{len(be_fields)} 项）；kind 词汇表 {sorted(kinds)} 全被前端接住")


def t3_routes_exist():
    from server.app import create_app
    declared = {r.path for r in create_app().routes}
    consumed = set()
    for f in (FE / "api" / "client.ts", FE / "stores" / "sessions.ts"):
        txt = f.read_text(encoding="utf-8")
        # fetch/EventSource 模板串里的 /api 路径（${x} → {x}，与 FastAPI 的占位符同名）
        for raw in re.findall(r"[`'\"](\/api\/[^`'\"\s?]*)", txt):
            consumed.add(re.sub(r"\$\{(\w+)\}", r"{\1}", raw))
    assert consumed, "client.ts/sessions.ts 里没解析出任何 /api 路径——正则失效，本门禁空转"
    # N1：client.ts 的 startsWith('/api/auth') 守卫串会进捕获——不是端点，是真前缀，豁免
    missing = {p for p in consumed
               if p not in declared and not any(d.startswith(p + "/") for d in declared)}
    assert not missing, f"前端消费的接口后端没注册：{sorted(missing)}"
    _ok("t3", f"前端消费的 {len(consumed)} 条 /api 路径全部在 FastAPI 路由表")


def t4_graph_endpoint():
    import server.sessions as ss
    keep, ss.SESSIONS_FILE = ss.SESSIONS_FILE, Path(tempfile.mkdtemp()) / "sessions.json"
    try:
        from fastapi.testclient import TestClient
        from server.app import create_app
        from codeharness.team import _default_agents
        with TestClient(create_app()) as c:
            sid = c.post("/api/sessions", json={"idea": "编排冒烟", "project_name": "s8graph"}).json()["id"]
            rsp = c.get(f"/api/sessions/{sid}/graph")
            assert rsp.status_code == 200, rsp.status_code
            mm = rsp.json()["mermaid"]
            agents = _default_agents()
            assert mm.startswith("flowchart LR"), mm[:40]
            for name in agents:
                assert f'["{name}"]' in mm, f"角色 {name} 没成为节点——画的不等于装的"
            for ag in agents.values():
                for tag in ag.watch:
                    assert f"|{tag}|" in mm, f"{tag} 订阅边缺失"
            assert c.get("/api/sessions/nope/graph").status_code == 404

            # 三态「画的就是跑的」（9.3 收口）：此前 dynamic/sop 会话画的也是 classic 表——
            # dynamic 会话的图里出现 PM 就是谎报；sop 会话的图必须来自模板装配。
            did = c.post("/api/sessions", json={"idea": "动态图", "paradigm": "dynamic"}).json()["id"]
            dmm = c.get(f"/api/sessions/{did}/graph").json()["mermaid"]
            from codeharness.const import TEAMLEADER_NAME
            assert f'["{TEAMLEADER_NAME}"]' in dmm, "dynamic 会话的图里没有队长"
            assert '["PM"]' not in dmm, "dynamic 会话画成了 classic 表"

            from codeharness.base.action import BaseAction
            from codeharness.const import RequirementTag
            from codeharness.roles.agent import Agent
            from codeharness.schema import Message
            from codeharness.sop.builder import get_template
            from codeharness.sop.templates import _EXT_TEMPLATES, SopTemplate, register_template

            class _Noop(BaseAction):
                async def run(self, msg: Message) -> Message:
                    return msg

            register_template(SopTemplate(
                name="s8graph_sop", desc="graph 门禁件",
                assemble=lambda llm: {"Solo": Agent(
                    {"name": "Solo", "profile": "p", "goal": "g"}, [_Noop(llm=llm)], llm,
                    watch={RequirementTag.USER_REQUIREMENT})},
                edges={RequirementTag.USER_REQUIREMENT: ["Solo"]}))
            try:
                xid = c.post("/api/sessions", json={"idea": "扩展图", "sop": "s8graph_sop"}).json()["id"]
                xmm = c.get(f"/api/sessions/{xid}/graph").json()["mermaid"]
                assert '["Solo"]' in xmm and '["PM"]' not in xmm, "sop 会话画成了 classic 表"
                assert f"|{RequirementTag.USER_REQUIREMENT}|" in xmm, "订阅边缺失"
                unknown = c.post("/api/sessions", json={"idea": "x", "sop": "nope"})
                assert unknown.status_code == 422, f"未知模板必须 create 即 422：{unknown.status_code}"
            finally:
                _EXT_TEMPLATES.pop("s8graph_sop", None)
        _ok("t4", f"/graph 三态各画各的装配（classic×{len(agents)} + dynamic + sop）、未知模板 create 即 422")
    finally:
        ss.SESSIONS_FILE = keep


def t5_workspace_file_response_shape():
    """预览分发的判据必须真的在响应里（真浏览器第十五处：ToolsPanel 读 rsp.ext 选
    markdown/image/.mmd 渲染器，而 /workspace/file 从来没回过 ext——三类预览从未命中）。
    断言打在**响应键**上，不给"路由在但形状错"留活路。"""
    import tempfile
    import server.sessions as ss
    keep, ss.SESSIONS_FILE = ss.SESSIONS_FILE, Path(tempfile.mkdtemp()) / "sessions.json"
    try:
        from fastapi.testclient import TestClient
        from server.app import create_app
        with TestClient(create_app()) as c:
            s = c.post("/api/sessions", json={"idea": "形状", "project_name": "s8shape"}).json()
            ws = Path(s["workspace"])
            (ws / "docs").mkdir(parents=True, exist_ok=True)
            (ws / "docs" / "design.md").write_text("# t\n```mermaid\ngraph TD;a-->b\n```\n", encoding="utf-8")
            (ws / "diagram.mmd").write_text("quadrantChart\n  x-axis low --> high\n", encoding="utf-8")
            (ws / "pic.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 32)
            for name, want in (("docs/design.md", ".md"), ("diagram.mmd", ".mmd"), ("pic.png", ".png")):
                r = c.get(f"/api/sessions/{s['id']}/workspace/file", params={"path": str(ws / name)})
                assert r.status_code == 200, r.text
                body = r.json()
                assert body.get("ext") == want, f"{name} 响应缺正确的 ext 键：{sorted(body)}"
            assert "content" not in c.get(f"/api/sessions/{s['id']}/workspace/file",
                                          params={"path": str(ws / "pic.png")}).json(), "图片不该回文本内容"
        _ok("t5", "/workspace/file 三类预览的判据键（ext）齐，image 免二进制乱码")
    finally:
        ss.SESSIONS_FILE = keep


def t6_trace_span_vocabulary():
    """N4 面板判据（第十六处预防）：/trace 必须回 spans 键；runner 记的 span 字段 ==
    前端 sessionTrace 消费的字段集——「路由在但形状错」t3 查不出（真浏览器第十五处同族洞）。"""
    import tempfile
    import server.sessions as ss
    keep, ss.SESSIONS_FILE = ss.SESSIONS_FILE, Path(tempfile.mkdtemp()) / "sessions.json"
    try:
        from fastapi.testclient import TestClient
        from server.app import create_app
        with TestClient(create_app()) as c:
            s = c.post("/api/sessions", json={"idea": "形状", "project_name": "s8trace"}).json()
            body = c.get(f"/api/sessions/{s['id']}/trace").json()
            assert isinstance(body.get("spans"), list), sorted(body)
        rec = re.search(r"trace\.record\(sid, \{([^}]*)\}",
                        (ROOT / "server" / "runner.py").read_text(encoding="utf-8")).group(1)
        be_keys = set(re.findall(r'"(\w+)":', rec))
        fe = re.search(r"sessionTrace[\s\S]*?spans: \{([^}]*)\}",
                       (FE / "api" / "client.ts").read_text(encoding="utf-8")).group(1)
        fe_keys = set(re.findall(r"(\w+):", fe))
        assert be_keys == fe_keys, f"span 字段漂移 runner={sorted(be_keys)} fe={sorted(fe_keys)}"
        _ok("t6", f"/trace 形状（spans 键）+ span 词汇表 runner==frontend（{len(be_keys)} 字段）")
    finally:
        ss.SESSIONS_FILE = keep


def t7_chat_target_from_assembly():
    """直聊目标必须出自真装配。前端那份硬编码名单写着 ProductManager/Engineer2/DataAnalyst，
    一个都不在装配里 → route 判 `recv in agents` 失败，追问静默蒸发而 API 回 200。"""
    import server.sessions as ss
    keep, ss.SESSIONS_FILE = ss.SESSIONS_FILE, Path(tempfile.mkdtemp()) / "sessions.json"
    try:
        from fastapi.testclient import TestClient
        from server.app import create_app
        from codeharness.team import classic_team, dynamic_assembly
        from codeharness.environment.team_graph import SOP
        classic = classic_team(None)
        dyn, dyn_route = dynamic_assembly(None)
        for name, ag, route in (("classic", classic, SOP), ("react", classic, SOP),
                                ("dynamic", dyn, dyn_route)):
            entry = next((r for r in route.get("UserRequirement") or [] if r in ag), "")
            assert entry, f"{name} 线的 USER_REQUIREMENT 目标不在装配 {sorted(ag)} 里 → 插话无人接"
        with TestClient(create_app()) as c:
            sid = c.post("/api/sessions", json={"idea": "x", "project_name": "s8chat"}).json()["id"]
            c.app.state.store.update(sid, roles=["PM"], entry_role="PM")
            bad = c.post(f"/api/sessions/{sid}/chat", json={"content": "hi", "send_to": "Ghost"})
            assert bad.status_code == 422 and "PM" in bad.json()["detail"], (bad.status_code, bad.text[:120])
            good = c.post(f"/api/sessions/{sid}/chat", json={"content": "hi", "send_to": "PM"})
            assert good.status_code == 409, f"真目标应过校验、停在「没在跑」：{good.status_code}"
        # 剥掉注释再查：讲这条历史 bug 的注释里必然出现那些旧名字
        src = re.sub(r"/\*.*?\*/|//[^\n]*", "",
                     (FE / "components" / "composer" / "ComposerCard.vue").read_text(encoding="utf-8"),
                     flags=re.S)
        stale = [n for n in ("ProductManager", "Engineer2", "DataAnalyst") if n in src]
        assert "roles" in src and not stale, f"ComposerCard 又硬编码直聊目标了：{stale}——必须渲染 Session.roles"
        _ok("t7", f"三线插话目标出自真装配（classic/react→PM、dynamic→Mike）；未知目标 /chat 即 422")
    finally:
        ss.SESSIONS_FILE = keep


def t8_tool_approval_gate():
    """批次36：工具审批。全程不打模型（云端额度只剩几块钱，判定与端点都能离线验）。

    断言六件事：判定表 fail-closed、三档矩阵、两张图真连了 gate、重放幂等、
    未批的动作**不执行**、端点值域。"""
    import asyncio
    import server.sessions as ss
    from codeharness.tools._approval import (ACTION_TIER, TIERS, TIER_RANK, TOOL_TIER,
                                             approval_id, gate_decide, needs_approval,
                                             preview, required_tier)
    # ① fail-closed：认不出的、越界的都往高档走
    assert required_tier("no_such_tool") == "full_access"
    assert required_tier("NoSuchAction", kind="action") == "full_access"
    assert required_tier("write_file", {"path": "../escape.md"}) == "full_access"
    assert required_tier("write_file", {"path": "a.md"}) == "workspace_write"
    assert required_tier("terminal_command", {"command": "dir"}) == "full_access"
    assert required_tier("read_file", {"path": "a.md"}) == "readonly"
    # ② 三档矩阵 9 格 + 认不出的会话档按最严
    for perm in TIERS:
        for req in TIERS:
            assert needs_approval(perm, req) is (TIER_RANK[req] > TIER_RANK[perm]), (perm, req)
    assert needs_approval("bogus", "readonly") is False
    # ③ 表覆盖全量：新加工具/Action 不登记就判红（不靠默认值兜，兜哪边都不对）
    from codeharness.tools import REGISTRY
    miss_tools = {t.name for t in REGISTRY} - set(TOOL_TIER)
    assert not miss_tools, f"这些工具没定档：{sorted(miss_tools)}"
    acts = {m.group(1) for p in (ROOT / "codeharness" / "actions").rglob("*.py")
            for m in re.finditer(r"class\s+([A-Za-z0-9_]+)\(([^)]*)\)",
                                 p.read_text(encoding="utf-8", errors="replace"))
            if "Action" in m.group(2)}
    miss_acts = acts - set(ACTION_TIER)
    assert not miss_acts, f"这些 Action 没定档：{sorted(miss_acts)}"

    # ④ 两张图真连了 gate（think→gate→act / route→gate→act）
    from codeharness.roles.agent import Agent
    from codeharness.roles.role_zero import RoleZero
    for cls in (Agent, RoleZero):
        nodes = cls({"name": "Solo", "profile": "p", "goal": "g"}, [], None).build().get_graph().nodes
        assert "gate" in nodes, f"{cls.__name__} 的图里没有 gate 节点——审批会被绕过"

    # ⑤ 重放幂等：同一 approval_id 答过就不再问
    class _IO:
        sid = "t8"

        def __init__(self):
            self.d, self.asked = {}, []

        def decision(self, aid):
            return self.d.get(aid)

        def request(self, item):
            self.asked.append(item["id"])
            return True

    io_ = _IO()
    verdict, item = gate_decide("terminal_command", {"command": "dir"}, node="gate",
                                io_=io_, permission="readonly")
    assert verdict is None and item["id"] == approval_id("t8", "gate", "terminal_command",
                                                         {"command": "dir"})
    io_.d[item["id"]] = "allowed-once"
    assert gate_decide("terminal_command", {"command": "dir"}, node="gate", io_=io_,
                       permission="readonly")[0] == "allowed"
    io_.d[item["id"]] = "rejected"      # 首个回执生效，后来的改不动（这里直接改台账模拟二次回执）
    assert gate_decide("terminal_command", {"command": "dir"}, node="gate", io_=io_,
                       permission="readonly")[0] == "rejected"

    # ⑤b 调用点不显式传档时，必须从 PERMISSION ContextVar 读——生产路径（两个 gate 与两处
    # _act）全都不传 permission，而本门禁原来每次都显式传，等于把这条路径留成盲区：
    # 「服务端装的档位永远不生效、full_access 的会话也被当只读拦」就是这么溜过去的（s7 t13 抓到）。
    from codeharness.runtime import PERMISSION as _PERM
    io_ctx = _IO()
    for tier, want in (("full_access", "allowed"), ("readonly", None)):
        tok = _PERM.set(tier)
        try:
            got = gate_decide("write_file", {"path": "a.md"}, node="gate", io_=io_ctx)
            assert got[0] == want, f"ContextVar={tier} 应该判 {want}，实际 {got[0]}"
        finally:
            _PERM.reset(tok)

    # ⑥ 未批/被拒的动作不执行：拿一个会举手的 stub Action 直接喂 _act
    from codeharness.base.action import Action as BaseAction
    from codeharness.schema import Message

    class _Hand(BaseAction):
        ran: bool = False          # Action 是 pydantic 模型，举手标记必须是字段

        async def run(self, msg: Message) -> Message:
            self.ran = True
            return msg

    stub = _Hand(llm=None)
    solo = Agent({"name": "Solo", "profile": "p", "goal": "g"}, {}, None)
    solo.actions = {"hand": stub}
    state = {"name": "Solo", "inbox": [Message(content="跑一下", role="user")], "memory": [],
             "action_cursor": 0, "chosen": "hand", "plan": [], "loops": 0, "output": []}
    from codeharness.runtime import APPROVAL_IO, PERMISSION
    tok_io, tok_p = APPROVAL_IO.set(_IO()), PERMISSION.set("readonly")
    try:
        out = asyncio.run(solo._act(state))
        assert not stub.ran and "[已拒绝]" in out["output"][-1].content, "没批的动作被执行了"
        # 同一个 state 再喂一次：载荷不变 → approval_id 不变，批过就该真执行
        name, args = solo._approval_key(state)
        aid = approval_id("t8", "gate", name, args)
        APPROVAL_IO.get().d[aid] = "allowed-once"
        asyncio.run(solo._act(state))
        assert stub.ran, "批过的动作没被执行"

        # ⑥b S4：批准粒度必须落在真实参数上。同一条触发消息、instruct_content 里 command
        #       不同的两笔 RunCode，此前算出同一个 approval_id（批一次=后面全放行）。
        def _st(cmd, wd):
            st = dict(state)
            st["inbox"] = [Message(content="跑一下", role="user",
                                   instruct_content={"command": cmd, "working_directory": wd})]
            return st

        st_a = _st(["python", "-m", "pytest", "test_a"], "repo/tests")
        st_b = _st(["python", "-m", "pytest", "test_b"], "repo/tests")
        n_a, a_a = solo._approval_key(st_a)
        n_b, a_b = solo._approval_key(st_b)
        id_a, id_b = approval_id("t8", "gate", n_a, a_a), approval_id("t8", "gate", n_b, a_b)
        assert id_a != id_b, "command 不同的两笔动作共用一个 approval_id——批一次等于批全部"
        assert "test_a" in preview(n_a, a_a) and "test_b" in preview(n_b, a_b), \
            f"审批卡上看不见将要执行的命令：{preview(n_a, a_a)} / {preview(n_b, a_b)}"
        assert "repo/tests" in preview(n_a, a_a), f"卡上缺 working_directory：{preview(n_a, a_a)}"
        solo.actions = {"hand": _Hand(llm=None)}        # 批 A 只放行 A：B 必须仍被拦
        APPROVAL_IO.get().d[id_a] = "allowed-once"
        out_a = asyncio.run(solo._act(st_a))            # _act 自己重算 id：与 gate 一致才会执行
        assert solo.actions["hand"].ran and "[已拒绝]" not in out_a["output"][-1].content, \
            "gate 与 act 按新载荷算出的 id 不一致（instruct 没被稳定复现）"
        solo.actions = {"hand": _Hand(llm=None)}
        out_b = asyncio.run(solo._act(st_b))
        assert not solo.actions["hand"].ran and "[已拒绝]" in out_b["output"][-1].content, \
            "批过 A 就把 B 也放行了——批准粒度仍停在消息级"
    finally:
        APPROVAL_IO.reset(tok_io)
        PERMISSION.reset(tok_p)

    # ⑧ 真图跑一遍挂起→重放→续跑（这一步才是「拆 gate 节点」这个选择要买的保险）
    from langgraph.checkpoint.memory import InMemorySaver
    from langgraph.graph import StateGraph, END
    from langgraph.types import Command
    from codeharness.roles.role_zero import RoleZero, RoleZeroState

    class _FakeTool:
        def __init__(self):
            self.calls = 0

        async def ainvoke(self, args):
            self.calls += 1
            return "ran"

    fake = _FakeTool()
    rz = RoleZero({"name": "Z", "profile": "p", "goal": "g"}, [], None)
    rz.tools = {"terminal_command": fake}
    sub = StateGraph(RoleZeroState)
    sub.add_node("gate", rz._gate_commands)
    sub.add_node("act", rz._act)
    sub.set_entry_point("gate")
    sub.add_edge("gate", "act")
    sub.add_edge("act", END)
    runner_graph = sub.compile(checkpointer=InMemorySaver())
    cfg = {"configurable": {"thread_id": "t8graph"}}
    state = {"task": "装依赖", "experience": "", "respond_language": "中文", "finished": False,
             "history": [{"thought": "先装依赖", "commands": [
                 {"command_name": "terminal_command", "args": {"command": "pip install x"}}]}]}
    # 换一条载荷不同的命令 → 新 aid → 重新挂起（这一次批「允许一次」，就该真执行）
    state2 = {**state, "history": [{"thought": "再装一次", "commands": [
        {"command_name": "terminal_command", "args": {"command": "pip install y"}}]}]}
    cfg2 = {"configurable": {"thread_id": "t8graph2"}}
    io3 = _IO()
    tok3 = APPROVAL_IO.set(io3)

    async def _drive():
        held = (await runner_graph.ainvoke(state, cfg)).get("__interrupt__")
        assert held, "gate 没挂起图"
        aid = held[0].value["approval"]["id"]
        io3.d[aid] = "rejected"
        res = (await runner_graph.ainvoke(Command(resume=aid), cfg))["history"][-1]["results"]
        held2 = (await runner_graph.ainvoke(state2, cfg2)).get("__interrupt__")
        aid2 = held2[0].value["approval"]["id"]
        io3.d[aid2] = "allowed-once"
        res2 = (await runner_graph.ainvoke(Command(resume=aid2), cfg2))["history"][-1]["results"]
        return aid, aid2, str(res), str(res2)

    try:
        aid, aid2, res, res2 = asyncio.run(_drive())
        assert io3.asked == [aid, aid2], io3.asked          # 各问过一次的只有这两条
        assert aid != aid2
        assert fake.calls == 1, f"被拒不执行 / 允许才执行没成立：calls={fake.calls}"
        assert "[已拒绝]" in res, res
        assert "ran" in res2, res2
    finally:
        APPROVAL_IO.reset(tok3)

    # ⑦ 端点值域
    keep, ss.SESSIONS_FILE = ss.SESSIONS_FILE, Path(tempfile.mkdtemp()) / "sessions.json"
    try:
        from fastapi.testclient import TestClient
        from server.app import create_app
        with TestClient(create_app()) as c:
            assert c.post("/api/sessions", json={"idea": "x", "permission": "bogus"}).status_code == 422
            sid = c.post("/api/sessions", json={"idea": "x", "project_name": "s8appr"}).json()["id"]
            assert c.patch(f"/api/sessions/{sid}", json={"permission": "nope"}).status_code == 422
            got = c.patch(f"/api/sessions/{sid}", json={"permission": "workspace_write"})
            assert got.status_code == 200 and got.json()["permission"] == "workspace_write", got.text[:120]
            assert c.get(f"/api/sessions/{sid}/approvals").json() == {"pending": [], "decided": []}
            assert c.post(f"/api/sessions/{sid}/approvals/nope/respond",
                          json={"outcome": "allowed-once"}).status_code == 404
            assert c.post(f"/api/sessions/{sid}/approvals/x/respond",
                          json={"outcome": "maybe"}).status_code == 422
            from platforms.approval_store import ApprovalStore, new_item
            st = ApprovalStore(sid)
            item = new_item("a1", "terminal_command", "dir", "要列目录", "full_access",
                            "workspace_write", "gate")
            assert st.request(item) and st.request(item) is False, "重放登记不该出第二条"
            assert [p["id"] for p in st.pending()] == ["a1"]
            r = c.post(f"/api/sessions/{sid}/approvals/a1/respond", json={"outcome": "allowed-once"})
            assert r.status_code == 200 and r.json()["outcome"] == "allowed-once", r.text[:120]
            assert st.pending() == [] and st.decision("a1") == "allowed-once"
            assert c.post(f"/api/sessions/{sid}/approvals/a1/respond",
                          json={"outcome": "rejected"}).json()["outcome"] == "allowed-once", "二次回执改了首个结论"
            assert [d["id"] for d in c.get(f"/api/sessions/{sid}/approvals").json()["decided"]] == ["a1"]
            c.delete(f"/api/sessions/{sid}")
        _ok("t8", "工具审批：判定表 fail-closed 且覆盖全量、两图真连 gate、重放幂等、"
                  "未批不执行、端点值域与首个回执生效")
    finally:
        ss.SESSIONS_FILE = keep


def t9_request_deadline():
    """F2：req() 的 fetch 必须真带 deadline（源码级钉住；15s 到点与按钮复位的读数在浏览器取，
    见 plan/governance-gate-stage4.md）。判据打在**信号是否进了 fetch 选项**，不是打在
    「文件里出现过 AbortSignal」——挪个位置就等于没接。"""
    fe = (FE / "api" / "client.ts").read_text(encoding="utf-8")
    req_body = re.search(r"async function req<.*?\n\}", fe, re.S).group(0)
    sig = re.search(r"signal:\s*AbortSignal\.timeout\((\w+|'\d+')\)", req_body)
    assert sig, "F2 回归：req() 的 fetch 选项里没有 AbortSignal.timeout——服务端挂起=前端永久 pending"
    ref = sig.group(1).strip("'")
    ms = int(ref) if ref.isdigit() else int(re.search(rf"const {ref} = (\d+)", fe).group(1))
    assert 5000 <= ms <= 60000, f"F2 超时取值不合理：{ms}ms（导入实测 0.71s 封顶，15s 是同步返回的余量）"
    assert "TimeoutError" in req_body, "F2：AbortSignal.timeout 抛的是 DOMException，没改名直接进 toast 用户看不懂"
    # 同类面：绕过 req() 的直接 fetch 也得带同一个 deadline，否则 F2 只修了主路、旁路照旧永久 pending
    for f in list(FE.rglob("*.ts")) + list(FE.rglob("*.vue")):
        if f.name == "client.ts":
            continue
        txt = f.read_text(encoding="utf-8")
        for m in re.finditer(r"\bfetch\(", txt):
            assert "AbortSignal.timeout" in txt[m.end():m.end() + 300], \
                f"F2 同类面：{f.relative_to(FE)} 绕过 req() 的 fetch 没带 deadline（挂起即永久 pending）"
    _ok("t9", f"F2 fetch deadline={ms}ms 且 TimeoutError 已翻中文；旁路 fetch 全部同带信号")


def t10_approval_rollback_realign():
    """F3：respondApproval 的 catch 必须「快照回滚 + 服务端重取」两句都在（顺序：先回滚后重取）。
    只回滚=把别处已决议的卡复活；只重取=loadApprovals 失败是静默的，断网时卡片再也回不来。"""
    st = (FE / "stores" / "sessions.ts").read_text(encoding="utf-8")
    respond = re.search(r"async respondApproval\(.*?\n    \},", st, re.S).group(0)
    catch = respond.split("catch", 1)[1]
    assert "this.approvals = keep" in catch, \
        "F3 回归：只剩服务端重取——loadApprovals 失败是静默的，断网时卡片再也回不来"
    assert "loadApprovals(" in catch, \
        "F3 回归：整快照回滚没有服务端真值对齐——别处已决议的卡会被复活"
    assert catch.index("this.approvals = keep") < catch.index("loadApprovals("), \
        "F3：顺序反了会让错误提示等到重取回来才弹（最长一个 timeout），先回滚再对齐"
    _ok("t10", "F3 catch 里回滚与服务端重取都在，且顺序是先回滚后对齐")


def _ok(n, msg):
    print(f"✅ {n}: {msg}")


def main():
    t1_blocktype_vocabulary()
    t2_envelope_and_kinds()
    t3_routes_exist()
    t4_graph_endpoint()
    t5_workspace_file_response_shape()
    t6_trace_span_vocabulary()
    t7_chat_target_from_assembly()
    t8_tool_approval_gate()
    t9_request_deadline()
    t10_approval_rollback_realign()
    print("\ns8_frontend_contract: 10/10 全绿")


if __name__ == "__main__":
    main()
