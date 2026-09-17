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
    tl = (FE / "components" / "Timeline.vue").read_text(encoding="utf-8")
    dispatched = set(re.findall(r"b\.type === '([^']+)'", tl))
    missing = {b.value for b in BlockType} - dispatched
    assert not missing, f"BlockType {missing} 在 Timeline.vue 无分发分支——块会静默降级灰色 GenericBlock"
    _ok("t1", f"九值 BlockType 词汇表 ↔ Timeline 分发逐一对上（{len(dispatched)} 支）")


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
    missing = {p for p in consumed if p not in declared}
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
        _ok("t4", f"/graph 节点×{len(agents)}、边取自真 watch 订阅表（画的就是跑的）")
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


def _ok(n, msg):
    print(f"✅ {n}: {msg}")


def main():
    t1_blocktype_vocabulary()
    t2_envelope_and_kinds()
    t3_routes_exist()
    t4_graph_endpoint()
    t5_workspace_file_response_shape()
    print("\ns8_frontend_contract: 5/5 全绿")


if __name__ == "__main__":
    main()
