"""C174 取证（零花费）：真图 + 真 `astream_events` 下，一笔 LLM 调用的 metadata 里到底有没有
「这一笔属于哪个角色」可读的身份。

为什么需要它：`_live_blk` 是每会话一个槽（`server/runner.py:334`），`on_chat_model_start` 的快照取的
就是那个槽（:1148）⇒ 桩 `tests/manual_stream_landing.py` ⑩ 已现证「一块还开着时它的第二笔逐片整把落进
后开的那块」。要分槽就得两侧共用一个身份键，可 sink 侧登记的是**角色名**（`thought_block(role=名字)`），
`_translate` 侧读到的键是 `metadata["langgraph_node"]`；而外层节点名**就是角色名**
（`role_zero.as_node` 末尾 `return name, _run`），内层才是 think/act。所以要数的是：
`astream_events` 送到 chat-model 事件上的 metadata，除了内层节点名，有没有带外层节点名 / checkpoint_ns
这种能追回角色的东西。有 ⇒ C174 不必扩契约就能按身份分槽（自推）；没有 ⇒ 得让内核把身份传下来，
那是跨 `report`/`runtime`/`runner` 三层的契约决定，摊拍不代拍。

⚠ 为什么不能用 FakeLLM：`codeharness/provider/fake.py` 里的 FakeLLM 是普通对象、不是 langchain chat
model，挂在图里跑 `astream_events` **一个 `on_chat_model_*` 都不会发**（本仓「判据不许拿替身冒充真形状」
那一族的又一个形状）。所以这里打本机 OpenAI 兼容桩 + 真 `ChatOpenAI`：零花费，但真流、真回调树。

跑法：cd /e/Codeharness && PYTHONPATH=/e/Codeharness:/e/Codeharness/logs PYTHONIOENCODING=utf-8 \\
      REDIS__DB=15 LANGFUSE__ENABLED=0 NO_PROXY=127.0.0.1,localhost,::1 \\
      F:/anaconda/python.exe -B tests/manual_c174_llm_identity.py
"""
import asyncio
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import TypedDict

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "logs"))

BASE_URL = ""
ROLES = ("Alice", "Bob")


def _sse(content: str) -> str:
    frames = [json.dumps({"id": "x", "object": "chat.completion.chunk", "created": 1,
                          "model": "step-3.5-flash",
                          "choices": [{"index": 0, "delta": {"content": content[i:i + 12]},
                                       "finish_reason": None}]}, ensure_ascii=False)
              for i in range(0, len(content), 12)]
    frames.append(json.dumps({"id": "x", "object": "chat.completion.chunk", "created": 1,
                              "model": "step-3.5-flash",
                              "choices": [{"index": 0, "delta": {"role": "assistant", "content": ""},
                                           "finish_reason": "stop"}],
                              "usage": {"prompt_tokens": 40, "completion_tokens": 20,
                                        "total_tokens": 60}}))
    return "".join(f"data: {f}\n\n" for f in frames) + "data: [DONE]\n\n"


class Stub(BaseHTTPRequestHandler):
    def do_POST(self):  # noqa: N802
        raw = self.rfile.read(int(self.headers.get("content-length") or 0)).decode("utf-8", "replace")
        who = next((n for n in ROLES if n in raw), ROLES[0])       # 谁在说话：请求里那句「我是〈角色〉」
        thought = f"{who} 的思考正文，长度足够跨过二十四字的门槛线，这样逐片才会真的发布出来"
        b = _sse(json.dumps({"thought": thought,
                             "commands": [{"command_name": "end", "args": {}}]}, ensure_ascii=False)).encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()
        for i in range(0, len(b), 512):
            seg = b[i:i + 512]
            self.wfile.write(f"{len(seg):X}\r\n".encode() + seg + b"\r\n")
        self.wfile.write(b"0\r\n\r\n")

    def log_message(self, *a):
        pass


class St(TypedDict, total=False):
    who: str
    ran: str
    out: str


async def main() -> int:
    from langchain_core.messages import HumanMessage
    from langchain_openai import ChatOpenAI
    from langgraph.graph import END, START, StateGraph
    from langgraph.types import Send
    from codeharness.roles.role_zero import ZeroThought

    llm = ChatOpenAI(model="step-3.5-flash", api_key="stub", base_url=BASE_URL, streaming=True)
    chain = llm.with_structured_output(ZeroThought.model_json_schema(), method="json_schema", strict=True)

    # 内层子图：节点名 think 调模型（照 RoleZero 的形状——外层是角色名，内层才是 think/act）
    inner = StateGraph(St)

    inside = []

    async def think(state: St):
        # 第二问（实现要用它）：节点内部 `get_config()` 拿到的 ns，和 chat-model 事件 metadata 里的
        # ns 是不是**同一个串**——两侧同源才能直接拿来当落点键，不然「名字相等」这个前提就是赌。
        from langgraph.config import get_config
        cfg = get_config()
        inside.append({"node": state["who"],
                       "configurable_ns": (cfg.get("configurable") or {}).get("checkpoint_ns"),
                       "md_ns": (cfg.get("metadata") or {}).get("langgraph_checkpoint_ns"),
                       "md_checkpoint_ns": (cfg.get("metadata") or {}).get("checkpoint_ns"),
                       "md_node": (cfg.get("metadata") or {}).get("langgraph_node")})
        r = await chain.ainvoke([HumanMessage(content=f"我是 {state['who']}，说一句够长的话")])
        return {"out": str(r)[:40]}
    inner.add_node("think", think)
    inner.add_edge(START, "think")
    inner_graph = inner.compile()

    # 外层：节点名＝角色名（同 as_node 的 `return name, _run`），START 一次 fan-out 两条 Send ⇒ 同 superstep 并发
    outer = StateGraph(St)

    def fanout(state: St):
        return [Send(n, {"who": n}) for n in ROLES]

    for n in ROLES:
        async def run(state: St, _n: str = n):
            await inner_graph.ainvoke({"who": state["who"]})
            return {}   # 并发两支都写同一键会撞 last_value，本探针不判状态
        outer.add_node(n, run)
        outer.add_edge(n, END)
    outer.add_conditional_edges(START, fanout, {n: n for n in ROLES})
    g = outer.compile()

    seen = []
    async for ev in g.astream_events({"who": ROLES[0]}, version="v2"):
        if ev.get("event") != "on_chat_model_start":
            continue
        md = ev.get("metadata") or {}
        seen.append({"node": md.get("langgraph_node"), "ns": md.get("langgraph_checkpoint_ns"),
                     "keys": sorted(md.keys()), "tags": list(ev.get("tags") or []),
                     "all_md": md})
    assert seen, "一个 on_chat_model_start 都没抓到 ⇒ 探针压根没在测东西（判红不判绿）"
    assert len(seen) == 2, f"该有两笔调用（Alice/Bob 各一），实际 {len(seen)} 笔"

    def carries(s, name):
        return name in json.dumps([s["node"], s["ns"], s["tags"], s["keys"], s["all_md"]],
                                  ensure_ascii=False)

    lines = ["真图 astream_events 下 on_chat_model_start 的读数（两条 Send 并发）："]
    for s in seen:
        lines.append(f"  langgraph_node={s['node']!r}  ns={s['ns']!r}  tags={s['tags']}")
        lines.append(f"  metadata 全量={s['all_md']}")
    for n in ROLES:
        lines.append(f"  认得出 {n} 吗：node 等于名字={any(s['node'] == n for s in seen)}｜"
                     f"任一字段含名字={any(carries(s, n) for s in seen)}")
    ident = any(carries(s, n) for s in seen for n in ROLES)
    lines.append("  节点内 get_config() 的读数（与事件侧对照）：")
    for d in inside:
        lines.append(f"    who={d['node']!r} configurable.checkpoint_ns={d['configurable_ns']!r} "
                     f"metadata.langgraph_checkpoint_ns={d['md_ns']!r} "
                     f"metadata.checkpoint_ns={d['md_checkpoint_ns']!r} node={d['md_node']!r}")
    # 两侧同源的判法：都取「第一段冒号前的外层节点名」，逐个角色对上
    def head(v):
        return (v or "").split(":")[0].split("|")[0]
    pairs = {(head(d["configurable_ns"]), head(d["md_ns"])) for d in inside}
    lines.append(f"  节点内侧首段 vs 事件侧首段 = {sorted(pairs)} ⇒ 同一个串吗："
                 f"{all(a == b for a, b in pairs) and len({head(d['configurable_ns']) for d in inside}) == len(ROLES)}")
    lines.append("⇒ " + ("metadata 里**有**可读的外层身份 ⇒ C174 不必扩契约，落点可按它分槽（自推）"
                         if ident else
                         "metadata 里**没有**外层身份（只有内层节点名 think）⇒ 分槽必须让内核把角色传下来："
                         "跨 report/runtime/runner 的契约决定，摊拍不代拍"))
    text = "\n".join(lines)
    Path("E:/tmp/ch_c174_metadata.out").write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    srv = HTTPServer(("127.0.0.1", 0), Stub)
    BASE_URL = f"http://127.0.0.1:{srv.server_address[1]}/v1"
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    sys.exit(asyncio.run(main()))


if __name__ == "__main__":
    srv = HTTPServer(("127.0.0.1", 0), Stub)
    BASE_URL = f"http://127.0.0.1:{srv.server_address[1]}/v1"
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    sys.exit(asyncio.run(main()))
