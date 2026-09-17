"""S7 门禁（施工4 门禁清单）：三接缝双实现 + 跨 worker 语义 + 限流 + 计量真值。

跑法（两种配置都要绿）：
  cd /e/Codeharness && PYTHONPATH=/e/Codeharness PYTHONIOENCODING=utf-8 F:/anaconda/python.exe tests/s7_platform.py
  同上 + 环境变量 PLATFORM__USE_REDIS=1 REDIS__DB=15   # redis 模式（本脚本自带 db=15 隔离）

- 进程内部分（t1）永远跑——边界纪律「拿不到 Redis 退回进程内」的常驻证据。
- Redis 部分探活（s5 t25 姿势）：6379 不通则整段跳过并明说，**门禁不挂在外部服务上**。
- 测试键固定 db=15，开跑前清库：绝不碰开发库（开发 server 用 db=0）。
"""
import asyncio
import json
import tempfile
from pathlib import Path

from codeharness.configs.settings import RedisConfig, settings
from codeharness.schema import Document, Message

TEST_DB = RedisConfig(host=settings.redis.host, port=settings.redis.port, db=15)
REDIS_UP = False

# 与 test_e2e_classic_line 同一份剧本（t13 的 FakeLLM 线复用）
PRD = {"language": "en_us", "programming_language": "python", "original_requirements": "双runner",
       "project_name": "s7e2e", "product_goals": ["g"], "user_stories": ["u"],
       "competitive_analysis": ["a"], "competitive_quadrant_chart": "q",
       "requirement_analysis": "ra", "requirement_pool": [["P0", "core"]],
       "ui_design_draft": "s", "anything_unclear": ""}


def _ok(n, msg):
    print(f"✅ {n}: {msg}")


def _redis_up() -> bool:
    import redis
    try:
        return bool(redis.Redis.from_url(TEST_DB.to_url()).ping())
    except Exception:
        return False


def _flush_test_db():
    import redis
    redis.Redis.from_url(TEST_DB.to_url()).flushdb()


# ---------------- t1 进程内三接缝（永远跑） ----------------
def t1_inproc_roundtrip():
    from server.events import SessionEventBus
    import server.sessions as ss
    keep, ss.SESSIONS_FILE = ss.SESSIONS_FILE, Path(tempfile.mkdtemp()) / "sessions.json"
    try:
        store = ss.SessionStore()
        s = store.create("进程内冒烟", project_name="s7p")
        store.update(s.id, status="running")
        store.set_cost(s.id, {"total_prompt_tokens": 7}, persist=True)
        assert store.get(s.id).cost["total_prompt_tokens"] == 7
        bus = SessionEventBus()
        bus.publish(s.id, kind="report", block="Thought", value="x")
        assert [e.kind for e in bus.history(s.id)] == ["report"]
        from codeharness.runtime import ChatQueue
        cq = ChatQueue()
        cq.enqueue("插话", "PM")
        assert cq.drain() == [("插话", "PM")] and cq.drain() == []
        _ok("t1", "进程内 store/bus/chatqueue 往返正常（feature flag 关=默认路，S1–S6 自测据此零容器）")
    finally:
        ss.SESSIONS_FILE = keep


# ---------------- t10 checkpointer 白名单（永远跑，不依赖 redis） ----------------
def t10_checkpoint_msgpack_whitelist():
    """README 未闭合 #4：读断点每次打 "Deserializing unregistered type codeharness.schema.Message"
    （langgraph 官方声明未来版本将拦截）。双向断言：白名单**没配时必现**（证明本门禁不是空转）、
    配了之后 round-trip 静默——判据打在 warning 上，不是'序列化成功'（配前配后都成功）。"""
    import io
    import logging
    from codeharness.environment import checkpoint as ck
    from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer

    buf = io.StringIO()
    h = logging.StreamHandler(buf)
    lg = logging.getLogger("langgraph")
    lg.addHandler(h)
    lg.setLevel(logging.WARNING)
    try:
        val = {"m": Message(content="x", cause_by="UserRequirement"),
               "d": Document(filename="a.md", content="t")}
        JsonPlusSerializer().loads_typed(JsonPlusSerializer().dumps_typed(val))
        assert "unregistered" in buf.getvalue(), "前置不成立：没白名单时没出 warning，本门禁会空转"
        buf.truncate(0); buf.seek(0)
        s = ck._serde()
        back = s.loads_typed(s.dumps_typed(val))
        assert type(back["m"]).__name__ == "Message" and back["d"].filename == "a.md", back
        assert "unregistered" not in buf.getvalue(), buf.getvalue()
        _ok("t10", "checkpointer msgpack 白名单：未配必警、配了静默（Message/Document 原样回读）")
    finally:
        lg.removeHandler(h)


# ---------------- t11 start 的 409 store 化（永远跑） ----------------
def t11_start_409_store_view():
    """多 worker 下 is_running 是本地视角——别的 worker 在跑的会话，本 worker 必须也 409。
    真源=store 状态（running/awaiting_human 必有人在跑：残态由启动期 heal_running 自愈）。"""
    import server.sessions as ss
    keep, ss.SESSIONS_FILE = ss.SESSIONS_FILE, Path(tempfile.mkdtemp()) / "sessions.json"
    try:
        from fastapi.testclient import TestClient
        import server.app as sa
        with TestClient(sa.create_app()) as c:
            sid = c.post("/api/sessions", json={"idea": "双开", "project_name": "s7dual"}).json()["id"]
            c.app.state.store.update(sid, status="running")     # 模拟：另一 worker 已接手
            assert c.post(f"/api/sessions/{sid}/start").status_code == 409, \
                "store 说在跑还放行 = 双开，两 worker 同 thread_id 抢一张图"
            c.app.state.store.update(sid, status="stopped")
            started = []
            c.app.state.runner.start = lambda s: started.append(s.id)   # 打桩：守卫放行与否看这里，不真起会话
            assert c.post(f"/api/sessions/{sid}/start").status_code == 200 and started == [sid], \
                "非 running 状态不该再被 store 拦"
        _ok("t11", "start 409 过 store 判定：跨 worker 已运行的会话不再双开")
    finally:
        ss.SESSIONS_FILE = keep


# ---------------- redis 部分 ----------------
async def t2_dual_worker_replay():
    from platforms.event_store import RedisEventBus
    a, b = RedisEventBus(TEST_DB), RedisEventBus(TEST_DB)      # 两个「worker」共享 redis
    a.start(); b.start()
    try:
        for i in range(10):
            a.publish("sX", kind="report", block="Thought", value=f"m{i}", uuid=f"u{i}")
        await a.flush_now()
        full = a.history("sX")
        assert len(full) == 10 and all(full[i].seq < full[i + 1].seq for i in range(9)), "seq 非单调"
        # 游标取**真实事件 seq**（前端 ?after=<lastSeq> 的语义）：seq 编码自 stream id，不是小整数
        cut = full[2].seq
        ha, hb = a.history("sX", after_seq=cut), b.history("sX", after_seq=cut)
        assert [e.seq for e in ha] == [e.seq for e in hb] and len(ha) == 7, (len(ha), len(hb))
        assert [e.value for e in hb] == [f"m{i}" for i in range(3, 10)], hb
        _ok("t2", "双 worker：after=seq 重放在两个 worker 上逐条一致（seq 由服务端承接，单调）")
    finally:
        await a.aclose(); await b.aclose()


async def t3_cross_worker_stop():
    from platforms.session_store import RedisSessionStore
    from platforms.event_store import RedisEventBus
    import server.sessions as ss
    keep, ss.SESSIONS_FILE = ss.SESSIONS_FILE, Path(tempfile.mkdtemp()) / "sessions.json"
    store, bus = RedisSessionStore(TEST_DB), RedisEventBus(TEST_DB)
    bus.start()
    try:
        from server.runner import SessionRunner
        a = SessionRunner(store, bus)
        b = SessionRunner(store, bus)
        a.enable_redis_control(); b.enable_redis_control()
        await asyncio.sleep(0.3)      # 等订阅落地（生产在 lifespan 启动期装好，无此窗口）
        s = store.create("跨worker停", project_name="s7ctl")
        stopped = asyncio.Event()

        async def _job():
            try:
                await asyncio.sleep(30)
            except asyncio.CancelledError:
                stopped.set()
                raise
        b.tasks[s.id] = asyncio.create_task(_job())          # 会话跑在 B 上
        store.update(s.id, status="running")                  # 真跑起来 store 就是 running（stop 转发守卫读它）
        assert await a.stop(s.id) is True                     # A 手上没有 → 控制通道
        assert await a.stop(s.id) is False                    # 已 stopping：不再对没在跑的会话谎报停成功
        await asyncio.wait_for(stopped.wait(), timeout=3)     # B 收到并取消
        assert store.get(s.id).status.value == "stopping"
        _ok("t3", "跨 worker stop：PUBLISH ch:ctl，持任务的 worker 自己取消（t.cancel 只及本地）")
    finally:
        a._ctl_task.cancel(); b._ctl_task.cancel()
        await a._ctl.aclose(); await b._ctl.aclose()
        await bus.aclose()
        ss.SESSIONS_FILE = keep


async def t4_cross_worker_chat():
    from platforms.chat_queue import RedisChatQueue
    a = RedisChatQueue("s7chat", TEST_DB)
    b = RedisChatQueue("s7chat", TEST_DB)
    a.enqueue("投一条", "PM"); b.enqueue("再一条", "")
    got = b.drain()
    assert sorted(got) == [("再一条", ""), ("投一条", "PM")], got
    assert b.drain() == [] and a.drain() == []                # 逐条 LPOP：不重不漏不双喂
    _ok("t4", "跨 worker 插话：RPUSH/LPOP，投递方与消费方可以不是同一个进程")


async def t5_quota_no_oversell():
    from platforms.quota import Quota
    q = Quota(TEST_DB)
    res = await asyncio.gather(*[asyncio.to_thread(q.allow, "burst", 50, 60) for _ in range(100)])
    assert sum(res) == 50, f"放行 {sum(res)}，限流超卖/少卖"
    _ok("t5", "限流：并发 100 请求 limit=50 精确放行 50（INCR 原子，多 worker 共享配额）")


async def t6_metering_over_redis_bus():
    from platforms.session_store import RedisSessionStore
    from platforms.event_store import RedisEventBus
    import server.sessions as ss
    from codeharness.provider.cost import CostManager
    keep, ss.SESSIONS_FILE = ss.SESSIONS_FILE, Path(tempfile.mkdtemp()) / "sessions.json"
    store, bus = RedisSessionStore(TEST_DB), RedisEventBus(TEST_DB)
    bus.start()
    try:
        from server.runner import SessionRunner
        runner = SessionRunner(store, bus)
        s = store.create("计量合流", project_name="s7meter")
        cm = CostManager()
        runner.costs[s.id] = cm
        runner.projects[s.id] = "s7meter"
        cm.update_cost(2000, 100, "qwen3.8-flash")
        runner._translate(s.id, {"event": "on_chat_model_end", "metadata": {"langgraph_node": "PM"}})
        await bus.flush_now()
        st = [e for e in bus.history(s.id) if e.kind == "status"]
        assert st and st[-1].value["cost"]["total_prompt_tokens"] == 2000, st[-1] if st else "no status"
        assert store.get(s.id).cost["total_prompt_tokens"] == 2000
        _ok("t6", "计量真值过 redis bus：SSE 与 GET 两头读到同一账本实例的非零快照（s8 t3 的 redis 镜像）")
    finally:
        await bus.aclose()
        ss.SESSIONS_FILE = keep


async def t7_trace_spans():
    from platforms.trace import TraceStore
    tr = TraceStore(TEST_DB)
    tr.record("s7tr", {"node": "PM", "pt": 100, "ct": 20, "cost": 0.001, "ts": 1.0})
    tr.record("s7tr", {"node": "Engineer", "pt": 50, "ct": 10, "cost": 0.0005, "ts": 2.0})
    spans = tr.spans("s7tr")
    assert [s["node"] for s in spans] == ["PM", "Engineer"], spans
    _ok("t7", "trace（N4 数据层）：每笔 span 落 ZSET 可读，出 P95/token 归因的原料就位")


async def t8_field_level_concurrency():
    from platforms.session_store import RedisSessionStore
    a, b = RedisSessionStore(TEST_DB), RedisSessionStore(TEST_DB)
    s = a.create("并发写", project_name="s7conc")
    # 两个 worker 各改各的字段：error 与 status 互不覆盖（全量重写 JSON 的旧路正是死在这）
    a.update(s.id, error="E1")
    b.update(s.id, status="running")
    got = a.get(s.id)
    assert got.error == "E1" and got.status.value == "running", got.model_dump()
    lst = a.list()                              # list 走 zrevrange——同步客户端缺参会当场炸（多 worker 冒烟实测）
    mine = [x for x in lst if x.id == s.id]
    assert len(mine) == 1 and mine[0].error == "E1" and mine[0].status.value == "running", lst
    _ok("t8", "会话态字段级 HSET：并发改不同字段互不覆盖；get/list 读回一致")


async def t9_app_wires_redis_mode():
    """lifespan 装配全链路：flag 置位 + redis 在场 → 三接缝/trace/quota/控制通道真的换上了。
    「写好了没通电」是本项目反复应验的教训族，这条钉的就是**通电本身**。"""
    import server.sessions as ss
    from platforms.session_store import RedisSessionStore
    from platforms.event_store import RedisEventBus
    from platforms.trace import TraceStore
    keep = ss.SESSIONS_FILE, settings.platform.use_redis, settings.redis.db
    ss.SESSIONS_FILE = Path(tempfile.mkdtemp()) / "sessions.json"
    settings.platform.use_redis, settings.redis.db = True, 15
    try:
        from fastapi.testclient import TestClient
        import server.app as sa
        with TestClient(sa.create_app()) as c:
            assert c.get("/api/health").json()["ok"]
            assert isinstance(c.app.state.store, RedisSessionStore), "store 没换装"
            assert isinstance(c.app.state.bus, RedisEventBus), "bus 没换装"
            assert isinstance(c.app.state.runner.trace, TraceStore), "trace 没接上"
            assert c.app.state.quota is not None and c.app.state.runner._ctl is not None
            sid = c.post("/api/sessions", json={"idea": "装配冒烟", "project_name": "s7wire"}).json()["id"]
            c.app.state.bus.publish(sid, kind="report", block="Thought", value="w", uuid="w1")
            c.portal.call(c.app.state.bus.flush_now)
            evs = c.get(f"/api/sessions/{sid}/events/history").json()["events"]
            assert [e["kind"] for e in evs] == ["status", "report"], evs
            assert c.get(f"/api/sessions/{sid}/trace").json() == {"spans": []}
            assert c.get("/api/sessions/nope/graph").status_code == 404
        _ok("t9", "lifespan 换装通电：redis 模式下 store/bus/trace/quota/控制通道全部就位，回放走 Streams")
    finally:
        ss.SESSIONS_FILE, settings.platform.use_redis, settings.redis.db = keep


async def t12_sse_reconnect_continuity():
    """断线重连的 after=seq 语义在 redis 总线上不重不漏（SSE 活链路的等价覆盖：
    历史段走 XRANGE 补、续推段从流尾 XREAD BLOCK，两段的接缝由 seq 游标钉住）。"""
    from platforms.event_store import RedisEventBus
    bus = RedisEventBus(TEST_DB)
    bus.start()
    try:
        for i in range(5):
            bus.publish("sR", kind="report", block="Thought", value=f"r{i}")
        await bus.flush_now()
        hist = bus.history("sR")
        assert len(hist) == 5
        last = hist[-1].seq
        bus.publish("sR", kind="report", block="Thought", value="gap")   # 「断线窗口」里产生的事件
        await bus.flush_now()
        catchup = bus.history("sR", after_seq=last)                     # 重连补历史
        assert [e.value for e in catchup] == ["gap"], catchup
        q = bus.subscribe("sR")                                          # 补完后从流尾续推
        bus.publish("sR", kind="report", block="Thought", value="live")
        await bus.flush_now()
        ev = await asyncio.wait_for(q.get(), timeout=3)
        assert ev.value == "live" and ev.seq > catchup[-1].seq, ev
        _ok("t12", "SSE 断线重连：XRANGE 补洞 + 流尾续推，seq 游标不重不漏")
    finally:
        await bus.aclose()


async def t13_dual_runner_fakellm_line():
    """双 runner 共享 redis 跑一条 FakeLLM 线（切默认前的「真进程部署」等价物）：
    A 走 runner 真实路径（_run→astream_events→sink→bus→store→cost），B 全程只靠 redis
    看到状态/账本/事件，并在 A 消费前从自己这边注入插话——route drain 读的是 LIST。"""
    import codeharness.team as team
    from codeharness.provider.fake import FakeLLM
    from codeharness.roles.agent import Agent
    from codeharness.actions.prepare_documents import PrepareDocuments
    from codeharness.actions.write_prd import WritePRD
    from codeharness.const import RequirementTag
    from codeharness.schema import Message
    from codeharness.environment.team_graph import build_team
    from platforms.session_store import RedisSessionStore
    from platforms.event_store import RedisEventBus
    from platforms.chat_queue import RedisChatQueue
    from server.runner import SessionRunner
    import server.sessions as ss
    import shutil
    from server.settings import WORKSPACE_ROOT
    shutil.rmtree(WORKSPACE_ROOT / "s7e2e", ignore_errors=True)   # 旧产物会让 WritePRD 走更新路径（_is_related 还要一次剧本响应）
    keep, ss.SESSIONS_FILE = ss.SESSIONS_FILE, Path(tempfile.mkdtemp()) / "sessions.json"
    bus = RedisEventBus(TEST_DB)
    bus.start()
    try:
        factory = lambda sid: RedisChatQueue(sid, TEST_DB)
        # 两个 worker 各持**自己的** store 实例（生产形态），共享的只有 redis——B 读到的都是 A 写的
        a = SessionRunner(RedisSessionStore(TEST_DB), bus, chat_factory=factory)
        b = SessionRunner(RedisSessionStore(TEST_DB), bus, chat_factory=factory)
        s = a.store.create("双runner线", project_name="s7e2e")
        # B 不经 runner.enqueue_chat（它手上没有这个会话的队列）——直接投递到共享 LIST，
        # 语义等同 HTTP 请求打到 B worker 后 B 写 redis。目标 Ghost 不在 agents：drain 后静默丢，
        # 但 LIST 被清空本身就是「A 的 route 从 redis 消费了 B 的投递」的证据。
        factory(s.id).enqueue("另一worker的插话", "Ghost")
        def fake_prepare(idea, project, checkpointer=None, cost_manager=None):
            # FakeLLM 各带独立账本——必须接到 runner 传进来的那一个，否则合流读到的恒 0
            # （正是 s8 t4 双账本门禁钉的同一件事，这条是它的端到端版）
            def mk(script):
                f = FakeLLM(script)
                f.cost_manager = cost_manager
                return f
            pm2 = Agent({"name": "PM", "profile": "Product Manager", "goal": "PRD"},
                        [PrepareDocuments(llm=mk([])), WritePRD(llm=mk([json.dumps(PRD)]))],
                        mk([]), react_mode="BY_ORDER", max_loops=3, watch={"UserRequirement"})
            g = build_team({"PM": pm2}, checkpointer=checkpointer,
                           sop={RequirementTag.USER_REQUIREMENT: ["PM"]})
            cfg = {"configurable": {"thread_id": project}}
            init = {"messages": [Message(content=idea, cause_by=RequirementTag.USER_REQUIREMENT)],
                    "memories": {}, "docs": {}, "round": 0, "debug_rounds": 0, "finished": False}
            return g, cfg, init
        saved = team.prepare_project
        team.prepare_project = fake_prepare
        try:
            await a._run(s)
        finally:
            team.prepare_project = saved
        await bus.flush_now()
        assert b.store.get(s.id).status.value == "finished", b.store.get(s.id).status   # B 跨实例读状态
        assert RedisChatQueue(s.id, TEST_DB).drain() == []          # B 的投递被 A 消费掉了
        evs = b.bus.history(s.id)                                   # B 视角的事件流（同一个 redis，不是 A 的内存）
        assert {e.kind for e in evs} >= {"report", "status"}, sorted({e.kind for e in evs})
        cost = b.store.get(s.id).cost
        assert cost["total_prompt_tokens"] > 0, cost                # FakeLLM 记账非零 + 合流走 redis store
        _ok("t13", "双 runner 端到端：A 跑线、B 靠 redis 看状态/账本/事件，插话跨进程投递被 A 的 route 消费")
    finally:
        await bus.aclose()
        from codeharness.environment.checkpoint import close_all
        await close_all()       # t13 经 runner._saver 懒建了 AsyncSqliteSaver——不关，aiosqlite 线程吊住退出（s8 t5 同款教训）
        ss.SESSIONS_FILE = keep


async def _redis_suite():
    _flush_test_db()
    for fn in (t2_dual_worker_replay, t3_cross_worker_stop, t4_cross_worker_chat,
               t5_quota_no_oversell, t6_metering_over_redis_bus, t7_trace_spans,
               t8_field_level_concurrency, t9_app_wires_redis_mode,
               t12_sse_reconnect_continuity, t13_dual_runner_fakellm_line):
        print(f"  … {fn.__name__}", flush=True)
        try:
            # 看门狗：redis 路的任何一条卡死 60s 直接点名——挂住的门禁比失败的门禁更难查
            await asyncio.wait_for(fn(), timeout=60)
        except asyncio.TimeoutError:
            raise AssertionError(f"{fn.__name__} 卡死（60s watchdog 到点）")


def main():
    t1_inproc_roundtrip()
    t10_checkpoint_msgpack_whitelist()
    t11_start_409_store_view()
    global REDIS_UP
    REDIS_UP = _redis_up()
    if not REDIS_UP:
        print("⏭  redis 未起（127.0.0.1:6379 不通）——t2–t9/t12/t13 跳过；起容器/`docker compose up -d` 后复跑")
        print("\ns7_platform: 3/3 过（进程内路 t1+t10+t11），redis 路待环境")
        return
    asyncio.run(_redis_suite())
    _flush_test_db()
    print("\ns7_platform: 13/13 全绿（双配置）")


if __name__ == "__main__":
    main()
