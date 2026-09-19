"""S8 门禁（第一件）：runner 计量合流——还债清单 #1 的账本这条链。

钉四件事（2026-09-15 真模型首跑实测的三个洞）：
  t1  add_usage 拿不到 usage 时**必须留 warning**（静默记 0 让一场会话几十次调用只有零星入账，
      现场无从分辨哪条路没回执）；两种标准形态（usage_metadata / token_usage）照常计。
  t2  重启后 resume 的账本从 sessions.json 快照续算，不从 0 起（否则终态覆盖历史用量）。
  t3  跑动中每一笔 on_chat_model_end 都把账本合进 store + 落盘 + SSE status 事件——
      「跑动中 GET /sessions/{sid} 的 cost 恒 0」到此为止。断言打在**同一个 CostManager 实例**上。
  t4  _ensure_graph 重建路径：传给 prepare_project 的 cost_manager 必须就是 runner.costs[sid] 那个
      实例（双账本防回归，docs 第 0 步的教训）。

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
    cm = _seeded_ledger({"total_prompt_tokens": 490, "total_completion_tokens": 21, "total_cost": 0.0123})
    assert (cm.total_prompt_tokens, cm.total_completion_tokens, cm.total_cost) == (490, 21, 0.0123)
    empty = _seeded_ledger({})
    assert empty.total_prompt_tokens == 0
    broken = _seeded_ledger({"total_prompt_tokens": None, "total_cost": ""})
    assert (broken.total_prompt_tokens, broken.total_cost) == (0, 0.0)
    _ok("t2", "_seeded_ledger 从落盘快照续算，缺项/坏项退 0 不炸")


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
                 cost={"total_prompt_tokens": 490, "total_completion_tokens": 21, "total_cost": 0.05})
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
        assert (cm.total_prompt_tokens, cm.total_completion_tokens, cm.total_cost) == (490, 21, 0.05), \
            "重启路径没从快照续算"
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


def main():
    t1_add_usage_visible()
    t2_seeded_ledger()
    asyncio.run(t3_midrun_sync())
    asyncio.run(t4_ensure_graph_single_ledger())
    t5_lifespan_unwires_seams()
    t6_events_history_bounded()
    asyncio.run(t7_span_timing())
    print("\ns8_runner_meter: 7/7 全绿")


if __name__ == "__main__":
    main()
