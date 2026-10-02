"""S7 门禁（施工4 门禁清单）：三接缝双实现 + 跨 worker 语义 + 限流 + 计量真值。

跑法（两种配置都要绿）：
  cd /e/Codeharness && PYTHONPATH=/e/Codeharness PYTHONIOENCODING=utf-8 F:/anaconda/python.exe tests/s7_platform.py
  同上 + 环境变量 PLATFORM__USE_REDIS=1 REDIS__DB=15   # redis 模式（本脚本自带 db=15 隔离）

- 进程内部分（t1）永远跑——边界纪律「拿不到 Redis 退回进程内」的常驻证据。
- Redis 部分探活（s5 t25 姿势）：6379 不通则整段跳过并明说，**门禁不挂在外部服务上**。
- 测试键固定 db=15，开跑前清库：绝不碰开发库（开发 server 用 db=0）。
"""
import asyncio
import inspect
import json
import sys
import tempfile
import threading
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
        # C7：一边有人投一边有人取，**一条都不许丢**。route 每轮 drain，而 HTTP 线程随时 enqueue，
        # 旧写法是「快照 list(deque) → clear()」两步——中间到达的那条被 clear 一起抹掉，
        # 用户点了「追问」而系统一个字都不说。新写法逐条 get_nowait，取走即消费，没有那个窗口。
        hot, got, stop = ChatQueue(), [], threading.Event()

        def _drain():
            while not stop.is_set():
                got.extend(hot.drain())
        # 线程切换间隔压到 1µs：不压的话「快照→清空」那个窗口在 CPython 上很难撞上，
        # 这条判据就变成偶尔红的运气测试（反向验证实测：默认间隔下旧写法 3 次里过 2 次）。
        # ⚠ 别写成 `a, sys.setswitchinterval = ...`——那句会把函数本身换成 1e-6（本门禁第一版就中了）。
        old_interval = sys.getswitchinterval()
        sys.setswitchinterval(1e-6)
        try:
            watcher = threading.Thread(target=_drain, daemon=True)
            watcher.start()
            for i in range(500):
                hot.enqueue(f"插话{i}", "PM")
            stop.set()
            watcher.join(timeout=10)
            got.extend(hot.drain())
        finally:
            sys.setswitchinterval(old_interval)
        assert len(got) == 500 and sorted(got) == sorted([(f"插话{i}", "PM") for i in range(500)]), (
            f"插话通道丢/重了：投 500 收 {len(got)}（两步 drain 的窗口就是这里，别改回去）")
        # 结构判据兜底：行为那格再密也不是 100%，而「快照 + 清空」这个形状本身可以直接禁。
        # 只看 `drain` 的函数体——类 docstring 里就抄着旧写法长什么样，拿整类源码判会自己判自己。
        # B6 起 drain 在锁里逐条 `popleft`（要同一台支持 pending/remove，Queue 摘不掉中间那条），
        # 所以这条从「必须写 get_nowait」改成「必须逐条取走且体内无 clear」——旧的两步写法照样红。
        src = inspect.getsource(ChatQueue.drain)
        assert ("get_nowait" in src or "popleft" in src) and "clear" not in src, (
            "ChatQueue.drain 又退回「先快照再 clear」的两步写法了——那正是 C7 修的丢消息")
        _ok("t1", "进程内 store/bus/chatqueue 往返正常 + 插话边投边取不丢（feature flag 关=默认路）")
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
        # 白名单只管真正进过 TeamState 的类型（Message 系）。C2 删掉 docs 假通道后
        # Document/Documents 已从 _ALLOWED 摘掉，所以这一半只钉 Message——
        # 判据仍在 warning 上，不是"序列化成功"（配前配后都成功）。
        back = s.loads_typed(s.dumps_typed({"m": val["m"]}))
        assert type(back["m"]).__name__ == "Message", back
        assert "unregistered" not in buf.getvalue(), buf.getvalue()
        # 反向：Document 不该进图状态（产物走磁盘 ArtifactStore），配好的 serde 遇到它必须照旧告警——
        # 这一格防止有人把白名单放宽成"什么都放行"，也钉住 C2 的口径不反弹。
        buf.truncate(0); buf.seek(0)
        s.loads_typed(s.dumps_typed({"d": val["d"]}))
        assert "codeharness.schema.Document" in buf.getvalue(), \
            "Document 竟然不再告警——白名单被放宽，或 docs 假通道长回来了"
        buf.truncate(0); buf.seek(0)
        _ok("t10", "checkpointer msgpack 白名单：未配必警、配了 Message 静默、Document 照旧告警"
                   "（C2 删 docs 通道后白名单只剩 Message 系）")
    finally:
        lg.removeHandler(h)


# ---------------- t11 start 的 409 store 化（永远跑） ----------------
def t11_start_409_store_view():
    """多 worker 下 is_running 是本地视角——别的 worker 在跑的会话，本 worker 必须也 409。
    真源=store 状态（`running` 必有人在跑：残态由启动期 heal_running 自愈；`awaiting_human` 自 C18① 起
    是**不被自愈触碰的可信驻留态**——图可能早没了，但那一场所不许重开，去路是回答或点停止）。"""
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


def t15_project_name_cannot_escape_workspace():
    """S1（09-26 审查）：`project_name` 直接拼成 `workspace/{name}`，而 `_single_dir_name` 只判
    `Path(v).name != v`——`Path("..").name == ".."` 让它**放行 `..`**，于是 `session.workspace`
    变成 `workspace/..`，`resolve()` 就是**仓库根**；`_ws()` 正是拿 `session.workspace` 当 root，
    文件树与 `/workspace/file` 随之把整个仓（`.env` 就在里面）暴露给这个用户。
    同族第二条绕过：校验跑在**没 strip 的值**上，而 create 路由取 `req.project_name.strip()`
    ⇒ `" .. "` 通过校验、strip 完照样是 `..`。

    两格判据：① 六种越界写法必须在**建会话之前**被 422 挡下、零会话落库（不是「建了再修」）；
    ② 合法名建出来的 `session.workspace` 的 `resolve()` 必须仍在 `WORKSPACE_ROOT` 之内
    ——这才是口径，名字怎么写只是手段。"""
    import server.sessions as ss
    from server.settings import WORKSPACE_ROOT
    keep, ss.SESSIONS_FILE = ss.SESSIONS_FILE, Path(tempfile.mkdtemp()) / "sessions.json"
    try:
        from fastapi.testclient import TestClient
        import server.app as sa
        with TestClient(sa.create_app()) as c:
            before = len(c.get("/api/sessions").json())
            for bad in ("..", " .. ", ".", "a/b", "a\\b", "../x"):
                r = c.post("/api/sessions", json={"idea": "越界", "project_name": bad})
                assert r.status_code == 422, \
                    f"project_name={bad!r} 应 422，实际 {r.status_code}: {r.text[:120]}"
            assert len(c.get("/api/sessions").json()) == before, "被拒的名字居然建出了会话"
            r = c.post("/api/sessions", json={"idea": "合法", "project_name": " s7 ok "})
            assert r.status_code == 200, f"合法名（含首尾空格）被误拒：{r.text[:120]}"
            body = r.json()
            ws = Path(body["workspace"]).resolve()
            root = WORKSPACE_ROOT.resolve()
            assert ws != root and ws.is_relative_to(root), \
                f"会话工作区落到 workspace 之外或根部（越界已现形）：{ws}"
            assert body["project_name"] == "s7 ok", f"名字没归一：{body['project_name']!r}"
        _ok("t15", "project_name 越界六种写法全 422 且零落库；合法名归一后 workspace 仍在 WORKSPACE_ROOT 内")
    finally:
        ss.SESSIONS_FILE = keep


def t16_event_flusher_survives_a_failing_xadd():
    """S3（09-26 审查）：`RedisEventBus._flush_loop` 原先只有 `try/finally`（`finally` 只管
    `_inflight`），`xadd` 一抛异常就穿出 `while True` ⇒ flusher 任务当场死掉，而 `self._flusher`
    仍持着那个死任务（`start()` 只在 `is None` 时建、不重建）⇒ 一次 Redis 抖动之后
    **所有 SSE 活流与 `/events/history` 永远读不到新事件**、零日志、不重启不恢复。
    同文件的 `_reader`（订阅侧）与 `runner._listen` 都 catch + 重连，只有这一处没有——C41 的同族另一半。

    用桩客户端，**不依赖在线 Redis**。三格：① 前两发 xadd 抛错时事件**不许丢**（放回队首重试，
    最终真写进 stream）；② flusher 任务不许结束；③ 失败要留可 grep 的响，且 `cancel()` 仍能收场
    （停机只该由 `aclose()` 结束）。修复前 ① ② ③ 全红：第一发就抛，任务带异常结束、事件一条没写。"""
    async def _case():
        from collections import deque
        from platforms.event_store import RedisEventBus
        import codeharness.logs as _lgs

        bus = RedisEventBus.__new__(RedisEventBus)          # 不给 __init__ 连真 Redis 的机会
        bus.maxlen, bus._ring, bus._lock, bus._inflight = 100, deque(), threading.Lock(), 0

        class _FlakyClient:
            def __init__(self):
                self.calls, self.written = 0, []

            async def xadd(self, key, fields, maxlen=0, approximate=True):
                self.calls += 1
                if self.calls <= 2:                        # 头两发必炸：模拟 Redis 抖动
                    raise RuntimeError("redis 抖了一下")
                self.written.append(fields["d"])
                return f"{1758800000000 + self.calls}-0"

        bus.client = _FlakyClient()
        warned = []
        orig = _lgs.logger.warning
        _lgs.logger.warning = lambda *a, **k: warned.append(a)      # event_store 与本处是同一个 logger 对象
        bus._flusher = asyncio.get_running_loop().create_task(bus._flush_loop())
        try:
            bus.publish("sZ", kind="report", value="v1")
            bus.publish("sZ", kind="report", value="v2")
            for _ in range(80):                             # 失败退避 1s，最多等 4s
                if len(bus.client.written) >= 2:
                    break
                await asyncio.sleep(0.05)
            assert len(bus.client.written) == 2, \
                f"抖动之后事件没写进 stream（flusher 死了？）：written={len(bus.client.written)} calls={bus.client.calls}"
            assert not bus._flusher.done(), "flusher 任务已结束——下一次抖动后再没人搬运事件"
            assert any("事件刷盘失败" in str(w) for w in warned), \
                f"刷盘失败没留可 grep 的响（零日志=排障时看不见）：{warned}"
            bus._flusher.cancel()
            try:
                await bus._flusher
            except asyncio.CancelledError:
                pass
            assert bus._flusher.cancelled(), "停机路径：cancel() 之后任务该收场"
        finally:
            _lgs.logger.warning = orig
            if not bus._flusher.done():
                bus._flusher.cancel()

    asyncio.run(_case())
    _ok("t16", "xadd 抖动：两条事件放回队首后仍写出、flusher 不退出、warning 留痕、cancel 可收场")


def t17_event_bus_thread_safe():
    """C63：进程内 `SessionEventBus.publish` 必须顶得住**跨线程**并发。

    `LogBridge` 的 loguru sink 在**发日志的那个线程**里跑 `broadcast_log → publish`
    （`server/bridges.py:17-20`；loguru 未开 enqueue，回调跟调用方线程走），与 loop 线程的
    publish 并发。Redis 版为此专门有 `_lock`（`event_store.py:66`），进程内版此前没有：
    `_counters += 1` 非原子 ⇒ 丢更新 ⇒ 两条事件同 seq/同 cursor ⇒ 前端按
    `cursor <= lastCursor` 去重**吞掉一条**；且 `asyncio.Queue.put_nowait` 被跨线程调用。

    两格（竞态放大器 `sys.setswitchinterval(1e-6)`，C7 先例）：
      ① 计数器不丢更新：4 线程 × 250 发 + loop 线程 250 发并发 ⇒ 1250 条事件的 seq 必须
         恰好 1..1250 各一次（丢了更新就有重复+空洞），cursor 与 seq 一致；
      ② 跨线程投递：线程里 publish ⇒ 订阅者经 `call_soon_threadsafe` 转投，loop 里收得到
         （`asyncio.Queue` 非线程安全，线程侧不许直接 put）；同 loop 的 publish 照旧
         **立刻可见**（get_nowait 快路，既有语义不变）。
    """
    from server.events import SessionEventBus, cursor_of

    old = sys.getswitchinterval()
    sys.setswitchinterval(1e-6)
    try:
        async def case():
            bus = SessionEventBus()
            q = await bus.subscribe("sC")
            errs = []

            def hammer():
                try:
                    for _ in range(250):
                        bus.publish("sC", kind="log", value="t")
                except Exception as exc:                     # 线程里的崩别吞：算了 1250 条对不上就查不出来
                    errs.append(exc)

            threads = [threading.Thread(target=hammer) for _ in range(4)]
            for t in threads:
                t.start()
            for i in range(250):
                bus.publish("sC", kind="report", value=f"l{i}")
            for t in threads:
                t.join()
            assert not errs, f"线程侧 publish 抛错：{errs[:2]}"

            hist = bus.history("sC")
            seqs = [e.seq for e in hist]
            assert len(hist) == 1250, f"事件数 {len(hist)} != 1250"
            assert sorted(seqs) == list(range(1, 1251)), \
                "seq 有重复/空洞（计数器丢更新：两条事件同 seq 同 cursor，前端去重要吞一条）"
            assert all(e.cursor == cursor_of(e.seq) for e in hist), "cursor 与 seq 不一致"
            assert [e.seq for e in hist] == sorted(e.seq for e in hist), "history 非升序"

            # ② 跨线程投递 + 同 loop 快路
            q2 = await bus.subscribe("sD")

            def fire():
                bus.publish("sD", kind="log", value="from-thread")

            th = threading.Thread(target=fire)
            th.start()
            th.join()
            ev2 = await asyncio.wait_for(q2.get(), timeout=5)
            assert ev2.value == "from-thread", f"线程侧 publish 没进订阅者队列：{ev2}"
            ev3 = bus.publish("sD", kind="log", value="from-loop")
            assert q2.get_nowait() is ev3, "同 loop 的 publish 不再立刻可见（快路语义变了）"

        asyncio.run(case())
        _ok("t17", "bus 跨线程并发：1250 发 seq 恰好 1..1250 各一次、cursor 一致；"
                   "线程侧 publish 经 call_soon_threadsafe 进订阅者队列、同 loop 照旧立刻可见")
    finally:
        sys.setswitchinterval(old)


def t18_shutdown_flush_is_bounded():
    """C64：停机路径先把 ring 里的账冲完再拆 bus，且等待**必须有界**。

    `server/app.py` 的 lifespan 停机原先直接 `aclose()`——它 cancel flusher 与在途 XADD，
    ring 里没落库的事件直接丢（留账：s8:626 与 s7 t2 的测试路径都按 `flush_now → aclose`
    的顺序，唯独唯一的产线停机路径没接）。而 C51 之后失败事件**放回 ring 队首重试**：
    Redis 不可达时 ring 永不空，无界 `flush_now` 会把停机挂死 ⇒ 必须带界 grace，到点认
    「没冲完」并喊一声（C28 的 `LANGFUSE__SHUTDOWN_GRACE_SEC` 同款形状，配置 =
    `PLATFORM__SHUTDOWN_GRACE_SEC`，默认 3s；在线时 1000 条 0.07s 就冲完，B⑥ 读数）。

    三格（①② 用假 client，不碰真 Redis；③ 走真 create_app 的 lifespan 停机路径）：
      ① 有界性：xadd 必炸 ⇒ `flush_now(grace=0.3)` 在有限时间返回（包 `wait_for` 防「真挂死」
         把门禁一起拖走），且留可 grep 的「没冲完」warning（含 ring 剩量）；
      ② 阳性对照：xadd 健康 ⇒ flush 后 ring 空、事件已写出、**不打**那行 warning（恒亮=没有）；
      ③ 接线：redis 模式 create_app → 往 ring 塞必炸事件 → 退出 TestClient（触发 lifespan
         停机）⇒ warning 留痕且 bus 已被 aclose（`_flusher` 归 None）。进程内路无 ring，无此半边。
    """
    import server.settings as srv_settings  # noqa: F401  （create_app 的工作区根，门禁里不另动）
    from platforms.event_store import RedisEventBus
    import codeharness.logs as _lgs

    class _Boom:
        """xadd 恒炸的假客户端：模拟 Redis 不可达（C51 会把事件放回 ring 队首，ring 永不空）。"""
        async def xadd(self, *a, **kw):
            raise RuntimeError("redis 不可达")
        async def aclose(self):
            pass

    class _Ok:
        def __init__(self):
            self.written = []

        async def xadd(self, key, fields, maxlen=0, approximate=True):
            self.written.append(fields["d"])
            return f"{1758900000000 + len(self.written)}-0"

        async def aclose(self):
            pass

    def capture_warning():
        buf = []
        orig = _lgs.logger.warning
        _lgs.logger.warning = lambda *a, **k: buf.append(" ".join(str(x) for x in a))
        return buf, orig

    # ① 有界性 + 喊一声
    async def case_bounded():
        bus = RedisEventBus(TEST_DB)
        bus.client = _Boom()
        buf, orig = capture_warning()
        bus._flusher = asyncio.get_running_loop().create_task(bus._flush_loop())
        try:
            bus.publish("sT18", kind="report", value="v1")
            bus.publish("sT18", kind="report", value="v2")
            t0 = asyncio.get_running_loop().time()
            # 界 15s 远大于 grace 0.3s：修复前这里会挂满 15s 被掐成 TimeoutError（= 红）
            await asyncio.wait_for(bus.flush_now(grace=0.3), timeout=15)
            cost = asyncio.get_running_loop().time() - t0
            assert cost < 5, f"flush_now(grace=0.3) 花了 {cost:.1f}s——有界等待没生效"
            assert any("没冲完" in w and "还剩" in w for w in buf), \
                f"到点没喊「没冲完」（运维 grep 不到这次丢弃）：{buf}"
        finally:
            _lgs.logger.warning = orig
            if bus._flusher and not bus._flusher.done():
                bus._flusher.cancel()
            try:
                await bus._flusher
            except asyncio.CancelledError:
                pass

    asyncio.run(case_bounded())
    _ok("t18①", "ring 永不空（C51 放回重试）时 flush_now(grace=0.3) 界内返回且留「没冲完」warning")

    # ② 阳性对照：冲得动就不许喊
    async def case_healthy():
        bus = RedisEventBus(TEST_DB)
        ok_client = _Ok()
        bus.client = ok_client
        buf, orig = capture_warning()
        bus._flusher = asyncio.get_running_loop().create_task(bus._flush_loop())
        try:
            bus.publish("sT18", kind="report", value="v1")
            bus.publish("sT18", kind="report", value="v2")
            await asyncio.wait_for(bus.flush_now(grace=5), timeout=15)
            assert len(ok_client.written) == 2, f"健康路径没写出：{len(ok_client.written)}"
            assert not any("没冲完" in w for w in buf), \
                f"冲干净了还喊「没冲完」（它恒亮就等于没有）：{buf}"
        finally:
            _lgs.logger.warning = orig
            if bus._flusher and not bus._flusher.done():
                bus._flusher.cancel()
            try:
                await bus._flusher
            except asyncio.CancelledError:
                pass

    asyncio.run(case_healthy())
    _ok("t18②", "阳性对照：健康路径 flush 后事件全写出、不打「没冲完」")

    # ③ 产线接线：create_app 的 lifespan 停机真调了 flush_now(grace=…)
    if not _redis_up():
        print("  ⏭  t18③ 跳过（redis 未起）：create_app 的 redis 模式起不来，接线半边待环境")
        return
    from fastapi.testclient import TestClient
    import server.sessions as ss
    import server.app as sa
    from codeharness.configs.settings import settings as _settings

    keep_sess, keep_db = ss.SESSIONS_FILE, _settings.redis.db
    keep_flag, keep_grace = _settings.platform.use_redis, _settings.platform.shutdown_grace_sec
    ss.SESSIONS_FILE = Path(tempfile.mkdtemp()) / "sessions.json"   # 别把开发会话表搬进测试库
    _settings.redis.db = 15                                          # create_app 直读 settings：钉进测试库
    _settings.platform.use_redis = True
    _settings.platform.shutdown_grace_sec = 0.3                      # 别让这一格真等 3s
    try:
        buf, orig = capture_warning()
        with TestClient(sa.create_app()) as c:
            bus = c.app.state.bus
            assert type(bus).__name__ == "RedisEventBus", type(bus).__name__
            bus.client = _Boom()                                     # xadd 必炸 ⇒ ring 冲不出去
            bus.publish("sT18w", kind="report", value="w1")
            bus.publish("sT18w", kind="report", value="w2")
            # 退出 context ⇒ lifespan 停机：flush_now(grace) → aclose
        _lgs.logger.warning = orig
        assert any("没冲完" in w for w in buf), \
            f"停机路径没接 flush_now（ring 里的账随 aclose 直接丢了）：{buf}"
        assert bus._flusher is None, "停机没走到 aclose（flusher 还挂着）"
        print("  ok  t18③ 产线接线：lifespan 停机先 flush_now(grace) 再 aclose，到点喊「没冲完」")
    finally:
        _lgs.logger.warning = orig
        ss.SESSIONS_FILE = keep_sess
        _settings.redis.db = keep_db
        _settings.platform.use_redis = keep_flag
        _settings.platform.shutdown_grace_sec = keep_grace


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
        # 游标取**真实事件的 cursor**（前端 ?after=<cursor> 的语义）：seq 编码自 stream id，
        # 量级 1.79e18 超出 float64 安全整数，只有定宽补零的字符串游标能原样回到服务端
        cut = full[2].cursor
        assert all(len(e.cursor) == len(cut) for e in full), "cursor 必须定宽，否则字典序 != 数值序"
        assert [e.cursor for e in full] == sorted(e.cursor for e in full), "cursor 字典序非单调"
        ha, hb = a.history("sX", after=cut), b.history("sX", after=cut)
        assert [e.seq for e in ha] == [e.seq for e in hb] and len(ha) == 7, (len(ha), len(hb))
        assert [e.value for e in hb] == [f"m{i}" for i in range(3, 10)], hb
        _ok("t2", "双 worker：after=cursor 重放在两个 worker 上逐条一致（定宽补零，字典序==数值序）")
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
    # B6：跨 worker 也要能「看见 + 撤回这一条」。撤回按整条值 LREM，所以次序天生不动——
    # 这一格钉的是「摘掉中间那条，前后两条的相对次序原样」（进程内那台的同判据在 s8 t18）。
    ids = [a.enqueue(f"插话{i}", "PM") for i in range(3)]
    assert [p["id"] for p in a.pending()] == ids, f"pending 的次序不是 FIFO：{a.pending()}"
    assert a.remove(ids[1]) is True and a.remove("deadbeef") is False
    assert [p["content"] for p in a.pending()] == ["插话0", "插话2"], a.pending()
    assert b.drain() == [("插话0", "PM"), ("插话2", "PM")], "撤回一条之后剩下的被重排/丢了"
    _ok("t4", "跨 worker 插话：RPUSH/LPOP，投递方与消费方可以不是同一个进程"
              " + B6 pending/remove 平价（撤中间那条不重排）")


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
    from platforms.trace import KEY, MAX_SPANS, TraceStore
    tr = TraceStore(TEST_DB)
    # span 的形状与 runner._trace_span 一致：成本是**两桶**（C12），一笔调用只会有一个币种在动
    tr.record("s7tr", {"node": "PM", "pt": 100, "ct": 20, "cost_usd": 0.001, "cost_cny": 0, "ts": 1.0})
    tr.record("s7tr", {"node": "Engineer", "pt": 50, "ct": 10, "cost_usd": 0, "cost_cny": 0.0005, "ts": 2.0})
    spans = tr.spans("s7tr")
    assert [s["node"] for s in spans] == ["PM", "Engineer"], spans
    # B11：只加不裁 → 长跑会话的 trace 无界增长。验收 = 记 6000 条后 ZCARD==5000 且最新条在。
    # i 补零到 6 位：同一 time.time() 的多笔按 member 字典序排，不补零时 "999" > "1000" 会让断言
    # 随本机时钟粒度翻脸（补零后字典序==数值序，与插入序一致）。
    cap_sid = "s7tr_cap"
    n = MAX_SPANS + 1000
    for i in range(n):
        tr.record(cap_sid, {"node": "PM", "i": f"{i:06d}"})
    assert tr.r.zcard(KEY.format(cap_sid)) == MAX_SPANS, \
        f"记 {n} 条后 ZCARD={tr.r.zcard(KEY.format(cap_sid))}，应裁到 {MAX_SPANS}"
    kept = tr.spans(cap_sid)
    assert len(kept) == MAX_SPANS and kept[-1]["i"] == f"{n - 1:06d}", \
        f"最新条不在了：末条={kept[-1]['i'] if kept else None}"
    assert kept[0]["i"] == f"{1000:06d}", f"该被淘汰的头部没被淘汰：首条={kept[0]['i']}"
    tr.r.delete(KEY.format(cap_sid))
    _ok("t7", f"trace（N4 数据层）：每笔 span 落 ZSET 可读；记 {n} 条裁到 {MAX_SPANS} 且最新在、最老 1000 条已淘汰")


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
        last = hist[-1].cursor
        bus.publish("sR", kind="report", block="Thought", value="gap")   # 「断线窗口」里产生的事件
        await bus.flush_now()
        catchup = bus.history("sR", after=last)                          # 重连补历史
        assert [e.value for e in catchup] == ["gap"], catchup

        # 突发：seq 编码 ms*10^6+cnt 量级 1.79e18，过一遍 JS 的 JSON.parse 会舍入到
        # ulp=256，同毫秒内上千个 seq 塌缩成几个不同值——前端正是按 seq 去重才丢事件的。
        gapCursor = catchup[-1].cursor
        before = len(bus.history("sR"))
        for i in range(1000):
            bus.publish("sR", kind="report", block="Thought", value=f"b{i}", uuid=f"bu{i}")
        await bus.flush_now()
        burst = bus.history("sR", after=gapCursor)
        assert len(burst) == 1000, f"突发回放少给了：{len(burst)}/1000"
        assert len({e.cursor for e in burst}) == 1000, "cursor 不唯一，前端去重会吞事件"
        assert len({e.seq for e in burst}) > 900, "seq 本身就撞了（同毫秒 cnt 递增应保证唯一）"
        assert burst == sorted(burst, key=lambda e: e.cursor), "cursor 字典序必须等于到达序"
        # 反证：把 seq 当作前端会看到的 JS number，它确实塌缩——所以只能用 cursor
        collapsed = {int(float(e.seq)) for e in burst}
        assert len(collapsed) < 1000, "float64 未塌缩说明环境不符，此断言的保护失效"
        assert len(bus.history("sR")) == before + 1000
        q = await bus.subscribe("sR")                                          # 补完后从流尾续推
        bus.publish("sR", kind="report", block="Thought", value="live")
        await bus.flush_now()
        try:
            ev = await asyncio.wait_for(q.get(), timeout=3)
        except asyncio.TimeoutError:
            # 内层超时别冒到外层，否则报的是「60s 卡死」，把方向整个带偏成挂死
            raise AssertionError("subscribe 后 3s 内没收到续推事件——流尾游标竞态？") from None
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
        # 审批档位显式给满：这条只验跨 worker 的管线，审批本身由 s8 t8 钉（批次36 起新建
        # 会话默认 readonly，不指定档位的经典线会在第一个 Action 就挂在 gate 上）。
        s = a.store.create("双runner线", project_name="s7e2e", permission="full_access")
        # B 不经 runner.enqueue_chat（它手上没有这个会话的队列）——直接投递到共享 LIST，
        # 语义等同 HTTP 请求打到 B worker 后 B 写 redis。目标 Ghost 不在 agents：drain 后静默丢，
        # 但 LIST 被清空本身就是「A 的 route 从 redis 消费了 B 的投递」的证据。
        factory(s.id).enqueue("另一worker的插话", "Ghost")
        def fake_prepare(idea, project, agents=None, checkpointer=None, cost_manager=None, sop=None):
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
                    "memories": {}, "debug_rounds": 0, "finished": False}
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


async def t14_control_listener_survives_a_dropped_pubsub():
    """B5：跨 worker 停止的监听断了，必须**留日志并重连**，不许静默退出。

    旧写法 `except Exception: return`（注释的理由是「停机时连接被关是预期路径，不留孤儿任务栈」）
    顺手把日志也留没了。后果可查：监听一旦因 Redis 抖动退出，跨 worker 的「停止」就无声失效，
    而 `stop()` 那半程**已经**把状态写成 stopping、PUBLISH 完返回 True ⇒ 会话钉死在 stopping、
    一行日志都没有，只能重启进程（t3 旁边那条 `awaiting_human` 分支就是上一轮同一个病的修复现场）。
    三格：
      ① 订阅流断掉之后监听任务**不许结束**（`_ctl_task.done()` 必须为假）；
      ② 必须留下一条 warning（抓 `server.runner.logger`，与 `_fail` 那条 `[session-failed]` 同形）；
      ③ 重连之后跨 worker 停止**照旧到位**（真 PUBLISH → 持任务的 worker 真取消）——少了这格，
         ②可能只是「进程活着但通道死了」的假活。
    造断线的办法是在监听任务真正跑起来**之前**把客户端换成「第一次订阅必断」的代理：`create_task`
    不 await 就不跑，这个窗口是确定的，不必去猜 redis 的断连时序。
    """
    from platforms.session_store import RedisSessionStore
    from platforms.event_store import RedisEventBus
    import server.runner as runner_mod
    from server.runner import SessionRunner
    import server.sessions as ss

    class _DeadPubsub:
        def __init__(self, exc):
            self._exc = exc

        async def subscribe(self, *a, **kw):
            raise self._exc                       # 模拟 Redis 抖一下：订阅这一跳就断

        async def aclose(self):
            return None

    class _FlakyClient:
        def __init__(self, real, exc):
            self.real, self.exc, self.calls = real, exc, 0

        def pubsub(self):
            self.calls += 1
            return _DeadPubsub(self.exc) if self.calls == 1 else self.real.pubsub()

        async def publish(self, *a, **kw):
            return await self.real.publish(*a, **kw)

        async def aclose(self):
            await self.real.aclose()

    class _Rec:
        def __init__(self):
            self.warnings = []

        def warning(self, m):
            self.warnings.append(str(m))

        def info(self, m):
            pass

        def error(self, m):
            self.warnings.append(str(m))

    keep_sess, ss.SESSIONS_FILE = ss.SESSIONS_FILE, Path(tempfile.mkdtemp()) / "sessions.json"
    # `enable_redis_control` 自己按 settings 建客户端 ⇒ 这里把 db 钉到 15（不靠外面那个环境变量）
    keep_db, settings.redis.db = settings.redis.db, TEST_DB.db
    bus = RedisEventBus(TEST_DB)
    bus.start()
    rec, saved_logger = _Rec(), runner_mod.logger
    runner_mod.logger = rec
    store = RedisSessionStore(TEST_DB)
    a, b = SessionRunner(store, bus), SessionRunner(store, bus)
    try:
        b.enable_redis_control()
        a.enable_redis_control()
        flaky = _FlakyClient(a._ctl, ConnectionError("redis 抖了一下"))
        a._ctl = flaky                            # 任务还没跑：这一换是确定的
        for _ in range(50):
            await asyncio.sleep(0.1)
            if flaky.calls >= 2:                  # 断一次（2s 退避）后重连成功
                break
        assert flaky.calls >= 2, f"监听没重连（只订阅了 {flaky.calls} 次）"
        assert not a._ctl_task.done(), "监听任务在断线后结束了——跨 worker 停止从此无声失效"
        assert any("监听断了" in w for w in rec.warnings), f"断线没留任何日志（B5）：{rec.warnings}"

        s = store.create("监听重连", project_name="s7ctl2")
        stopped = asyncio.Event()

        async def _job():
            try:
                await asyncio.sleep(30)
            except asyncio.CancelledError:
                stopped.set()
                raise
        b.tasks[s.id] = asyncio.create_task(_job())
        store.update(s.id, status="running")
        assert await a.stop(s.id) is True
        await asyncio.wait_for(stopped.wait(), timeout=5)     # ③ 重连之后通道真活着
        _ok("t14", "监听断线不退出：留 warning + 2s 重连，且重连后跨 worker 停止照旧到位")
    finally:
        runner_mod.logger = saved_logger
        a._ctl_task.cancel()
        b._ctl_task.cancel()
        await a._ctl.aclose()
        await b._ctl.aclose()
        await bus.aclose()
        ss.SESSIONS_FILE = keep_sess
        settings.redis.db = keep_db


async def t21_sse_queue_bounded_and_offloop():
    """C90（09-28 全量审查批）：SSE 订阅队列**有界**（慢消费丢最旧）+ 建流「读流尾」不在 loop 上。

    现象：`RedisEventBus.subscribe` 原先 `asyncio.Queue()` **无界**——一条慢消费（后台标签页、
    断了没关的 SSE 连接）把内存拖到 O(未消费事件数)，一场的 token delta 就是成千上万条；
    且在 **loop 线程**上直接跑同步 Redis 读流尾（「亚毫秒」是同机房 RTT 的平均值不是上界，
    Redis 一 hiccup 整个 loop 的所有会话一起卡）。
    修法：队列 `maxsize=MAX_SSE_QUEUE` 满了丢**最旧**（游标语义下旧事件重连走
    `/events/history` 补，每 500 条留一行 `[sse-drop]` warning）；读流尾 `await
    asyncio.to_thread(...)`；进程内 `SessionEventBus.subscribe` 一起改 async（调用方统一 await）。

    三格：
      ① **有界 + 丢最旧**：不消费灌 MAX+50 条 + 一条哨兵 ⇒ 队列恰好 MAX 条、队首是被丢后
         的第一第（c51）、哨兵（最新）在队尾；
      ② 留 `[sse-drop]` warning（静默丢不许）；
      ③ **不在 loop 上**：把 `_sync.xrevrange` 换成睡 0.3s 的假货再建流，事件循环照常跳
         （s19 t1 的 tick 计数同款；修复前是同步调用 ⇒ ticks=0）。
    """
    import io
    import time as _t
    from codeharness.logs import logger               # s7 里 logger 也是局部导入（同 C76 的 t17）
    from platforms.event_store import MAX_SSE_QUEUE, RedisEventBus
    bus = RedisEventBus(TEST_DB)
    bus.start()
    try:
        # ③ 建流不卡 loop：同步读流尾换成睡 0.3s 的假货，loop 的 ticker 照常跳
        ticks = {"n": 0}
        orig_xrev = bus._sync.xrevrange
        bus._sync.xrevrange = lambda *a, **k: (_t.sleep(0.3), [])[1]
        try:
            stop = asyncio.Event()

            async def ticker():
                while not stop.is_set():
                    ticks["n"] += 1
                    await asyncio.sleep(0.05)
            tk = asyncio.create_task(ticker())
            try:
                q0 = await asyncio.wait_for(bus.subscribe("sC90x"), timeout=10)
            finally:
                stop.set()
                await tk
        finally:
            bus._sync.xrevrange = orig_xrev
        assert ticks["n"] >= 3, \
            f"t21③ 建流期间 loop 只跳了 {ticks['n']} 次（0.3s/50ms 应约 6 次）——读流尾还在 loop 上"
        bus.unsubscribe("sC90x", q0)

        # ①② 有界 + 丢最旧 + warning
        sid = "sC90"
        buf = io.StringIO()
        hid = logger.add(buf, format="{message}", level="WARNING")
        try:
            q = await bus.subscribe(sid)                 # 从流尾起：此前流里的事件不进队列
            for i in range(MAX_SSE_QUEUE + 50):
                bus.publish(sid, kind="report", block="Thought", value=f"c{i}")
            bus.publish(sid, kind="report", block="Thought", value="SENTINEL")
            await bus.flush_now()

            async def _sentinel_in(limit=20.0):
                end = asyncio.get_running_loop().time() + limit
                while asyncio.get_running_loop().time() < end:
                    if any(getattr(x, "value", "") == "SENTINEL" for x in list(q._queue)):
                        return True
                    await asyncio.sleep(0.05)
                return False
            assert await _sentinel_in(), "哨兵 20s 没进队列（reader 没跑？）"
            got = []
            while not q.empty():
                got.append(q.get_nowait())
            assert len(got) == MAX_SSE_QUEUE, \
                f"t21① 队列里 {len(got)} 条（该恰好 {MAX_SSE_QUEUE}——无界队列这里会是 {MAX_SSE_QUEUE + 51}）"
            assert got[-1].value == "SENTINEL", f"t21① 最新的哨兵必须还在（丢的是最旧）：{got[-1].value!r}"
            assert got[0].value == "c51", \
                f"t21① 队首是 {got[0].value!r}（该是 c51：MAX+51 条里丢掉最旧 51 条）"
            assert "[sse-drop]" in buf.getvalue(), \
                f"t21② 丢了事件却没留可 grep 的 warning：{buf.getvalue()[:200]!r}"
        finally:
            logger.remove(hid)
            bus.unsubscribe(sid, q)
        _ok("t21", f"SSE 订阅队列有界：灌 {MAX_SSE_QUEUE + 51} 条 ⇒ 队列恰好 {MAX_SSE_QUEUE} 条、"
                   f"丢的是最旧 51 条（哨兵在队尾）、[sse-drop] 有留、建流不卡 loop（ticks={ticks['n']}）")
    finally:
        await bus.aclose()


async def t22_inproc_sse_queue_bounded_like_redis():
    """C103（P0-3）：**进程内那台 bus 的订阅队列也要有界**——与 t21 成对，同判同形状。

    为什么必须有这支：C90 只补了 Redis 版（`platforms/event_store.py` 的 `subscribe`），而
    `PLATFORM__USE_REDIS=0` 才是默认档（Redis 不可达时 `create_app` 退回 `SessionEventBus`），
    进程内那台的 `asyncio.Queue()` 一直无界；`t21` 又只构造 `RedisEventBus` ⇒ **默认档零判据**。
    五格：
      ① 两台 bus 的**运行时队列** `maxsize` 都等于同一个 `MAX_SSE_QUEUE`（拿真队列问，不是读常量——
         读常量只会证明"代码里写了个数"，两态同判这条也正是 (d) 要求不许省的）；
      ② 进程内灌 `MAX+51` 条不消费 ⇒ 队列恰好 `MAX`、队首是 `c51`（丢的是最旧 51 条）、哨兵在队尾；
      ③ 留可 grep 的 `[sse-drop]`（静默丢不许）；
      ④ 阳性对照：正常消费速率下一条不丢、计数为 0、也不喊（否则③是恒绿的空警告）；
      ⑤ 字面守卫：`publish` 体内不许再出现裸 `q.put_nowait` / `call_soon_threadsafe(q.put_nowait, …)`
         ——两个投递点必须都走 `_offer`，以后谁加第三个投递点绕开有界路径就地红（C63 同族的病）。
    """
    import inspect
    import io
    from codeharness.logs import logger
    from server.events import MAX_SSE_QUEUE, SessionEventBus

    # ⑤ 先钉形状：两处投递都收进 _offer
    src = inspect.getsource(SessionEventBus.publish)
    assert "q.put_nowait(ev)" not in src, \
        "t22⑤ publish 里又出现裸 put_nowait——这条连接绕开了有界投递（满队列会无声撑内存）"
    assert "call_soon_threadsafe(q.put_nowait" not in src, \
        "t22⑤ 线程侧转投没走 _offer（同上一条：有界与丢最旧只在一个地方生效才有意义）"
    assert "self._offer(" in src and "self._offer, sid" in src, \
        "t22⑤ publish 不再调用 _offer ⇒ 有界逻辑整条被绕过，①②③ 全是空判"

    ib = SessionEventBus()
    pair = {}

    async def _shape_only():
        from platforms.event_store import RedisEventBus
        rb = RedisEventBus(TEST_DB)
        rb.start()
        try:
            q = await rb.subscribe("t22shape")
            pair["rb"] = rb
            pair["redis_max"] = q.maxsize
            ib_q = await ib.subscribe("t22shape")
            pair["inproc_max"] = ib_q.maxsize
            rb.unsubscribe("t22shape", q)                  # 同步口（只有 subscribe/aclose 是 async）
            ib.unsubscribe("t22shape", ib_q)
        finally:
            await rb.aclose()
    if REDIS_UP:
        await _shape_only()
        assert pair["redis_max"] == MAX_SSE_QUEUE and pair["inproc_max"] == MAX_SSE_QUEUE, \
            (f"t22① 两台 bus 的运行时队列上界不是同一个数：Redis={pair['redis_max']} "
             f"进程内={pair['inproc_max']} 常量={MAX_SSE_QUEUE}（两条 bus 共用一个数是 C103 的原命题）")
    else:
        q0 = await ib.subscribe("t22shape")
        assert q0.maxsize == MAX_SSE_QUEUE, f"t22① 进程内队列 maxsize={q0.maxsize}（该 {MAX_SSE_QUEUE}）"
        ib.unsubscribe("t22shape", q0)
        print("  ⚠ t22① Redis 那台未测（REDIS_UP=False），这一轮『两态同判』只算了进程内那一半")

    # ②③ 满档：灌 MAX+51 不消费
    sid = "t22over"
    q = await ib.subscribe(sid)
    buf = io.StringIO()
    hid = logger.add(buf, format="{message}", level="WARNING")
    try:
        # 与 t21 同数量：`MAX+50` 条 c 事件 + 1 条哨兵 = 共灌 `MAX+51` 条 ⇒ 溢出正好 51 条、队首 c51。
        # （本格第一版写成 `MAX+51` 条 c 事件，实测队首是 c52——门禁把我的 off-by-one 抓出来了，
        #   改期望对齐 t21，而不是把断言顺手改成 c52。）
        for i in range(MAX_SSE_QUEUE + 50):
            ib.publish(sid, kind="report", block="Thought", value=f"c{i}")
        ib.publish(sid, kind="report", block="Thought", value="SENTINEL")
        got = []
        while not q.empty():
            got.append(q.get_nowait())
        assert len(got) == MAX_SSE_QUEUE, \
            f"t22② 队列里 {len(got)} 条（无界队列会是 {MAX_SSE_QUEUE + 51} ⇒ 慢消费者把内存撑开就是这条格要拦的）"
        assert got[-1].value == "SENTINEL", f"t22② 丢的必须是最旧：队尾现在是 {got[-1].value!r}"
        assert got[0].value == "c51", f"t22② 队首 {got[0].value!r}（该是 c51：丢了最旧 51 条）"
        assert "[sse-drop]" in buf.getvalue(), \
            f"t22③ 丢了 51 条却没留可 grep 的 warning：{buf.getvalue()[:160]!r}"
    finally:
        logger.remove(hid)
        ib.unsubscribe(sid, q)

    # ④ 阳性对照：正常消费 ⇒ 一条不丢、也不喊
    sid2 = "t22ok"
    q2 = await ib.subscribe(sid2)
    buf2 = io.StringIO()
    hid2 = logger.add(buf2, format="{message}", level="WARNING")
    try:
        for i in range(60):
            ib.publish(sid2, kind="report", block="Thought", value=f"d{i}")
            while not q2.empty():                 # 每发一条就抽干＝正常消费速率
                q2.get_nowait()
        left = q2.qsize()
        assert left == 0 and ib._dropped.get(q2, 0) == 0, \
            f"t22④ 正常消费也被丢了？队列剩 {left}、该连接计数 {ib._dropped.get(q2)}"
        assert "[sse-drop]" not in buf2.getvalue(), "t22④ 没丢事件却喊了 [sse-drop]（那③就成恒绿噪音）"
    finally:
        logger.remove(hid2)
        ib.unsubscribe(sid2, q2)
    assert q2 not in ib._dropped, "t22⑤ unsubscribe 没带走该连接的丢包计数（断开的连接一直占着字典）"
    _ok("t22", f"进程内 SSE 订阅队列有界（与 t21 成对）：两台 bus 运行时 maxsize 同为 {MAX_SSE_QUEUE}、"
               f"灌 {MAX_SSE_QUEUE + 51} 条 ⇒ 恰好 {MAX_SSE_QUEUE}、丢最旧 51、[sse-drop] 有留、"
               "正常消费一条不丢且不喊、publish 里两处投递都走 _offer")


async def _redis_suite():
    _flush_test_db()
    for fn in (t2_dual_worker_replay, t3_cross_worker_stop, t4_cross_worker_chat,
               t5_quota_no_oversell, t6_metering_over_redis_bus, t7_trace_spans,
               t8_field_level_concurrency, t9_app_wires_redis_mode,
               t12_sse_reconnect_continuity, t13_dual_runner_fakellm_line,
               t14_control_listener_survives_a_dropped_pubsub,
               t21_sse_queue_bounded_and_offloop):
        print(f"  … {fn.__name__}", flush=True)
        try:
            # 看门狗：redis 路的任何一条卡死 60s 直接点名——挂住的门禁比失败的门禁更难查
            await asyncio.wait_for(fn(), timeout=60)
        except asyncio.TimeoutError:
            raise AssertionError(f"{fn.__name__} 卡死（60s watchdog 到点）")


def t19_entry_body_limits_and_deploy_coherence():
    """C86+C88（09-28 全量审查批）：入口的字段/body 上界，与部署层的口径是否成对。

    C86 现象：`/chat` 的 `content`、建会话的 `idea`、`/human-input` 的 `content`、招人档案三字段
    原先**只判 `min_length`**——auth **关**时这些端点不需要登录（`current_user` 恒 "default"），
    整段 body 先被 Starlette 读进内存、再进队列/会话表/checkpointer/prompt；`import_repo` 还把
    `request.json()` 排在**归属判定之前**（B11/C47 那条「边界判定先于昂贵操作」的同族第三处）。
    C88 现象：`frontend/nginx.conf` 的 `client_max_body_size 25m` 与业务口径（20 文件 × 20MB）矛盾
    ——多文件一次传合计超 25MB 会被网关**裸 413** 掐掉，走不到 `upload_kb` 的逐条 `errors[]` 回执。

    四格：
      ① **字段上限**：超长 `idea` ⇒ 422 且**零会话落库**（边界先于副作用）；合法长度 200（阳性对照）。
      ② **body 档位（真发）**：3MB 的 JSON body ⇒ **413**（中间件在读内存之前挡）；1MB 的同形请求
         不许是 413（阈值两侧各测一次）。
      ③ **分档函数**：`multipart/form-data` 走上传大档、`application/json` 走小档，且大档 ≥ 400MB
         ——不然真发 400MB 太重（只判函数）。
      ④ **部署口径成对**：`nginx.conf` 的 `client_max_body_size` ≥ 业务上限之和。谁把它改小谁当场红
         ——这是 C88 的常驻守卫（改回 25m 就红）。
    """
    import re
    import server.sessions as ss
    from server.api.sessions import MAX_IDEA_CHARS
    keep, ss.SESSIONS_FILE = ss.SESSIONS_FILE, Path(tempfile.mkdtemp()) / "sessions.json"
    try:
        from fastapi.testclient import TestClient
        import server.app as sa
        with TestClient(sa.create_app()) as c:
            # ① 字段上限：超长被 422 挡在副作用之前
            before = len(c.get("/api/sessions").json())
            r = c.post("/api/sessions", json={"idea": "x" * (MAX_IDEA_CHARS + 1), "project_name": "s7big"})
            assert r.status_code == 422, f"超长 idea 应 422，实际 {r.status_code}: {r.text[:120]}"
            assert len(c.get("/api/sessions").json()) == before, "被拒的 idea 居然建出了会话"
            ok = c.post("/api/sessions", json={"idea": "合法长度", "project_name": "s7len"})
            assert ok.status_code == 200, f"合法 idea 被误拒：{ok.text[:120]}"
            sid = ok.json()["id"]

            # ② body 档位：阈值两侧各测一次（同一个请求形状，只差体积）
            big = json.dumps({"content": "y" * (3 * 1024 * 1024)})
            rb = c.post(f"/api/sessions/{sid}/chat", content=big,
                        headers={"content-type": "application/json"})
            assert rb.status_code == 413, f"3MB 的 JSON body 应 413，实际 {rb.status_code}: {rb.text[:120]}"
            mid = json.dumps({"content": "y" * (1024 * 1024)})
            rm = c.post(f"/api/sessions/{sid}/chat", content=mid,
                        headers={"content-type": "application/json"})
            assert rm.status_code != 413, f"1MB 的 body 不该触发 413（阈值判错了）：{rm.status_code}"

            # ③ 分档：multipart 走大档（真发 400MB 太重，只判函数）
            assert sa._body_cap_for("application/json") == sa._MAX_JSON_BODY_BYTES, "JSON 没走小档"
            assert sa._body_cap_for("multipart/form-data; boundary=X") == sa._MAX_UPLOAD_BODY_BYTES, \
                "multipart 没走上传大档"
            assert sa._MAX_UPLOAD_BODY_BYTES >= 400 * 1024 * 1024, \
                f"上传档 {sa._MAX_UPLOAD_BODY_BYTES} 比业务口径（20×20MB）还小"
    finally:
        ss.SESSIONS_FILE = keep

    # ④ 部署口径成对（静态守卫：nginx 那一层不许比业务上限更紧）
    from server.api.workspace import MAX_UPLOAD_BYTES, MAX_UPLOAD_FILES
    nginx = (Path(__file__).resolve().parents[1] / "frontend" / "nginx.conf").read_text(encoding="utf-8")
    m = re.search(r"client_max_body_size\s+(\d+)\s*([mk])", nginx, re.I)
    assert m, "nginx.conf 里找不到 client_max_body_size（部署层没了这道口径）"
    size = int(m.group(1)) * (1024 * 1024 if m.group(2).lower() == "m" else 1024)
    need = MAX_UPLOAD_BYTES * MAX_UPLOAD_FILES
    assert size >= need, \
        f"nginx 只放 {size // 1048576}MB 而业务口径要 {need // 1048576}MB —— 多文件上传会被网关裸 413 掐掉（C88）"
    _ok("t19", f"入口上界与部署口径成对：超长 idea→422（零会话落库）、3MB JSON→413、1MB 放行、"
               f"multipart 走 {sa._MAX_UPLOAD_BODY_BYTES // 1048576}MB 大档、nginx {size // 1048576}MB ≥ "
               f"业务 {need // 1048576}MB")


def t20_approval_ledger_dual_impl_and_two_readers():
    """C87（09-28 全量审查批）：审批台账的两条读路必须拿到**同一个**台账，且无 Redis 档真有实现。

    现象：`runner._session_ctx` 无条件建 Redis 版 `ApprovalStore(sid)`，approvals 路由又各建各的
    ——Redis 不可达的部署（app.py 明写「退回进程内实现」）里，store/bus 都退了、**唯独台账没退**：
    只读档（默认）第一个写工具就在 gate 上 `ConnectionError`；且两条读路各拿各的对象、永远对不上。
    修法 = `platforms/approval_store.py::ledger_for(sid)` 唯一出口（模式首调定死 + 每会话注册表），
    两条读路都改走它。

    三格：
      ① 两条读路同对象：进程内档同 sid 两次 `ledger_for` 是**同一个实例**（Redis 档各建各的、
         真源在 Redis，天然成立——按配置分支断言，别把「同对象」错钉到 Redis 档上）；
      ② 进程内实现与 Redis 版**同语义**：request 新建 True/重放 False（HSETNX）、pending 按 ts
         排序且滤掉已决议、decide 首个回执生效、decision/settled/item 各半；
      ③ 端到端（TestClient）：往 `ledger_for(sid)` 登记 → GET /approvals 真列出来 → POST respond
         真回执 → 再 GET 卡没了、重复回执按首个生效（「内核登记的卡 HTTP 能读能回」）。
         `answer_human` 按 t11 的先例打桩——这格盯的是台账，不是图的恢复。
    """
    from platforms.approval_store import (ApprovalStore, InProcessApprovalStore, ledger_for,
                                          new_item)
    # ⓪ 模式判定（C87 的另一半）：「use_redis 关」与「置位但不可达」都必须退到进程内——
    # 修前 app.py 只把 store/bus 退了，台账没退（这正是原症状）。`_MODE` 与 from_url 都要在
    # finally 里还原，否则污染本进程后面的格。
    import platforms.approval_store as ap
    import redis as _redis
    from codeharness.configs.settings import settings
    saved = (ap._MODE, settings.platform.use_redis, ap.redis.Redis.from_url)
    try:
        ap._MODE = None
        settings.platform.use_redis = False
        led = ap.ledger_for("s7t20mode")
        assert isinstance(led, InProcessApprovalStore), \
            f"use_redis 关却给了 {type(led).__name__}（台账没跟着 store/bus 一起退）"
        ap._MODE = None
        settings.platform.use_redis = True

        def _down(*_a, **_k):
            raise _redis.ConnectionError("s7t20: redis down")
        ap.redis.Redis.from_url = staticmethod(_down)
        led2 = ap.ledger_for("s7t20down")
        assert isinstance(led2, InProcessApprovalStore), \
            f"置位但不可达却给了 {type(led2).__name__}（C87 的原症状没治）"
        assert ap._MODE == "memory", "退回进程内时模式没记下来（下一次又会去 ping）"
    finally:
        ap._MODE, settings.platform.use_redis, ap.redis.Redis.from_url = saved

    # ① 两条读路同对象
    a, b = ledger_for("s7t20"), ledger_for("s7t20")
    if isinstance(a, InProcessApprovalStore):
        assert a is b, "进程内档两次 ledger_for 不是同一个对象（两条读路会对不上，C87 的根因）"
    else:
        assert isinstance(a, ApprovalStore) and isinstance(b, ApprovalStore), \
            f"redis 档该给 ApprovalStore，实际 {type(a).__name__}"

    # ② 进程内实现与 Redis 版同语义
    mem = InProcessApprovalStore("s7t20mem")
    it0 = new_item("a0", "read_file", "{}", "只读不用批", "readonly", "readonly")
    it1 = new_item("a1", "write_file", '{"path":"x"}', "要写盘", "workspace_write", "readonly")
    assert mem.request(it1) is True and mem.request(it1) is False, "重放必须命中（HSETNX 语义）"
    assert mem.request(it0) is True
    assert [x["id"] for x in mem.pending()] == ["a0", "a1"], "pending 该按 ts 排序"
    assert mem.decide("a1", "rejected") == "rejected" \
        and mem.decide("a1", "allowed-once") == "rejected", "首个回执生效，后来的改不动"
    assert mem.decision("a1") == "rejected" and mem.decision("a0") is None
    assert [x["id"] for x in mem.pending()] == ["a0"], "已决议的要从 pending 里滤掉"
    assert mem.settled()[0]["id"] == "a1" and mem.settled()[0]["outcome"] == "rejected"
    assert mem.item("a0")["tool"] == "read_file" and mem.item("nope") is None

    # ③ 端到端：内核侧登记的卡，HTTP 读得到、回执得掉
    import server.sessions as ss
    keep, ss.SESSIONS_FILE = ss.SESSIONS_FILE, Path(tempfile.mkdtemp()) / "sessions.json"
    try:
        from fastapi.testclient import TestClient
        import server.app as sa
        with TestClient(sa.create_app()) as c:
            sid = c.post("/api/sessions",
                         json={"idea": "审批台账", "project_name": "s7ledger"}).json()["id"]
            c.app.state.runner.answer_human = lambda _sid, _content: False   # t11 同款打桩
            ledger_for(sid).request(new_item("aid-e2e", "write_file", '{"path":"x"}', "要写盘",
                                             "workspace_write", "readonly"))
            r = c.get(f"/api/sessions/{sid}/approvals")
            assert [x["id"] for x in r.json()["pending"]] == ["aid-e2e"], \
                f"HTTP 读不到内核登记的卡（两条读路没对上）：{r.text[:150]}"
            rr = c.post(f"/api/sessions/{sid}/approvals/aid-e2e/respond", json={"outcome": "rejected"})
            assert rr.status_code == 200, f"回执失败：{rr.text[:150]}"
            assert c.get(f"/api/sessions/{sid}/approvals").json()["pending"] == [], "回执后卡还在"
            again = c.post(f"/api/sessions/{sid}/approvals/aid-e2e/respond",
                           json={"outcome": "allowed-once"}).json()
            assert again["outcome"] == "rejected", f"重复回执没按首个生效：{again}"
    finally:
        ss.SESSIONS_FILE = keep
    _ok("t20", "审批台账：两条读路同对象（进程内档注册表 / Redis 档真源）、内存实现与 Redis 版同语义、"
               "HTTP 列卡与回执端到端真通")


def t23_chunked_body_must_carry_content_length():
    """C109：`Content-Length` 缺失时，JSON 腿原先**整段读进内存**才由 pydantic 拒。

    `server/app.py` 那道 `guard_body_size` 只判 `length.isdigit()`——缺该头（chunked）时整支闸不参与，
    而 `Request.json()` 会把流读完再解析，字段上限发生在缓冲**之后**。探针现证（10-01 00:1x，裸 socket
    对隔离实例 8799 发 chunked）：8MB 的 chunked JSON 回的是 **422 `string_too_long`**、不是 413。
    修法取「非 multipart 且缺长度 ⇒ **一个字节都不读**、直接 411」——试过的「带界预读 + 重建 receive」
    被实测否掉（Starlette 1.6 的 `BaseHTTPMiddleware` 把内层 app 接到它自己的 `wrapped_receive`，
    中间件改 `request._receive` 传不下去，症状是合法 chunked 请求变 422、会话压根没建出来）。

    五格：
      ① chunked 的合法小体 ⇒ **411**（改前是 200——体被读完、路由正常受理，那才是漏的那一面）；
      ② **同一具请求**改成带 `Content-Length` ⇒ 200（阳性对照：闸没把好请求一起挡掉，也不改字段口径）；
      ③ 被 411 拒掉的那几发**零副作用**（会话数一格没涨）；
      ④ multipart 且缺长度 ⇒ **不许**是 411/413（上传那一支仍由 `workspace.py` 门口的逐条判据管，
         本件不顺手改它；路由那边解析失败是另一回事，只判状态码不是这两个）；
      ⑤ 字面守卫：`server/app.py` 里不许再出现 `request._receive =`（本仓实测否掉的流手术，别被
         「看起来更周到」的版本写回来）。
    """
    import json
    import server.sessions as ss
    keep, ss.SESSIONS_FILE = ss.SESSIONS_FILE, Path(tempfile.mkdtemp()) / "sessions.json"
    try:
        from fastapi.testclient import TestClient
        import server.app as sa

        def chunks(payload, size=4096):
            for i in range(0, len(payload), size):
                yield payload[i:i + size]

        body = json.dumps({"idea": "chunked 探针", "project_name": "s7chunk"}).encode()
        with TestClient(sa.create_app()) as c:
            before = len(c.get("/api/sessions").json())

            r1 = c.post("/api/sessions", content=chunks(body),
                        headers={"content-type": "application/json"})
            assert r1.status_code == 411, f"① 缺 Content-Length 的 JSON 应 411（一个字节都不该读），实际 {r1.status_code}: {r1.text[:120]}"

            r2 = c.post("/api/sessions", content=body, headers={"content-type": "application/json"})
            assert r2.status_code == 200, f"② 同一具请求带上 Content-Length 必须照常 200（闸不许误伤）：{r2.text[:120]}"

            assert len(c.get("/api/sessions").json()) == before + 1, \
                f"③ 被 411 拒掉的那一发居然建出了会话（或②那发没建）：应 {before + 1}，现 {len(c.get('/api/sessions').json())}"

            rm = c.post("/api/sessions", content=chunks(b"--x\r\n\r\n"),
                        headers={"content-type": "multipart/form-data; boundary=x"})
            assert rm.status_code not in (411, 413), \
                f"④ multipart 缺长度被这道闸一起挡了（本件只动 JSON 腿）：{rm.status_code}"
    finally:
        ss.SESSIONS_FILE = keep

    src = (Path(__file__).resolve().parents[1] / "server" / "app.py").read_text(encoding="utf-8")
    assert "request._receive =" not in src, \
        "⑤ `request._receive` 那套流手术又回来了——Starlette 1.6 的 BaseHTTPMiddleware 不把它传给内层 app，实测会把合法请求打成空体"
    _ok("t23", "chunked 无长度的 JSON 请求 ⇒ 411 且**一个字节都不读**；同体带上 Content-Length ⇒ 200（阳性对照）；"
               "411 零副作用；multipart 缺长度不被这道闸顺手挡掉；字面守卫钉住不许再用 `request._receive` 流手术")


async def t24_dropped_prefix_comes_back_from_history():
    """C110：C90/C103 都写着「丢了最旧没关系，重连走 history 补得回来」——**这句话得有读数**。

    补的是**数据面**：客户端在丢包之后拿它已应用的游标来要历史，被订阅队列丢掉的那段必须
    一条不少地带回来（不重不漏）。触发面（回前台重开流）另在 `frontend/scripts/check_foreground_resync.mjs`
    与 `s8` 的 t30 里钉——**两边都成立，那句话才成立**；只有数据面成立而没人去要，就是 C110 修的那个洞。

    四格（两台 bus 各判一次，与 t21/t22 成对）：
      ① 进程内：灌 `MAX+51` 条不消费（队列必然丢最旧 51 条）⇒ 以「客户端只剩第 51 条之前的
         游标」去 `history(after=…)` ⇒ 从 `c51` 起**全部**带回、条数与序号都连续；
      ② Redis 那台同判（同一个形状、同一个断言）；
      ③ 不重：`after` 换成队列里**现存**第一条的游标 ⇒ 第一条不许再出现；
      ④ 成对守卫：`MAX_SSE_QUEUE < MAX_EVENTS_PER_SESSION`——队列要是比保留窗口还大，
         「补得回来」就成了空话（丢掉的那些早就掉出 ring 了）。
    """
    from server.events import MAX_EVENTS_PER_SESSION, MAX_SSE_QUEUE, SessionEventBus, cursor_of

    assert MAX_SSE_QUEUE < MAX_EVENTS_PER_SESSION, \
        f"t24④ 队列上界 {MAX_SSE_QUEUE} 不小于保留窗口 {MAX_EVENTS_PER_SESSION} ⇒ 丢掉的必然补不回"

    async def _probe(bus, tag):
        sid = "t24-" + tag
        q = await bus.subscribe(sid)
        try:
            for i in range(MAX_SSE_QUEUE + 51):
                bus.publish(sid, kind="report", block="Thought", name="content", value="c%d" % i)
            await asyncio.sleep(0)
            assert q.maxsize == MAX_SSE_QUEUE, f"t24 {tag}① 队列没上界（{q.maxsize}）"
            held = []
            while True:
                try:
                    held.append(q.get_nowait())
                except asyncio.QueueEmpty:
                    break
            assert len(held) == MAX_SSE_QUEUE and held[0].value == "c51", \
                f"t24 {tag}① 队列形状不对（{len(held)} 条，首条 {held[0].value if held else None}）"
            # value "c{i}" 的 seq 是 i+1 ⇒ 客户端最后一条是 c50（seq 51），c51 起那 51 条是它没见到的
            client_cursor = cursor_of(51)
            back = bus.history(sid, after=client_cursor)
            vals = [e.value for e in back]
            assert vals[:3] == ["c51", "c52", "c53"], f"t24 {tag}① 被丢的前缀没补回（开头 {vals[:3]}）"
            assert vals == ["c%d" % i for i in range(51, MAX_SSE_QUEUE + 51)], \
                f"t24 {tag}① 补回不连续：{len(vals)} 条，首 {vals[:1]} 末 {vals[-1:]}"
            again = [e.value for e in bus.history(sid, after=cursor_of(52))]
            assert again[0] == "c52", f"t24 {tag}③ 开区间上界不严 ⇒ 第一条被重复带回（{again[0]}）"
        finally:
            bus.unsubscribe(sid, q)

    ib = SessionEventBus()
    await _probe(ib, "进程内")
    if REDIS_UP:
        from platforms.event_store import RedisEventBus
        rb = RedisEventBus(TEST_DB)
        rb.start()
        try:
            await _probe(rb, "Redis")
        finally:
            await rb.aclose()
    _ok("t24", "丢了最旧再按游标要历史 ⇒ c51 起一条不少地带回（进程内与 Redis 各判一次）；"
               "换游标不重带；成对守卫 MAX_SSE_QUEUE < 保留窗口")


async def t25_terminal_ring_offloads_to_cold_storage():
    """C115：终态会话的 ring 必须**卸得掉、且不丢一条历史**。

    §1.8 现证 `_events[sid]` 全仓没有 per-session 驱逐 ⇒ 常驻只增不减（8 场 56.7 MB，那 8 场早已
    finished）；§1.12 的 D 臂又现证**无 Redis 档里 ring 就是唯一的历史源** ⇒ 裸卸等于把那场的
    补回窗口压成 0。所以判据的主形不是「卸了没」，而是「卸完之后历史逐字还在」。

    七格：
      ① retire 前后 `history()` 的 cursor 序列**逐字相等**，ring 从内存走、冷档在盘上（行数也对）；
      ② **有订阅者就不许卸**（有人正看着这条流）⇒ retire 回 False、ring 还在、不生成冷档；
      ③ 没有 `spill_dir` ⇒ 整个不卸（宁可留内存也不丢历史）；
      ④ retire 后再发（会话被重开那一形）⇒ seq 从冷档尾继续、游标不断档（计数器播种那条），
         且历史＝冷档全部 + 新发，一条不重；
      ⑤ `discard` ⇒ ring 与冷档都不留（不留孤儿文件）；
      ⑥ **接线（bus 侧）**：走真 `create_app()` 的那台进程内 bus 必须带着冷档目录、且真卸得动——
         上面五格都只在 bus 自己身上，产品接没接它是另一件事；
      ⑦ **接线（runner 侧）**：`_forget(terminal=True)` 必须调到 `retire`，`terminal=False`
         （停在待人工处）不许调——断点那场还要接着看历史。
    """
    import shutil
    import tempfile
    from pathlib import Path
    from server.events import SessionEventBus

    tmp = Path(tempfile.mkdtemp(prefix="s7t25-"))
    try:
        sid = "t25-a"
        bus = SessionEventBus(max_events=100, spill_dir=tmp)
        for i in range(7):
            bus.publish(sid, kind="report", block="Thought", name="content", value="v%d" % i)
        before = [e.cursor for e in bus.history(sid)]
        assert len(before) == 7, f"t25① 前置就没攒够 7 条（{len(before)}）"
        assert bus.retire(sid) is True, "t25① 无订阅者的终态会话应当卸掉"
        assert sid not in bus._events, "t25① ring 没从内存卸出去"
        spill = tmp / f"{sid}.jsonl"
        assert spill.exists(), "t25① 冷档没落盘（卸了内存却没落档＝历史没了）"
        lines = [ln for ln in spill.read_text(encoding="utf-8").splitlines() if ln.strip()]
        after = [e.cursor for e in bus.history(sid)]
        assert after == before, f"t25① 卸完之后历史变了（前 {len(before)} 后 {len(after)}）"
        assert len(lines) == 7, f"t25① 冷档行数 {len(lines)} != ring 里的 7 条"

        # ② 有订阅者不许卸
        sid2 = "t25-b"
        q = await bus.subscribe(sid2)
        try:
            bus.publish(sid2, kind="report", block="Thought", name="content", value="watching")
            assert bus.retire(sid2) is False, "t25② 有人订阅也照卸＝把正看着的那屏抽掉"
            assert sid2 in bus._events, "t25② ring 被卸走了（有订阅者时不许）"
            assert not (tmp / f"{sid2}.jsonl").exists(), "t25② 不许卸却生成了冷档"
        finally:
            bus.unsubscribe(sid2, q)

        # ③ 没有冷档目录 ⇒ 不卸
        nospill = SessionEventBus(max_events=50)
        nospill.publish("t25-c", kind="report", block="Thought", name="content", value="x")
        assert nospill.retire("t25-c") is False, "t25③ 无 spill_dir 竟卸了＝裸卸，历史唯一源没了"
        assert "t25-c" in nospill._events, "t25③ ring 被裸卸"

        # ④ 重开：seq 必须从冷档尾继续，历史＝冷档 + 新发，不重不断
        bus.publish(sid, kind="report", block="Thought", name="content", value="reopened")
        new = bus.history(sid)
        seqs = [e.seq for e in new]
        assert seqs[-1] == 8, f"t25④ 重开后 seq 从 {seqs[-1]} 起（没从冷档尾播种＝新事件会被前端游标去重整段吞掉）"
        assert seqs == list(range(1, 9)), f"t25④ 游标不连续：{seqs}"
        assert [e.cursor for e in new[:7]] == before, "t25④ 冷档那 7 条读回来不是原样"
        assert new[-1].value == "reopened", "t25④ 新发那条没接在历史尾部"

        # ⑤ 删除会话：ring 与冷档一起走
        bus.discard(sid)
        assert sid not in bus._events and not spill.exists(), \
            f"t25⑤ discard 后仍有残留（ring={sid in bus._events} 冷档={spill.exists()}）"
        assert bus.history(sid) == [], "t25⑤ 删掉的会话还能翻出历史"

        # ⑥⑦ 接线（「套件全绿≠代码被接上」那条：上面五格都只在 bus 自己身上，产品到底用没用它是另一件事）
        from codeharness.configs.settings import settings as core_settings
        from fastapi.testclient import TestClient
        import server.app as sa
        import server.sessions as _ss
        keep = (core_settings.platform.use_redis, _ss.SESSIONS_FILE)
        core_settings.platform.use_redis = False           # 这条判据验的就是进程内那台的接线
        _ss.SESSIONS_FILE = tmp / "gate-sessions.json"
        try:
            with TestClient(sa.create_app()) as app_c:
                wired = app_c.app.state.bus
                assert isinstance(wired, SessionEventBus) and wired.spill_dir == tmp / "event_history", \
                    f"t25⑥ 产线 bus 没接冷档目录（spill_dir={getattr(wired, 'spill_dir', None)}）⇒ retire 会永远不卸"
                w = "t25-wired"
                for i in range(3):
                    wired.publish(w, kind="report", block="Thought", name="content", value="w%d" % i)
                before_w = [e.cursor for e in wired.history(w)]
                assert wired.retire(w) is True and w not in wired._events, "t25⑥ 产线那台卸不动（接线/目录不对）"
                assert [e.cursor for e in wired.history(w)] == before_w, "t25⑥ 产线那台卸完历史变了"
        finally:
            core_settings.platform.use_redis, _ss.SESSIONS_FILE = keep

        from server.runner import SessionRunner
        rec = []

        class _StubBus:
            def retire(self, s):
                rec.append(s)
                return True

        r = SessionRunner.__new__(SessionRunner)          # 只测这条接缝，不拖进真依赖
        r.bus = _StubBus()
        # `_forget` 扫的登记表（逐个列出来是有意的：判据要跟着这条出口一起改，
        # 用 `vars(run)` 那种自动播种会被「新增一张表但没在这里登记」的形态糊过去）
        for attr in ("chats", "costs", "_last_span", "_trunc_reported", "_call_t0", "_prose",
                     "_prompts", "_used_kernel_block", "_live_blk", "graphs", "projects"):
            setattr(r, attr, {})
        r._closers = set()                                # 这张是 set（add/discard），别给成 dict

        async def _check_terminal():
            # retire 走 `to_thread` 的任务（不卡事件循环），所以断言前必须先把在途任务收完——
            # 上一版在这里假红过一次（任务还没跑就断言 `rec==[…]` 空，读起来像产品没接上）。
            async def _drain():
                for t in list(r._closers):
                    await t
            r._forget("t25-terminal", terminal=False)
            await _drain()
            assert rec == [], f"t25⑦ 停在待人工处就去卸 ring（{rec}）——断点那场还要接着看历史"
            r._forget("t25-terminal", terminal=True)
            await _drain()
            assert rec == ["t25-terminal"], f"t25⑦ 终态没接上 retire（{rec}）⇒ ring 永远留在内存，C115 等于没修"
        await _check_terminal()

        # ⑧ 同一个冷档目录里**两条 bus**（≈两个 worker 各揣一份 ring）都 retire 同一 sid：
        #    写盘是 `.tmp` + `os.replace`，所以后写者整份胜出——读回来必须是**完整可读的一档**，
        #    不许两次写交错成半截（这条钉的就是 §1.14 里「多 worker + 无 Redis 以最后写的那台为准」那句边界）。
        from server.events import Event
        sid8, other = "t25-two", SessionEventBus(max_events=50, spill_dir=tmp)
        for i in range(4):
            bus.publish(sid8, kind="report", block="Thought", name="delta", value="A%d" % i)
            other.publish(sid8, kind="report", block="Thought", name="delta", value="B%d" % i)
        assert bus.retire(sid8) is True and other.retire(sid8) is True, "t25⑧ 两台都没卸成（前置失配）"
        raw = [ln for ln in (tmp / "t25-two.jsonl").read_text(encoding="utf-8").splitlines() if ln.strip()]
        assert len(raw) == 4, f"t25⑧ 两档写成交错/半截：读到 {len(raw)} 行"
        vals = [Event.model_validate_json(ln).value for ln in raw]
        assert vals in (["A0", "A1", "A2", "A3"], ["B0", "B1", "B2", "B3"]), \
            f"t25⑧ 读回来的不是任何一整档（混了 ⇒ 原子替换没生效）：{vals}"

        _ok("t25", "终态会话的 ring 落冷档再卸、history 逐字不减；有订阅者不卸；无 spill_dir 不裸卸；"
                   "重开从冷档尾接 seq（不重不断）；discard 连冷档一起清；"
                   "⑥ 产线 create_app 那台真带冷档目录且卸得动、⑦ runner 的 terminal 出口真调 retire"
                   "（terminal=False 不许调）；⑧ 同目录两台 bus 竞写同一 sid ⇒ 读回来必是完整一档，不许交错")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main():
    t1_inproc_roundtrip()
    t10_checkpoint_msgpack_whitelist()
    t11_start_409_store_view()
    t15_project_name_cannot_escape_workspace()
    t16_event_flusher_survives_a_failing_xadd()
    t17_event_bus_thread_safe()
    t18_shutdown_flush_is_bounded()
    t19_entry_body_limits_and_deploy_coherence()
    t20_approval_ledger_dual_impl_and_two_readers()
    t23_chunked_body_must_carry_content_length()
    asyncio.run(t24_dropped_prefix_comes_back_from_history())
    asyncio.run(t25_terminal_ring_offloads_to_cold_storage())   # C115：终态 ring 卸得掉、历史一条不减
    global REDIS_UP
    REDIS_UP = _redis_up()
    asyncio.run(t22_inproc_sse_queue_bounded_like_redis())   # C103：进程内那台，两条路都跑
    if not REDIS_UP:
        print("⏭  redis 未起（127.0.0.1:6379 不通）——t2–t9/t12/t13 跳过；起容器/`docker compose up -d` 后复跑")
        print("\ns7_platform: 13/13 过（进程内路 t1+t10+t11+t15+t16+t17+t18+t19+t20+t22+t23+t24+t25），redis 路待环境")
        return
    asyncio.run(_redis_suite())
    _flush_test_db()
    print("\ns7_platform: 25/25 全绿（双配置；t25 是本轮 C115 新加的那组）")


if __name__ == "__main__":
    main()
