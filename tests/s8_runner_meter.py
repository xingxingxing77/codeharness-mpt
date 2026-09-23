"""S8 门禁（第一件）：runner 计量合流——还债清单 #1 的账本这条链。

钉四件事（2026-09-15 真模型首跑实测的三个洞）：
  t1  add_usage 拿不到 usage 时**必须留 warning**（静默记 0 让一场会话几十次调用只有零星入账，
      现场无从分辨哪条路没回执）；两种标准形态（usage_metadata / token_usage）照常计。
  t2  重启后 resume 的账本从 sessions.json 快照续算，不从 0 起（否则终态覆盖历史用量）。
  t3  跑动中每一笔 on_chat_model_end 都把账本合进 store + 落盘 + SSE status 事件——
      「跑动中 GET /sessions/{sid} 的 cost 恒 0」到此为止。断言打在**同一个 CostManager 实例**上。
  t4  _ensure_graph 重建路径：传给 prepare_project 的 cost_manager 必须就是 runner.costs[sid] 那个
      实例（双账本防回归，docs 第 0 步的教训）。
（后续各加一格：**t7** span 的 t0/ft 计时；**t8** C12 跨币种分桶——两种币价各记各的、
 快照与 `Costs` 里不存在混币种合计字段、未知模型不入桶，并带「清空 CNY 名单」的对照组。）
  **t9** 再往真处走一步：两台本机端点各回一种币价的真 usage 回执、进同一本账——t8 用构造好的 AIMessage 证口径，t9 证「真跑一次调用之后两桶各有数、逐笔 cc 分得开」。）

跑法：
  cd /e/Codeharness && PYTHONPATH=/e/Codeharness PYTHONIOENCODING=utf-8 F:/anaconda/python.exe tests/s8_runner_meter.py
"""
import asyncio
import json
import tempfile
from pathlib import Path

from langchain_core.messages import AIMessage

from codeharness.provider.cost import CostManager
from server.runner import SessionRunner, _seeded_ledger
from server.events import SessionEventBus
from server.sessions import SessionStore, SessionStatus


def _ok(n, msg):
    print(f"✅ {n}: {msg}")


class _RecLogger:
    """替掉 codeharness.provider.cost.logger：断言打在「有没有留这条 warning」上。"""

    def __init__(self):
        self.warnings, self.infos = [], []

    def warning(self, m):
        self.warnings.append(str(m))

    def info(self, m):
        self.infos.append(str(m))


def t1_add_usage_visible():
    import codeharness.provider.cost as cost_mod
    saved, rec = cost_mod.logger, _RecLogger()
    cost_mod.logger = rec
    try:
        cm = CostManager()
        m1 = AIMessage(content="x")
        m1.usage_metadata = {"input_tokens": 120, "output_tokens": 30}
        cm.add_usage(m1, model="gpt-4o", tag="a")                    # 标准口径（流式也走这条）
        m2 = AIMessage(content="y", response_metadata={"token_usage": {"prompt_tokens": 7, "completion_tokens": 3}})
        cm.add_usage(m2, model="gpt-4o", tag="b")                    # OpenAI 原始口径
        m3 = AIMessage(content="z")
        cm.add_usage(m3, model="gpt-4o", tag="silent-leak")          # 两种都没有 → 必须留话
        assert cm.total_prompt_tokens == 127 and cm.total_completion_tokens == 33, cm.get_costs()
        assert any("silent-leak" in w for w in rec.warnings), f"零用量没留 warning: {rec.warnings}"
        _ok("t1", "add_usage：两形态照常计，零用量漏账留 warning（tag 可 grep）")
    finally:
        cost_mod.logger = saved


def t2_seeded_ledger():
    cm = _seeded_ledger({"total_prompt_tokens": 490, "total_completion_tokens": 21,
                         "cost_usd": 0.0123, "cost_cny": 0.045})
    assert (cm.total_prompt_tokens, cm.total_completion_tokens, cm.cost_usd, cm.cost_cny) \
        == (490, 21, 0.0123, 0.045)
    empty = _seeded_ledger({})
    assert empty.total_prompt_tokens == 0
    broken = _seeded_ledger({"total_prompt_tokens": None, "cost_usd": ""})
    assert (broken.total_prompt_tokens, broken.cost_usd) == (0, 0.0)
    # C12：老记录里那个「两种币价加在一个 float 上」的 total_cost 必须**不读**——
    # 读它等于把错口径继续带进新快照（用户拍板：不留兼容字段）。
    legacy = _seeded_ledger({"total_cost": 9.99})
    assert (legacy.cost_usd, legacy.cost_cny) == (0.0, 0.0), \
        f"C12 回归：又去读老的混币种 total_cost 了：{legacy.cost_usd} / {legacy.cost_cny}"
    _ok("t2", "_seeded_ledger 从落盘快照续算两桶，缺项/坏项退 0 不炸，且不回读混币种 total_cost")


async def _make_runner():
    tmp = Path(tempfile.mkdtemp())
    store = SessionStore(path=tmp / "sessions.json")
    bus = SessionEventBus()
    runner = SessionRunner(store, bus)
    s = store.create("计量冒烟", project_name="meter_proj")
    store.update(s.id, status=SessionStatus.running)
    return tmp, store, bus, runner, s


async def t3_midrun_sync():
    tmp, store, bus, runner, s = await _make_runner()
    cm = CostManager()
    runner.costs[s.id] = cm
    runner.projects[s.id] = "meter_proj"
    cm.update_cost(3000, 150, "gpt-4o")
    runner._translate(s.id, {"event": "on_chat_model_end",
                             "metadata": {"langgraph_node": "PM"}})   # 模拟网关完成一笔
    mk = [e for e in bus.history(s.id)
          if e.kind == "report" and e.name == "end_marker" and e.uuid == "stream-PM"]
    assert mk, "on_chat_model_end 不收口 stream-{node} 块——跑完打字机光标永远闪（S8 终验现形）"
    got = store.get(s.id).cost
    assert got["total_prompt_tokens"] == 3000 and got["total_completion_tokens"] == 150, got
    disk = json.loads((tmp / "sessions.json").read_text(encoding="utf-8"))
    assert [d for d in disk if d["id"] == s.id][0]["cost"]["total_prompt_tokens"] == 3000, "没落盘"
    evs = [e for e in bus.history(s.id) if e.kind == "status"]
    assert evs and evs[-1].value["cost"]["total_prompt_tokens"] == 3000, "SSE 里没有增量"
    assert evs[-1].value["status"] == "running", "usage 事件不得改状态"
    cm.update_cost(1000, 60, "gpt-4o")
    runner._translate(s.id, {"event": "on_chat_model_end"})
    assert store.get(s.id).cost["total_prompt_tokens"] == 4000       # 合流是累计不是覆盖
    runner.costs.pop(s.id)                                           # _forget 之后再来事件
    runner._translate(s.id, {"event": "on_chat_model_end"})          # 不许炸
    _ok("t3", "跑动中每笔 LLM 结束：GET/落盘/SSE 三头同步，账累计，散会后到迟事件不炸")


async def t4_ensure_graph_single_ledger():
    tmp, store, bus, runner, s = await _make_runner()
    store.update(s.id, status=SessionStatus.awaiting_human,
                 cost={"total_prompt_tokens": 490, "total_completion_tokens": 21,
                       "cost_usd": 0.05, "cost_cny": 0.3})
    captured = {}

    def fake_prepare(idea, project, agents=None, checkpointer=None, cost_manager=None, sop=None):
        captured["cost_manager"] = cost_manager
        return object(), {"configurable": {"thread_id": project}}, None

    async def fake_saver():
        return None

    import codeharness.team as team
    saved, runner._saver = team.prepare_project, fake_saver
    team.prepare_project = fake_prepare
    try:
        packed = await runner._ensure_graph(s.id)
        assert packed is not None
        cm = runner.costs[s.id]
        assert captured["cost_manager"] is cm, "双账本：传给图的实例 ≠ runner 手上那个"
        assert (cm.total_prompt_tokens, cm.total_completion_tokens, cm.cost_usd, cm.cost_cny) \
            == (490, 21, 0.05, 0.3), "重启路径没从快照续算两桶"
        cm.update_cost(10, 2, "gpt-4o")
        assert cm.total_prompt_tokens == 500                         # 续算后累加正确
    finally:
        team.prepare_project = saved
    _ok("t4", "_ensure_graph 重建路径：单一账本实例传进图，重启后从落盘快照续算")


def t5_lifespan_unwires_seams():
    """停机必须成对拆（冒烟复现实测：TestClient 出 with 后留着 aiosqlite 的
    `_connection_worker_thread`——checkpoint.close_all 的 docstring 写着「server 应在
    lifespan 关闭时调用它」却一直没接线；LogBridge 不 remove 则每次重启 lifespan 多挂一个
    sink，往已死的旧 bus 灌日志）。断言打在真实 lifespan 的进入/退出上。"""
    import tempfile
    from pathlib import Path
    from fastapi.testclient import TestClient
    from codeharness.environment import checkpoint as ck
    from codeharness.logs import logger

    import server.app as sa
    app = sa.create_app()
    n_sinks = len(logger._core.handlers)
    db = Path(tempfile.mkdtemp()) / "ck.db"
    with TestClient(app) as c:
        assert c.get("/api/health").json()["ok"] is True
        c.portal.call(ck.make_checkpointer, str(db))       # 模拟 runner 懒建后的缓存状态
        assert ck._cache, "前置条件不成立：连接缓存根本没建起来"
        assert len(logger._core.handlers) == n_sinks + 1, "lifespan 启动应恰好装一个 log sink"
    assert not ck._cache, "lifespan 关闭没调 close_all：worker 线程会被留在退出路上"
    assert len(logger._core.handlers) == n_sinks, "LogBridge 的 sink 没随 lifespan 拆掉"
    print("✅ t5: lifespan 停机成对拆（checkpointer 连接清零 + log sink 摘除）")


def t6_events_history_bounded():
    """回放必须走有界口（冒烟两场挂死的根因门禁：对 SSE 无界流做读完型 GET，
    TestClient 的 transport 会把应用跑到底——`while True` 永不返回）。
    sessions.json 指到 tmp，自测不污染生产会话表。"""
    import tempfile
    from pathlib import Path
    import server.sessions as ss
    keep, ss.SESSIONS_FILE = ss.SESSIONS_FILE, Path(tempfile.mkdtemp()) / "sessions.json"
    try:
        from fastapi.testclient import TestClient
        import server.app as sa
        app = sa.create_app()
        with TestClient(app) as c:
            sid = c.post("/api/sessions", json={"idea": "回放", "project_name": "s8replay"}).json()["id"]
            app.state.bus.publish(sid, kind="report", block="Thought", value="想", role="PM")
            app.state.bus.publish(sid, kind="status", value={"status": "running", "cost": {},
                                                             "message": "run completed"})
            # 双配置：redis 总线 publish 走 ring→flusher（异步落库），有 flush_now 就先刷干净再读
            if hasattr(app.state.bus, "flush_now"):
                c.portal.call(app.state.bus.flush_now)
            r = c.get(f"/api/sessions/{sid}/events/history?after=0")
            assert r.status_code == 200
            evs = r.json()["events"]
            assert [e["kind"] for e in evs] == ["status", "report", "status"], evs   # seq1=created
            # 游标取**真实事件的 cursor**（redis 模式 seq≈1.79e18 过 JS JSON.parse 会舍入，
            # 所以线上契约是定宽补零字符串游标；断言仍不钉字面值，只钉游标语义）
            assert all(len(e["cursor"]) == len(evs[0]["cursor"]) for e in evs), "cursor 必须定宽"
            assert [e["cursor"] for e in evs] == sorted(e["cursor"] for e in evs), "cursor 字典序非单调"
            tail = c.get(f"/api/sessions/{sid}/events/history?after={evs[1]['cursor']}").json()["events"]
            assert len(tail) == 1 and tail[0]["cursor"] == evs[2]["cursor"], tail
            # 老口径的裸数字 after 仍要能用（历史 URL、外部脚本）
            assert len(c.get(f"/api/sessions/{sid}/events/history?after=0").json()["events"]) == 3
            assert c.get("/api/sessions/nope/events/history").status_code == 404
    finally:
        ss.SESSIONS_FILE = keep
    print("✅ t6: /events/history 有界回放 + seq 游标 + 404（SSE 无界流不再是读完型的口）")


async def t7_span_timing():
    """span 必须带派发时刻与首 token 时刻：尾行 TTFT、StatsLine、台账耗时列全吃这两个字段。
    没采到 run_id 的老路（structured 输出不走打字机）落 null 而不是 0——前端据此不显示读数，
    显示一个恒为 0 的 TTFT 比缺数更坏。"""
    tmp, store, bus, runner, s = await _make_runner()
    rows = []
    runner.trace = type("T", (), {"record": lambda self, sid, span: rows.append(span)})()
    runner.costs[s.id] = CostManager()
    runner._translate(s.id, {"event": "on_chat_model_start", "run_id": "r1"})
    runner._translate(s.id, {"event": "on_chat_model_stream", "run_id": "r1",
                             "metadata": {"langgraph_node": "PM"},
                             "data": {"chunk": type("C", (), {"content": "hi"})()}})
    runner._translate(s.id, {"event": "on_chat_model_end", "run_id": "r1",
                             "metadata": {"langgraph_node": "PM"}})
    assert rows, "trace 注入了却没落 span"
    got = rows[-1]
    assert got["t0"] and got["ft"] and got["ft"] >= got["t0"], got
    assert (s.id, "r1") not in runner._call_t0, "收口后在途表没清，长跑会一直攒"
    runner._translate(s.id, {"event": "on_chat_model_end", "metadata": {"langgraph_node": "PM"}})
    assert rows[-1]["t0"] is None and rows[-1]["ft"] is None, f"老路必须落 null：{rows[-1]}"
    _ok("t7", "span 带 t0/ft 且 ft≥t0，收口清在途表；无 run_id 的老路落 null 而不是 0")


def t8_two_currency_buckets():
    """C12 判据本体：同场会话混两种币价的模型时，两桶各记各的、**不相加**。

    ① 两个桶分别对上单价算出的数（USD 行 gpt-4o、CNY 行 step-3.5-flash，各 1000/500 token）；
    ② `Costs` 里不存在任何"合计"字段（有合计就等于把混加换了个名字留下）；
    ③ 快照 dict（GET 的 cost、SSE status 里那份、落盘的 `cost` 列）**两桶都在且没有 total_cost 键**；
    ④ 未登记模型不进任何桶（未知模型不许冒充 USD，那是第二种静默错账）；
    ⑤ **对照组**：把 CNY 名单换成空集，step-3.5-flash 就必须落进 USD 桶——
       证明分桶真按 `CNY_MODELS` 走，而不是代码里写死了两个名字。
    """
    from codeharness.provider.cost import Costs
    from codeharness.provider.token_costs import CNY_MODELS, TOKEN_COSTS
    from server.runner import cost_snapshot

    pt, ct = 1000, 500
    usd_expect = (pt * TOKEN_COSTS["gpt-4o"]["prompt"] + ct * TOKEN_COSTS["gpt-4o"]["completion"]) / 1000
    cny_expect = (pt * TOKEN_COSTS["step-3.5-flash"]["prompt"]
                  + ct * TOKEN_COSTS["step-3.5-flash"]["completion"]) / 1000

    cm = CostManager()
    # usage_metadata 走「构造后赋值」（与 t1 同法）：AIMessage 的 pydantic 校验要求
    # total_tokens 等一整套字段，直接传 dict 进构造器会先被校验拦掉。
    m_usd, m_cny = AIMessage(content="a"), AIMessage(content="b")
    m_usd.usage_metadata = {"input_tokens": pt, "output_tokens": ct}
    m_cny.usage_metadata = {"input_tokens": pt, "output_tokens": ct}
    cm.add_usage(m_usd, model="gpt-4o", tag="usd")
    cm.add_usage(m_cny, model="step-3.5-flash", tag="cny")
    c = cm.get_costs()
    assert abs(c.cost_usd - usd_expect) < 1e-12 and abs(c.cost_cny - cny_expect) < 1e-12, \
        f"两桶没各记各的：{c}"
    assert c.cost_usd != c.cost_cny, "两组单价恰好相等，这条判据分不出桶（换个模型或 token 数）"

    assert "total_cost" not in Costs._fields and set(Costs._fields) == {
        "total_prompt_tokens", "total_completion_tokens", "cost_usd", "cost_cny"}, Costs._fields
    snap = cost_snapshot(cm)
    # 键集仍然**整颗钉死**（这条防的是漂移，不是"多两个键就放宽到不检"）。
    # 后两个是 T4-③ 的「无效调用」计数：住在 manager 上、随快照持久化、被列表出口带出。
    assert set(snap) == {"cost_usd", "cost_cny", "total_prompt_tokens", "total_completion_tokens",
                          "truncated_calls", "unknown_command_calls", "empty_output_calls"}, snap
    assert "total_cost" not in snap, f"快照里又长出合计字段（C12 删的就是它）：{snap}"
    assert (snap["truncated_calls"], snap["unknown_command_calls"], snap["empty_output_calls"]) == (0, 0, 0), \
        f"这一格没制造无效调用，计数却非 0（那就是恒亮的告警）：{snap}"

    # C19：正文空＝这一发花了钱没产出（真云端实测最贵那发 ¥0.914、24.5 万 ct、正文是空串，
    # 而它既不进截断也不进未知命令）。三格一起钉：空的要计上、**只调工具不说话的不算浪费**（阳性对照）、
    # 非空的不许被计。
    ec = CostManager()
    e_empty = AIMessage(content="")
    e_empty.usage_metadata = {"input_tokens": 2400, "output_tokens": 500}
    ec.add_usage(e_empty, model="step-3.5-flash", tag="c19-empty")
    assert ec.empty_output_calls == 1, f"空正文没计上账：{ec.empty_output_calls}"
    e_tool = AIMessage(content="", tool_calls=[{"name": "read_file", "args": {"path": "a"},
                                                "id": "c1", "type": "tool_call"}])
    e_tool.usage_metadata = {"input_tokens": 2400, "output_tokens": 500}
    ec.add_usage(e_tool, model="step-3.5-flash", tag="c19-toolonly")
    assert ec.empty_output_calls == 1, \
        f"把「只调工具、没说话」也计成浪费了（那是合法的一轮）：{ec.empty_output_calls}"
    e_text = AIMessage(content="正常一句话")
    e_text.usage_metadata = {"input_tokens": 20, "output_tokens": 8}
    ec.add_usage(e_text, model="step-3.5-flash", tag="c19-text")
    assert ec.empty_output_calls == 1, f"非空正文也被计数 ⇒ 判据在数错东西：{ec.empty_output_calls}"
    assert cost_snapshot(ec)["empty_output_calls"] == 1, \
        f"这一笔没被快照带出（用量页那个格子吃的是快照）：{cost_snapshot(ec)}"

    unknown = CostManager()
    unknown.update_cost(10, 10, "no-such-model-xyz")
    assert (unknown.cost_usd, unknown.cost_cny) == (0.0, 0.0), \
        f"未知模型污染了成本桶：{unknown.get_costs()}"
    assert unknown.total_prompt_tokens == 10, "未知模型该记 token（用量可见）只是不算钱"

    assert "step-3.5-flash" in CNY_MODELS and CNY_MODELS <= set(TOKEN_COSTS), \
        "名单里有价表没有的键 = 那一行永远不进桶（静默零记账）"
    ctrl = CostManager(cny_models=frozenset())        # 对照组：名单清空
    ctrl.update_cost(pt, ct, "step-3.5-flash")
    assert ctrl.cost_cny == 0 and abs(ctrl.cost_usd - cny_expect) < 1e-12, \
        f"对照组不成立（说明分桶不是按名单走的）：{ctrl.get_costs()}"
    _ok("t8", f"C12 分桶：$ {c.cost_usd:.6f} / ¥ {c.cost_cny:.6f} 各记各的；"
              "Costs 与快照都没有混币种合计字段；未知模型不入桶；对照组证明确实按 CNY_MODELS 分流")


def t9_two_endpoints_one_ledger():
    """C12 的那半格正向读数：**同场两台端点、两种币价、各拿真 HTTP 回执进同一本账**。

    t8 钉的是分桶口径与字段形状（喂的是构造好的 AIMessage），它证不了「真跑一次调用之后
    两桶是不是各有数」。这一格补的就是那半句——两台本机 OpenAI 兼容桩（各自 model 名分别
    落在价表的 USD 行与 CNY 行），走真 `LLMGateway.ainvoke` → 真 usage 回执 → 同一个 CostManager：
      ① 两桶都 > 0 且各等于按价表算出的数；
      ② 逐笔留痕 `records[]` 里两笔的 `cc` 分别是 USD / CNY（C12 根因原话是「只有合计的话，
         混币种在数据里就看不见」——逐笔 cc 就是为这一句留的）；
      ③ 特异性对照：两台都回同一张 USD 行的模型名时，CNY 桶必须**保持 0**
         （证明币种是按回执里的 model 真算的，不是代码里写死了两个名字）；
      ④ 快照 `cost_snapshot()` 两桶都在、且没有 `total_cost` 键。
    零花费、不碰任何真厂商端点。**它替掉的是「等第二个厂商 key」这个借口**，
    但真厂商 usage 字段的差异（例如不回的、少字段的）仍属未验，写在 PLAN §4 C12 行末。
    """
    PT, CT = 1000, 500          # 与 t8 同量：按价表算出来的数要能手算核对
    from codeharness.provider.token_costs import TOKEN_COSTS
    import json as _j
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    from codeharness.configs.llm_config import LLMConfig, LLMType
    from codeharness.provider.gateway import LLMGateway
    from server.runner import cost_snapshot

    class _Stub(BaseHTTPRequestHandler):
        model_name = "unset"

        def do_POST(self):  # noqa: N802
            n = int(self.headers.get("content-length") or 0)
            self.rfile.read(n)
            body = {"id": "chatcmpl-t9", "object": "chat.completion", "created": 1790000000,
                    "model": type(self).model_name,
                    "choices": [{"index": 0, "finish_reason": "stop",
                                 "message": {"role": "assistant", "content": "好的"}}],
                    "usage": {"prompt_tokens": PT, "completion_tokens": CT,
                              "total_tokens": PT + CT}}
            out = _j.dumps(body, ensure_ascii=False).encode()
            self.send_response(200)
            self.send_header("Connection", "close")
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(out)))
            self.end_headers()
            self.wfile.write(out)

        def log_message(self, *a):
            pass

    def serve(model_name):
        # 端口 0：三台桩互不抢，也不跟别人抢；model 名决定这台桩「是哪个厂商的行」
        srv = HTTPServer(("127.0.0.1", 0), type("S", (_Stub,), {"model_name": model_name}))
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        return srv

    def price(model):
        r = TOKEN_COSTS[model]
        return (PT * r["prompt"] + CT * r["completion"]) / 1000

    async def ask(cm, port, model):
        cfg = LLMConfig(api_type=LLMType.OPENAI, base_url=f"http://127.0.0.1:{port}/v1",
                        api_key="stub", model=model, stream=False)
        await LLMGateway(cfg=cfg, cost_manager=cm).ainvoke("问一句")

    a, b, control = serve("gpt-4o"), serve("step-3.5-flash"), serve("gpt-4o")
    try:
        cm = CostManager()
        asyncio.run(ask(cm, a.server_address[1], "gpt-4o"))
        asyncio.run(ask(cm, b.server_address[1], "step-3.5-flash"))
        assert (cm.total_prompt_tokens, cm.total_completion_tokens) == (2 * PT, 2 * CT), \
            f"两台端点的真回执没都进同一本账：pt={cm.total_prompt_tokens} ct={cm.total_completion_tokens}"
        assert abs(cm.cost_usd - price("gpt-4o")) < 1e-9, \
            f"USD 桶对不上价表（{cm.cost_usd} ≠ 按 gpt-4o 算的 {price('gpt-4o')}）"
        assert abs(cm.cost_cny - price("step-3.5-flash")) < 1e-9, \
            f"CNY 桶对不上价表（{cm.cost_cny} ≠ 按 step-3.5-flash 算的 {price('step-3.5-flash')}）"
        assert {r["cc"] for r in cm.records} == {"USD", "CNY"}, \
            f"逐笔留痕没把两种币价分开：{cm.records}"

        cm2 = CostManager()
        asyncio.run(ask(cm2, a.server_address[1], "gpt-4o"))
        asyncio.run(ask(cm2, control.server_address[1], "gpt-4o"))
        assert cm2.cost_cny == 0 and cm2.cost_usd > 0, \
            f"对照失效：两台都回 USD 行的模型名，CNY 桶却还是 {cm2.cost_cny}——币种不是按回执算的"

        snap = cost_snapshot(cm)
        assert {"cost_usd", "cost_cny"} <= set(snap) and "total_cost" not in snap, \
            f"快照又出现合计字段或少了某一桶：{sorted(snap)}"
        assert snap["cost_usd"] > 0 and snap["cost_cny"] > 0, f"快照两桶没都带出去：{snap}"
        _ok("t9", f"两台端点真回执进同一本账：$ {cm.cost_usd:.6f} / ¥ {cm.cost_cny:.6f}、"
                  "逐笔 cc 分列 USD/CNY、快照两桶都在且无 total_cost；"
                  "对照组（两台同回 USD 行名字）CNY 保持 0")
    finally:
        for srv in (a, b, control):
            srv.shutdown()


def main():
    t1_add_usage_visible()
    t2_seeded_ledger()
    asyncio.run(t3_midrun_sync())
    asyncio.run(t4_ensure_graph_single_ledger())
    t5_lifespan_unwires_seams()
    t6_events_history_bounded()
    asyncio.run(t7_span_timing())
    t9_two_endpoints_one_ledger()
    t8_two_currency_buckets()
    print("\ns8_runner_meter: 9/9 全绿")


if __name__ == "__main__":
    main()
