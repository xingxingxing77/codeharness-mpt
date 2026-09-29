"""流式落地的离线判据工装（零花费：本机 OpenAI 兼容桩当 LLM，不发任何真请求）。
跑法：cd /e/Codeharness && PYTHONPATH=/e/Codeharness PYTHONIOENCODING=utf-8 \\
      REDIS__DB=15 LANGFUSE__ENABLED=0 NO_PROXY=127.0.0.1,localhost,::1 \\
      F:/anaconda/python.exe -B tests/manual_stream_landing.py

为什么要有它：`s8_runner_meter` t12 用的是**手搭的合成事件**，形状是我照印象写的——结果形状猜错两次
（先扁平列表、再批式嵌套列表），真会话里 `_prompt_text` 抽到空串、回显排除**静默空转**，判据两次都绿。
把假修好戳穿的是两场付费真跑。这台桩走的是**真 `astream_events` + 真 `with_structured_output`**，
所以事件的形状、`live`/`content` 两条通道、落点登记、回显计数全都是现取的，不用猜也不用花钱。
它同时是「事件形状不许凭印象钉进判据」这条教训的可复跑凭证。
"""
import asyncio
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "logs"))

ECHO = "做一个极小的静态网页，用一段话解释二分查找是什么。不要写测试，不要部署。"
PROSE = "以极简静态网页为载体，用一段通俗准确的表述向读者清晰解释二分查找的核心原理与适用场景"
GOALS = [PROSE, PROSE + "，第二条同样足够长以便越过 24 字的门槛"]
SCHEMA_OUT = {
    "ZeroThought": {"thought": PROSE, "commands": [{"command_name": "end", "args": {}}]},
    "PRDOutput": {"language": "en", "programming_language": "Vite, React, MUI, Tailwind CSS",
                  "original_requirements": ECHO, "project_name": "binary_page", "product_goals": GOALS,
                  "user_stories": [], "competitive_analysis": [], "competitive_quadrant_chart": "quadrantChart",
                  "requirement_analysis": PROSE, "requirement_pool": [["F001", "index.html", "一个段落"]],
                  "ui_design_draft": PROSE, "anything_unclear": "无"},
}
_SCHEMAS = {n: json.dumps(o, ensure_ascii=False) for n, o in SCHEMA_OUT.items()}


def _sse(content):
    """按 OpenAI 的 SSE 逐块吐（真 provider 就是这个形状，抽取器吃的也就是它）。"""
    frames = [json.dumps({"id": "x", "object": "chat.completion.chunk", "created": 1,
                          "model": "step-3.5-flash",
                          "choices": [{"index": 0, "delta": {"content": content[i:i + 12]},
                                       "finish_reason": None}]}, ensure_ascii=False)
              for i in range(0, len(content), 12)]
    frames.append(json.dumps({"id": "x", "object": "chat.completion.chunk", "created": 1,
                              "model": "step-3.5-flash",
                              "choices": [{"index": 0, "delta": {"role": "assistant", "content": ""},
                                           "finish_reason": "stop"}],
                              "usage": {"prompt_tokens": 300, "completion_tokens": 120,
                                        "total_tokens": 420}}))
    return "".join(f"data: {f}\n\n" for f in frames) + "data: [DONE]\n\n"


class Stub(BaseHTTPRequestHandler):
    def do_POST(self):  # noqa: N802
        raw = self.rfile.read(int(self.headers.get("content-length") or 0)).decode("utf-8", "replace")
        name = next((n for n in _SCHEMAS if f'"{n}"' in raw or f"{n}_" in raw), "ZeroThought")
        out = _sse(_SCHEMAS[name])
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Transfer-Encoding", "chunked")
        self.send_header("Connection", "close")
        self.end_headers()
        b = out.encode()
        for i in range(0, len(b), 512):
            seg = b[i:i + 512]
            self.wfile.write(f"{len(seg):X}\r\n".encode() + seg + b"\r\n")
        self.wfile.write(b"0\r\n\r\n")

    def log_message(self, *a):
        pass


async def run_case(name, schema_cls, msgs, open_block_uuid=None):
    """跑一笔真 structured 调用，把事件灌进真 `SessionRunner._translate`，返回总线上的块序列。"""
    from langchain_openai import ChatOpenAI
    from codeharness.provider.cost import CostManager
    from server.events import SessionEventBus
    from server.runner import SessionRunner
    from server.sessions import SessionStore, SessionStatus
    import tempfile

    tmp = Path(tempfile.mkdtemp())
    store = SessionStore(path=tmp / "sessions.json")
    bus = SessionEventBus()
    runner = SessionRunner(store, bus)
    s = store.create("流式落地", project_name="stream_landing")
    store.update(s.id, status=SessionStatus.running)
    runner.costs[s.id] = CostManager()      # 本工装不判账，只判流；给一份账免得 _sync_cost 走空路

    if open_block_uuid:            # 模拟内核 `async with docs_block(...)`：先开一块
        runner._make_sink(s.id)({"block": "Docs", "uuid": open_block_uuid, "name": "meta",
                                 "value": {"type": "prd"}, "role": "PM"})

    m = ChatOpenAI(model="step-3.5-flash", api_key="stub", base_url=BASE_URL, streaming=True)
    r = m.with_structured_output(schema_cls.model_json_schema(), include_raw=True,
                                 method="json_schema", strict=True)
    shapes = []
    async for ev in r.astream_events(msgs, version="v2"):
        if ev.get("event") == "on_chat_model_start":
            inp = (ev.get("data") or {}).get("input")
            shapes.append(type(inp).__name__)
        runner._translate(s.id, ev)
    if open_block_uuid:
        runner._make_sink(s.id)({"block": "Docs", "uuid": open_block_uuid, "name": "end_marker",
                                 "value": None, "role": "PM"})
    seq = [(e.name, str(e.uuid)[:8], len(str(e.value or ""))) for e in bus.history(s.id)
           if e.kind == "report"]
    live = "".join(str(e.value or "") for e in bus.history(s.id)
                   if e.kind == "report" and e.name == "live")
    runner._forget(s.id, terminal=True)
    print(f"\n[{name}] start.input 的类型 = {shapes} | 总线事件序列 = {seq}")
    print(f"  逐片 {len(live)} 字 = {live[:70]!r}")
    return live, seq, runner


async def main():
    from langchain_core.messages import HumanMessage, SystemMessage
    from codeharness.actions.write_prd import PRDOutput
    from codeharness.roles.role_zero import ZeroThought
    from server.runner import _prompt_text

    global BASE_URL

    fails = []
    msgs = [SystemMessage(content="你是产品经理，按 schema 输出 json。"),
            HumanMessage(content="项目：binary_page\n用户需求：" + ECHO + "\n请写 PRD。")]

    # ① 真形状：`data["input"]` 是 dict（判据里那条字面就是照它写的，改形状就得红）
    live, seq, runner = await run_case("PRD 落进开着的 Docs 块", PRDOutput, msgs, open_block_uuid="doc-1")
    if ECHO in live:
        fails.append("① 回显还在逐片里（`_prompt_text` 又抽不到真形状了？）")
    if not any(n == "live" and u == "doc-1" for n, u, _ in seq):
        fails.append("② 逐片没落进开着的那块（落点登记断了）")
    if any(n == "live" and u.startswith("stream-") for n, u, _ in seq):
        fails.append("② 块开着却同时往兜底行投了一份（两份同屏回来了）")
    if any(n == "end_marker" and u.startswith("stream-") for n, u, _ in seq):
        fails.append("②b 这一笔没碰兜底行却替它收了口（长流里会无端关掉上一笔开的行）")
    if any("{" in str(v) and '"language"' in str(v) for n, u, v in [(a, b, c) for a, b, c in seq]) \
            or "{" in live and '"language"' in live:
        fails.append("③ 逐片里出现 JSON 结构（抽取器退化回原样上屏）")

    # ④ 没有开着的块 ⇒ 兜底行 + start 建块（`meta`，不占 fts）
    live2, seq2, _ = await run_case("无内核块 ⇒ 兜底行", ZeroThought,
                                    [SystemMessage(content="按 schema 输出"),
                                     HumanMessage(content="用两三句话解释二分查找，说完结束。")])
    if not any(n == "meta" and u.startswith("stream-") for n, u, _ in seq2):
        fails.append("④ 没有内核块时没建兜底行（静默期又成一片黑）")
    if not any(n == "live" and u.startswith("stream-") for n, u, _ in seq2):
        fails.append("④ 兜底行没收到逐片")

    # ⑤ `_prompt_text` 三种形状都要认（dict 是现证的真实形状，扁平与批式是历史踩过的两种）
    for shape, data in (("dict", {"input": {"messages": msgs}}),
                        ("扁平", {"input": msgs}),
                        ("批式", {"input": [msgs]})):
        if _prompt_text(data).find("用户需求") < 0:
            fails.append(f"⑤ `_prompt_text` 认不出{shape}形状")

    print("\n" + ("\n".join(f"❌ {f}" for f in fails) if fails else "✅ 五条全过（零花费）"))
    return 1 if fails else 0


if __name__ == "__main__":
    srv = HTTPServer(("127.0.0.1", 0), Stub)
    BASE_URL = f"http://127.0.0.1:{srv.server_address[1]}/v1"
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    from codeharness.configs.settings import settings
    settings.llm.base_url = BASE_URL
    settings.llm.api_key = "stub-key"
    settings.llm.stream = True
    settings.enable_rag = False
    sys.exit(asyncio.run(main()))
