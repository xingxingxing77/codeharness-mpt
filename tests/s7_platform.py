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
        assert await a.stop(s.id) is True                     # A 手上没有 → 控制通道
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
    _ok("t8", "会话态字段级 HSET：并发改不同字段互不覆盖（sessions.py:54 全量重写的根治）")


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


async def _redis_suite():
    _flush_test_db()
    await t2_dual_worker_replay()
    await t3_cross_worker_stop()
    await t4_cross_worker_chat()
    await t5_quota_no_oversell()
    await t6_metering_over_redis_bus()
    await t7_trace_spans()
    await t8_field_level_concurrency()
    await t9_app_wires_redis_mode()


def main():
    t1_inproc_roundtrip()
    t10_checkpoint_msgpack_whitelist()
    global REDIS_UP
    REDIS_UP = _redis_up()
    if not REDIS_UP:
        print("⏭  redis 未起（127.0.0.1:6379 不通）——t2–t9 跳过；起容器/`docker compose up -d` 后复跑")
        print("\ns7_platform: 2/2 过（进程内路 t1+t10），redis 路待环境")
        return
    asyncio.run(_redis_suite())
    _flush_test_db()
    print("\ns7_platform: 10/10 全绿（双配置）")


if __name__ == "__main__":
    main()
