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
from server.runner import SessionRunner, _seeded_ledger, cost_snapshot
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
    # C78（09-28 审查批）：三个观测计数也要从落盘快照播种——它们在 `cost_snapshot` 里是被持久化、
    # 被 `/api/sessions` 带出去的字段（C19 的「无效调用」观测全靠它们）。不播种的后果不是「少个读数」，
    # 而是**重启后 resume 老会话时终态快照把历史值覆盖成 0**（0 是合法读数，看不出是丢的）。
    c78 = _seeded_ledger({"truncated_calls": 3, "unknown_command_calls": 2, "empty_output_calls": 1})
    assert (c78.truncated_calls, c78.unknown_command_calls, c78.empty_output_calls) == (3, 2, 1), \
        f"C78 回归：三个观测计数没被播种（truncated={c78.truncated_calls} " \
        f"unknown={c78.unknown_command_calls} empty={c78.empty_output_calls}）"
    assert cost_snapshot(c78)["truncated_calls"] == 3 and cost_snapshot(c78)["empty_output_calls"] == 1, \
        "C78：播种完的快照没带出这三个计数（持久化出口那半也没接上）"
    no_keys = _seeded_ledger({"total_prompt_tokens": 7})
    assert (no_keys.truncated_calls, no_keys.unknown_command_calls, no_keys.empty_output_calls) == (0, 0, 0), \
        "C78：老记录（没有这三个键）该退 0，不该炸也不该编数"
    # R1（09-28 普查批）：召回链那三个观测计数同规播种 + 同规带出。漏一个的后果与 C78 一模一样——
    # 重启后 resume 老会话，终态快照把「这场召不回过几次」静默覆盖成 0，而 0 是合法读数。
    r1 = _seeded_ledger({"recall_failures": 299, "recall_zero_hits": 12, "recall_returned": 340})
    assert (r1.recall_failures, r1.recall_zero_hits, r1.recall_returned) == (299, 12, 340), \
        f"R1 回归：召回三笔没被播种（{r1.recall_failures}/{r1.recall_zero_hits}/{r1.recall_returned}）"
    snap = cost_snapshot(r1)
    assert (snap["recall_failures"], snap["recall_zero_hits"], snap["recall_returned"]) == (299, 12, 340), \
        f"R1 回归：播种完的快照没把这三笔带出去（持久化出口那半没接上）：{snap}"
    r1_missing = _seeded_ledger({"total_prompt_tokens": 7})
    assert (r1_missing.recall_failures, r1_missing.recall_zero_hits, r1_missing.recall_returned) == (0, 0, 0), \
        "R1：老记录没有这三个键 ⇒ 退 0，不炸也不编数"
    # R4：写腿那两笔同规播种与带出（点数与次数成对，见 cost.py 的字段注释）
    r4 = _seeded_ledger({"overflow_failed": 3, "overflow_written": 48})
    assert (r4.overflow_failed, r4.overflow_written) == (3, 48), \
        f"R4 回归：写腿两笔没被播种（{r4.overflow_failed}/{r4.overflow_written}）"
    snap4 = cost_snapshot(r4)
    assert (snap4["overflow_failed"], snap4["overflow_written"]) == (3, 48), \
        f"R4 回归：写腿两笔没被快照带出：{snap4}"
    # C123（10-02 复审批）：C122 的第四笔「无效调用」同规播种与带出。C122 落地时漏了本套件，
    # 播种那半（runner.py `_seeded_ledger`）至今零判据——删掉那一行，重启 resume 就把
    # 「args 违例笔数」静默归零，而 0 是合法读数看不出丢，正是 C78 修的那个形状。
    c122 = _seeded_ledger({"invalid_args_calls": 5})
    assert c122.invalid_args_calls == 5, \
        f"C122 回归：invalid_args_calls 没被播种（{c122.invalid_args_calls}）"
    assert cost_snapshot(c122)["invalid_args_calls"] == 5, "C122 回归：播种完的快照没把这笔带出去"
    c122_missing = _seeded_ledger({"total_prompt_tokens": 7})
    assert c122_missing.invalid_args_calls == 0, "C122：老记录没有这个键 ⇒ 退 0，不炸也不编数"
    _ok("t2", "_seeded_ledger 从落盘快照续算两桶 + 九个观测计数（C78 三笔 + R1 召回三笔 + R4 写腿两笔 + C122 一笔），"
              "缺项/坏项退 0 不炸，且不回读混币种 total_cost")


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


async def t11_park_keeps_the_live_ledger():
    """B1：停在待人工处（`_park`）时**账本必须还读得到**。

    `_park` 走 `_forget(sid, terminal=False)`，而 park 的约定恰恰是「图还活着、断点已落、
    等 resume」——图里那个 gateway 仍持着 `_prepare` 建的那**同一个** CostManager 继续累计。
    旧写法在 `_forget` 里无条件 `costs.pop` ⇒ runner 从此刻起读不到这本活账：`_sync_cost` /
    `_trace_span` 拿 `cm is None` 静默 return、`_publish_status` 发 `"cost": {}`（前端
    `if (v.cost)` 里 `{}` 是真值 ⇒ 顶栏被覆盖成 0，刷新又跳回批准前那个数）、`_publish_max_tokens`
    读同一本账连截断提示一起丢。判据三格 + 阳性对照：
      ① park 之后 `sid in runner.costs` **且是图里那个实例**（不是重建出来的第二本）；
      ② park 之后的 `_sync_cost` 仍真把账写进 store、`_publish_status` 发出的是真快照而不是空 dict；
      ③ 阳性对照：`terminal=True`（散会）照旧清干净——「不清」不等于「永不回收」。
    """
    tmp, store, bus, runner, s = await _make_runner()
    cm = CostManager()
    runner.costs[s.id] = cm
    runner.projects[s.id] = "meter_proj"
    runner.graphs[s.id] = (object(), {"configurable": {}})      # park 的前置：图还在
    cm.update_cost(700, 40, "gpt-4o")
    runner._park(s.id, {"question": "要哪个方案？"})              # terminal=False

    assert s.id in runner.costs, \
        "停在待人工处时账本被 _forget 清了 ⇒ 图里那本活账 runner 再也读不到（B1 复发）"
    assert runner.costs[s.id] is cm, "留下的不是图里那个实例（那就是第二本账，恒 0 的根因）"
    assert runner.graphs.get(s.id), "park 的约定是留图供 resume，这里被清了"

    runner._sync_cost(s.id)                                     # ① 读得到 + 真写进 store
    got = store.get(s.id).cost
    assert got["total_prompt_tokens"] == 700 and got["total_completion_tokens"] == 40, got
    runner._publish_status(store.get(s.id), "parked")
    evs = [e for e in bus.history(s.id) if e.kind == "status"]
    assert evs and evs[-1].value["cost"].get("total_prompt_tokens") == 700, \
        f"park 之后发的 status 里账本是空的（前端那半格的根因）：{evs[-1].value if evs else None}"

    runner._forget(s.id, terminal=True)                         # ③ 阳性对照：散会照旧清干净
    assert s.id not in runner.costs and s.id not in runner.graphs, \
        "terminal=True 必须照旧清（不清不是永不回收）"
    _ok("t11", "park（terminal=False）留住图里那本活账：dict 在、实例是同一个、"
               "store 与 SSE 两路都读得到；terminal=True 仍照旧清干净")


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
    没采到 run_id 的老路（拿不到 run_id 就没有这笔的计时）落 null 而不是 0——前端据此不显示读数，
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
    # 末三个是 R1 的召回链观测（failures/zero_hits/returned）：这一格加宽键集就是这次契约变更的
    # **申报口**——守卫不红才说明有人偷偷加了字段没登记。
    # C123（10-02 复审批）：C122 的第四笔 invalid_args_calls 恰好绕过了这个申报口——
    # 加键时没跑本套件，t8 在 HEAD 上红了一拍才补进来。这就是申报口存在的意义。
    assert set(snap) == {"cost_usd", "cost_cny", "total_prompt_tokens", "total_completion_tokens",
                          "truncated_calls", "unknown_command_calls", "empty_output_calls",
                          "recall_failures", "recall_zero_hits", "recall_returned",
                          "overflow_failed", "overflow_written", "invalid_args_calls"}, snap
    assert "total_cost" not in snap, f"快照里又长出合计字段（C12 删的就是它）：{snap}"
    assert (snap["truncated_calls"], snap["unknown_command_calls"], snap["empty_output_calls"],
            snap["recall_failures"], snap["recall_zero_hits"], snap["recall_returned"],
            snap["overflow_failed"], snap["overflow_written"], snap["invalid_args_calls"]) == (0,) * 9, \
        f"这一格没制造无效调用、也没走召回与溢出，九个计数却非 0（那就是恒亮的告警）：{snap}"

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


def t10_cost_injection_end_to_end():
    """C19 未验②：**图内那笔账到底能不能被 runner 读到**——整场会话端到端，零花费。

    `cost.py` 那条 ⚠ 一直挂着这条：跑完读得到非零的前提是「角色手上的 manager 就是 runner
    `self.costs[sid]` 那一份」，而此前只证到「同一个 manager 的快照带得出那些键」（构造对象），
    没证过**注入链本身**。双账本正是当年「用量恒 0」的根因，所以这一格必须走真路径：

      真 `create_app()` + 真 `SessionRunner` + 真 `_make_llm`（**不打桩工厂函数**，打了就是在测我自己
      的桩）→ 网关指向一台本机 OpenAI 兼容桩（模型名取价表 CNY 行）→ 起一场 dynamic 线会话跑到终态
      → 从 **GET 出口**读 `cost`。

    四格各钉一种坏法：
      ① pt/ct 非零 —— 注入断了就恒 0（历史上就是这个形状）；
      ② 币种按价表算：CNY 桶 > 0 且 USD 桶 == 0（两桶不相加是 C12 的口径，这里顺带证明
         「桩回的名字真被当模型名用了」）；
      ③ 终态是 `finished` 而不是 `failed` —— 不然「跑完能看见」这句话没有意义；
      ④ 快照里三笔无效调用计数键齐（`truncated/unknown_command/empty_output`）——C19/T4-③ 那三个
         观测项与钱同路，路断在注入上它们也一起看不见。
    反证（改真代码、不在门禁里）：把 `_make_llm(cost_manager, …)` 的注入摘掉 ⇒ ①② 当场红。
    """
    import json as _j
    import shutil
    import threading
    import time
    from http.server import BaseHTTPRequestHandler, HTTPServer
    from pathlib import Path
    from tempfile import mkdtemp

    import server.sessions as ss
    from codeharness.configs.settings import settings

    PT, CT = 137, 29                      # 手得上数：与价表 CNY 行相乘后仍要能核对非零
    MODEL = "step-3.5-flash"              # 价表里落在 CNY 名单的那个名字

    class _Stub(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            self.rfile.read(int(self.headers.get("content-length") or 0))
            body = {"id": "chatcmpl-t10", "object": "chat.completion", "created": 1790000000,
                    "model": MODEL,
                    "choices": [{"index": 0, "finish_reason": "stop",
                                 "message": {"role": "assistant", "content": "好的"}}],
                    "usage": {"prompt_tokens": PT, "completion_tokens": CT,
                              "total_tokens": PT + CT}}
            out = _j.dumps(body).encode()
            self.send_response(200)
            self.send_header("Connection", "close")
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(out)))
            self.end_headers()
            self.wfile.write(out)

        def log_message(self, *a):
            pass

    srv = HTTPServer(("127.0.0.1", 0), _Stub)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    port = srv.server_address[1]
    keep = (settings.llm.base_url, settings.llm.api_key, settings.llm.model, settings.llm.stream,
            settings.enable_rag, settings.platform.auth_enabled, settings.platform.use_redis,
            ss.SESSIONS_FILE)
    tmp = Path(mkdtemp())
    project = "s8t10_inject"
    try:
        settings.llm.base_url = f"http://127.0.0.1:{port}/v1"
        settings.llm.api_key = "stub-key"
        settings.llm.model = MODEL
        settings.llm.stream = False         # 桩回一次性 JSON，流式那套要 SSE 分帧
        settings.enable_rag = False         # 本格问账本注入，不掺向量腿（也不发任何 embedding）
        settings.platform.auth_enabled = False
        settings.platform.use_redis = False
        ss.SESSIONS_FILE = tmp / "sessions.json"

        from fastapi.testclient import TestClient
        from server.app import create_app
        with TestClient(create_app()) as c:
            sid = c.post("/api/sessions",
                         json={"idea": "只回一句你好，不要调工具", "project_name": project,
                               "paradigm": "dynamic"}).json()["id"]
            assert c.post(f"/api/sessions/{sid}/start").status_code == 200, "起跑失败"
            status, body = "", {}
            for _ in range(120):                       # 上限 60s：桩是本地即时回，超时就是卡住了
                time.sleep(0.5)
                body = c.get(f"/api/sessions/{sid}").json()
                status = str(body.get("status"))
                if status != "running":
                    break
            cost = body.get("cost") or {}
            assert status == "finished", f"③这一场没跑到 finished（实为 {status}），①②的读数不作数"
            assert cost.get("total_prompt_tokens", 0) > 0 and cost.get("total_completion_tokens", 0) > 0, \
                (f"①GET 出口的账本是空的：注入链断了（双账本，正是当年恒 0 的形状）——{cost}")
            assert cost.get("cost_cny", 0) > 0 and cost.get("cost_usd") == 0, \
                f"②币种不对（{MODEL} 在 CNY 名单里，两桶不相加）：{cost}"
            missing = {"truncated_calls", "unknown_command_calls", "empty_output_calls"} - set(cost)
            assert not missing, f"④三笔观测项有键没带出出口：缺 {missing}（{sorted(cost)}）"
            _ok("t10", f"整场会话端到端：真装配跑到 {status}、GET 出口 pt={cost['total_prompt_tokens']} "
                       f"ct={cost['total_completion_tokens']} ¥{cost['cost_cny']}（$ 保持 0）、"
                       "三笔无效调用计数键都在 ⇒ 角色手上的 manager 就是 runner 那一份")
    finally:
        srv.shutdown()
        (settings.llm.base_url, settings.llm.api_key, settings.llm.model, settings.llm.stream,
         settings.enable_rag, settings.platform.auth_enabled, settings.platform.use_redis,
         ss.SESSIONS_FILE) = keep
        shutil.rmtree(tmp, ignore_errors=True)
        shutil.rmtree(Path("workspace") / project, ignore_errors=True)


async def t12_prose_from_structured_stream():
    """流式 UX 批：structured 的逐片 JSON 必须在翻译层抽成散文才上屏。

    病灶是第一手录像（09-28 一场真跑的 SSE，`stream-act` 块 956 片 / 15177 字）：JSON 原文
    `{\
  "language": "en"...` 被**原样**当打字机发出去，而正文要等内核解析完整段落地——
    用户报的「为什么前端不是流式输出」+「一大块文本出现」正是这两下。

    ① 抽取器与 stdlib 参照实现（`json.decoder.scanstring` 独立走一遍）逐字一致，且对切法不敏感
       （整片 / 3 字 / 1 字三种切法同结果——转义与代理对被切断也不能变）；
    ② 接线（真 `_translate` + bus）：`on_chat_model_start` 建块发的是 `meta` 而不是 `content`
       （静默期有得看，但不占 fts、不编 TTFT），JSON 逐片只出散文，收口清掉这笔的状态机，
       同节点第二笔重新抽（不沿用上一笔的进度）；
    ③ 裸文本流原样透传（改动前行为，一字不差）；
    ④ 全是短字段的结构化输出 ⇒ 零发布（阳性对照：结构脚手架一个字符都不许漏出去）；
    ⑤ 内容块形态（`content` 不是 str）不发布、也不炸。
    """
    from json.decoder import scanstring
    from server.runner import MIN_PROSE, _KEY_CAP, _ProseStream

    long_a = ("快排是典型的分治排序算法，核心逻辑是选取基准元素将待排序数组划分为「小于等于基准」"
              "和「大于基准」的两个子数组，再递归对子数组执行相同排序逻辑，平均时间复杂度 O(n log n)。")
    long_b = ("Second goal: a technically accurate, concise explanation of binary search that a "
              "non-technical reader can follow in under sixty seconds.")
    txt = json.dumps({"language": "en", "original_requirements": "短需求",
                      "product_goals": [long_a], "requirement_analysis": long_b},
                     ensure_ascii=False, indent=2)

    def ref(s):
        """参照实现：按序列化顺序扫所有字符串成员，取长度达标者按序用换行接起来。"""
        found, i = [], 0
        while i < len(s):
            if s[i] == '"':
                v, end = scanstring(s, i + 1, False)
                if len(v) >= MIN_PROSE:
                    found.append(v)
                i = end
            else:
                i += 1
        return "\n".join(found)

    want = ref(txt)
    assert want == long_a + "\n" + long_b, f"参照实现挑的成员不对：{want[:60]!r}"
    for step in (len(txt), 3, 1):
        pr = _ProseStream()
        got = "".join(pr.feed(txt[i:i + step]) for i in range(0, len(txt), step))
        assert got == want, f"切法 step={step} 改写了结果：{got[:60]!r}"
    assert "{" not in want and '": ' not in want, f"抽出来的还是 JSON：{want[:40]!r}"

    tmp, store, bus, runner, s = await _make_runner()
    runner.costs[s.id] = CostManager()
    meta = {"langgraph_node": "PM"}

    def contents(uuid=None, after=0):
        """这一笔之后落在某块上的正文事件（逐片走 live、定稿走 content，两边都要能取到）。"""
        return [e for e in bus.history(s.id)[after:] if e.kind == "report"
                and e.name in ("live", "content") and (uuid is None or e.uuid == uuid)]

    runner._translate(s.id, {"event": "on_chat_model_start", "run_id": "r1", "metadata": meta})
    opened = [e for e in bus.history(s.id) if e.kind == "report" and e.uuid == "stream-PM"]
    assert opened and opened[0].name == "meta", \
        f"start 没建块、或建块发的不是 meta（静默期全黑／fts 被点着是两种坏法）：{[e.name for e in opened]}"
    assert not [e for e in opened if e.name in ("content", "live")], \
        "静默期就发正文 ⇒ 前端把 b.fts 点着，TTFT 从「真散文首片」变成「调用派发」= 假读数"
    assert runner._call_t0[(s.id, "r1")][1] is None, "start 就把 ft 写了，span 的 TTFT 是编的"

    mark = len(bus.history(s.id))
    for i in range(0, len(txt), 7):
        runner._translate(s.id, {"event": "on_chat_model_stream", "run_id": "r1", "metadata": meta,
                                 "data": {"chunk": AIMessage(content=txt[i:i + 7])}})
    got = "".join(e.value for e in contents("stream-PM", mark))
    assert got == want, f"逐片抽出来的散文与参照实现不符：{got[:60]!r}"
    leak = [e.value for e in contents("stream-PM", mark) if "{" in e.value or '"language"' in e.value]
    assert not leak, f"结构脚手架漏上屏：{leak[:2]}"
    assert runner._call_t0[(s.id, "r1")][1] is not None, "散文首片没点着 ft"

    runner._translate(s.id, {"event": "on_chat_model_end", "run_id": "r1", "metadata": meta})
    assert (s.id, "r1") not in runner._prose, "收口没清抽取器，长跑会一直攒状态机"
    assert [e for e in bus.history(s.id) if e.name == "end_marker" and e.uuid == "stream-PM"], \
        "end 不收口 stream 块 ⇒ 跑完了光标一直闪（与 t7 同族）"

    mark = len(bus.history(s.id))
    runner._translate(s.id, {"event": "on_chat_model_stream", "run_id": "r2", "metadata": meta,
                             "data": {"chunk": AIMessage(content=txt)}})
    got2 = "".join(e.value for e in contents("stream-PM", mark))
    assert got2 == want, f"同节点第二笔没重新抽（沿用了上一笔的进度）：{got2[:60]!r}"

    raw_txt = "这是没有 JSON 包裹的裸文本流，逐片到达时应当一字不差地透传回来。"
    mark = len(bus.history(s.id))
    runner._translate(s.id, {"event": "on_chat_model_start", "run_id": "r3",
                             "metadata": {"langgraph_node": "raw"}})
    for i in range(0, len(raw_txt), 4):
        runner._translate(s.id, {"event": "on_chat_model_stream", "run_id": "r3",
                                 "metadata": {"langgraph_node": "raw"},
                                 "data": {"chunk": AIMessage(content=raw_txt[i:i + 4])}})
    assert "".join(e.value for e in contents("stream-raw", mark)) == raw_txt, "裸文本流被抽取器吃了"

    short = json.dumps({"issue_type": "REQUIREMENT", "reason": "短"}, ensure_ascii=False, indent=2)
    mark = len(bus.history(s.id))
    runner._translate(s.id, {"event": "on_chat_model_stream", "run_id": "r4", "metadata": meta,
                             "data": {"chunk": AIMessage(content=short)}})
    assert not contents(None, mark), f"全是短字段也该零发布（别把 {short[:12]!r} 打上去）"

    mark = len(bus.history(s.id))
    runner._translate(s.id, {"event": "on_chat_model_stream", "run_id": "r5", "metadata": meta,
                             "data": {"chunk": type("C", (), {"content": [{"type": "text", "text": "x"}]})()}})
    assert not contents(None, mark), "内容块形态（content 非 str）不该发布"

    # ⑥ 落点：内核块开着时，逐片进**那块**（不另起一行）；块收口后落点释放，下一笔回 `stream-{node}`。
    #    这条取代了上一版的「整段重发去重」——逐片走 live、定稿走 content，两份内容在结构上就不会
    #    同屏，不必再靠字符串比对去猜（`_prose_out`/`_already_streamed` 已随之删除）。
    tmp2, store2, bus2, runner2, s2 = await _make_runner()
    runner2.costs[s2.id] = CostManager()
    sink2 = runner2._make_sink(s2.id)
    sink2({"block": "Docs", "uuid": "doc-1", "name": "meta", "value": {"type": "prd"}, "role": "PM"})
    runner2._translate(s2.id, {"event": "on_chat_model_start", "run_id": "q1",
                               "metadata": {"langgraph_node": "act"}})
    early = [e for e in bus2.history(s2.id) if e.kind == "report"]
    assert not [e for e in early if str(e.uuid or "").startswith("stream-")], \
        "⑥ 内核块开着还另起一行 ⇒ 又回到两份同屏的老形状"
    for i in range(0, len(txt), 9):
        runner2._translate(s2.id, {"event": "on_chat_model_stream", "run_id": "q1",
                                   "metadata": {"langgraph_node": "act"},
                                   "data": {"chunk": AIMessage(content=txt[i:i + 9])}})
    live = [e for e in bus2.history(s2.id) if e.name == "live"]
    assert live and all(e.uuid == "doc-1" and e.block == "Docs" for e in live), \
        f"⑥ 逐片没投进开着的那块：{[(e.uuid, e.block) for e in live][:3]}"
    assert "".join(e.value for e in live) == want, "⑥ 换了落点，抽出来的散文不该变"
    sink2({"block": "Docs", "uuid": "doc-1", "name": "content", "value": "## 定稿", "role": "PM"})
    sink2({"block": "Docs", "uuid": "doc-1", "name": "end_marker", "value": None, "role": "PM"})
    runner2._translate(s2.id, {"event": "on_chat_model_stream", "run_id": "q2",
                               "metadata": {"langgraph_node": "act"},
                               "data": {"chunk": AIMessage(content=txt)}})
    late = [e for e in bus2.history(s2.id) if e.name == "live" and e.uuid == "stream-act"]
    assert late and "".join(e.value for e in late) == want, \
        "⑥ 块收口后落点没释放：下一笔还往那块里投（那块已经收口，用户看不见）"
    runner2._forget(s2.id, terminal=True)
    assert s2.id not in runner2._live_blk, "散会没清落点表（长跑会一直攒）"

    # ⑦ 回显排除：值原样出现在这一笔的输入里 ⇒ 那是模型在抄用户的话，不发。
    #    09-29 经典线活体（会话 `b2d49b85`）现证逐片最前面就是那句需求原文，紧跟其后的才是模型自己的话。
    from langchain_core.messages import SystemMessage

    echo_line = "做一个极小的静态网页，用一段话解释二分查找是什么。不要写测试，不要部署。"
    fresh = "构建加载速度极快、无冗余资源的轻量静态网页，仅用于传递二分查找的核心概念与适用场景。"
    txt2 = json.dumps({"original_requirements": echo_line, "requirement_analysis": fresh,
                       "anything_unclear": "无"}, ensure_ascii=False, indent=2)
    mark = len(bus.history(s.id))
    #    形状按**现证**钉（`E:/tmp/ch_diag_stream.py` 用 SSE 桩打真 astream_events 量出来的）：
    #    structured 链的 `data["input"]` 是 `{"messages": […]}` 这个 dict，不是列表——
    #    前两版分别只认扁平列表与批式嵌套列表，真会话里都抽到空串、规则静默空转，判据却全绿。
    runner._translate(s.id, {"event": "on_chat_model_start", "run_id": "r7", "metadata": meta,
                             "data": {"input": {"messages": [
                                 SystemMessage(content="系统提示……用户需求：" + echo_line)]}}})
    assert (s.id, "r7") in runner._prompts, "⑦ start 没把这笔的输入文本存下来（回显判定无从下手）"
    for i in range(0, len(txt2), 11):
        runner._translate(s.id, {"event": "on_chat_model_stream", "run_id": "r7", "metadata": meta,
                                 "data": {"chunk": AIMessage(content=txt2[i:i + 11])}})
    got7 = "".join(e.value for e in contents("stream-PM", mark))
    assert got7 == fresh, f"⑦ 回显没被排除，或把模型自己的话也吞了：{got7[:70]!r}"
    assert runner._prose[(s.id, "r7")].echoed == 1, "⑦ 回显计数没记上（判据就只能去看日志）"
    runner._translate(s.id, {"event": "on_chat_model_end", "run_id": "r7", "metadata": meta})
    assert (s.id, "r7") not in runner._prompts, "⑦ 收口没清输入文本（一笔几万字，长跑会一直攒）"
    mark = len(bus.history(s.id))
    runner._translate(s.id, {"event": "on_chat_model_stream", "run_id": "r8", "metadata": meta,
                             "data": {"chunk": AIMessage(content=txt2)}})
    got8 = "".join(e.value for e in contents("stream-PM", mark))
    assert echo_line in got8 and fresh in got8, f"⑦ 阳性对照失守（没存到 prompt 时不该排除任何东西）：{got8[:70]!r}"
    mark = len(bus.history(s.id))
    runner._translate(s.id, {"event": "on_chat_model_start", "run_id": "r9", "metadata": meta,
                             "data": {"input": [SystemMessage(content="系统提示……用户需求：" + echo_line)]}})
    for i in range(0, len(txt2), 13):
        runner._translate(s.id, {"event": "on_chat_model_stream", "run_id": "r9", "metadata": meta,
                                 "data": {"chunk": AIMessage(content=txt2[i:i + 13])}})
    got9 = "".join(e.value for e in contents("stream-PM", mark))
    assert got9 == fresh, f"⑦ 扁平列表形状没被认出来（只认 dict＝半条规则）：{got9[:70]!r}"
    runner._translate(s.id, {"event": "on_chat_model_end", "run_id": "r9", "metadata": meta})

    # ⑧ 白名单门控：块开块 meta 里声明了 `prose_fields` ⇒ 逐片只放行名单里的字段。
    #    这一格同时钉住「按长度挑」那一版的两个漏（都是现证，不是设想）：
    #    `data_structures_and_interfaces`（30 字）长过 MIN_PROSE=24，作为**键名**被打上屏；
    #    `program_call_flow` 的值是 mermaid 源码，也长过门槛。名单之外还有嵌套一层：
    #    `commands[].args.content` 是要写进文件的正文，它绝不该出现在思考行里。
    #    名单里**必须有一个多项列表字段**（`product_goals`）：变异刀 m8「把每个闭合的串都当键名」在第一版
    #    判据里三支全存活——值闭合后键名失焦成「上一个串的文字」，而当时名单内每个字段只有一个值，
    #    失焦与不误发观察不到。列表的第二项就是那个可观察处（真 payload 里 `product_goals`/`user_stories`
    #    /`competitive_analysis` 都是 list[str]，这不是设想）。
    appr = "用 Flask 起一个静态站点，路由只有一条 index，页面里用一段话解释二分查找的三步。"
    goal_a = "第一条目标：让读者六十秒内明白二分查找在比较什么、为什么每次都少一半。"
    goal_b = "第二条目标：页面零依赖、打开即用，不需要构建步骤也不需要后端服务。"
    unclear = "没有需要澄清的地方，需求已经足够确定一个静态页面的范围。"
    design_txt = json.dumps({
        "implementation_approach": appr,
        "product_goals": [goal_a, goal_b],
        "file_list": ["index.html", "app.py", "src/constants/explanation.js"],   # 28 字：活体现证的第四类漏
        "data_structures_and_interfaces": "classDiagram\n    Class01 <|-- AveryLongClass : 这条是 mermaid 源码",
        "program_call_flow": "sequenceDiagram\n    A->>B: 这条也是 mermaid 源码",
        "anything_unclear": unclear}, ensure_ascii=False, indent=2)

    tmp3, store3, bus3, runner3, s3 = await _make_runner()
    runner3.costs[s3.id] = CostManager()
    sink3 = runner3._make_sink(s3.id)
    sink3({"block": "Docs", "uuid": "doc-2", "name": "meta", "role": "Architect",
           "value": {"type": "design",
           "prose_fields": ["implementation_approach", "product_goals", "anything_unclear"]}})
    assert runner3._live_blk[s3.id][2] == ["implementation_approach", "product_goals", "anything_unclear"], \
        "⑧ 开块 meta 里的名单没跟着块登记（门控拿不到名单＝静默退回按长度挑，正是本仓踩过两次的空转形状）"
    # 名单要活得过非 meta 事件：内核在块里发 object/content 时不带 value 里的那些键
    sink3({"block": "Docs", "uuid": "doc-2", "name": "path", "value": "design.md", "role": "Architect"})
    assert runner3._live_blk[s3.id][2] is not None, "⑧ 一个非 meta 事件就把登记好的名单冲掉了"

    # C130：start 必须先于首片（生产 astream_events 恒有 start，这里补上对齐真实事件序——
    # 落点快照就取在 start 那一刻；改前判据只发 stream、靠「首片时读单槽」蒙混过去）。
    runner3._translate(s3.id, {"event": "on_chat_model_start", "run_id": "g1",
                               "metadata": {"langgraph_node": "Arch"}})
    for i in range(0, len(design_txt), 6):
        runner3._translate(s3.id, {"event": "on_chat_model_stream", "run_id": "g1",
                                   "metadata": {"langgraph_node": "Arch"},
                                   "data": {"chunk": AIMessage(content=design_txt[i:i + 6])}})
    live8 = [e for e in bus3.history(s3.id) if e.name == "live"]
    assert live8 and all(e.uuid == "doc-2" and e.block == "Docs" for e in live8), \
        f"⑧ 门控过的逐片没投进声明名单的那块：{[(e.uuid, e.block) for e in live8][:3]}"
    got8 = "".join(e.value for e in live8)
    assert got8 == appr + "\n" + goal_a + "\n" + goal_b + "\n" + unclear, \
        f"⑧ 名单内的字段没照发（列表的第二项被丢掉＝键名在值闭合后失焦），或发多了：{got8[:70]!r}"
    runner3._translate(s3.id, {"event": "on_chat_model_end", "run_id": "g1",
                               "metadata": {"langgraph_node": "Arch"}})
    for banned in ("classDiagram", "sequenceDiagram", "data_structures_and_interfaces",
                   "src/constants/explanation.js"):
        assert banned not in got8, f"⑧ 名单外的成员漏上屏：{banned!r}"

    # 阳性对照：同一份 payload，块**没声明**名单 ⇒ 退回按长度挑，那三样都该出现。
    # 少了这一格，「名单外不发」与「门槛太高没发」在判据里是同一个绿。
    tmp4, store4, bus4, runner4, s4 = await _make_runner()
    runner4.costs[s4.id] = CostManager()
    sink4 = runner4._make_sink(s4.id)
    sink4({"block": "Docs", "uuid": "doc-3", "name": "meta", "value": {"type": "design"}, "role": "Arch"})
    assert runner4._live_blk[s4.id][2] is None, "⑧ 没声明名单却拿到了名单（阳性对照自己先失效）"
    for i in range(0, len(design_txt), 9):
        runner4._translate(s4.id, {"event": "on_chat_model_stream", "run_id": "g2",
                                   "metadata": {"langgraph_node": "Arch"},
                                   "data": {"chunk": AIMessage(content=design_txt[i:i + 9])}})
    got4 = "".join(e.value for e in bus4.history(s4.id) if e.name == "live")
    assert got4 == ref(design_txt), f"⑧ 无名单时行为该与「按长度挑」那一版逐字一致（参照实现）：{got4[:70]!r}"
    assert "data_structures_and_interfaces" in got4, "⑧ 阳性对照没复现旧漏（说明参照实现没覆盖到键名）"
    assert "src/constants/explanation.js" in got4, "⑧ 阳性对照没复现第四类漏（够长的文件名本该在按长度挑那一版里上屏）"
    runner4._forget(s4.id, terminal=True)

    # ⑧c 嵌套一层：动态线的 ZeroThought 只放行 `thought`，`commands[].args.content` 不发
    think = "先写一个 index.html，再让 app.py 以静态方式服务它；这一轮只需要两个文件。"
    file_body = "<!DOCTYPE html><html><body>这段是要写进文件的整页正文，绝不该出现在思考行里。</body></html>"
    ztxt = json.dumps({"thought": think,
                       "commands": [{"command_name": "write_files",
                                     "args": {"filename": "static/index.html", "content": file_body}}]},
                      ensure_ascii=False, indent=2)
    sink3({"block": "Thought", "uuid": "doc-2", "name": "end_marker", "value": None, "role": "Architect"})
    sink3({"block": "Thought", "uuid": "zt-1", "name": "meta", "role": "Zero",
           "value": {"type": "react", "prose_fields": ["thought"]}})
    # C130：start 先于首片（对齐生产事件序，同 ⑧ 的 g1）
    runner3._translate(s3.id, {"event": "on_chat_model_start", "run_id": "g3",
                               "metadata": {"langgraph_node": "act"}})
    for i in range(0, len(ztxt), 7):
        runner3._translate(s3.id, {"event": "on_chat_model_stream", "run_id": "g3",
                                   "metadata": {"langgraph_node": "act"},
                                   "data": {"chunk": AIMessage(content=ztxt[i:i + 7])}})
    live9 = [e.value for e in bus3.history(s3.id) if e.name == "live" and e.uuid == "zt-1"]
    assert "".join(live9) == think, f"⑧c 思考行里混进了别的东西（args 正文/枚举值）：{(''.join(live9))[:70]!r}"
    sink3({"block": "Thought", "uuid": "zt-1", "name": "content", "value": think, "role": "Zero"})
    sink3({"block": "Thought", "uuid": "zt-1", "name": "end_marker", "value": None, "role": "Zero"})
    assert runner3._live_blk.get(s3.id) is None, "⑧c 块收口后落点没释放"
    runner3._forget(s3.id, terminal=True)

    # ⑨ Task 块（第七件）：开块那条 `meta` 是它进落点表的**唯一入口**。改前 `task_block` 只建 reporter
    #    就 yield，报道槽那条通道上一个事件都没有 ⇒ 那一笔调用的逐片只能落兜底行，名单也就挂不上。
    instr_a = "在首页文件里写出这段解释：先讲清比较的对象是什么，再讲每一步为什么能砍掉一半候选。"
    instr_b = "样式文件里只留排版与间距两类规则，不引入任何外部字体、图标、框架运行时与构建步骤。"
    knowl = "产物必须是纯静态文件：浏览器直接打开即用，不依赖任何后端服务、打包器或第三方脚本。"
    pkg_line = "本任务清单不需要任何第三方依赖，标准库与浏览器原生能力即可覆盖全部需求。"
    ttxt = json.dumps({"task_list": [
        {"filename": "src/index.html", "task_id": "T1", "instruction": instr_a},
        {"filename": "src/constants/explanation.js", "task_id": "T2", "instruction": instr_b}],
        "required_packages": [pkg_line], "shared_knowledge": knowl}, ensure_ascii=False, indent=2)

    tmp5, store5, bus5, runner5, s5 = await _make_runner()
    runner5.costs[s5.id] = CostManager()
    sink5 = runner5._make_sink(s5.id)
    sink5({"block": "Task", "uuid": "task-1", "name": "meta", "role": "PMManager",
           "value": {"type": "tasks", "prose_fields": ["instruction", "shared_knowledge"]}})
    runner5._translate(s5.id, {"event": "on_chat_model_start", "run_id": "k1",
                               "metadata": {"langgraph_node": "PMManager"}})
    assert not [e for e in bus5.history(s5.id) if str(e.uuid or "").startswith("stream-")], \
        "⑨ Task 块开着还另起一行兜底（那条 meta 白发了——落点表根本没登记上）"
    for i in range(0, len(ttxt), 8):
        runner5._translate(s5.id, {"event": "on_chat_model_stream", "run_id": "k1",
                                   "metadata": {"langgraph_node": "PMManager"},
                                   "data": {"chunk": AIMessage(content=ttxt[i:i + 8])}})
    tlive = [e for e in bus5.history(s5.id) if e.name == "live"]
    assert tlive and all(e.uuid == "task-1" and e.block == "Task" for e in tlive), \
        f"⑨ 逐片没进开着的那块 Task：{[(e.uuid, e.block) for e in tlive][:3]}"
    got9 = "".join(e.value for e in tlive)
    assert got9 == instr_a + "\n" + instr_b + "\n" + knowl, \
        f"⑨ Task 名单内的三段（含嵌套一层里的 instruction）没照发或发多了：{got9[:70]!r}"
    for banned9 in ("src/constants/explanation.js", pkg_line, "T1", "T2"):
        assert banned9 not in got9, f"⑨ 标识符漏进 Task 块的流：{banned9!r}"

    # 阳性对照一：同一 payload、块声明了但**没带名单** ⇒ 退回按长度挑，标识符该回来（与参照实现逐字一致）
    tmp6, store6, bus6, runner6, s6 = await _make_runner()
    runner6.costs[s6.id] = CostManager()
    runner6._make_sink(s6.id)({"block": "Task", "uuid": "task-2", "name": "meta",
                               "value": {"type": "tasks"}, "role": "PMManager"})
    for i in range(0, len(ttxt), 11):
        runner6._translate(s6.id, {"event": "on_chat_model_stream", "run_id": "k2",
                                   "metadata": {"langgraph_node": "PMManager"},
                                   "data": {"chunk": AIMessage(content=ttxt[i:i + 11])}})
    got6 = "".join(e.value for e in bus6.history(s6.id) if e.name == "live")
    assert got6 == ref(ttxt), f"⑨ 无名单时该与按长度挑那一版逐字一致：{got6[:70]!r}"
    assert pkg_line in got6, "⑨ 阳性对照没复现「标识符够长就上屏」（参照实现没覆盖到它？）"

    # 阳性对照二＝**改前的形状**：块开着但一个 meta 都没发 ⇒ 落点表无从登记，逐片只能落兜底行，
    # 那块零 live（这一格钉的就是第七件要修的那件事本身：少那条 meta，逐片进不去那块）
    tmp7, store7, bus7, runner7, s7 = await _make_runner()
    runner7.costs[s7.id] = CostManager()
    for i in range(0, len(ttxt), 13):
        runner7._translate(s7.id, {"event": "on_chat_model_stream", "run_id": "k3",
                                   "metadata": {"langgraph_node": "PMManager"},
                                   "data": {"chunk": AIMessage(content=ttxt[i:i + 13])}})
    assert not [e for e in bus7.history(s7.id) if e.name == "live" and e.block == "Task"], \
        "⑨ 没发 meta 却把逐片投进了那块（落点登记凭空多了一条路＝两处写、必漂移）"
    assert [e for e in bus7.history(s7.id) if e.name == "live" and e.uuid == "stream-PMManager"], \
        "⑨ 改前形状下逐片该落兜底行——现在不落说明这条对照没照住老形状"
    runner5._forget(s5.id, terminal=True)
    runner6._forget(s6.id, terminal=True)
    runner7._forget(s7.id, terminal=True)

    # ⑩ 经典线那笔「选动作」（ActionChoice）：这块挂名单**今天不改一个字节**，这句话得有读数而不是口号。
    #    现证的边界：装配出的 11 个动作名最长 22 字（`WriteCodePlanAndChange`），够不到 MIN_PROSE=24，只差 2 字。
    from codeharness.roles.agent import ActionChoice

    def feed(only, txt, step=5):
        ps = _ProseStream("", only)
        return "".join(ps.feed(txt[i:i + step]) for i in range(0, len(txt), step))

    think8 = "上游产物已就绪，本步该写代码了；先确认文件名与设计里的接口一致，再决定要不要补一轮评审。"
    a_txt = json.dumps({"thought": think8, "action": "WriteCodePlanAndChange"}, ensure_ascii=False, indent=2)
    assert ActionChoice.prose_fields == ("thought",), "⑩ ActionChoice 没声明名单（这块今天全靠长度兜着）"
    assert len("WriteCodePlanAndChange") < MIN_PROSE <= len("summarize_and_rewrite_the_whole_module"), \
        "⑩ 这条判据的前提变了（真动作名长过门槛，或顶界那档不够长），下面两格的含义要重写"
    assert feed(None, a_txt) == feed(ActionChoice.prose_fields, a_txt) == think8, \
        f"⑩ 挂名单该零差别：不挂={feed(None, a_txt)[:40]!r} 挂={feed(ActionChoice.prose_fields, a_txt)[:40]!r}"
    over = json.dumps({"thought": "该写代码了，这一步很短。", "action": "summarize_and_rewrite_the_whole_module"},
                      ensure_ascii=False, indent=2)
    assert "summarize_and_rewrite_the_whole_module" in feed(None, over), \
        "⑩ 阳性对照失守：按长度挑那一版本该把这个 36 字的动作名整条打上屏（不然这条守卫防的是不存在的形状）"
    assert feed(("thought",), over) == "", "⑩ 名单没挡住顶到界的动作名（挂它就没意义了）"

    # ⑪ `_KEY_CAP` 那道界今天没人顶过（本仓最长的键 30 字）。它是**内存的界**：`buf` 攒的是当前字符串的
    #    全长，而正文可以无限长，所以封顶必须生效；它的可观察后果是「键名超过 _KEY_CAP 一律当名单外」。
    #    必须测**相邻两档**（正好 128 认得 / 129 不认）——只测 127 与 130 的话，界被写成 127 或 129 都照样绿。
    #    读数（09-29 现取，E:/tmp/ch_prelock_proto2.out）：128 字键名发 35 字、129 字发 0 字、
    #    正文 7000 字挂在 6 字键名下 7000 字全发且跑完 buf=128。
    cap_key_ok = "q" * _KEY_CAP
    cap_key_over = "q" * (_KEY_CAP + 1)
    cap_prose = "这一段是给人读的理由说明，够长跨过散文门槛，用它看键名到底认没认出来。"
    assert len(cap_prose) >= MIN_PROSE, "⑪ 前提变了：正文不够长，下面测的就不是名单而是长度门槛"
    assert feed((cap_key_ok,), json.dumps({cap_key_ok: cap_prose}, ensure_ascii=False)) == cap_prose, \
        f"⑪ 键名正好 {_KEY_CAP} 字（界内最后一点）认不出来 ⇒ 封顶写小了，长字段名会静默不上屏"
    assert feed((cap_key_over,), json.dumps({cap_key_over: cap_prose}, ensure_ascii=False)) == "", \
        f"⑪ 键名 {_KEY_CAP + 1} 字（越界一字）仍被当名单内 ⇒ buf 没封顶，那条界只剩注释"
    ps_cap = _ProseStream("", ("reason",))
    cap_long = json.dumps({"reason": cap_prose * 200}, ensure_ascii=False)
    out_cap = "".join(ps_cap.feed(cap_long[k:k + 7]) for k in range(0, len(cap_long), 7))
    assert out_cap == cap_prose * 200, f"⑪ 正文超长该照发（界只管键名不管正文）：发 {len(out_cap)} 字"
    assert len(ps_cap.buf) <= _KEY_CAP, f"⑪ buf 被正文撑到 {len(ps_cap.buf)} 字 ⇒ 封顶失效（这条界的本意就是内存）"
    r_none = feed(None, json.dumps({cap_key_over: cap_prose}, ensure_ascii=False))
    assert len(r_none) == len(cap_key_over) + len(cap_prose) + 1, \
        f"⑪ 阳性对照失守：only=None 时整套键名跟踪不启动，那个 {_KEY_CAP + 1} 字的键名自己长过门槛、" \
        f"该整条上屏（第六件治的那个原始病形）：现发 {len(r_none)} 字"

    # ⑫（C104）落点表的**准入**：只有开块那条 meta、或已登记那块自己的后继事件才配占落点。
    #    病形一＝`Plan._report_plan` 那颗随机 uuid 的 Task object（真实发射器每推进一次换新 uuid，
    #    这里用两颗不同的十六进制串代表两次推进）；病形二＝交错块的定稿把落点从当前开着的块抢回去。
    #    顺带证 uid 收敛后同角色的计划事件只聚出一颗卡。
    tmp8, store8, bus8, runner8, s8i = await _make_runner()
    runner8.costs[s8i.id] = CostManager()
    sink8 = runner8._make_sink(s8i.id)
    sink8({"block": "Task", "uuid": "task-8", "name": "meta", "role": "PMManager",
           "value": {"type": "tasks", "prose_fields": ["instruction", "shared_knowledge"]}})
    plan_a = {"block": "Task", "uuid": "a" * 32, "name": "object", "role": "PMManager",
              "value": {"tasks": [{"task_id": "T1", "is_finished": False}], "current_task_id": "T1"}}
    sink8(dict(plan_a))
    assert runner8._live_blk[s8i.id][:2] == ("task-8", "Task"), \
        f"⑫ 计划卡的 object 抢走了逐片落点（改前形状）：现登记 {runner8._live_blk[s8i.id]!r}"
    # 同族第二形（这才是有牙的那格，n1 那刀就红在这里）：交错块里**上一块的定稿**不许把落点
    # 从当前开着的块抢回去——光比字典不够，要看这一笔的逐片到底投进了哪块。
    sink8({"block": "Docs", "uuid": "doc-9", "name": "meta", "role": "Architect",
           "value": {"type": "design", "prose_fields": ["anything_unclear"]}})
    sink8({"block": "Task", "uuid": "task-8", "name": "content", "value": "上一块的定稿", "role": "PMManager"})
    assert runner8._live_blk[s8i.id] == ("doc-9", "Docs", ["anything_unclear"]), \
        f"⑫ 交错块：上一块的定稿把落点从当前开着的 Docs 块抢回去了：{runner8._live_blk[s8i.id]!r}"
    dsnug = json.dumps({"anything_unclear": "这里是要上屏的一句人话说明，长度要过散文门槛所以再补几句凑够字。"},
                       ensure_ascii=False)
    # C130：start 先于首片（对齐生产事件序，同 ⑧ 的 g1/g3）
    runner8._translate(s8i.id, {"event": "on_chat_model_start", "run_id": "m0",
                                "metadata": {"langgraph_node": "Arch"}})
    for i in range(0, len(dsnug), 7):
        runner8._translate(s8i.id, {"event": "on_chat_model_stream", "run_id": "m0",
                                    "metadata": {"langgraph_node": "Arch"},
                                    "data": {"chunk": AIMessage(content=dsnug[i:i + 7])}})
    live8b = [e.uuid for e in bus8.history(s8i.id) if e.name == "live"]
    assert live8b and set(live8b) == {"doc-9"}, \
        f"⑫ 这一笔的逐片没全部投进当前开着的 Docs 块（落点被抢＝前端看不见）：{live8b}"

    # uid 收敛：同角色两次推进 ⇒ 转发出去同一颗 uuid；换个角色仍是另一颗卡
    sink8(dict(plan_a))
    sink8({**plan_a, "uuid": "b" * 32, "role": "Engineer"})
    objs = [e for e in bus8.history(s8i.id) if e.name == "object"]
    # 这条通道一共喂了三颗 object（抢落点那一格先发了一条），三条都得照发——守卫只管落点，不许吞事件
    assert len(objs) == 3, f"⑫ object 没全发出去（守卫把事件本身吞了＝改坏投递）：{len(objs)} 条"
    assert objs[0].uuid == objs[1].uuid == "plan-PMManager", \
        f"⑫ 同角色的计划卡没收敛：{[e.uuid for e in objs][:2]}"
    assert objs[-1].uuid == "plan-Engineer", f"⑫ 换角色也并进同一颗卡（该各一张）：{objs[-1].uuid}"
    assert {e.uuid for e in objs} == {"plan-PMManager", "plan-Engineer"}, \
        f"⑫ 计划卡的频道数不是「每角色一颗」：{sorted({e.uuid for e in objs})}"

    # 阳性对照＝改前的病能复现：没有开块 meta 时，object 之后那一笔的逐片该落兜底行、按长度挑
    # （改前它被 object 抢住 ⇒ 既没兜底行也没名单，正是第六件的病复发）
    tmp9, store9, bus9, runner9, s9i = await _make_runner()
    runner9.costs[s9i.id] = CostManager()
    runner9._make_sink(s9i.id)(dict(plan_a))
    assert not runner9._live_blk.get(s9i.id), "⑫ 孤身一颗 object 仍被登记成落点（守卫没生效＝下面两格白测）"
    runner9._translate(s9i.id, {"event": "on_chat_model_start", "run_id": "m1",
                                "metadata": {"langgraph_node": "PMManager"}})
    assert [e for e in bus9.history(s9i.id) if e.name == "meta" and e.uuid == "stream-PMManager"], \
        "⑫ 计划卡抑制了静默期兜底行（改前形状：那块本来就在，只是落点被抢）"
    for i in range(0, len(ttxt), 13):
        runner9._translate(s9i.id, {"event": "on_chat_model_stream", "run_id": "m1",
                                    "metadata": {"langgraph_node": "PMManager"},
                                    "data": {"chunk": AIMessage(content=ttxt[i:i + 13])}})
    got12 = "".join(e.value for e in bus9.history(s9i.id) if e.name == "live")
    assert got12 == ref(ttxt), f"⑫ 兜底行的逐片该退回按长度挑（与参照实现逐字一致）：{got12[:60]!r}"
    runner8._forget(s8i.id, terminal=True)
    runner9._forget(s9i.id, terminal=True)

    _ok("t12", "structured 的逐片 JSON 抽成散文才上屏（走 live 通道）：start 建块不占 fts、"
               "逐片与参照实现逐字一致、收口清状态机、同节点第二笔重抽；裸文本原样透传、"
               "短字段与内容块零发布；**落点**是开着的那块（Docs 逐片进 Docs 块），块收口后释放回 stream-{node}；"
               "抄用户原话的回显成员不发（未存 prompt 时两个成员都发，做阳性对照）；"
       "键名长过 `_KEY_CAP` 那界时整段按名单外处理（测的是**相邻两档** 128/129，不是 127/130——后者挡不住 off-by-one）；"
               "块声明 `prose_fields` 时按**键名**门控（mermaid 源码、键名本身、commands 的 args 正文都不上屏，"
               "没声明名单时与按长度挑那一版逐字一致；名单内字段是多项列表时**每一项**都要出；Task 块靠开块那条 `meta` 进落点表（没它就只落兜底行，做了阳性对照）；ActionChoice 挂名单今天零差别、而顶到界那档（36 字动作名）只有挂名单的不发；**落点表只认开块 meta 与已登记块自己的后继事件**（计划卡那颗随机 uuid 的 object 不配占落点，同角色的计划事件收敛成一颗 uuid）")


def t13_assembly_ledger_identity_recall():
    """R1 未验①的可信部分：**真装配出来的读者，计数到底落在哪一本账上**——零花费、离线。

    t43 证的是「召回的三笔语义对」（手搭的 LongTermMemory），t10 证的是「账本从 GET 出口看得见」
    （但它 `enable_rag=False`，压根不走召回）。中间缺的那截正是本格的靶子：**装配期的 `meter=` 指认**。
    它断了不会有人报错——三条装配路里漏一条（`build_hired_role` 那种「顺手挂 kb」的地方最容易漏），
    计数就永远恒 0，而恒 0 是最像结论的假读数。

      ① 三条装配路（dynamic / classic / 现场招人）的每条读腿，`meter` 必须是**同一个** manager 对象；
      ② 一次真失败的召回（embedding 指死端口，铁律 6 的唯一合法零花费姿势）必须把数记进**那一份**账本，
         并且短路位翻假、第二次不再出门；
      ③ 反对照：`meter=None` 时同样的失败**不产生任何计数**——证明 ② 的 +1 是那条指认带来的，不是恒亮。
    """
    from codeharness.configs.settings import settings
    from codeharness.memory.longterm import LongTermMemory
    from codeharness.provider.cost import CostManager
    from codeharness.team import build_hired_role, classic_team, default_team

    keep = (settings.enable_rag, settings.embedding.base_url, settings.embedding.api_key)
    settings.enable_rag = True
    settings.embedding.base_url = "http://127.0.0.1:1/v1"      # 死端口：不发任何云端请求
    settings.embedding.api_key = "dead"
    try:
        cm = CostManager()
        dyn = default_team(_make_llm_for(cm))
        cls = classic_team(_make_llm_for(cm))
        hired = build_hired_role({"name": "RecallProbe", "profile": "p", "goal": "g",
                                  "tools": []}, _make_llm_for(cm))
        legs = [(n, getattr(a, "ltm", None), "ltm") for n, a in dyn.items()] + \
               [(n, getattr(a, "kb", None), "kb") for n, a in dyn.items()] + \
               [(n, getattr(a, "ltm", None), "ltm") for n, a in cls.items()] + \
               [(n, getattr(a, "kb", None), "kb") for n, a in cls.items()] + \
               [("RecallProbe", hired.kb, "kb")]
        legs = [(n, l, tag) for n, l, tag in legs if l is not None]
        assert legs, "①装配出来一个读者都没有（enable_rag 真管事了没？）"
        for n, l, tag in legs:
            assert l.meter is cm, f"①断了：{n}.{tag} 的计数不落进 runner 那本账（会是恒 0 的假读数）"

        # ② 真失败一次：记进那一份账本、短路、第二次不出门
        probe = dyn[next(iter(dyn))].ltm
        calls = 0

        class _Dead:
            """只给 `recall` 用到的那一个方法——不必去继承 OpenAIEmbeddings（那是给自己造坑）。"""

            async def aembed_query(self, q):
                nonlocal calls
                calls += 1
                raise ConnectionError("embedding 端点不在线")

        probe.embeddings = _Dead()
        before = cm.recall_failures
        assert asyncio.run(probe.recall("任何任务", k=3)) == [], "②失效：失败没降级成空"
        assert cm.recall_failures == before + 1, \
            f"②失效：真失败没落进那本账（{before}→{cm.recall_failures}）"
        assert cm.recall_returned == 0 and cm.recall_zero_hits == 0, \
            f"②失效：失败被记成了条数或零命中（returned={cm.recall_returned} zero={cm.recall_zero_hits}）"
        assert probe.up is False
        asyncio.run(probe.recall("任何任务", k=3))
        assert calls == 1 and cm.recall_failures == before + 1, \
            f"②失效：短路没生效（端点被打了 {calls} 次，每次实测白等 2316ms）"

        # ③ 反对照：没有 meter 就没有计数
        alone = LongTermMemory(project_id="p", embeddings=_Dead(), doc_type="kb", store=probe.store)
        alone.embeddings = _Dead()
        assert asyncio.run(alone.recall("任何任务", k=3)) == [] and alone.meter is None, \
            "③对照失守：meter=None 那条路不该炸，也不该有计数"
        assert cm.recall_failures == before + 1, "③对照失守：没接账本的读者把数记到了公共账上"
        _ok("t13", f"装配身份：{len(legs)} 条读腿的 meter 全是同一本账；真失败一次 ⇒ "
                   f"recall_failures {before}→{cm.recall_failures} 且第二次不出门；meter=None 不计数")
    finally:
        settings.enable_rag, settings.embedding.base_url, settings.embedding.api_key = keep


def _make_llm_for(cm):
    """给装配函数造一个网关：`cost_manager` 必须是外面那一份（这正是被测的那根线）。"""
    from codeharness.provider.gateway import LLMGateway
    return LLMGateway(cost_manager=cm)


def t14_prose_key_positions_never_leak():
    """C129（10-02 复审批）：键名字符**永远不上屏**。

    改前「这串是不是键名」要等闭合后见冒号才定，而门控在字符过 `_emit` 的当下就拿
    `self.key`——那是**上一个成员的键**。白名单成员后跟 ≥MIN_PROSE 的名单外键名时
    （真 schema 里 PRDOutput 的 `competitive_analysis` → 26 字 `competitive_quadrant_chart`
    恰是这条相邻关系），键名趁旧键在名单内整条漏上屏；今天不显形全靠 echo 排除兜底。
    修后串开的一刻按括号栈定键位/值位：键名字符只进 buf 比对。
    两刀对照钉死边界：① 相邻键名不漏；② m8 那刀不许复活——名单内字段是多项列表时**每一项**
    照出（数组元素串是值位，不是键位）。
    """
    from server.runner import _ProseStream

    long_value = "四象限正文写得足够长，稳稳超过二十四字的门槛线没有悬念"
    payload = ('{"competitive_analysis": "%s", '
               '"competitive_quadrant_chart": "quadrantChart 字数也凑够二十四字才好办"}' % long_value)
    ps = _ProseStream("", frozenset({"competitive_analysis"}))
    out = ps.feed(payload)
    assert "competitive_quadrant_chart" not in out, \
        f"① C129：名单外键名趁旧键整条漏上屏（改前形状）：{out!r}"
    assert long_value in out, f"① 白名单成员的值不许被误伤：{out!r}"

    payload2 = ('{"product_goals": ["第一个目标也要够长才上屏，二十四字的门槛线必须过掉才行", '
                '"第二条目标同样要过线才行，也认认真真数够二十四个字再说"]} ')
    ps2 = _ProseStream("", frozenset({"product_goals"}))
    out2 = ps2.feed(payload2)
    assert "第一个目标" in out2 and "第二条目标" in out2, \
        f"② m8 那刀不许复活：数组每一项都要出（数组元素是值位）：{out2!r}"
    assert "product_goals" not in out2, "② 键名照旧不上屏"

    # ③ 嵌套对象：内层键名 ≥24 字时，改前趁「上一个键（competitive_analysis）在名单内」整条漏上屏
    #    ——这正是 C129 的本命形状。修后键位字符一律不漏；内层**值**按既有口径不逐片发
    #    （它的键是内层键、不在名单——改前改后一致，不是本件改出来的行为）。
    payload3 = ('{"competitive_analysis": {"tier_one_players_in_the_competitive_landscape": "嵌套值按既有口径不逐片发", '
                '"tier_two_players_also_with_a_very_long_name_here": "第二个嵌套值同样不发"}}')
    ps3 = _ProseStream("", frozenset({"competitive_analysis"}))
    out3 = ps3.feed(payload3)
    assert "tier_one_players" not in out3 and "tier_two_players" not in out3, \
        f"③ 嵌套对象的长键名漏上屏（C129 本命形）：{out3!r}"
    assert out3 == "", f"③ 嵌套对象的值不该逐片发（内层键不在名单，既有口径）：{out3!r}"
    print("  ok  t14 C129 键位/值位：相邻名单外键名不漏、数组元素照出（m8 反向刀）、嵌套键名不漏")


async def t15_parallel_sends_each_keep_their_block():
    """C130（10-02 复审批）：并行 Send（一条消息路由多个角色）下逐片**各投各的块**。

    改前逐片实时读 `_live_blk[sid]` 这个每会话单槽——两个内核块并发开时，后开的 meta 把登记
    覆盖掉，前一个角色的散文就投进别人块的 uuid（事件 role 还是自己的节点名，前端拼出串色块）。
    修后 start 时刻把落点快照进 `(sid, run_id)` 槽，逐片读自己那份；end 收口即弃。
    """
    tmp, store, bus, runner, s = await _make_runner()
    try:
        sink = runner._make_sink(s.id)
        # 块 A 开 → runA start → 块 B 开（覆盖单槽，改前形状的覆盖点）→ runB start
        sink({"uuid": "blk-A", "name": "meta", "block": "Thought",
              "value": {"prose_fields": ("p",)}, "role": "PM"})
        runner._translate(s.id, {"event": "on_chat_model_start", "run_id": "rA",
                                 "metadata": {"langgraph_node": "PM"}})
        sink({"uuid": "blk-B", "name": "meta", "block": "Thought",
              "value": {"prose_fields": ("p",)}, "role": "Engineer"})
        runner._translate(s.id, {"event": "on_chat_model_start", "run_id": "rB",
                                 "metadata": {"langgraph_node": "Engineer"}})
        # runA 的首片：散文按名单 `p` 抽出（echo 为空、值 ≥24 字 ⇒ 达标发布）
        runner._translate(s.id, {"event": "on_chat_model_stream", "run_id": "rA",
                                 "metadata": {"langgraph_node": "PM"},
                                 "data": {"chunk": type("C", (), {"content": '{"p": "'})()}})
        runner._translate(s.id, {"event": "on_chat_model_stream", "run_id": "rA",
                                 "metadata": {"langgraph_node": "PM"},
                                 "data": {"chunk": type("C", (), {
                                     "content": "A 角色的思考正文，长度足够跨过二十四字的门槛线"})()}})
        live = [e for e in bus.history(s.id) if e.name == "live"]
        assert live and all(e.uuid == "blk-A" for e in live), \
            f"C130：后开的块把 rA 的散文抢走了（改前形状），实投 {[e.uuid for e in live]}"
        assert all(e.role == "PM" for e in live), "rA 的事件 role 变了（串色的另一半形状）"
        # runB 的首片照旧落 blk-B（两路同拍）
        runner._translate(s.id, {"event": "on_chat_model_stream", "run_id": "rB",
                                 "metadata": {"langgraph_node": "Engineer"},
                                 "data": {"chunk": type("C", (), {"content": '{"p": "'})()}})
        runner._translate(s.id, {"event": "on_chat_model_stream", "run_id": "rB",
                                 "metadata": {"langgraph_node": "Engineer"},
                                 "data": {"chunk": type("C", (), {
                                     "content": "B 角色的思考正文，长度同样足够跨过门槛线二十四个字"})()}})
        uuids = {e.uuid for e in bus.history(s.id) if e.name == "live"}
        assert uuids == {"blk-A", "blk-B"}, f"两路各投各的没成立：{uuids}"
        # 收口：rA end ⇒ 它的快照槽即弃（不跨笔携带）
        runner._translate(s.id, {"event": "on_chat_model_end", "run_id": "rA",
                                 "metadata": {"langgraph_node": "PM"}})
        assert (s.id, "rA") not in runner._blk_of, "rA 收口后快照槽没清（长跑会攒）"
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)
    print("  ok  t15 C130 落点快照：并行 Send 下逐片各投各的块、role 不串、收口即弃")


async def t16_forgotten_run_closes_the_stream_rows():
    """C172+C173（10-05）：中断的那一场，散会必须把还开着的打字机行收掉，而且**收在落冷档之前**。

    改前收口标记只挂在 `on_chat_model_end` 上，而 cancel/异常走不到那儿——零花费真 astream 桩现证：
    被 cancel 那一笔见过的事件名止于 `on_chat_model_stream`，`on_chat_model_end` 一个没有
    （`tests/manual_stream_landing.py` ⑧，散会前后各数一次）。那块在事件流里保持开着，而前端
    **三处只认 `b.closed`**：Think 行停在 running 扫光（ReasoningRow.vue:43）、Docs 的 mermaid 永不
    水合（MarkdownText.vue:32）、轮尾行不发（turns.ts:70 ⇒ 复制/点赞/**分叉入口**一起没）。

    顺序这一半怎么钉的要说实话：冷档那条**测不出顺序**——落盘排在 `to_thread` 里，快照在 `_forget`
    返回之后才取，补发放前面还是后面，档里都有那条 end_marker。所以冷档只证「回放里那行是收口的」
    （用户看得见的就是这件事），顺序由 ⑤ 的字面守卫钉：`_forget` 体内补发那一行必须在
    `_retire_ring` 之前，变异刀 k2 把补发行挪到落盘之后就该红在 ⑤。
    （顺带量到一条既存形状：`_forget(terminal=True)` 从**没有 running loop 的线程**里调用会在
    `create_task(close_terminal(sid))` 那一句抛穿，把后面的在途表清扫与 `close_editor` 整段跳掉——
    隔壁 `_retire_ring` 有 `except RuntimeError` 就地跑的兜底，这一句没有。本件的格走主线程，
    没在生产路径上撞它，故只登记不动它：今天五处终态全在协程里。）
    ② 没收过字的行只补收口、**不补换行**：`ChatNode.vue:14` 的判据是 `open || text`，一个光秃秃的
    `\\n` 会让「收口后零字的 Think 行不渲染」那条守卫失效，屏上多一行空白行。
    ③ 正常收口那一路（end 自己发过）散会不许再补第二条。
    ④ 停在待人工（terminal=False）照样收口，但不落冷档——收口与卸出是两件事。
    """
    tmp = Path(tempfile.mkdtemp())
    store = SessionStore(path=tmp / "sessions.json")
    spill = tmp / "spill"
    bus = SessionEventBus(spill_dir=spill)
    runner = SessionRunner(store, bus)

    async def forget(sid, terminal=True):
        runner._forget(sid, terminal)
        # 旁路落盘是 fire-and-forget（`_closers`），断言之前先等它真跑完——C115 那条「异步任务没跑完
        # 就断言」的假红就是这儿埋的
        for t in list(runner._closers):
            await t

    def start(sid, rid, node):
        runner._translate(sid, {"event": "on_chat_model_start", "run_id": rid,
                                "metadata": {"langgraph_node": node}})

    def piece(sid, rid, node, content):
        runner._translate(sid, {"event": "on_chat_model_stream", "run_id": rid,
                                "metadata": {"langgraph_node": node},
                                "data": {"chunk": type("C", (), {"content": content})()}})

    def new_session(tag):
        s = store.create(tag, project_name="c172")
        store.update(s.id, status=SessionStatus.running)
        return s

    def cold(sid, uuid):
        p = spill / f"{sid}.jsonl"
        if not p.exists():
            return None
        rows = [json.loads(line) for line in p.read_text(encoding="utf-8").splitlines()]
        return [(r.get("name"), r.get("value")) for r in rows if r.get("uuid") == uuid]

    try:
        # ① 第一笔正常收口、第二笔被 cancel ⇒ 接缝两条换行，末条必须是收口标记
        s1 = new_session("c172 中断场")
        start(s1.id, "rA", "PM")
        piece(s1.id, "rA", "PM", '{"p": "')
        piece(s1.id, "rA", "PM", "甲角色的思考正文，长度足够跨过二十四字的门槛线，这一笔由 end 自己收口")
        runner._translate(s1.id, {"event": "on_chat_model_end", "run_id": "rA",
                                  "metadata": {"langgraph_node": "PM"}})
        start(s1.id, "rB", "PM")
        piece(s1.id, "rB", "PM", '{"p": "')
        piece(s1.id, "rB", "PM", "第二笔正文同样够长，它这一笔永远不会收到 end 事件")
        await forget(s1.id)
        rows = cold(s1.id, "stream-PM")
        assert rows is not None, "① 冷档没写出来，这格就没在测顺序（量法失效，判红不判绿）"
        assert rows[-1] == ("end_marker", None), \
            f"① 补发排在落冷档之后（＝改前形状，回放里那行永远开着）：末两条 {rows[-2:]}"
        assert [r for r in rows if r[0] == "live" and r[1] == "\n"] and \
            sum(1 for r in rows if r == ("live", "\n")) == 2, \
            f"① 两次调用的接缝各该有一条换行分隔（C173）：{rows}"
        assert not runner._stream_rows.get(s1.id), "① 散会后这张表还留着这一场的账"

        # ② 只有静默期建块、一个逐片都没发 ⇒ 补收口，绝不补换行
        s2 = new_session("c172 空行")
        start(s2.id, "rC", "QA")
        await forget(s2.id)
        assert cold(s2.id, "stream-QA") == [("meta", {"streaming": "QA"}), ("end_marker", None)], \
            f"② 零字的兜底行被塞了东西（那会渲染出一行空白 Think）：{cold(s2.id, 'stream-QA')}"

        # ③ 正常收口过的那一行，散会不许再补第二条 end_marker
        s3 = new_session("c172 已收")
        start(s3.id, "rD", "Eng")
        piece(s3.id, "rD", "Eng", '{"p": "')
        piece(s3.id, "rD", "Eng", "这一笔走完 on_chat_model_end，收口由它自己发")
        runner._translate(s3.id, {"event": "on_chat_model_end", "run_id": "rD",
                                  "metadata": {"langgraph_node": "Eng"}})
        await forget(s3.id)
        got3 = [r for r in (cold(s3.id, "stream-Eng") or []) if r[0] == "end_marker"]
        assert len(got3) == 1, f"③ 散会替已收口的行重复发了收口标记：{got3} 条"

        # ④ 停在待人工：收口照做，冷档不落（卸出只在 terminal=True 那一支）
        s4 = new_session("c172 待人工")
        start(s4.id, "rE", "Boss")
        piece(s4.id, "rE", "Boss", '{"p": "')
        piece(s4.id, "rE", "Boss", "停在待人工这一格测的是 terminal=False 也别忘了收口")
        await forget(s4.id, terminal=False)
        live4 = [(e.name, e.value) for e in bus.history(s4.id) if e.uuid == "stream-Boss"]
        assert live4[-1] == ("end_marker", None), f"④ 待人工散会没收口：{live4[-2:]}"
        assert not (spill / f"{s4.id}.jsonl").exists(), "④ terminal=False 不该落冷档"

        # ⑤ 顺序的字面守卫（冷档测不出顺序，原因写在上面）：`_forget` 体内补发那一行必须排在落盘之前
        import inspect
        src = inspect.getsource(type(runner)._forget)
        a, c = src.rindex("self._end_stream_rows(sid)"), src.rindex("self._retire_ring(sid)")
        assert a < c, f"⑤ 补发行（偏移 {a}）落到了 `_retire_ring`（偏移 {c}）之后：冷档里那行永远开着"
        assert src.count("self._end_stream_rows(sid)") == 1, "⑤ 分母不是一（多插一处就没人管顺序了）"
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)
    print("  ok  t16 C172/C173 散会补收兜底行：冷档末条是 end_marker（顺序有牙）、"
          "接缝有分隔、零字行不塞换行、已收不重复、待人工也收口但不落档")


def main():
    t1_add_usage_visible()
    t2_seeded_ledger()
    asyncio.run(t3_midrun_sync())
    asyncio.run(t4_ensure_graph_single_ledger())
    asyncio.run(t11_park_keeps_the_live_ledger())
    t5_lifespan_unwires_seams()
    t6_events_history_bounded()
    asyncio.run(t7_span_timing())
    t9_two_endpoints_one_ledger()
    t8_two_currency_buckets()
    t10_cost_injection_end_to_end()      # C19 未验②：注入链端到端（要起本机桩，放最后）
    asyncio.run(t12_prose_from_structured_stream())   # 流式 UX 批：抽取器与翻译层接线
    t13_assembly_ledger_identity_recall()             # R1 未验①可信部分：装配期的 meter 指认（零花费）
    t14_prose_key_positions_never_leak()              # C129：键位/值位区分（括号栈）
    asyncio.run(t15_parallel_sends_each_keep_their_block())   # C130：并行 Send 落点快照
    asyncio.run(t16_forgotten_run_closes_the_stream_rows())   # C172+C173：散会补收兜底行（顺序用冷档钉）
    print("\ns8_runner_meter: 16/16 全绿")


if __name__ == "__main__":
    main()
