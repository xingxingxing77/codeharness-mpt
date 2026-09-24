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
  t11 B2：/events/history 的 before/limit 反向分页——两台 bus 同签名 + 窗口语义同判 + 路由传参与值域。
  t12 B1：断线横幅只有一个状态源（store.stream 三态，不从非响应式的 evtSource.readyState 派生）
     + 轮内 error 行走块管线且 ChatNode 有显式 'Error' 分支（'Error' 不是 BlockType，t1 查不到）。

跑法（PYTHONPATH 必须带 logs 那截，少了会撞本机 WMI 永久卡死，看着像代码挂死）：
  cd /e/Codeharness && PYTHONPATH=/e/Codeharness:/e/Codeharness/logs PYTHONIOENCODING=utf-8 \
    PYTHONDONTWRITEBYTECODE=1 F:/anaconda/python.exe -B tests/s8_frontend_contract.py
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

    # 契约 §1 抬头那两个数是手写的，已经错过两次（`8695b50` 修过一次「38→39」的口径，本笔又漂一格）。
    # 数字要么由脚本产出，要么就别写死——这里把它钉成实测值，加路由忘了改文档当场红。
    api = [r for r in create_app().routes if getattr(r, "path", "").startswith("/api")]
    rows = sum(len(set(r.methods) - {"HEAD", "OPTIONS"}) for r in api)
    paths = {r.path for r in api}
    doc = (ROOT / "docs" / "前端" / "接口契约.md").read_text(encoding="utf-8")
    head = re.search(r"^## 1\. 路由总表（(\d+) 行 / 去重后 (\d+) 条路径）", doc, re.M)
    assert head, "契约 §1 抬头的计数格式变了（本门禁的核对点跟着失效，去改这里而不是删断言）"
    assert int(head.group(1)) == rows and int(head.group(2)) == len(paths),         f"契约 §1 抬头写的是 {head.group(1)} 行 / {head.group(2)} 条路径，实测 {rows} 行 / {len(paths)} 条——"         "路由表或文档改了一边没改另一边"
    tbl_rows = len(re.findall(r"^\| (?:GET|POST|PATCH|PUT|DELETE) \| `/api", doc, re.M))
    assert tbl_rows == rows, f"§1 表里只有 {tbl_rows} 行路由，后端有 {rows} 行（少写的那条没人知道要先查契约）"

    _ok("t3", f"前端消费的 {len(consumed)} 条 /api 路径全部在路由表；"               f"契约 §1 的 {rows} 行 / {len(paths)} 条路径与表体行数三处对得上账")


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
    见 E:/codeharness-plan/governance-gate-stage4.md）。判据打在**信号是否进了 fetch 选项**，不是打在
    「文件里出现过 AbortSignal」——挪个位置就等于没接。"""
    fe = (FE / "api" / "client.ts").read_text(encoding="utf-8")
    req_body = re.search(r"async function req<.*?\n\}", fe, re.S).group(0)
    sig = re.search(r"signal:\s*AbortSignal\.timeout\((\w+|'\d+')\)", req_body)
    assert sig, "F2 回归：req() 的 fetch 选项里没有 AbortSignal.timeout——服务端挂起=前端永久 pending"
    ref = sig.group(1).strip("'")
    if ref == "timeoutMs":
        # B12：deadline 从常量变成 req() 的入参（上传那一档要更长）。这里必须跟着解到**默认值**上，
        # 否则「每条请求都有 deadline」这条判据会因为换了写法而空转。
        ref = re.search(r"timeoutMs\s*=\s*([A-Za-z_]\w*)", req_body).group(1)
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


def _redis_up():
    """探活（s7 同姿势）：db=15 才碰，6379 不通则返回 None 让 Redis 段明说跳过。"""
    import redis
    from codeharness.configs.settings import RedisConfig, settings
    try:
        cfg = RedisConfig(host=settings.redis.host, port=settings.redis.port, db=15)
        return cfg if redis.Redis.from_url(cfg.to_url()).ping() else None
    except Exception:
        return None


def t11_events_history_window():
    """B2：`/events/history` 反向分页（撑「加载更早」胶囊的那条后端面）。钉三个洞：
    ① **两个 bus 实现同签名**——`settings.platform.use_redis` 默认 True，生产走
       RedisEventBus；只给进程内那台加分页，Redis 模式下就是 TypeError（批次36
       「每个用例都显式传 permission」留盲区同族，门禁必须按生产调用形状覆盖两条路）。
    ② 窗口语义（两台共用一份断言）：before 是**开区间**上界、limit 取靠近 before 的
       **尾部**、始终升序、limit=0 保持老「全给」语义、翻到头 = 短页后空页（客户端停止条件）。
       外加判据 B2 第二条的正向读数：45 块（>40）的流按 20 一块往回翻，两页拼回全 45 条
       不重不漏——不拿「逻辑上应该能翻」充数。
    ③ 路由真把 before/limit 传下去，且 limit 值域在入口夹住。"""
    import inspect
    from platforms.event_store import RedisEventBus, STREAM
    from server.events import SessionEventBus

    sig_mem, sig_rds = inspect.signature(SessionEventBus.history), inspect.signature(RedisEventBus.history)
    assert list(sig_mem.parameters) == list(sig_rds.parameters), \
        f"两台 bus 的 history 参数表漂移 进程内={list(sig_mem.parameters)} redis={list(sig_rds.parameters)}"
    assert [p.default for p in sig_mem.parameters.values()] == \
        [p.default for p in sig_rds.parameters.values()], "默认值漂移：limit=0 的老语义在其中一台不成立"

    SID, SID45 = "win", "win45"

    def _seed(bus):
        """两台 bus 喂同一份剧本：12 条窗口样本 + 45 条「>40 块」长会话样本。"""
        for i in range(12):
            bus.publish(SID, kind="report", value=f"m{i}", uuid=f"u{i}")
        for i in range(45):
            bus.publish(SID45, kind="report", block="Docs", value=f"b{i}", uuid=f"v{i}")

    def _check(bus, label):
        cur = [e.cursor for e in bus.history(SID)]
        assert [e.value for e in bus.history(SID)] == [f"m{i}" for i in range(12)], label
        assert cur == sorted(cur) and all(len(x) == len(cur[0]) for x in cur), f"{label} 游标非定宽升序"
        # 往回翻：不含 before 本身，取最靠近它的 3 条，且回给升序
        p1 = bus.history(SID, before=cur[7], limit=3)
        assert [e.value for e in p1] == ["m4", "m5", "m6"], (label, [e.value for e in p1])
        # 翻页协议：下一页 before = 本页首条 cursor ⇒ 与上一页零重叠、零缺口
        p2 = bus.history(SID, before=p1[0].cursor, limit=3)
        assert [e.value for e in p2] == ["m1", "m2", "m3"], (label, [e.value for e in p2])
        p3 = bus.history(SID, before=p2[0].cursor, limit=3)      # 短页=到底前最后一屏
        assert [e.value for e in p3] == ["m0"], (label, [e.value for e in p3])
        assert bus.history(SID, before=p3[0].cursor, limit=3) == [], f"{label} 到底了还给数据"
        assert [e.value for e in p3 + p2 + p1] == [f"m{i}" for i in range(7)], \
            f"{label} 反向翻页拼接与全量不符（重叠或漏条）"
        # 上下界叠加；窗口尾部不许越过 after
        assert [e.value for e in bus.history(SID, after=cur[2], before=cur[8])] == \
            [f"m{i}" for i in range(3, 8)], label
        assert [e.value for e in bus.history(SID, after=cur[4], before=cur[9], limit=3)] == \
            ["m6", "m7", "m8"], f"{label} 尾部窗口越过了 after 下界"
        # 老语义零回归：只给 after 仍是「之后的全部」；什么都不给仍是全量
        assert [e.value for e in bus.history(SID, after=cur[5])] == [f"m{i}" for i in range(6, 12)], label
        assert len(bus.history(SID, after=cur[5], limit=0)) == 6, f"{label} limit=0 应视为不限"
        # **limit 始终取窗口尾部**：不给 before 就是「最新一屏」——这是首屏只吞一屏的正解。
        # 若退化成取头部，打开会话看到的是最老几条，且「加载更早」永远没有下一页。
        assert [e.value for e in bus.history(SID, limit=3)] == ["m9", "m10", "m11"], \
            f"{label} 首屏取的不是最新一屏"
        assert [e.value for e in bus.history(SID, after=cur[2], limit=2)] == ["m10", "m11"], \
            f"{label} after+limit 取成了头部"
        # 「>40 块的会话能翻页」（判据 B2 第二条的正向读数）：按界面真实走法——
        # 首屏渲染尾部 20 块，胶囊用**已渲染最老一条**的 cursor 往回按 20 一块逐页取。
        big = bus.history(SID45)
        assert len(big) == 45, (label, len(big))
        rendered, pages, oldest = big[-20:], [], big[-20].cursor
        while True:
            grp = bus.history(SID45, before=oldest, limit=20)
            if not grp:
                break
            pages.append(grp)
            oldest = grp[0].cursor
            assert len(pages) < 5, f"{label} 翻页不到头（停止条件失效）"
        assert [len(p) for p in pages] == [20, 5], (label, [len(p) for p in pages])   # 满页 + 短页
        assert [e.value for e in pages[1] + pages[0] + rendered] == [f"b{i}" for i in range(45)], \
            f"{label} 45 块会话翻页拼不回全量"

    _seed(b := SessionEventBus())
    _check(b, "进程内")

    cfg = _redis_up()
    if cfg is None:
        print("⚠️ t11: db=15 Redis 不可达——窗口语义只验了进程内那台（Redis 段跳过，"
              "门禁不挂在外部服务上）")
    else:
        import asyncio

        async def _redis_window():
            bus = RedisEventBus(cfg)
            bus.start()                             # flusher 要在运行中的 loop 里建
            try:
                _seed(bus)
                await bus.flush_now()               # seq/cursor 由 XADD 承接，未刷完读不到
                _check(bus, "redis")
            finally:
                await bus.aclose()

        asyncio.run(_redis_window())
        import redis
        r = redis.Redis.from_url(cfg.to_url())
        r.delete(STREAM.format(SID), STREAM.format(SID45))       # db15 不留测试流

    import server.sessions as ss
    keep, ss.SESSIONS_FILE = ss.SESSIONS_FILE, Path(tempfile.mkdtemp()) / "sessions.json"
    try:
        import time
        from fastapi.testclient import TestClient
        from server.app import create_app
        with TestClient(create_app()) as c:
            sid = c.post("/api/sessions", json={"idea": "窗口", "project_name": "s8win"}).json()["id"]
            bus = c.app.state.bus
            for i in range(6):
                bus.publish(sid, kind="log", value=f"h{i}")
            for _ in range(60):                     # redis 模式 seq/cursor 由 flusher 承接，等它
                evs = c.get(f"/api/sessions/{sid}/events/history").json()["events"]
                if len(evs) >= 7:
                    break
                time.sleep(0.05)
            assert len(evs) == 7, f"路由回放只拿到 {len(evs)} 条（created 状态事件 + 6 条 log）"
            url = f"/api/sessions/{sid}/events/history"
            assert [e["value"] for e in evs[1:]] == [f"h{i}" for i in range(6)], evs
            # 老写法（只有 after）行为一字不变
            assert [e["cursor"] for e in c.get(url, params={"after": evs[0]["cursor"]}).json()["events"]] == \
                [e["cursor"] for e in evs[1:]], "after 老语义被改坏"
            # 新参真的进到了 bus：before=末条 + limit=2 → 倒数第 2、3 条（升序）
            page = c.get(url, params={"before": evs[-1]["cursor"], "limit": 2}).json()["events"]
            assert [e["cursor"] for e in page] == [e["cursor"] for e in evs[-3:-1]], page
            # has_more：服务端多取一条判「前面还有没有」，那条不回给前端（前端不必为显隐再打一次）
            r1 = c.get(url, params={"limit": 3}).json()
            assert [e["cursor"] for e in r1["events"]] == [e["cursor"] for e in evs[-3:]], \
                f"首屏 limit=3 取的不是最新三条：{r1}"
            assert r1["has_more"] is True, r1
            r2 = c.get(url, params={"before": r1["events"][0]["cursor"], "limit": 20}).json()
            assert r2["has_more"] is False and \
                [e["cursor"] for e in r2["events"]] == [e["cursor"] for e in evs[:4]], \
                f"翻到头还报 has_more 或多取那条漏进响应了：{r2}"
            assert c.get(url, params={"limit": 0}).json()["has_more"] is False, "不限即全给，不该报还有更早"
            assert c.get(url, params={"before": evs[0]["cursor"], "limit": 5}).json()["events"] == [], \
                "第一条之前还有数据？反向翻页的到头条件不成立"
            # 值域在入口夹住（查询参数是不可信输入，负数/超大不能靠下游兜）
            assert c.get(url, params={"limit": -1}).status_code == 422
            assert c.get(url, params={"limit": 1001}).status_code == 422
            c.delete(f"/api/sessions/{sid}")
        _ok("t11", "B2 反向分页：两台 bus 同签名 + 窗口语义逐条同判（before 开区间/取尾/升序/"
                   "短页后空页）+ 路由传参落地、limit 值域 422")
    finally:
        ss.SESSIONS_FILE = keep


def t12_offline_banner_and_turn_error_row():
    """B1：断线横幅 + 轮内 error 红点行（施工5 档三那两格，纯前端）。钉三个洞：
    ① 横幅只有一个状态源：store.stream 三态（idle/open/down），且**只有 open 过之后报错**
       才进 down。两件事都不能省：拿 `!connected` 判会在首屏握手期闪假横幅；而从
       `evtSource.readyState` 派生更糟——EventSource 实例不是 plain object，Vue 不 proxy 它，
       readyState 变了 computed 根本不重算，本轮实测那样写横幅在活后端下也常驻不消。
    ② error 事件必须进**块管线**（type='Error' + closed=true + 入 blockOrder）。另开一份
       errors 数组就是第二个游标（SSE seq 精度丢事件那条洞同族）；closed 不给真是「末轮
       永不出尾行」的形状。
    ③ ChatNode 必须有 'Error' 显式分支——'Error' 不是 BlockType，t1 查不到漏掉的分支，
       漏了就静默降级成灰色折叠行（t1 想防的那类洞换个入口又回来了）。"""
    app = (FE / "App.vue").read_text(encoding="utf-8")
    st = (FE / "stores" / "sessions.ts").read_text(encoding="utf-8")
    node = (FE / "components" / "conversation" / "ChatNode.vue").read_text(encoding="utf-8")

    banner = re.search(r'''<div[^>]*v-if="store\.stream === 'down'"[^>]*>([\s\S]*?)</div>''', app)
    assert banner, "B1 回归：App.vue 里没有以 store.stream==='down' 为条件的横幅节点"
    assert "连接已断开" in banner.group(1), f"B1：横幅只剩空条，文案丢了：{banner.group(1)!r}"
    assert "position: fixed" in app.split(".connBanner", 1)[-1][:400], \
        "B1 回归：.connBanner 不再是 fixed 顶条（照参考项目 ConnectionBanner.module.css）"
    assert "connected" not in app and "readyState" not in app, \
        "B1：横幅别再引第二个状态源（connected 布尔 / readyState 派生都会与 stream 漂移）"
    for w in ("this.stream = 'idle'", "this.stream = 'open'", "this.stream = 'down'"):
        assert w in st, f"B1 回归：store.stream 少了 {w} 这一笔（三态缺一态=横幅要么闪要么常驻）"
    assert "'idle' as 'idle' | 'open' | 'down'" in st, "B1：stream 的三值联合类型没了"
    assert "connected" not in st, "B1：connected 与 stream 是同一事实的两份记账，留一份"
    # 注释里出现 readyState 是讲解，所以禁的是**代码形状**，不是这个词
    assert "reconnecting" not in st and "readyState !=" not in st and "readyState ===" not in st, \
        "B1：横幅别改回从 evtSource.readyState 派生——EventSource 实例非响应式，实测横幅常驻不消"

    err = re.search(r"=== 'error'\) \{(.*?)\n      \} else if", st, re.S)
    assert err, "B1：applyEvent 的 error 分支不见了"
    body = err.group(1)
    assert "b.type = 'Error'" in body and "b.closed = true" in body \
        and "this.blockOrder.push(key)" in body, \
        f"B1 回归：error 不再走块管线（另开数组=第二个游标；closed 缺=末轮不出尾行）\n{body}"
    assert "this.logs.push(`[error]" in body, "B1：右栏台账那份 error 行被删了（横幅/红点不替台账）"
    assert "type === 'Error'" in node, \
        "B1 回归：ChatNode 少了 'Error' 分支——它会静默降级成灰色折叠行，而 t1 查不到"
    css = node.split(".errRow", 1)[-1]
    for prop in ("grid-template-columns: 10px minmax(0, 1fr)", "font-size: 13px", "line-height: 20px"):
        assert prop in css, f"B1 回归：.errRow 源值 {prop} 丢了"
    _ok("t12", "B1：横幅唯一状态源=store.stream 三态（idle/open/down，readyState 派生已禁）+ "
               "fixed 顶条源值 + error 走块管线（type/closed/入序/台账并存）+ ChatNode 显式分支与 .errRow 源值")


def t13_size_cap_and_truncation_reach_the_user():
    """F-E（治理 §3 第 2 条）：阶段三 B10/B7 只落了服务端，这两个信号必须在**两侧同判**下才算接上。

    ① 后端仍回这两个信号：`/workspace/file` 超上限回 413（且是先判大小、**不读字节**），
       import 的返回体带 `truncated`；
    ② 状态码穿得过 `client.ts`：抛出的 Error 上必须挂 `status`——不然调用方只能去猜文案；
    ③ 前端按**状态码**分流（`=== 413`），并按 `truncated` 换 toast 语气。
       文案由服务端给（「超过预览上限」那句里带真实大小与阈值），前端**不重抄数字**——
       两处各写一份 5MB 就是等它改上限那天漂移。
    ④ 超时文案里的秒数必须是**本次那一发的档位**（`timeoutMs`）：上传走 120s 长档，写死常量的话界面会说「超时（15s 无响应）」——假数字比不报更容易把人带错方向。
    少任何一边这格都该红：只钉前端=后端哪天不回 truncated 也没人知道；只钉后端=界面照样静默。"""
    ws = (ROOT / "server" / "api" / "workspace.py").read_text(encoding="utf-8")
    act = (ROOT / "codeharness" / "actions" / "import_repo.py").read_text(encoding="utf-8")
    fe = (FE / "api" / "client.ts").read_text(encoding="utf-8")
    dp = (FE / "components" / "DetailsPanel.vue").read_text(encoding="utf-8")

    cap = re.search(r"if size > MAX_PREVIEW_BYTES:\s*(?:#[^\n]*\n\s*)*raise HTTPException\(413", ws)
    assert cap, "B10 回归：/workspace/file 的大小上限分支没了（超上限会被整份读进内存）"
    assert '"truncated": truncated' in act and "MAX_IMPORT_NODES" in act, \
        "B7 回归：import_repo 不再回 truncated，或扫描上限没了"

    req_body = re.search(r"async function req<.*?\n\}", fe, re.S).group(0)
    assert re.search(r"err\.status = rsp\.status", req_body), \
        "F-E 回归：req() 抛的 Error 没挂 status——调用方只能拿 message 猜状态码"

    # 锚点必须落在 openFile 这个函数体上：C11 之后文件里有了第二处 `=== 413`（回放详情也按码分流），
    # 拿全文第一次出现去切会打错地方（本仓清单：反向验证与形状判据的锚点要唯一）。
    fn = re.search(r"async function openFile\(.*?\n\}", dp, re.S)
    assert fn, "F-E 回归：openFile 没了"
    assert "=== 413" in fn.group(0), "F-E 回归：DetailsPanel 不再按 413 分流（点开大文件只剩一条会消失的报错 toast）"
    body = fn.group(0).split("=== 413", 1)[-1].split("else {", 1)[0]
    assert "preview.value" in body and "'warn'" in body, \
        f"F-E：413 分支要在预览面板里留话 + 用 warn 语气，实际只有\n{body.strip()[:160]}"
    assert "5MB" not in dp and "5 MB" not in dp, "F-E：前端把预览阈值抄成了第二份数字（会漂）"

    imp = re.search(r"const capped = (.*?)\n(.*?)await load\(\)", dp, re.S)
    assert imp and "Boolean(r.truncated)" in imp.group(1), \
        "F-E 回归：导入结果不再看 truncated（撞上限的导入与完整导入长得一模一样）"
    assert "'warn'" in imp.group(2) and "截断" in imp.group(2), \
        f"F-E：撞上限时 toast 语气/文案没到位\n{imp.group(2).strip()[:200]}"

    # ④ 超时秒数取本次档位（ADR-20260922-06）
    to_msg = re.search(r"name === 'TimeoutError'\)\s*\{[\s\S]{0,260}?throw new Error\(`([^`]*)`", fe)
    assert to_msg and 'timeoutMs' in to_msg.group(1), \
        f"超时文案没取本次档位（{to_msg.group(1)[:90] if to_msg else '没抓到那条 throw'}）"
    assert 'REQUEST_TIMEOUT_MS / 1000' not in fe, \
        'C16 回归：client.ts 又拿常量拼超时秒数——120s 档的上传会报出「15s 无响应」这种假数字'
    _ok("t13", "F-E：413 与 truncated 两侧同判（后端仍回信号 → Error 挂 status → "
               "前端按状态码分流 + 预览面板留话 + 上限处截断换 warn），阈值不在前端重抄；"
               "外加超时文案必须取本次档位（假秒数比不报更坏）")


def t14_checkpoint_replay_surface():
    """C11 回放面的**两侧同判**（与 t6/t13 同一手法：只钉一边就等于没钉）。

    ① 后端两个路由在（列表 + 单份详情）；② 前端两个方法在，且路径与后端一致
       ——t3 钉的是「前端消费的后端有」，这一格补反向：**后端新开的只读面前端有消费者**，
       否则路由就是死面（C3 那条 UploadKB 的教训形状）；
    ③ 列表页项字段集 runner == 前端声明，**不许含 values/state**（整份黑板不进列表）；
    ④ 界面有 回放 tab、点行取详情、413 按**状态码**分流（F-E 立的那条规矩）、
       换会话清分页游标（不清会把 A 场的超步显示在 B 场名下）。"""
    api_src = (ROOT / "server" / "api" / "sessions.py").read_text(encoding="utf-8")
    runner_src = (ROOT / "server" / "runner.py").read_text(encoding="utf-8")
    fe = (FE / "api" / "client.ts").read_text(encoding="utf-8")
    dp = (FE / "components" / "DetailsPanel.vue").read_text(encoding="utf-8")

    assert '@router.get("/{sid}/checkpoints")' in api_src, "C11 回归：超步列表路由没了"
    assert '@router.get("/{sid}/checkpoints/{checkpoint_id}")' in api_src, "C11 回归：单份超步详情路由没了"
    for call, path in (("api.checkpoints(", "/checkpoints?before="),
                       ("api.checkpointState(", "/checkpoints/${")):
        assert call in dp, f"C11 回归：界面不再消费 {call}——后端那条只读路由成了死面"
        assert path in fe, f"C11：client.ts 里 {call} 的路径形状变了（{path}），与后端路由不同判"

    page_body = re.search(r"out\.append\(\{(.*?)\}\)", runner_src, re.S).group(1)
    be_keys = set(re.findall(r'"(\w+)":', page_body))
    fe_keys = set(re.findall(r"(\w+):", re.search(
        r"checkpoints: \{ (.*?)\}\[\]", fe, re.S).group(1)))
    assert be_keys == fe_keys, f"超步摘要字段漂移 runner={sorted(be_keys)} fe={sorted(fe_keys)}"
    assert not (be_keys & {"values", "state"}), f"列表页混进了整份黑板：{sorted(be_keys)}"
    assert "before=before" in runner_src and "limit=limit + 1" in runner_src, \
        "C11 回归：翻页不再是 before 关键字（塞进 config 是闭区间，会重复吐边界那一条）"

    assert "{ key: 'replay', label: '回放' }" in dp, "C11 回归：右栏没有 回放 tab"
    assert "openCp(cp)" in dp, "C11 回归：超步行不再点开单份 state"
    assert "err.status === 413" in dp, "C11 回归：413 又回到猜文案（应按状态码分流，见 F-E）"
    assert re.search(r"watch\(\(\) => store\.currentId, \(\) => \{\s*Object\.assign\(replay", dp), \
        "C11 回归：换会话没清回放游标——会把上一场的超步显示在当前场名下"

    # 样式必须有参照系出处：E:\deepseek-harness 是唯一前端参照系，参照系没有的界面
    # 也要按它的形状设计（用户 2026-09-21 定的口径）。本格钉的是**具体源值**，不是"用了 token"：
    # ui-trajectory/src/client/TrajectoryTable.module.css 的 `.table th`/`.table td`/
    # `[data-selected='true']`/`.historyLoadButton`。
    css = dp.split("<style", 1)[-1]
    tbl = re.search(r"\.cpTable th,?\s*\.cpTable td \{([^}]*)\}", css)
    assert tbl and "height: 30px" in tbl.group(1) and "padding: 0 8px" in tbl.group(1), \
        "C11 回归：回放表格不再是参照系的 30px 行 / 0 8px 内距"
    assert "--dsw-alias-interactive-bg-active" in css, \
        "C11 回归：选中行用的不是参照系表格的 selected token（interactive-bg-active；hover 才是 -hover）"
    assert "border-bottom: 1px solid var(--dsw-alias-border-l1)" in css, \
        "C11 回归：行线不是参照系表格的 border-l1（面板/条线才用 l2）"
    assert re.search(r"\.cpTable th \{[^}]*position: sticky", css), "C11 回归：表头不跟随滚动"
    more = re.search(r"\.cpMore \{([^}]*)\}", css)
    assert more and "height: 29px" in more.group(1) and "border: none" in more.group(1), \
        "C11 回归：「加载更早」不是参照系表格里的整行按钮 `.historyLoadButton`（29px、无框）"
    assert "14px" not in (more.group(0) if more else "") and "999px" not in css, \
        "C11：表格里的加载开关用了对话流那颗圆角按钮（`.older` 的 14px / 自造的 999px），选错族"
    assert 'class="code stateBox"' in dp, \
        "C11 回归：展开的 state 没复用同文件的 `.code`（参照系右栏代码块 pad16/r12/13-22）"
    # 界面词汇跟参照系走：它 UI 层没有 superstep/checkpoint 这两个词，用的是 trajectory/turn/request/step。
    # 判据只能钉"看得见的文案"（模板里的属性名如 `cp.checkpoint_id` 是代码不是文案），
    # 所以这里取三条已知用户可见串 + 列头，而不是全文扫词——覆盖面有限这件事写在注释里，别当全防。
    tpl = re.sub(r"<!--[\s\S]*?-->", "", dp.split("<script")[0])     # 模板区，剥掉 HTML 注释
    for copy in ("这个会话还没有步", "加载更早", "看第 ${cp.step} 步的状态", "<th class=\"num\">步</th>"):
        assert copy in tpl, f"C11 回归：界面文案「{copy}」没了（参照系词汇是 step/步，别改回 superstep/checkpoint）"
    assert "superstep" not in tpl and "Checkpoint" not in tpl, \
        "C11：模板正文里出现了 superstep/Checkpoint 字样——界面层该说「步」"
    _ok("t14", "C11 回放面两侧同判：两路由+两消费者+摘要字段集 runner==fe（无 values）+ before 走关键字"
               "（开区间）+ 413 按码分流 + 换场清游标 + 样式钉参照系源值（30px 行/selected token/"
               "l1 行线/sticky 表头/29px 整行加载按钮/复用 .code），界面只说「步」")


def _ok(n, msg):
    print(f"✅ {n}: {msg}")


def t15_kb_upload_entry():
    """B12（C3 那条链的界面入口）：后端在 `c21b881` 就通了，缺的是「用户点得着」。三侧同判。

    ① 后端门口三判还在（basename / 门口判据与拒因文案都 import 自摄取件 / 两个上限），响应四字段齐；
       C26 之后这一格钉的是「端点里不许出现第二份后缀白名单，也不许自己拼那句拒因」；
    ② `client.ts` 走 multipart——**手设 Content-Type 会把 boundary 打掉**，所以那条分支必须
       是「有 body 且不是 FormData 才设 JSON」；并且这条路由进了前端消费集（t3 自动覆盖形状）；
    ③ `DetailsPanel` 把 `errors[]` 与成功数**一起**说出去（部分成功是合法结局：只报成功数=悄悄
       吞掉坏文件，只报错=进去一半还说成一笔没成），且传完刷新文件树（原件落 `kb/`，看得见才算传上）。
    另钉一条「不重抄」：前端不许出现第二份后缀白名单或 20MB/20 个的数字——同 F-E 的阈值口径。
    上传那一档 deadline 也钉在这里：它比默认档长是**语义**（摄取在请求内跑），不是随手调大。"""
    ws = (ROOT / "server" / "api" / "workspace.py").read_text(encoding="utf-8")
    act = (ROOT / "codeharness" / "actions" / "upload_kb.py").read_text(encoding="utf-8")
    fe = (FE / "api" / "client.ts").read_text(encoding="utf-8")
    dp = (FE / "components" / "DetailsPanel.vue").read_text(encoding="utf-8")

    # ① 后端
    assert 'Path(f.filename or "").name' in ws and "is_relative_to(root)" in ws, \
        "C3 回归：upload_kb 的文件名判据没了（`../../` 会穿出 kb/ 目录）"
    assert "from codeharness.actions.upload_kb import door_refusal" in ws \
        and "door_refusal(target.suffix" in ws, \
        "C3/C26 回归：端点不再从摄取件 import 那一份门口判据（白名单与拒因文案都应当只住一处）"
    assert '".docx"' not in ws and '".pdf"' not in ws, \
        "端点里出现了第二份知识库后缀白名单——C26 之后连「不收 / 本机读不了」那两句也只许住在摄取件里"
    assert re.search(r"SUPPORTED\s*[:=]", act), "摄取件里的 SUPPORTED 没了"
    for field in ("uploaded_count", "chunk_count", "errors", "written"):
        assert field in ws, f"C3 回归：响应体不再带 {field}（前端按这四个字段显示）"

    # ② client.ts
    req_body = re.search(r"async function req<.*?\n\}", fe, re.S).group(0)
    assert "body instanceof FormData" in req_body and "if (body && !form)" in req_body, \
        "B12 回归：req() 对 FormData 也会设 Content-Type——boundary 会被打掉，后端收不到文件"
    up = re.search(r"uploadKb: \(.*?\n  \},", fe, re.S)
    assert up and "/workspace/upload_kb" in up.group(0), "B12 回归：client.ts 不再消费 upload_kb"
    assert "fd.append('files', f)" in up.group(0), "B12：字段名必须叫 files（后端签名是 files: list[UploadFile]）"
    default_ms = int(re.search(r"const REQUEST_TIMEOUT_MS = (\d+)", fe).group(1))
    up_ms = int(re.search(r"const KB_UPLOAD_TIMEOUT_MS = (\d+)", fe).group(1))
    assert up_ms > default_ms, f"上传档({up_ms})不该短于/等于默认档({default_ms})：摄取在请求内跑"
    assert re.search(r"KB_UPLOAD_TIMEOUT_MS\)", up.group(0)), "B12：uploadKb 没把长档传给 req()"

    # ③ DetailsPanel
    fn = re.search(r"async function doUploadKb\(.*?\n\}", dp, re.S)
    assert fn, "B12 回归：DetailsPanel 里没有 doUploadKb（后端那条链又没有入口了）"
    body = fn.group(0)
    assert "r.errors" in body and "errs.length" in body and "head" in body, \
        "B12 回归：上传结果不再同时报「摄入了多少」与「被拒哪几条」（部分成功被说成全成/全败）"
    assert "await load()" in body, "B12 回归：传完不刷新文件树（原件就在 kb/ 里，看不见等于没传）"
    # 「摄取中…」这句只在摄取期间成立：响应一落地就该换回「加入知识库」。此前它一直挂到
    # `load()` 返回，而 load() 并行等 `/graph`（2026-09-24 隔离实例 8794 实测 2.2~2.3s，
    # 同形状的 `/workspace/files` 只有 4ms）——按钮替那次图刷新背了 4 秒，用户看到「还在传」。
    assert body.index("kbBusy.value = false") < body.index("await load()"), \
        "B12 回归：kbBusy 只在 finally 里清，「摄取中…」被 load() 里那发 /graph 拖长了两秒"
    assert "kbInput.value.value = ''" in body, "B12 回归：不清 input.value，同名文件第二次选不中"
    # C16 的最后一米：后端非 2xx（含向量服务不可达那格 503）时，界面必须把 detail 原文显示出来。
    # 浏览器导航本轮被自动模式挡，所以这一格只能钉在源码判据上——它钉的是「原文有没有走到那行字」，
    # 不是「渲染好不好看」，后者仍属 B12/F-E 取过的活体读数。
    assert re.search(r"catch\s*\(e\)[\s\S]{0,80}kbMsg\.value = \{ text: \(e as Error\)\.message, err: true", body), \
        "C16 回归：doUploadKb 的 catch 不再把 `e.message` 原样显示（后端 detail 会在最后一米被换成固定文案）"
    assert "white-space: pre-line" in dp.split("<style", 1)[-1], \
        "C16 回归：.kbMsg 不再是 pre-line——后端 detail 是三行文本（哪台 / 原件在 kb/ 没切片 / 该查什么），" \
        "不换行就糊成一条看不完的横条"
    assert 'accept=' not in dp and ".docx" not in dp and "20MB" not in dp, \
        "B12：前端把后缀白名单或大小上限抄成了第二份（会漂），拒因照后端原文显示就够"

    # ---- C30 下架单份知识库文档：三侧同判（与 B12 同形状，因为它是同一条链的反向动作） ----
    assert '@router.delete("/{sid}/workspace/kb_doc")' in ws, \
        "C30 回归：workspace.py 里没有 kb_doc 的删除路由（界面那个动作位就又成了假控件）"
    assert 'Path(name).name != name' in ws, \
        "C30 回归：source 的 basename 判据没了——`../` 这类串会进过滤器"
    assert re.search(r'_filters\("kb", uid, project=workspace\.name, source=name\)', ws), \
        "C30 回归：过滤器不再由会话推出来的 doc_type/user_id/project 打头——source 一旦能拼过滤器就能跨租户"
    assert re.search(r'delete_scope\(doc_type="kb", user_id=uid, project=workspace\.name, source=name\)', ws), \
        "C30 回归：删那一步与数那一步的 scope 不一致（数的是一套、删的是另一套）"
    assert 'HTTPException(404, f"知识库里没有' in ws, \
        "C30 回归：不存在的 source 静默回 ok 了——「删了 0 条」与「删掉了」在界面上长得一样"
    rk = re.search(r"removeKbDoc:[\s\S]{0,320}", fe)
    assert rk and "/workspace/kb_doc" in rk.group(0), "C30 回归：client.ts 不再消费 kb_doc"
    assert "URLSearchParams" in rk.group(0), \
        "C30：source 没走 URLSearchParams——文件名里的 `&`/空格会把这个 query 拼坏"
    down = re.search(r"async function doRemoveKb\(.*?\n\}", dp, re.S)
    assert down, "C30 回归：DetailsPanel 里没有 doRemoveKb（后端那条路由又没有入口了）"
    body = down.group(0)
    assert "api.removeKbDoc" in body and "await load()" in body, \
        "C30：下架没调 removeKbDoc / 下架后不刷文件树"
    assert "if (!store.currentId || !n || kbDownBusy.value) return" in body, \
        "C30：确认之前就发请求了——`kbDown` 为空必须直接 return（删除不可逆，先确认再发是这一格的要点）"
    assert re.search(r'v-if="kbDown"[\s\S]{0,600}?取消', dp), \
        "C30：确认条上没有「取消」（删除不可逆，用户得有退路）"
    assert "isKbDoc" in dp and "从知识库下架" in dp, \
        "C30：文件树上没有给 `kb/` 那份文件的「下架」动作位"
    _ok("t15", "B12：upload_kb 三侧同判（后端门口三判+四字段 → client.ts multipart+长档 → "
               "界面 errors 与成功数一起说 + 传完刷新树），白名单不在前端重抄；"
               "C16 追加：非 2xx 的 detail 必须原样走到 .kbMsg（catch 用 e.message + pre-line 分行）；"
               "C30 追加：kb_doc 下架三侧同判（路由 + basename/会话 scope/404 → client.ts 转义 query → "
               "界面先确认再发且确认条带取消）")


def t16_max_tokens_notice():
    """B8：`finish_reason=='length'` 是一条**静默**信号——半截回答看不出自己是半截
    （`.env:13` 那句 LengthFinishReasonError 只在空 content 上抛，非空就默默返回半截）。
    后端必须把它说出去，前端才有东西可渲染。

    两半各自取证，都不打合成事件（A4 那轮 s17 拿 `{"event":"on_interrupt"}` 喂判据、而生产里那条
    分支从不触发——「断言打在构造/合成上」正是本仓点名的病）：
    ① **计数**：发真 HTTP 流式调用（本机桩 OpenAI 端点，零花费）→ 真 `LLMGateway` 落账 →
       `CostManager.truncated_calls`。同一根桩按提示词决定收尾原因，`stop` 那一发是**特异性对照**：
       判定写成无条件计数也照样过的判据不算判据。
    ② **发出去**：真 `SessionRunner._settle` 在收口时按这条计数发 `turn/end`；同一跑再收口一次
       不许冒第二条（长跑会 resume 多次）。"""
    import asyncio
    import json as j
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer
    from types import SimpleNamespace

    from codeharness.configs.llm_config import LLMConfig, LLMType
    from codeharness.provider.cost import CostManager
    from codeharness.provider.gateway import LLMGateway
    from server.runner import SessionRunner

    class _Stub(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            n = int(self.headers.get("content-length") or 0)
            req = j.loads(self.rfile.read(n) or b"{}")
            fin = "length" if "trunc" in str(req.get("messages", {})) else "stop"
            self.send_response(200)
            # 不发 Connection: close 的话 httpx 那条异步生成器会在连接被复用时「didn't stop after
            # athrow()」——判据不红但门禁输出刷一片 traceback，看着像代码坏了。
            self.send_header("Connection", "close")
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            seq = {"i": 0}

            def chunk(delta, reason=None, usage=None):
                # OpenAI 的 chunk 必须自带 id/object/created/model：少了这些，openai SDK 的
                # `_process_chunk` 一片都不收，`get_final_completion()` 在 snapshot is None 上断言
                # （本门建的桩第一版就中在这儿）。
                seq["i"] += 1
                body = {"id": f"chatcmpl-s8-{seq['i']}", "object": "chat.completion.chunk",
                        "created": 1790000000, "model": "stub-model",
                        "choices": [{"index": 0, "delta": delta, "finish_reason": reason}]}
                if usage is not None:
                    body["choices"] = []
                    body["usage"] = usage
                self.wfile.write(("data: " + j.dumps(body, ensure_ascii=False) + "\n\n").encode())

            chunk({"role": "assistant", "content": ""})
            chunk({"content": "前半段"})
            chunk({}, fin)
            chunk({}, None, {"prompt_tokens": 7, "completion_tokens": 4, "total_tokens": 11})
            self.wfile.write(b"data: [DONE]\n\n")

        def log_message(self, *a):
            pass

    srv = HTTPServer(("127.0.0.1", 0), _Stub)          # 端口 0：不跟别人抢固定口
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    async def one_call(prompt: str) -> CostManager:
        cm = CostManager()
        cfg = LLMConfig(api_type=LLMType.OPENAI, base_url=f"http://127.0.0.1:{port}/v1",
                        api_key="stub", model="stub-model", max_token=64)
        gw = LLMGateway(cfg=cfg, cost_manager=cm)
        await gw.ainvoke(prompt, stream=True)
        return cm

    class _Ev:
        """Session 的最小形状：_settle/_publish_status 只读这四个属性。"""

        def __init__(self, sid):
            self.id, self.status, self.error, self.cost = sid, "running", "", {}

    class _Store:
        def __init__(self):
            self.writes = []

        def update(self, sid, **kw):
            self.writes.append(kw)
            return _Ev(sid)

        def get(self, sid):
            return _Ev(sid)

        def set_cost(self, sid, cost, persist=False):
            pass

    class _Bus:
        def __init__(self):
            self.events = []

        def publish(self, sid, kind="report", **fields):
            self.events.append(SimpleNamespace(kind=kind, **fields))
            return None

    try:
        cm_trunc = asyncio.run(one_call("这一次要被截断 trunc"))
        cm_whole = asyncio.run(one_call("这一次正常收尾"))
        assert cm_trunc.total_completion_tokens > 0, "桩没被真调用（token 零入账），下面的计数判据就是空转"
        assert cm_trunc.truncated_calls == 1, \
            f"B8：真流式调用以 length 收尾，账本却没数到截断（truncated_calls={cm_trunc.truncated_calls}）"
        assert cm_whole.truncated_calls == 0, \
            "B8 对照失效：finish_reason=stop 那一发也被计成截断——这条判定等于无条件计数"

        store, bus = _Store(), _Bus()
        runner = SessionRunner(store, bus)
        runner.costs["s8b8"] = cm_trunc
        asyncio.run(runner._settle("s8b8"))
        turns = [e for e in bus.events if e.kind == "turn"]
        assert len(turns) == 1, f"B8：收口应发且只发一条 turn，实发 {len(turns)}（总线 {[e.kind for e in bus.events]}）"
        assert turns[0].name == "end" and turns[0].value == {"reason": {"kind": "max-tokens"}}, \
            f"B8：turn/end 的 reason 不再是参照系那个形状 {{'reason': {{'kind': 'max-tokens'}}}}，实际 {turns[0].value}"
        kinds = [e.kind for e in bus.events]
        assert kinds.index("turn") == len(kinds) - 2 and kinds[-1] == "status", \
            f"B8：提示不在终态 status 前面一格（顺序 {kinds}）——前端按事件顺序建行，" \
            "跑完了才冒行就是「已完成」旁边挂着一条截断提示"
        bus.events.clear()
        asyncio.run(runner._settle("s8b8"))
        assert not [e for e in bus.events if e.kind == "turn"], \
            "B8：同一跑重复收口又冒了一条（长跑 resume 会把同一次截断刷成 N 条提示）"
    finally:
        srv.shutdown()

    ss = (FE / "stores" / "sessions.ts").read_text(encoding="utf-8")
    assert "ev.kind === 'turn'" in ss, "B8 回归：applyEvent 不接 kind=turn（后端说出去了但没人听）"
    assert "b.type = 'MaxTokens'" in ss and 'reason?.kind === \'max-tokens\'' in ss, \
        "B8 回归：turn 分支不再按 reason.kind 建 MaxTokens 块（提示行会消失或不分原因乱发）"

    cn = (FE / "components" / "conversation" / "ChatNode.vue").read_text(encoding="utf-8")
    assert 'v-else-if="b.type === \'MaxTokens\'" class="errRow"' in cn, \
        "B8 回归：MaxTokens 行不再复用 .errRow 那一族几何（B1 已钉过源值，别另造一档）"
    # 文案逐字取参照系 packages/client/ui-conversation/src/client/locales.ts:132-133
    for copy in ("已达到输出 token 上限",
                 "回答被截断，已有输出保留在对话中。发送“继续”可让模型接着输出。"):
        assert copy in cn, f"B8 回归：界面少了参照系那句文案「{copy}」"
    css = cn.split("<style", 1)[-1]
    assert 'state="warning"' in cn and "--dsw-alias-state-warn-primary" in css, \
        "B8 回归：截断行用的是 error 档的红（截断不是失败，参照系给的是 warn 档）"
    _ok("t16", "B8：真 HTTP 流式 length 收尾 → 记账口数到截断（stop 对照零计）→ `_settle` 发一条 "
               "turn/end+reason（重复收口不第二条、发在终态 status 之前）→ applyEvent 建 MaxTokens 块 "
               "→ ChatNode warn 行与参照系两句文案逐字在位")


def t17_goal_surface():
    """B5：会话目标——单条字符串、只有用户能改与完成、变更进事件流。

    三侧同判：
    ① 端点语义（**真 TestClient**，不碰共享 dev Redis：这一格里把 `use_redis` 关掉、
       注册表指向临时文件，否则门禁每跑一次就往 db0 塞一条会话）；
    ② 「完成只由用户点确认」这条口径钉成**可执行的不复燃守卫**——除 `Session` 模型与那三条路由
       之外，全仓不许出现写 `goal_done_at` 的代码（内核/runner/角色一旦能写，口径当场失效）；
    ③ 前端：三条路由都在消费集里、`kind='goal'` 有分支、`GoalBar.vue` 的文案与几何取参照系源值。
    """
    fe_api = (FE / "api" / "client.ts").read_text(encoding="utf-8")
    fe_store = (FE / "stores" / "sessions.ts").read_text(encoding="utf-8")
    bar = (FE / "components" / "composer" / "GoalBar.vue").read_text(encoding="utf-8")

    for route in ("/goal", "/goal/complete", "/goal/clear"):
        assert f"/api/sessions/${{sid}}{route}" in fe_api, f"B5 回归：client.ts 不再消费 {route}"
    assert "ev.kind === 'goal'" in fe_store and "v.objective" in fe_store, \
        "B5 回归：applyEvent 不接 kind=goal（活流里改了目标要刷新才看得见）"

    # ② 「完成只由用户点确认」钉成可执行的不复燃守卫：
    #    内核一侧（codeharness/**）一个字都不许出现 goal_done_at；
    #    HTTP 一侧只有 complete 那一处写**非空**值（set/clear 写的是空串复位，不算确认）。
    kernel = [f"{p.relative_to(ROOT)}" for p in (ROOT / "codeharness").rglob("*.py")
              if "goal_done_at" in p.read_text(encoding="utf-8")]
    assert not kernel, f"B5 回归：内核侧出现了 goal_done_at {kernel}——完成态只能由 HTTP 侧的用户动作写"
    api_py = (ROOT / "server" / "api" / "sessions.py").read_text(encoding="utf-8")
    others = len(re.findall(r"goal_done_at=_now\(\)", api_py))
    outside = sum(len(re.findall(r"goal_done_at=_now\(\)", p.read_text(encoding="utf-8")))
                  for p in list((ROOT / "server").rglob("*.py")) + list((ROOT / "codeharness").rglob("*.py"))
                  if p != ROOT / "server" / "api" / "sessions.py")
    complete_fn = re.search(r"def complete_goal\(.*?\n(?=\n@|\ndef )", api_py, re.S).group(0)
    assert others == 1 and outside == 0 and "goal_done_at=_now()" in complete_fn, \
        (f"B5 回归：写「已完成」时间戳的语句应恰好一条且就在 complete_goal 里，"
         f"实到 api={others} 其它={outside}")

    # ① 端点语义
    import server.sessions as ss
    from fastapi.testclient import TestClient
    from server.app import create_app
    from codeharness.configs.settings import settings
    keep_file, ss.SESSIONS_FILE = ss.SESSIONS_FILE, Path(tempfile.mkdtemp()) / "sessions.json"
    keep_redis = settings.platform.use_redis
    settings.platform.use_redis = False          # 不把测试会话写进共享 dev Redis（db0）
    try:
        with TestClient(create_app()) as c:
            sid = c.post("/api/sessions", json={"idea": "目标门禁", "project_name": "s8goal"}).json()["id"]
            bad = c.post(f"/api/sessions/{sid}/goal", json={"objective": "  "})
            assert bad.status_code == 422, f"空白目标该被值域拒掉，实回 {bad.status_code}"
            early = c.post(f"/api/sessions/{sid}/goal/complete")
            assert early.status_code == 422, f"没有目标就点完成该 422，实回 {early.status_code}"

            first = c.post(f"/api/sessions/{sid}/goal", json={"objective": "做一个 2048"}).json()
            assert first["goal"] == "做一个 2048" and first["goal_done_at"] == "", first
            second = c.post(f"/api/sessions/{sid}/goal", json={"objective": "改成 2048 + 排行榜"}).json()
            assert second["goal"].startswith("改成") and second["goal_done_at"] == ""

            done = c.post(f"/api/sessions/{sid}/goal/complete").json()
            assert done["goal_done_at"], f"完成没落时间戳：{done}"
            again = c.post(f"/api/sessions/{sid}/goal/complete").json()
            assert again["goal_done_at"] == done["goal_done_at"], "重复点完成刷新了时间戳（幂等破了）"

            hist = c.get(f"/api/sessions/{sid}/events/history").json()["events"]
            goal_evs = [e for e in hist if e["kind"] == "goal"]
            ops = [e["name"] for e in goal_evs]
            assert ops == ["create", "edit", "complete"], \
                f"B5：goal 事件的 operation 序列应是 create/edit/complete，实为 {ops}"
            assert sum(1 for e in goal_evs if e["name"] == "complete") == 1, \
                "B5：重复点完成又发了一条事件（幂等要在事件流上也成立）"
            assert goal_evs[-1]["value"]["objective"].startswith("改成"), \
                f"B5：complete 事件没带上当前目标：{goal_evs[-1]}"

            cleared = c.post(f"/api/sessions/{sid}/goal/clear").json()
            assert cleared["goal"] == "" and cleared["goal_done_at"] == "", cleared
            assert [e["name"] for e in hist if e["kind"] == "goal"] == ops, "回放里多了/少了 goal 事件"
    finally:
        settings.platform.use_redis = keep_redis
        ss.SESSIONS_FILE = keep_file

    # ③ 界面源值：文案与几何（GoalBar.module.css 的 .bar/.label/.objective/.objectiveInput）
    css = bar.split("<style", 1)[-1]
    for copy in ("进行中的目标", "保存目标", "取消编辑", "编辑目标", "清除目标", "输入目标，智能体将持续执行"):
        assert copy in bar, f"B5 回归：目标条少了参照系那句文案「{copy}」（locales.ts:5-15 / :15-16）"
    bar_blk = re.search(r"\.bar \{([^}]*)\}", css)
    assert bar_blk and "height: 36px" in bar_blk.group(1) and "padding: 4px 5px 4px 12px" in bar_blk.group(1), \
        "B5 回归：目标条不再是参照系的 36px 高 / 4px 5px 4px 12px 内距"
    assert "border-radius: 12px" in bar_blk.group(1) and "--dsw-alias-border-l1" in bar_blk.group(1), \
        "B5 回归：目标条的圆角/边线不再是参照系那份（12px + border-l1）"
    assert re.search(r"\.objective \{[^}]*text-overflow: ellipsis", css), "B5：目标文本不截断就会把条撑破"
    assert "--dsw-specific-tip" in css, "B5 回归：底色不再用参照系目标条的 tip token"
    _ok("t17", "B5 目标三面同判：端点值域/幂等/事件序列 create→edit→complete（clear 后回放条数不变）"
               "+ 全仓只有 complete 一处写 goal_done_at + 前端三路由与 GoalBar 源值文案在位")


def t18_steer_queue():
    """B6：跑图途中投进来的插话要**看得见、撤得回**，而且撤一条不许把其余的重排。

    接缝做在队列本身（PLAN §9 第 5 条当年指的就是这个位置）：runner 建队时装上 `on_change`，
    add / remove / drain 三个动作的留痕因此与「谁投的、谁取走的」同源——不在前端另起一份列表
    （那是本仓反复出事的「第二个游标」形状）。Redis 那台的跨 worker 平价在 `s7 t4`。
    """
    from codeharness.runtime import ChatQueue
    from platforms.chat_queue import RedisChatQueue

    for m in ("enqueue", "drain", "pending", "remove"):
        assert m in vars(ChatQueue) and m in vars(RedisChatQueue), \
            f"B6：两台插话通道接口不再同名同签名（缺 {m}）——route 只认一套，前端也只消费一套事件"

    fired = []
    q = ChatQueue(on_change=lambda a, it: fired.append((a, it)))
    ids = [q.enqueue(f"插话{i}", "PM") for i in range(3)]
    assert [p["id"] for p in q.pending()] == ids, f"B6：pending 不是 FIFO，实为 {q.pending()}"
    assert q.remove(ids[1]) is True, "B6：撤回一条存在的插话回了 False"
    assert q.remove("deadbeef") is False, "B6：撤回不存在的 id 竟然成功（前端会以为撤掉了）"
    assert q.drain() == [("插话0", "PM"), ("插话2", "PM")], \
        "B6：撤掉中间那条之后，剩下的被重排或丢了（口径是「可撤回不重排」）"
    assert [f[0] for f in fired] == ["add", "add", "add", "remove", "drain"], \
        f"B6：队列留痕的动作序列不对 {fired}"
    assert fired[3][1] == [{"id": ids[1]}] and fired[4][1][0]["content"] == "插话0", \
        "B6：remove/drain 事件不再带得出「是哪几条」——界面只能整份猜着刷新"
    assert q.pending() == [], "B6：drain 之后还挂着 pending，胶囊永远撤不掉"

    import server.sessions as ss
    from fastapi.testclient import TestClient
    from server.app import create_app
    from codeharness.configs.settings import settings
    keep_file, ss.SESSIONS_FILE = ss.SESSIONS_FILE, Path(tempfile.mkdtemp()) / "sessions.json"
    keep_redis = settings.platform.use_redis
    settings.platform.use_redis = False
    try:
        with TestClient(create_app()) as c:
            sid = c.post("/api/sessions", json={"idea": "队列门禁", "project_name": "s8queue"}).json()["id"]
            empty = c.get(f"/api/sessions/{sid}/queue")
            assert empty.status_code == 200 and empty.json() == {"items": []}, \
                f"B6：没在跑的会话该回空队列，实回 {empty.status_code} {empty.text[:80]}"
            gone = c.delete(f"/api/sessions/{sid}/queue/abc12345")
            assert gone.status_code == 404, f"B6：没有队列时撤回该 404，实回 {gone.status_code}"
            c.app.state.store.update(sid, roles=["PM"], entry_role="PM")   # 目标校验吃装配名册
            chat = c.post(f"/api/sessions/{sid}/chat", json={"content": "hi", "send_to": "Ghost"})
            assert chat.status_code == 422, "B6：插话目标校验被绕过（加队列端点时把这条弄丢了）"
    finally:
        settings.platform.use_redis = keep_redis
        ss.SESSIONS_FILE = keep_file

    fe_api = (FE / "api" / "client.ts").read_text(encoding="utf-8")
    assert "/api/sessions/${sid}/queue" in fe_api and "dropQueued" in fe_api, \
        "B6 回归：前端不再消费队列两条路由"
    st = (FE / "stores" / "sessions.ts").read_text(encoding="utf-8")
    assert "ev.kind === 'queue'" in st, "B6 回归：applyEvent 不接 kind=queue"
    assert "queue: [] as QueueItem[]" in st, "B6 回归：队列投影不再是唯一那一份 store 状态"
    assert "this.queue = []" in st, "B6 回归：切会话不清队列胶囊（上一场排着的会挂在下一场名下）"
    assert "!this.queue.some((q) => q.id === it.id)" in st and "filter((q) => !items.some" in st, \
        "B6 回归：queue 分支改成整份覆盖了——add 与 GET 竞态会把刚投的那条吞掉"
    dock = (FE / "components" / "composer" / "QueueDock.vue").read_text(encoding="utf-8")
    assert "store.queue" in dock and "api.dropQueued" in dock, "B6 回归：QueueDock 不再读队列/不再会撤回"
    assert "(e as Error).message" in dock, "B6 回归：撤回失败又去猜文案（404 原文就写着为什么撤不掉）"
    assert "store.loadQueue(" in dock,         "B6 回归：撤回拿到 404 之后不向服务端对齐——胶囊会挂在一条已经不存在的插话上，"+         "让人点第二下（活体实测过这一格：drain 事件落在活流断档期间，按游标被丢掉）"
    _ok("t18", "B6 三面同判：两台通道接口平价 + FIFO/撤中间不重排/留痕序列 add×3→remove→drain"
               " + 端点（空队列 200、无队列撤回 404、目标校验仍在）+ 前端按 id 增删不整份覆盖")


def t19_fork_surface():
    """B3：从某一轮分叉。验收只有两句，但两句都必须是**真读数**而不是字段名：
    ① 分叉出的会话有**独立游标**——所以断言的是「两条会话的 cursor 集合交集为空」，
       而不是「响应里带了个新 id」：照抄源 seq/cursor 才是最容易写错的那种「看着能用」。
       （前端 `applyEvent` 只认 `cursor <= lastCursor`，共用一份游标会把新会话的头几条吞掉。）
    ② 有**独立产物目录**——断言磁盘上真有一个新的 `workspace/{新 sid}/` 且源文件确实拷进来了。
    另钉 `from_cursor` 含本轮（切在本轮之后）与未知游标 422。
    B3②（09-25）补第三句：**产物拷贝有上限、撞顶要说出去**——旧写法是整份 `copytree` 无上限，
    一次点击可以在 HTTP 请求里陪一个大会话目录走完全程（导入面实测过 12k 文件量级），慢到像失败；
    现在按 `MAX_FORK_FILES` 截断并回 `copied_truncated`，界面把它翻成人话。两格互为对照：
    撞顶那场报截断、没撞顶那场**不许**报（否则「报截断」这条判据本身是恒真的）。
    """
    import server.sessions as ss
    from fastapi.testclient import TestClient
    from server.app import create_app
    from codeharness.configs.settings import settings
    from pathlib import Path as _P

    keep_file, ss.SESSIONS_FILE = ss.SESSIONS_FILE, Path(tempfile.mkdtemp()) / "sessions.json"
    keep_redis = settings.platform.use_redis
    settings.platform.use_redis = False
    try:
        with TestClient(create_app()) as c:
            sid = c.post("/api/sessions", json={"idea": "分叉门禁", "project_name": "s8fork"}).json()["id"]
            bus = c.app.state.bus
            src = c.app.state.store.get(sid)
            src_ws = _P(src.workspace)
            src_ws.mkdir(parents=True, exist_ok=True)
            (src_ws / "docs").mkdir(exist_ok=True)
            (src_ws / "docs" / "requirement.md").write_text("第一轮的产物", encoding="utf-8")
            for i in range(4):
                bus.publish(sid, kind="report", block="Thought", uuid=f"stream-x{i}",
                            name="content", value=f"第{i}句", role="PM")
            cur = [e.cursor for e in bus.history(sid)]
            # 源会话的头一条是 create 时发的 status，所以「切在第 2 条 report 之后」= 游标 cur[2]，
            # 搬过来的是 [status, 第0句, 第1句] 三条——含端点这件事按 report 数核。
            mid = c.post(f"/api/sessions/{sid}/fork", json={"from_cursor": cur[2]}).json()
            kid = mid["id"]
            assert kid != sid and mid["forked_from"] == sid, mid
            assert mid["carried_events"] == 3, f"游标含端点应搬 3 条，实搬 {mid.get('carried_events')}"
            kcur = [e.cursor for e in bus.history(kid)]
            assert kcur == sorted(kcur) and len(set(kcur)) == len(kcur), \
                f"B3：分叉会话的游标不是自洽递增的独立序列 {kcur}"
            assert bus.history(sid) and [e.cursor for e in bus.history(sid)] == cur, \
                "B3：分叉动了源会话的流（两条会话共用一份游标空间的形状）"
            # 「独立游标」的行为形状：两条流各自计数，源会话再发一条不许出现在分叉里，反之亦然。
            # （进程内那台总线每台都从 1 起号，所以「seq 不相等」在这台上是**假判据**——
            #   真 Redis 那台是 ms 级全局号；只有这条双向断言在两台上都成立。）
            bus.publish(sid, kind="log", value="分叉之后源会话又走了一步")
            bus.publish(kid, kind="log", value="分叉之后新会话又走了一步")
            tail_src = [e.value for e in bus.history(sid)]
            tail_kid = [e.value for e in bus.history(kid)]
            assert "分叉之后新会话又走了一步" not in tail_src, "B3：两条会话共用一份流（新会话写的会串回源会话）"
            assert "分叉之后源会话又走了一步" not in tail_kid, "B3：两条会话共用一份流（源会话续推会顶掉分叉场的游标）"
            assert [e.value for e in bus.history(kid) if e.kind == "report"][:2] == ["第0句", "第1句"], \
                "搬过来的内容次序或值不对"
            kid2 = c.post(f"/api/sessions/{sid}/fork", json={"from_cursor": ""}).json()["id"]
            assert len([e for e in bus.history(kid2) if e.kind == "report"]) == 4, \
                "全量分叉应带 4 条内容事件"

            ks = c.get(f"/api/sessions/{kid}").json()
            assert ks["workspace"] != src.workspace, "产物目录没独立"
            copied = _P(ks["workspace"]) / "docs" / "requirement.md"
            # `copied_files` 从今天起数的是**拷过来的文件**（旧写法数的是「新目录里的所有条目」，
            # 把 `docs/` 这个目录也算成一份 ⇒ 那份回执一直是虚高一格的假数）。
            assert mid["copied_files"] == 1 and copied.is_file() and \
                copied.read_text(encoding="utf-8") == "第一轮的产物", \
                f"B3：源产物没真拷进新目录（{ks['workspace']}），或回执不再只数文件：{mid.get('copied_files')}"
            assert mid["copied_truncated"] is False, "小目录的分叉不该报截断"
            assert c.get(f"/api/sessions/{kid}").json()["idea"] == "分叉门禁", "分叉丢了议题"
            bad = c.post(f"/api/sessions/{sid}/fork", json={"from_cursor": "1234567890-000009"})
            assert bad.status_code == 422, f"未知游标该 422，实回 {bad.status_code}"

            # ---- B3②（09-25）：拷贝**有上限、撞顶要说出去** ----
            # 上限是「一次 HTTP 请求里搬多少份文件」的上限（旧写法整份 copytree、无上限：大会话目录
            # 能把分叉拖到像失败，而用户会再点一次）。这里把档改小来验机制，不改生产值。
            import server.api.sessions as sa_api
            big = c.post("/api/sessions", json={"idea": "大目录源场", "project_name": "cap_src"}).json()
            big_ws = _P(big["workspace"])
            big_ws.mkdir(parents=True, exist_ok=True)
            for i in range(5):
                (big_ws / f"f{i}.md").write_text(f"第{i}份", encoding="utf-8")
            keep_cap, sa_api.MAX_FORK_FILES = sa_api.MAX_FORK_FILES, 3
            try:
                cut3 = c.post(f"/api/sessions/{big['id']}/fork", json={"from_cursor": ""}).json()
                kid3 = _P(c.get(f"/api/sessions/{cut3['id']}").json()["workspace"])
                assert cut3["copied_files"] == 3 and cut3["copied_truncated"] is True, \
                    f"B3②：上限没生效或没报截断（档 3）：{cut3.get('copied_files')}/{cut3.get('copied_truncated')}"
                assert (kid3 / "f0.md").is_file() and not (kid3 / "f4.md").exists(), \
                    "B3②：截断截在哪不可复现（拷进来的不是按序的前 3 份）"
                sa_api.MAX_FORK_FILES = 9
                whole = c.post(f"/api/sessions/{big['id']}/fork", json={"from_cursor": ""}).json()
                assert whole["copied_files"] == 5 and whole["copied_truncated"] is False, \
                    (f"B3②：没撞顶时也报了截断（阳性对照失配 ⇒ 上面那条红可能只是「永远报截断」）："
                     f"{whole.get('copied_files')}/{whole.get('copied_truncated')}")
            finally:
                sa_api.MAX_FORK_FILES = keep_cap
    finally:
        settings.platform.use_redis = keep_redis
        ss.SESSIONS_FILE = keep_file

    api_ts = (FE / "api" / "client.ts").read_text(encoding="utf-8")
    assert "/api/sessions/${sid}/fork" in api_ts, "B3 回归：前端不再消费 fork"
    st = (FE / "stores" / "sessions.ts").read_text(encoding="utf-8")
    assert "async forkFrom(" in st and "this.select(r.id)" in st, \
        "B3 回归：分叉后不切到新会话（用户看不见自己刚分出去的那场）"
    tt = (FE / "components" / "conversation" / "TurnTail.vue").read_text(encoding="utf-8")
    assert "turn.endCursor" in tt and "forkFrom" in tt, "B3 回归：TurnTail 的 branch 钮没了"
    turns = (FE / "utils" / "turns.ts").read_text(encoding="utf-8")
    assert "b.endCursor" in turns and ".sort().at(-1)" in turns, \
        "B3 回归：轮末游标不再取「最后一个」——分叉点会切错一轮"
    # 最后一米：后端报了截断，还得真到用户眼前（C16 那族的病就是断在最后一米）
    assert "copied_truncated" in tt and "没拷过来" in tt, \
        "B3② 回归：界面不再把「产物只拷了一部分」说出去（用户以为分叉带走了全部产物）"
    assert "return r" in st and "return r.id as string" not in st, \
        "B3② 回归：store 的 forkFrom 又把响应折回只留 id —— 截断字段在到组件之前就没了"
    _ok("t19", "B3 分叉三面同判：两条流各自计数（互不串台）+ 含端点切轮 + 全量分叉 + 产物目录真独立"
               "（文件内容逐字对）+ 未知游标 422 + 前端 forkFrom/branch 钮在位；"
               "B3②：拷贝撞上限只带前 N 份并报 truncated（没撞顶的阳性对照仍 False）+ 界面把截断说出来")


def t23_run_after_fork():
    """B3①（09-25，零花费）：分叉出的会话**真跑第二跑**——此前只验了它有自己的流与产物目录。

    「分叉能用」真正的意思是**接着那儿往下走得通**，所以这一格走全链：源场跑一跑 → 分叉 → 子场再跑
    一跑，四件各钉一种坏法：
      ① 子场跑到 `finished`（起不来或半途 failed 都不算「跑得通」）；
      ② 第二跑的事件只出现在**子场**的流里，源场的流一字不变（t19 在「跑之前」验过不串台，
         真正会露馅的是跑之后），且子场游标仍单调；
      ③ 源场的产物目录**逐字节不变**（两场合写一份工作区才是最坏的形状）；
      ④ 子场带过来的前文真在磁盘上（逐字对得上，不是空目录）。
    模型端点用本机 OpenAI 兼容桩（与 `s8_runner_meter::t10` 同一形状），`enable_rag` 关掉——
    本格问「分叉后能不能接着跑」，不掺向量腿。
    """
    import json as _j
    import shutil
    import threading
    import time
    from http.server import BaseHTTPRequestHandler, HTTPServer
    from pathlib import Path as _P

    import server.sessions as ss
    from codeharness.configs.settings import settings
    from fastapi.testclient import TestClient
    from server.app import create_app

    class _Stub(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            self.rfile.read(int(self.headers.get("content-length") or 0))
            body = {"id": "chatcmpl-t23", "object": "chat.completion", "created": 1790000000,
                    "model": settings.llm.model,
                    "choices": [{"index": 0, "finish_reason": "stop",
                                 "message": {"role": "assistant", "content": "好的"}}],
                    "usage": {"prompt_tokens": 12, "completion_tokens": 3, "total_tokens": 15}}
            out = _j.dumps(body).encode()
            self.send_response(200)
            self.send_header("Connection", "close")
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(out)))
            self.end_headers()
            self.wfile.write(out)

        def log_message(self, *a):
            pass

    def run_to_end(cc, sid, budget=90.0):
        t0 = time.time()
        st = ""
        while time.time() - t0 < budget:
            st = str(cc.get(f"/api/sessions/{sid}").json().get("status"))
            if st not in ("running", "awaiting_human"):
                return st
            time.sleep(0.5)
        return st

    def snap(root):
        p = _P(root)
        return {} if not p.is_dir() else {str(f.relative_to(p)): f.read_bytes()
                                          for f in p.rglob("*") if f.is_file()}

    srv = HTTPServer(("127.0.0.1", 0), _Stub)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    keep = (ss.SESSIONS_FILE, settings.platform.use_redis, settings.platform.auth_enabled,
            settings.enable_rag, settings.llm.base_url, settings.llm.model, settings.llm.stream)
    tmp = Path(tempfile.mkdtemp())
    proj = "s8forkrun"
    try:
        ss.SESSIONS_FILE = tmp / "sessions.json"
        settings.platform.use_redis = settings.platform.auth_enabled = False
        settings.enable_rag = False
        settings.llm.base_url = f"http://127.0.0.1:{srv.server_address[1]}/v1"
        settings.llm.stream = False
        with TestClient(create_app()) as cc:
            p = cc.post("/api/sessions", json={"idea": "源场：先跑一跑再分叉",
                                                "project_name": proj, "paradigm": "dynamic"}).json()
            assert cc.post(f"/api/sessions/{p['id']}/start").status_code == 200, "源场起跑失败"
            assert run_to_end(cc, p["id"]) == "finished", "①前置失配：源场自己没跑到 finished"
            seed = _P(p["workspace"]) / "docs"
            seed.mkdir(parents=True, exist_ok=True)
            (seed / "前文.md").write_text("第一跑留下的产物", encoding="utf-8")
            before = snap(p["workspace"])
            ev_before = [e.cursor for e in cc.app.state.bus.history(p["id"])]

            kid = cc.post(f"/api/sessions/{p['id']}/fork", json={"from_cursor": ""}).json()["id"]
            assert cc.post(f"/api/sessions/{kid}/start").status_code == 200, "子场起跑失败"
            st = run_to_end(cc, kid)
            assert st == "finished", f"①分叉出的会话第二跑没跑到终态（实为 {st}）"
            carried = _P(cc.get(f"/api/sessions/{kid}").json()["workspace"]) / "docs" / "前文.md"
            assert carried.is_file() and carried.read_text(encoding="utf-8") == "第一跑留下的产物", \
                f"④子场没带来源场的产物（分叉＝空目录 ⇒ 第二跑根本读不到前文）：{carried}"
            assert snap(p["workspace"]) == before, \
                "③子场的第二跑动了源场的产物目录（两场合写一份工作区）"
            # ③的阳性对照（这条断言不许是恒真的）：真往源场目录塞一份东西，同一套比较必须报不等。
            # 为什么对照做在门禁里而不是靠变异工装：变异「子场沿用源场 project」在 Windows 上根本
            # 走不到这一格——两场合写同一份 sqlite 会先炸 `PermissionError [WinError 32]`（09-25 现证）。
            probe = _P(p["workspace"]) / "_对照.txt"
            probe.write_text("子场不该碰这里", encoding="utf-8")
            try:
                dirty = snap(p["workspace"])
            finally:
                probe.unlink()      # 不管断言红不绿都得清：留着它，下一次跑 `before` 就带上脏文件，
                shutil.rmtree(_P(p["workspace"]) / "_子场残留", ignore_errors=True)   # 对照会假红成「恒真」
            assert dirty != before, "③恒真：源场目录被改了都没报出来，这条断言没有牙"
            assert snap(p["workspace"]) == before, "③对照没复原（清理漏了，下一格会假绿）"
            ev_after = [e.cursor for e in cc.app.state.bus.history(p["id"])]
            assert ev_after == ev_before, \
                f"②子场第二跑的事件串进了源场的流（源场多出 {len(ev_after) - len(ev_before)} 条）"
            kid_ev = [e.cursor for e in cc.app.state.bus.history(kid)]
            assert len(kid_ev) > len(ev_before), \
                f"②子场跑完却没发出新事件（流里还是搬来的那些：{len(kid_ev)} 条）"
            assert kid_ev == sorted(kid_ev), f"②子场第二跑之后游标不再单调：{kid_ev[:6]}"
        _ok("t23", "B3① 分叉出的会话真跑第二跑：跑到 finished、第二跑只进子场的流且游标仍单调、"
                   "源场目录逐字节不变、带过来的前文逐字对得上")
    finally:
        srv.shutdown()
        (ss.SESSIONS_FILE, settings.platform.use_redis, settings.platform.auth_enabled,
         settings.enable_rag, settings.llm.base_url, settings.llm.model, settings.llm.stream) = keep
        shutil.rmtree(tmp, ignore_errors=True)
        shutil.rmtree(_P("workspace") / proj, ignore_errors=True)


def t20_icon_names_resolve():
    """F-G：活组件按名字要的图标，必须真能在 `GLYPHS` 里取到。

    `DsIcon.vue` 的解法是 `ALIAS[name] || name` → `GLYPHS[key]`，取不到就走 FALLBACK
    画一个**空 svg**——图标位空白，不是豆腐块，所以只有人对着眼看才发现（`file` 就是这么藏着的：
    ChatNode 的产物链接与 ToolCard 都在用它，别名表里从来没这个名字）。
    本门建那台一次性对账脚本（`log/tidy_icon_audit.py`，随开发记录停写已删）的账，并且更严：
    别名表**指向不存在的字形**同样算红——那只把空白挪后一格。
    只收字面量 `name="x"`；`:name="x"` 是绑定值，静态判不了（`settingsGlyph`/`icon` 那两处）。
    """
    ds = (FE / "components" / "ui" / "DsIcon.vue").read_text(encoding="utf-8")
    gl = (FE / "components" / "ui" / "glyphs.ts").read_text(encoding="utf-8")
    block = ds[ds.index("const ALIAS"):ds.index("const FALLBACK")]
    alias = dict(re.findall(r"^\s{2}'?([a-z0-9-]+)'?:\s*'([A-Za-z0-9]+)'", block, re.M))
    glyphs = set(re.findall(r'^\s*"([A-Za-z0-9]+)":', gl, re.M))
    assert len(glyphs) >= 50, f"GLYPHS 只解出 {len(glyphs)} 个键——本门自己的解析器先失效了"

    used = {}
    for p in sorted((FE / "components").rglob("*.vue")):
        for tag in re.findall(r"<DsIcon\b[^>]*>", p.read_text(encoding="utf-8"), re.S):
            m = re.search(r"(?<![:\w])name=\"([a-z0-9-]+)\"", tag)
            if m:
                used.setdefault(m.group(1), str(p.relative_to(FE)))
    missing = {n: u for n, u in used.items() if n not in alias and n not in glyphs}
    assert not missing, (f"F-G 回归：这些图标名取不到字形、会画成空白 {missing}"
                         "（要么接进 ALIAS 的已有字形，要么改 extract_glyphs.py 的清单重跑，别手改 glyphs.ts）")
    dangling = {k: v for k, v in alias.items() if v not in glyphs}
    assert not dangling, f"F-G 回归：别名指向不存在的字形 {dangling}——空白只是挪后了一格"
    assert alias.get("file") == "IconPaperclipOutline16", \
        f"F-G 回归：`file` 又没接上字形（现值 {alias.get('file')!r}）"
    # 阳性对照：一个压根不在表里的名字必须判不出来，否则上面两条断言是空转
    assert "no-such-glyph-xyz" not in alias and "no-such-glyph-xyz" not in glyphs
    _ok("t20", f"F-G：活组件用到 {len(used)} 个图标名，逐个能在别名表或 GLYPHS（{len(glyphs)} 键）里取到；"
               "`file`→IconPaperclipOutline16 已接上，别名表无悬空指向")


def t21_hire_surface():
    """B9：招人控件——后端那条链早就通了（`s23` 六组），缺的是**用户点得着**。

    本门只补 `s23` 覆盖不到的那一段：「表单里的可选项」与「后端判定用的」必须是同一张表。
    前端自己抄一份工具清单，就会长成 C7 的 `MessageQueue` 那种形状——看着有，其实判的是另一套。
    """
    from codeharness.team import required_tier
    from codeharness.tools import TOOL_REGISTRY

    known = [t.name for t in TOOL_REGISTRY.all()]
    import server.sessions as ss
    from fastapi.testclient import TestClient
    from server.app import create_app
    from codeharness.configs.settings import settings
    keep_file, ss.SESSIONS_FILE = ss.SESSIONS_FILE, Path(tempfile.mkdtemp()) / "sessions.json"
    keep_redis = settings.platform.use_redis
    settings.platform.use_redis = False
    try:
        with TestClient(create_app()) as c:
            # full_access：招进来的成员声明的工具要能真的批得下来（readonly 场会被
            # required_tier 那道闸拒掉——那是正确行为，另有一格专门钉它）。
            sid = c.post("/api/sessions", json={"idea": "招人门禁", "paradigm": "dynamic",
                                                "permission": "full_access",
                                                "project_name": "s8hire"}).json()["id"]
            got = [t["name"] for t in c.get(f"/api/sessions/{sid}/tools").json()["tools"]]
            assert got == known, (f"B9：`/{{sid}}/tools` 投影出的名单与 `TOOL_REGISTRY` 不一致 "
                                  f"（少 {set(known) - set(got)} 多 {set(got) - set(known)}）")
            tiers = {t["name"]: t["tier"] for t in c.get(f"/api/sessions/{sid}/tools").json()["tools"]}
            assert all(v == required_tier([k]) for k, v in tiers.items()), \
                "B9：档位不再是 `required_tier` 那一个出口给的（表单显示的和执行期判的会分叉）"
            assert len(set(tiers.values())) > 1, \
                "B9：18 件工具的档位读数全是同一档——`required_tier` 吃的是**一批名字**，" \
                "把单个名字传成字符串会按字符迭代、全部 fail-closed 成 full_access"

            bad = c.post(f"/api/sessions/{sid}/roles", json={
                "name": "Cleo", "profile": "会查资料", "goal": "把资料补齐", "constraints": "",
                "tools": ["no_such_tool_xyz"]})
            assert bad.status_code == 422 and "no_such_tool_xyz" in bad.text, \
                f"B9：未注册工具名该当场 422 并点名，实回 {bad.status_code}"
            cls = c.post("/api/sessions", json={"idea": "x", "paradigm": "classic",
                                               "project_name": "s8hire2"}).json()["id"]
            cls2 = c.post("/api/sessions", json={"idea": "x", "paradigm": "dynamic",
                                                "project_name": "s8hire3"}).json()["id"]
            wrong = c.post(f"/api/sessions/{cls}/roles", json={
                "name": "Cleo", "profile": "p", "goal": "g", "constraints": "", "tools": []})
            assert wrong.status_code == 422, "B9：非 dynamic 线招人该拒（招进来是醒不过来的死成员）"
            tight = c.post(f"/api/sessions/{cls2}/roles", json={
                "name": "Cleo", "profile": "会查资料", "goal": "把资料补齐", "constraints": "",
                "tools": [n for n in known if tiers[n] == "workspace_write"][:1]})
            assert tight.status_code == 422 and "readonly" in tight.text,                 f"B9：readonly 场招高档工具该拒并说清要切哪一档，实回 {tight.status_code}"
            ok = c.post(f"/api/sessions/{sid}/roles", json={
                "name": "Cleo", "profile": "会查资料", "goal": "把资料补齐", "constraints": "不写文件",
                "tools": [known[0]]})
            assert ok.status_code == 200 and ok.json()["takes_effect"] == "next_start", ok.text[:160]
            assert any(r["name"] == "Cleo" for r in c.get(f"/api/sessions/{sid}").json()["role_defs"]), \
                "B9：确认招人后档案没落库"
    finally:
        settings.platform.use_redis = keep_redis
        ss.SESSIONS_FILE = keep_file

    api_ts = (FE / "api" / "client.ts").read_text(encoding="utf-8")
    for route in ("/tools", "/roles/draft", "/roles"):
        assert f"/api/sessions/${{sid}}{route}" in api_ts, f"B9 回归：client.ts 不再消费 {route}"
    hr = (FE / "components" / "HireRole.vue").read_text(encoding="utf-8")
    assert "paradigm === 'dynamic'" in hr, "B9 回归：招人入口不再只对 dynamic 线出现（classic 线点了必被拒）"
    # 09-23 搬位：招人从中间列 composer 上方挪进右栏「成员」页签（中间列被它挤掉一屏）。
    # 三件事一起钉：右栏真的挂了它、页签只对 dynamic 出现、中间列不再挂它——少一条就是"看着搬了其实没搬"。
    dp = (FE / "components" / "DetailsPanel.vue").read_text(encoding="utf-8")
    assert "import HireRole from './HireRole.vue'" in dp and "ui.rightView === 'team'" in dp, \
        "B9 回归：右栏不再挂招人（或页签正文分支没了）"
    assert "{ key: 'team', label: '成员' }" in dp and 'v-for="v in VIEWS"' in dp, \
        "B9 回归：「成员」页签又被按线藏起来了——用户在 classic 线上不知道有这一页（实测反馈就是「没看到」）"
    assert "canHire" in hr and 'v-if="canHire"' in hr, \
        "B9 回归：页签常驻后动作没按线收（非 dynamic 线点招人必被后端拒）"
    assert "这条线不能招人" in hr, "B9 回归：非 dynamic 线只收了动作、没说明为什么不能 —— 那就是死胡同"
    assert "装配自带" in hr, "B9 回归：classic/react 线打开这一页是空的（看不见这场到底有谁）"
    app = (FE / "App.vue").read_text(encoding="utf-8")
    assert "<HireRole" not in app, "B9 回归：中间列 composer 又挂回招人（右栏那份会两份并存）"
    assert "rightView: 'cards' as" in (FE / "stores" / "ui.ts").read_text(encoding="utf-8") \
        and "'team'" in (FE / "stores" / "ui.ts").read_text(encoding="utf-8"), \
        "B9 回归：ui.rightView 的联合类型没加 'team'（切到该页签会被 TS 判成非法值）"
    assert "api.hireTools(" in hr and "api.roleDraft(" in hr and "api.hireRole(" in hr, \
        "B9 回归：三件事不再由 HireRole 串起来（草案→勾选→确认）"
    assert "hireRole" not in hr.split("async function draft(")[-1].split("async function hire(")[0], \
        "B9 回归：**草案**路径里去调了落库端点——`/roles/draft` 的「不落库」这条契约被前端破坏了"
    assert "(e as Error).message" in hr, "B9 回归：422/502/503 又去猜文案（原文里点名了哪个工具没注册）"
    assert "store.loadSessions()" in hr, "B9 回归：招完不刷新，直聊下拉要重开一次才看得见新成员"
    _ok("t21", "B9 三面同判：/{sid}/tools 与 TOOL_REGISTRY 逐字同名单 + 档位出自 required_tier 单口 + "
               "未注册工具 422 点名 + 非 dynamic 拒 + 落库后 takes_effect=next_start；"
               "前端三路由在位、草案不落库、错误照原文")


def t22_feedback_surface():
    """B4：反馈三端点 + 尾行图标联动。参照系带版本 compare-and-set，本仓只有
    `Session.feedback` 一张 last-write-wins 的表 —— 判据因此**不假装乐观并发**，
    钉的是「幂等 + 改票 + 取消 + 值域」这四件真会做的事。
    """
    from platforms.session_store import _JSON_FIELDS
    assert "feedback" in _JSON_FIELDS, \
        ("B4 回归：`Session.feedback` 是 dict 字段却没登记 `_JSON_FIELDS` —— "
         "Redis 那台会 `str(dict)` 落库、回读直接炸（两台 store 长得不一样，本仓反复踩的那类洞）")

    import server.sessions as ss
    from fastapi.testclient import TestClient
    from server.app import create_app
    from codeharness.configs.settings import settings
    keep_file, ss.SESSIONS_FILE = ss.SESSIONS_FILE, Path(tempfile.mkdtemp()) / "sessions.json"
    keep_redis = settings.platform.use_redis
    settings.platform.use_redis = False
    try:
        with TestClient(create_app()) as c:
            sid = c.post("/api/sessions", json={"idea": "反馈门禁", "project_name": "s8fb"}).json()["id"]
            key = "t:stream-PM"
            bad = c.put(f"/api/sessions/{sid}/feedback", json={"key": key, "vote": "meh"})
            assert bad.status_code == 422, f"B4：非法 vote 该 422，实回 {bad.status_code}"
            assert c.put(f"/api/sessions/{sid}/feedback", json={"key": key, "vote": "like"}).status_code == 200
            again = c.put(f"/api/sessions/{sid}/feedback", json={"key": key, "vote": "like"})
            assert again.status_code == 200, again.text[:120]
            votes = c.get(f"/api/sessions/{sid}/feedback").json()["votes"]
            # B4 之后票值是 `{"v","at"}`（旧记录才是裸字符串，读侧两边都归一）。这里三件事一起钉：
            # 形状对、重复点同一票不新增键、也不刷新时刻（那个时刻是"用户第一次这么说"的时间）。
            assert set(votes) == {key}, f"B4：表里键不对 {votes}"
            entry = votes[key]
            assert isinstance(entry, dict) and entry.get("v") == "like" and entry.get("at"), \
                f"B4：新票形状不对（应为 {{v, at}}）：{entry}"
            at0 = entry["at"]
            hist = c.get(f"/api/sessions/{sid}/events/history").json()["events"]
            assert sum(1 for e in hist if e["kind"] == "feedback") == 1, \
                "B4：重复点同一个票又发了一条事件（幂等要在事件流上也成立，不然活流会闪）"
            # 先塞一条旧形态的票（裸字符串＝没记时刻），再改现在这条：改票必须**刷新时刻**
            # （那是另一次表态），且旧形态不能把读侧打炸——归一在前端 `parseVote`、后端 `_vote_of` 各一份同规则。
            store_obj = c.app.state.store
            legacy = dict(store_obj.get(sid).feedback)
            legacy["t:legacy"] = "like"
            store_obj.update(sid, feedback=legacy)
            c.put(f"/api/sessions/{sid}/feedback", json={"key": key, "vote": "dislike"})
            after = c.get(f"/api/sessions/{sid}/feedback").json()["votes"]
            assert isinstance(after[key], dict) and after[key]["v"] == "dislike", \
                f"B4：改票没覆盖掉旧票（用户在纠错，不是脏数据）：{after}"
            # 「改票要刷新时刻」不能靠连着点两次比字符串——`_now()` 只到秒，同一秒内两次 PUT
            # 必然相等（这条断言第一版就这么写，实测必红）。做法：把首次时刻改成哨兵值再改票。
            stamped = dict(after)
            stamped[key] = {"v": "dislike", "at": "2020-01-01 00:00:00"}
            store_obj.update(sid, feedback=stamped)
            c.put(f"/api/sessions/{sid}/feedback", json={"key": key, "vote": "like"})
            r2 = c.get(f"/api/sessions/{sid}/feedback").json()["votes"][key]
            assert r2["at"] != "2020-01-01 00:00:00", \
                f"B4：改票没刷新时刻——按时间分会把它记在首次表态那天：{r2}"
            assert c.put(f"/api/sessions/{sid}/feedback", json={"key": "t:legacy", "vote": "like"}).status_code == 200
            same = c.get(f"/api/sessions/{sid}/feedback").json()["votes"]["t:legacy"]
            # 同票幂等走的是"没变化就不写、不发事件"那支 ⇒ 旧形态**原地保留**，不趁机改写历史。
            # 读侧（后端 `_vote_of` / 前端 `parseVote`）负责把两态归一，所以这不构成缺陷。
            assert same == "like", f"B4：同票幂等竟然改写了旧记录形态：{same}"
            gone = c.delete(f"/api/sessions/{sid}/feedback", params={"key": "t:never"})
            assert gone.status_code == 200 and gone.json()["ok"] is True, \
                "B4：取消一条不存在的票不该报错（用户要的就是「让它不亮」）"
            c.delete(f"/api/sessions/{sid}/feedback", params={"key": key})
            # 本格中途塞过一条旧形态的票（t:legacy），一起清掉才谈得上"表空了"——
            # 第一版没清它，于是"DELETE 没清掉那一票"这条红是**我自己的账没平**，不是端点的错。
            c.delete(f"/api/sessions/{sid}/feedback", params={"key": "t:legacy"})
            assert c.get(f"/api/sessions/{sid}/feedback").json()["votes"] == {}, "B4：DELETE 没清掉那两票"
            names = [e["name"] for e in
                     c.get(f"/api/sessions/{sid}/events/history").json()["events"] if e["kind"] == "feedback"]
            # 序列：like(记) → 同票重复点(不发) → dislike(改) → t:legacy 同票幂等但值要归一(发) → 取消
            # 事件序列（实测）：like → 同票重复点(不发) → dislike → 改回 like → t:legacy 同票(不发)
            # → 删不存在的键(不发) → 删两条(各发一次 clear)。"没变化就不留事件"这条在流上也成立。
            assert names == ["set", "set", "set", "clear", "clear"], f"B4：事件动作序列不对 {names}"
    finally:
        settings.platform.use_redis = keep_redis
        ss.SESSIONS_FILE = keep_file

    api_ts = (FE / "api" / "client.ts").read_text(encoding="utf-8")
    assert "feedback: (sid: string)" in api_ts and "putFeedback" in api_ts and "deleteFeedback" in api_ts \
        and "`/api/sessions/${sid}/feedback`" in api_ts, "B4 回归：client.ts 不再消费那三条反馈路由"
    st = (FE / "stores" / "sessions.ts").read_text(encoding="utf-8")
    assert "ev.kind === 'feedback'" in st and "feedback: {} as Record<string, string>" in st, \
        "B4 回归：反馈投影或 kind=feedback 分支没了"
    icon = (FE / "components" / "conversation" / "MessageIconActions.vue").read_text(encoding="utf-8")
    # 票值两态的归一只有**一条规则**（`utils/votes.ts::parseVote`），但**消费点有三处**：
    # 会话投影（loadFeedback）、PUT/DELETE 回执（图标组件）、活流事件（kind=feedback 分支）。
    # 漏一处的症状很阴：回执那次没归一时 `vote === "like"` 恒假 ⇒ 「再点一次取消」静默变成"又投一次"，
    # 而 PUT 本身还是 200，端点判据全绿（本轮第一版就漏在这儿，是 grep 消费点抓出来的）。
    assert "normalizeVotes(r.votes" in st, "B4 回归：会话投影不再归一票值两态"
    assert "normalizeVotes(r.feedback" in icon, "B4 回归：图标组件不再归一 PUT/DELETE 回执的票值"
    assert api_ts.count("/feedback`") >= 3 or "putFeedback" in api_ts, "B4 回归：client.ts 少了反馈路由"
    assert "this.feedback = {}" in st, "B4 回归：切会话不清反馈表（上一场的票会亮在这一场的尾行上）"
    mac = (FE / "components" / "conversation" / "MessageIconActions.vue").read_text(encoding="utf-8")
    for token in ("api.deleteFeedback", "api.putFeedback",
                  "store.feedback[props.feedbackKey]", "DsIcon name=\"like\"", "DsIcon name=\"dislike\""):
        assert token in mac, f"B4 回归：尾行图标少了 {token}（点亮/取消/联动都靠它）"
    # 按下态的机制**照参照系钉**：两颗都吃 outline 字形，靠 `data-active` + 颜色 token 表示"已记上"。
    # （参照系 ui-message-feedback 里 IconLikeFill16/IconDislikeFill16 零消费处，实测取证；
    #   本仓曾用 `vote==='like' ? 'like-fill' : 'like'` 是自家分叉，且只给 like 一颗 ⇒ 「没用」点了没反馈。）
    for token in (":data-active=\"vote === 'like' || undefined\"",
                  ":data-active=\"vote === 'dislike' || undefined\""):
        assert token in mac, f"B4 回归：少了 {token} —— 按下态没有可读属性，只剩颜色，深浅两色下会看不出来"
    assert ".act[data-active]" in mac, "B4 回归：`data-active` 没有对应样式（属性挂上但界面不变）"
    alias = (FE / "components" / "ui" / "DsIcon.vue").read_text(encoding="utf-8")
    assert "'like-fill'" not in alias and "'dislike-fill'" not in alias, \
        "B4 分叉复燃：别名表又长出 Fill 字形——按下态按参照系走 data-active+颜色，不换字形"
    # 两枚都要有按下态：只数「出现过一次」会被另一枚撑过去（反向验证第一版就这么漏过一格）
    assert mac.count("aria-pressed") == 2, \
        f"B4 回归：aria-pressed 只出现 {mac.count('aria-pressed')} 次——两枚图标钮各要一个，" \
        "少一个就是「点亮了但屏幕阅读器不知道」"
    assert mac.count(":aria-label=") >= 2, "B4 回归：反馈按钮没有可读名字"
    tt = (FE / "components" / "conversation" / "TurnTail.vue").read_text(encoding="utf-8")
    assert ":feedback-key=\"turn.key\"" in tt, "B4 回归：尾行不再把轮键传给图标（按钮根本不出现）"
    _ok("t22", "B4 三面同判：vote 值域 422 + 同票幂等（事件也只一条）+ 改票覆盖 + 取消不存在的键回 ok + "
               "set/set/clear 序列 + `feedback` 已登记 _JSON_FIELDS + 尾行两颗各有 aria-pressed/data-active"
               "（按下态＝outline 字形 + 颜色 token，照参照系；Fill 别名不许复燃）")


def t24_checkpoint_to_chat_jump():
    """C11 第四件：右栏「步」那一行 → 中栏对话流里它产出的那一块。

    这条映射是**凑的**（超步那侧只有节点名与时刻，块那侧只有 role/ts，两边没有共同主键），
    所以这里判的不是「算得准不准」（那是 `frontend/scripts/check_cp_jump.mjs` 的活，纯函数、
    三档各有断言），而是**三件同批接线有没有各就各位**：右栏算锚点 → `ui.jumpKey` 递口令 →
    中栏消费并清回空串。少任何一环，症状都是「点了没反应」——DOM 层面的跳转失败
    连个报错都不给，比跳错更难发现。

    本格是**文本级**的（与 s8 全部结构格同族）：把那一行整行注掉仍然绿——变异 p5 第一版就是这么暴露的。
    要防住得引 AST 解析器，不值；跳转逻辑的真牙在 `frontend/scripts/check_cp_jump.mjs`（10 格，
    反向验证 n1–n4 各红各的档）。这里只保证三处接线不会哪一处悄悄断。
    """
    cj = (FE / "utils" / "cpJump.ts").read_text(encoding="utf-8")
    assert "export function pickCpAnchor" in cj and "export function anchorKeyOf" in cj, \
        "C11 回归：锚点键换算不再是**一处**——两个跳转方各拼一次前缀必然分叉"
    # 前缀的真值源在 ChatNode 的模板里。它一改而 anchorKeyOf 没跟，症状是「跳用户发言静默失效」
    cn = (FE / "components" / "conversation" / "ChatNode.vue").read_text(encoding="utf-8")
    assert "'user:' + b.key" in cn, "C11 回归：ChatNode 的锚点前缀变了，`anchorKeyOf` 得跟着改（本门钉的就是这一对）"
    assert "user:${b.key}" in cj, "C11 回归：anchorKeyOf 不再拼 ChatNode 那个 `user:` 前缀"

    ui = (FE / "stores" / "ui.ts").read_text(encoding="utf-8")
    assert "jumpKey: ''" in ui, "C11 回归：口令字段被删了？右栏写、中栏读都靠它"
    cr = (FE / "components" / "conversation" / "ConversationRoot.vue").read_text(encoding="utf-8")
    assert "watch(() => ui.jumpKey" in cr, "C11 回归：中栏不再消费口令，右栏那颗按钮点了什么都没有"
    body = cr.split("watch(() => ui.jumpKey")[1].split("})")[0]
    assert "if (!k) return" in body, "C11 回归：清空口令时 watcher 也会进一次回调，不挡掉就是空跳"
    assert body.index("ui.jumpKey = ''") < body.index("jumpToBlock(k)"), \
        "C11 回归：改成「跳完再清」的话，连点同一行第二次是同一个值 ⇒ watcher 不再触发，只滚得动一次"

    dp = (FE / "components" / "DetailsPanel.vue").read_text(encoding="utf-8")
    for token, why in (
        ("pickCpAnchor(store.blockList", "右栏不再算锚点（按钮这侧断链）"),
        (":disabled=\"!cpAnchor.key\"", "没有对应块时按钮仍可点 ⇒ 静默不跳，用户只会以为界面坏了"),
        ("'在对话流里定位（就近）'", "兜底那一档没被说出去 ⇒ 用户会把「就近」当成「对话流缺这块」"),
        ("ui.jumpKey = cpAnchor.value.key", "口令不再递给中栏"),
        ("replay.rows.find", "动作位不跟着展开的那一行走（会在没展开时冒出来）"),
    ):
        assert token in dp, f"C11 回归：{why}"

    tt = (FE / "components" / "conversation" / "TrajectoryTable.vue").read_text(encoding="utf-8")
    assert "anchorKeyOf(b)" in tt and "r.blocks.at(-1)?.key" not in tt, \
        ("C11 回归：台账行的跳转换回裸 `b.key` —— 落到用户块时 DOM 里根本没这个锚点，"
         "点了静默没反应（这条是本轮顺手修的，别再改回去）")
    _ok("t24", "C11 第四件三处同批：右栏算锚点（三档各有文案、无目标即置灰）→ ui.jumpKey 递口令 → "
               "中栏先清再跳（连点同一行仍会滚）；锚点前缀与 ChatNode 同一真值源，台账行也走同一处")


def main():
    checks = (t1_blocktype_vocabulary, t2_envelope_and_kinds, t3_routes_exist,
              t4_graph_endpoint, t5_workspace_file_response_shape, t6_trace_span_vocabulary,
              t7_chat_target_from_assembly, t8_tool_approval_gate, t9_request_deadline,
              t10_approval_rollback_realign, t11_events_history_window,
              t12_offline_banner_and_turn_error_row, t13_size_cap_and_truncation_reach_the_user,
              t14_checkpoint_replay_surface, t15_kb_upload_entry, t16_max_tokens_notice,
              t17_goal_surface, t18_steer_queue, t19_fork_surface,
              t20_icon_names_resolve, t21_hire_surface, t22_feedback_surface,
              t23_run_after_fork, t24_checkpoint_to_chat_jump)
    for fn in checks:
        fn()
    print(f"\ns8_frontend_contract: {len(checks)}/{len(checks)} 全绿")


if __name__ == "__main__":
    main()
