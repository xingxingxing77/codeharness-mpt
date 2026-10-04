"""流式落地的离线判据工装（零花费：本机 OpenAI 兼容桩当 LLM，不发任何真请求）。
跑法：cd /e/Codeharness && PYTHONPATH=/e/Codeharness PYTHONIOENCODING=utf-8 \\
      REDIS__DB=15 LANGFUSE__ENABLED=0 NO_PROXY=127.0.0.1,localhost,::1 \\
      F:/anaconda/python.exe -B tests/manual_stream_landing.py

为什么要有它：`s8_runner_meter` t12 用的是**手搭的合成事件**，形状是我照印象写的——结果形状猜错两次
（先扁平列表、再批式嵌套列表），真会话里 `_prompt_text` 抽到空串、回显排除**静默空转**，判据两次都绿。
把假修好戳穿的是两场付费真跑。这台桩走的是**真 `astream_events` + 真 `with_structured_output`**，
所以事件的形状、`live`/`content` 两条通道、落点登记、回显计数全都是现取的，不用猜也不用花钱。
它同时是「事件形状不许凭印象钉进判据」这条教训的可复跑凭证。
⑥ 那一格还顺手挡掉另一类空转：名单里打错一个字段名不会报错，只会让该字段静默落回名单外——
   所以对照用的是「没声明名单」那一跑，它必须把 `programming_language` 打回流里。

10-05 加 ⑧⑨⑩ 三格（流式收口批 C172/C173 的取证，同样零花费）：⑧ 真 cancel 落在飞行中的那一笔上，
数「有没有人给兜底行收口」——前端三处只认 `b.closed`（Think 行扫光、Docs 的 mermaid 水合、轮尾行），
所以「没人收」是可见缺陷而不是过渡态；⑨ 同一节点第二笔块外调用重开兜底行，量接缝有没有分隔；
⑩ 落点表每会话只有一个槽，量「一块还开着时它的第二笔投进了谁」。三格都带**前提自证**：桩吐太快、
第二笔没重开行、压根没发逐片，一律判红——数到一个像是结论的读数，前提没成立就是假绿。
"""
import asyncio
import json
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "logs"))

ECHO = "做一个极小的静态网页，用一段话解释二分查找是什么。不要写测试，不要部署。"
PROSE = "以极简静态网页为载体，用一段通俗准确的表述向读者清晰解释二分查找的核心原理与适用场景"
GOALS = [PROSE, PROSE + "，第二条同样足够长以便越过 24 字的门槛"]
SCHEMA_OUT = {
    "ZeroThought": {"thought": PROSE, "commands": [{"command_name": "end", "args": {}}]},
    "TaskList": {"task_list": [
        {"filename": "src/index.html", "task_id": "T1",
         "instruction": "在首页文件里写这段解释：先讲清比较的对象是什么，再讲每一步为什么能砍掉一半候选。"},
        {"filename": "src/constants/explanation.js", "task_id": "T2",
         "instruction": "样式与脚本都内联在这一个文件里，不引入任何外部字体、图标、框架运行时或构建步骤。"}],
        "required_packages": ["本任务不需要任何第三方依赖，标准库与浏览器原生能力即可覆盖全部需求。"],
        "shared_knowledge": "产物必须是纯静态文件：浏览器直接打开即用，不依赖后端服务、打包器或第三方脚本。"},
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


# ⑧ 要「这一笔还在飞行中就被 cancel」，而桩一口气吐完的话 on_chat_model_end 自己就到了 ⇒ 假绿。
# 逐块之间垫一点延时，让流持续 ~1 秒；只有 ⑧ 那两格用它，其余格保持原来的快档。
STUB_DELAY = 0.0


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
            if STUB_DELAY:
                time.sleep(STUB_DELAY)
        self.wfile.write(b"0\r\n\r\n")

    def log_message(self, *a):
        pass


def _env():
    """一份真的 store/bus/runner + 一场建好的会话（⑨⑩ 要两笔调用共用同一场，不能各跑各的）。"""
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
    return store, bus, runner, s


async def run_case(name, schema_cls, msgs, open_block_uuid=None, prose_fields=None, block="Docs",
                   env=None, forget=True):
    """跑一笔真 structured 调用，把事件灌进真 `SessionRunner._translate`，返回总线上的块序列。"""
    from langchain_openai import ChatOpenAI

    store, bus, runner, s = env or _env()

    if open_block_uuid:            # 模拟内核 `async with docs_block(...)` / `task_block(...)`：先开一块
        val = {"type": "prd"} if block == "Docs" else {"type": "tasks"}
        if prose_fields:           # 真开块时这一格由 `report._meta_with_prose` 填（schema 的 ClassVar）
            val["prose_fields"] = list(prose_fields)
        runner._make_sink(s.id)({"block": block, "uuid": open_block_uuid, "name": "meta",
                                 "value": val, "role": "PM"})

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
        runner._make_sink(s.id)({"block": block, "uuid": open_block_uuid, "name": "end_marker",
                                 "value": None, "role": "PM"})
    seq = [(e.name, str(e.uuid)[:8], len(str(e.value or ""))) for e in bus.history(s.id)
           if e.kind == "report"]
    live = "".join(str(e.value or "") for e in bus.history(s.id)
                   if e.kind == "report" and e.name == "live")
    if forget:
        runner._forget(s.id, terminal=True)
    print(f"\n[{name}] start.input 的类型 = {shapes} | 总线事件序列 = {seq}")
    print(f"  逐片 {len(live)} 字 = {live[:70]!r}")
    return live, seq, runner


async def main():
    from langchain_core.messages import HumanMessage, SystemMessage
    from codeharness.actions.write_prd import PRDOutput
    from codeharness.roles.role_zero import ZeroThought
    from server.runner import _prompt_text

    global BASE_URL, STUB_DELAY

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

    # ⑥ 白名单门控：块声明 `prose_fields` 后，名单外那个 30 字的 `programming_language` 值
    #    （"Vite, React, MUI, Tailwind CSS"，长过 MIN_PROSE=24）不再进流；名单内的照旧。
    #    阳性对照＝同一份 payload、同一块、meta **不带**名单 ⇒ 那一行必须回到流里，
    #    否则「没发」到底是门控挡掉的还是门槛挡掉的，判据分不出来。
    gated, seqg, _ = await run_case("白名单门控", PRDOutput, msgs, open_block_uuid="doc-9",
                                    prose_fields=PRDOutput.prose_fields)
    free, seqf, _ = await run_case("同一份 payload、没声明名单", PRDOutput, msgs, open_block_uuid="doc-8")
    STACK = "Vite, React, MUI, Tailwind CSS"
    if STACK in gated:
        fails.append("⑥ 名单外的字段还在流里（门控没接上：名单没进 meta／没登记／没传给抽取器）")
    if PROSE not in gated:
        fails.append("⑥ 名单内的字段被一起挡掉了（门控挑错了对象）")
    if GOALS[1] not in gated:
        # `product_goals` 在名单里且有两项长文本：值闭合后键名失焦那一类错只在这里看得见
        fails.append("⑥ 名单内列表的第二项没出来（值闭合后键名失焦成上一个串的文字）")
    if STACK not in free:
        fails.append(f"⑥ 阳性对照失守：没声明名单时{STACK!r}本该出现（说明门槛或桩自己变了）")
    if not any(n == "live" and u == "doc-9" for n, u, _ in seqg):
        fails.append("⑥ 门控过的逐片没落进声明名单的那块")

    # ⑦ Task 块（第七件）：内核 `task_block` 现在开块发 meta，名单是 TaskList 那份 ClassVar。
    #     这里照那份名单开一块，走真 `astream_events` + 真 `_translate`：清单里每条的 `instruction` 与
    #     `shared_knowledge` 该进流，而 `filename`/`task_id`/`required_packages` 该一个字都不进。
    from codeharness.actions.project_management import TaskList
    tlive, tseq, _ = await run_case("Task 块（名单来自 TaskList.prose_fields）", TaskList,
                                    [SystemMessage(content="按 schema 输出任务清单"),
                                     HumanMessage(content="把那个静态页面的需求拆成两个文件级任务。")],
                                    open_block_uuid="task-1", prose_fields=TaskList.prose_fields, block="Task")
    if "src/constants/explanation.js" in tlive or "第三方依赖" in tlive:
        fails.append("⑦ Task 块里混进了标识符（filename / required_packages）")
    if "砍掉一半候选" not in tlive or "纯静态文件" not in tlive:
        fails.append("⑦ Task 名单内的 instruction（嵌套那一层）或 shared_knowledge 没进流")
    if not any(n == "live" and u == "task-1" for n, u, _ in tseq):
        fails.append("⑦ 逐片没落进开着的 Task 块（那条开块 meta 没把块登记进落点表）")

    # ============ 10-05 取证三格（C172/C173/落点单槽）：形状全由真 astream 现取，不猜 ============

    # ⑨ 同一节点跑第二笔**块外**调用 ⇒ 兜底行被重开。前端正文是 `live.join('')`，而换行分隔只在
    #    同一次 `_ProseStream` 内部给（emitted 每笔调用新建）⇒ 第二笔第一句直接接在第一笔末句后面。
    st9, bus9, rn9, s9 = _env()
    env9 = (st9, bus9, rn9, s9)
    msgs9 = [SystemMessage(content="按 schema 输出"), HumanMessage(content="用两三句话解释二分查找，说完结束。")]
    la, seqa, _ = await run_case("⑨a 兜底行第一笔", ZeroThought, msgs9, env=env9, forget=False)
    lb, seqb, _ = await run_case("⑨b 同一节点第二笔（行被重开）", ZeroThought, msgs9, env=env9, forget=False)
    own_b = lb[len(la):]
    n_meta = len([1 for n, u, _ in seqb if n == "meta" and u.startswith("stream-")])
    n_end = len([1 for n, u, _ in seqb if n == "end_marker" and u.startswith("stream-")])
    print(f"\n[⑨ 重开] 兜底行 meta={n_meta} 次、end_marker={n_end} 次 | 接缝 {la[-12:]!r} ⇄ {own_b[:12]!r}")
    if n_meta < 2 or not own_b:
        fails.append("⑨ 前提没成立：第二笔没重开行或一个字没发，这格测不到分隔符（判红，不留假读数）")
    elif la.endswith("\n") or own_b.startswith("\n"):
        print("  读数：接缝有换行 ⇒ 修后形状（这条要是变成「零分隔」就是 C173 复发）")
    else:
        print("  读数：接缝没有任何分隔 ⇒ 两笔的话粘成一句（C173 的改前形状）")

    # ⑩ 落点表是**每会话一个槽**（runner.py:334），start 时刻的快照取的就是那个槽（:1148）。
    #     形状：Alice 的 Thought 块开着 → 她的第一笔落自己那块；Bob 的 Docs 块此时开起来（顶掉槽）→
    #     Alice 的第二笔（她那块**还没收口**，`_think` 块内本就有 structured/aask/repair 三笔）
    #     快照到的就是 Bob 的块 ⇒ 她的话进他的卡。可观察的是「落点 uuid」，归属靠 blk-A 无收口标记自证。
    from collections import Counter
    stX, busX, rnX, sX = _env()
    envX = (stX, busX, rnX, sX)
    sinkX = rnX._make_sink(sX.id)
    sinkX({"block": "Thought", "uuid": "blk-A", "name": "meta", "value": {"type": "react"}, "role": "Alice"})
    _, seqX1, _ = await run_case("⑩1 Alice 第一笔（槽=A 那块）", ZeroThought, msgs9, env=envX, forget=False)
    sinkX({"block": "Docs", "uuid": "blk-B", "name": "meta", "value": {"type": "prd"}, "role": "Bob"})
    _, seqX2, _ = await run_case("⑩2 Alice 第二笔（她那块还开着，槽已被 Bob 顶）", ZeroThought, msgs9,
                                 env=envX, forget=False)
    own_live = (Counter(u for n, u, _ in seqX2 if n == "live") - Counter(u for n, u, _ in seqX1 if n == "live"))
    closedA = [u for n, u, _ in seqX2 if n == "end_marker" and u == "blk-A"]
    print(f"\n[⑩ 落点单槽] 第二笔逐片落点={dict(own_live)} | 此刻 blk-A 的收口标记={closedA}")
    if not own_live:
        fails.append("⑩ 前提没成立：第二笔压根没发逐片，这格测不到落点")
    elif not closedA and set(own_live) == {"blk-B"}:
        print("  读数：一块**还开着**的块，它的第二笔投进了后开的那块 ⇒ 串块现证（立 C174）")
    else:
        print("  读数：这一形状没出现 ⇒ 落点单槽今天不构成串块，不立号")

    # ⑧ 这一笔还在飞行中就被 cancel（C172 的核心前提：langchain 会不会补发 on_chat_model_end）。
    #     不成立就判红——数到「有收口」如果是桩自己吐完了，那这格测的不是取消路径。
    from langchain_openai import ChatOpenAI
    STUB_DELAY = 0.05
    st8, bus8, rn8, s8 = _env()
    mm = ChatOpenAI(model="step-3.5-flash", api_key="stub", base_url=BASE_URL, streaming=True)
    rr = mm.with_structured_output(ZeroThought.model_json_schema(), include_raw=True,
                                   method="json_schema", strict=True)
    seen = []

    async def consume():
        async for ev in rr.astream_events(msgs9, version="v2"):
            seen.append(ev.get("event", ""))
            rn8._translate(s8.id, ev)

    t8 = asyncio.get_running_loop().create_task(consume())
    for _ in range(2000):
        if any(e == "on_chat_model_stream" for e in seen):
            break
        await asyncio.sleep(0.01)
    t8.cancel()
    try:
        await t8
    except asyncio.CancelledError:
        pass
    STUB_DELAY = 0.0
    ev8 = [(e.name, str(e.uuid)[:10]) for e in bus8.history(s8.id) if e.kind == "report"]
    print(f"\n[⑧ 取消那一笔] 见过的事件名={sorted(set(seen))} | 总线 report={ev8}")
    if "on_chat_model_end" in seen:
        fails.append("⑧ 前提没成立：桩吐完了（on_chat_model_end 自己到了），这格没测到取消路径＝假绿")
    elif not any(n == "meta" and u.startswith("stream-") for n, u in ev8):
        fails.append("⑧ 前提没成立：取消前没建出兜底行，这格测不到收口")
    else:
        mid = [u for n, u in ev8 if n == "end_marker" and u.startswith("stream-")]
        rn8._forget(s8.id, terminal=True)      # 散会：五处终态唯一的汇聚口，修法就挂这儿
        ev8b = [(e.name, str(e.uuid)[:10]) for e in bus8.history(s8.id) if e.kind == "report"]
        swept = [u for n, u in ev8b if n == "end_marker" and u.startswith("stream-")]
        print(f"  读数：cancel 当场收口标记={mid or '无（预期：cancel 走不到 on_chat_model_end）'}"
              f" | _forget(terminal=True) 之后={swept or '还是没有'}")
        if not swept:
            fails.append("⑧ C172 复发：散会没把还开着的兜底行收掉 ⇒ 前端那行停在 running 扫光、整轮没有轮尾行")

    print("\n" + ("\n".join(f"❌ {f}" for f in fails) if fails else "✅ 十条全过（零花费）"))
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
