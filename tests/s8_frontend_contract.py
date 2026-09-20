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
                   ("events.py", "runner.py") ] + [ROOT / "server" / "api" / "sessions.py"]
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
        # fetch/EventSource 模板串里的 /api 路径（${...} → {sid}）
        for raw in re.findall(r"[`'\"](\/api\/[^`'\"\s?]*)", txt):
            consumed.add(re.sub(r"\$\{[^}]*\}", "{sid}", raw))
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
    print("\ns8_frontend_contract: 7/7 全绿")


if __name__ == "__main__":
    main()
