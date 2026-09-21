"""事件流 Redis 化（施工4 目标结构第 2 行 + 本步唯一硬工程点：sync→async 桥）。

- 通道：`XADD ch:ev:{sid} MAXLEN ~5000`；补历史 `XRANGE (seq`，续推 `XREAD BLOCK`。
  **不用 Pub/Sub**：无持久化、慢消费者丢事件，`after=seq` 兑现不了（施工4 原话）。
- 内核报道槽是**普通函数**（桥接铁律，report.py:67），Redis 写入是异步——不能改成
  `await XADD`（那会迫使内核全链路 async 化，污染 report.py 与所有 Action）。
  正解：publish 只入有界 ring buffer，后台 flusher 批量 XADD；**seq 由服务端承接**
  （XADD 返回的 stream id 单调，编码成 int 写回事件），内核 report.py 零改动。
- seq 编码：`ms * 10**6 + counter`（redis stream id "ms-counter"）。前端只要求单调，
  跨会话序无意义；重复投递（flush 滞后于 subscribe 的窗口）由前端 `ev.seq <= lastSeq` 去重。
"""
import asyncio
import json
import threading
import time
from collections import deque
from typing import Union

import redis
import redis.asyncio as aioredis

from codeharness.configs.settings import RedisConfig, settings
from server.events import Event, MAX_EVENTS_PER_SESSION, norm_cursor

STREAM = "ch:ev:{}"
_RING_MAX = 20000


def enc_id(seq: int) -> str:
    """XRANGE 边界用。cnt 必须定宽补零：否则同毫秒内 "...-9" 的字典序大于
    "...-10"，前端按游标去重会反过来吞事件。"""
    ms, cnt = divmod(seq, 10**6)
    return f"{ms:013d}-{cnt:06d}"


def pad_eid(eid: str) -> str:
    """Redis 返回的是规范化 id（不带前导零），不能直接当前端游标，过一道归一。"""
    ms, cnt = eid.split("-", 1)
    return f"{int(ms):013d}-{int(cnt):06d}"


def dec_id(sid_str: str) -> int:
    ms, cnt = sid_str.split("-", 1)
    return int(ms) * 10**6 + int(cnt)


def _ev_from(eid: str, fields: dict) -> Event:
    """stream 条目 → Event。**seq/cursor 一律以 eid 为准**（落库的 d 里那份是 publish 时的
    占位 0，XADD 之后才由服务端承接）。"""
    ev = Event(**json.loads(dict(fields)["d"]))
    ev.seq = dec_id(eid)
    ev.cursor = pad_eid(eid)
    return ev


class RedisEventBus:
    def __init__(self, config: RedisConfig | None = None, max_events: int = MAX_EVENTS_PER_SESSION):
        cfg = config or settings.redis
        self.client = aioredis.from_url(cfg.to_url(), decode_responses=True)
        # history 走同步客户端：与进程内 bus 同签名（sync）——SSE 端点、有界回放口、
        # 既有测试全部零改动。每连接一次 XRANGE，低频亚毫秒，不值得为它 async 化读路径。
        self._sync = redis.Redis.from_url(cfg.to_url(), decode_responses=True)
        self.maxlen = max_events
        self._ring: deque = deque(maxlen=_RING_MAX)     # (sid, Event)——publish 入队即返回
        self._lock = threading.Lock()                   # loguru sink 线程与 loop 线程都会 publish
        self._flusher: asyncio.Task | None = None
        self._inflight = 0                                    # 正在写出的批次数，停机要等它归零
        self._readers: dict[asyncio.Queue, asyncio.Task] = {}

    # ---- 同步出口（桥接铁律：普通函数，不 await） ----
    def publish(self, sid: str, kind: str = "report", **fields) -> Event:
        ev = Event(session_id=sid, seq=0, ts=time.time(), kind=kind, **fields)
        with self._lock:
            self._ring.append((sid, ev))     # maxlen=20k：满即丢最老，有界背压的显式代价
        return ev

    def broadcast_log(self, message: str, active_sessions: list):
        for sid in active_sessions:
            self.publish(sid, kind="log", value=message)

    # ---- 异步侧 ----
    def start(self):
        if self._flusher is None:
            self._flusher = asyncio.get_running_loop().create_task(self._flush_loop())

    async def _flush_loop(self):
        while True:
            batch = []
            with self._lock:
                while self._ring:
                    batch.append(self._ring.popleft())
            if batch:
                self._inflight += 1
                try:
                    for sid, ev in batch:
                        key = STREAM.format(sid)
                        eid = await self.client.xadd(key, {"d": json.dumps(ev.model_dump(), ensure_ascii=False)},
                                                     maxlen=self.maxlen, approximate=True)
                        ev.seq = dec_id(eid)                    # seq 由服务端承接（XADD id 单调）
                        ev.cursor = pad_eid(eid)                # 前端去重/续传只认这个串
                finally:
                    self._inflight -= 1
            await asyncio.sleep(0.02)                           # 攒批窗口：20ms 对 SSE 无感

    async def flush_now(self):
        """测试/停机用：把 ring 里的积压刷干净（生产靠 flusher 自转）。
        必须连在途批次一起等——ring 在 popleft 时就空了，那时 XADD 还没发完，
        只看 ring 会让停机路径丢掉整批事件。"""
        while True:
            with self._lock:
                idle = not self._ring
            if idle and self._inflight == 0:
                return
            await asyncio.sleep(0.03)

    def history(self, sid: str, after: Union[str, int] = "", before: Union[str, int] = "",
                limit: int = 0) -> list:
        """游标窗口回放，语义与进程内 bus 一致（`server/events.py`）：after 开下界、
        before 开上界（往回翻）、limit>0 取窗口一侧的 limit 条、返回始终升序。
        XRANGE 的 COUNT 截的是**头部**，所以给了 before 要改走 XREVRANGE 取最近
        limit 条再翻回来，否则「加载更早」会拿到整条流最老的 N 条。"""
        key = STREAM.format(sid)
        a, b = norm_cursor(after), norm_cursor(before)
        lo = f"({a}" if a and a != "0" else "-"
        hi = f"({b}" if b and b != "0" else "+"
        take = limit if limit and limit > 0 else None
        if take and b:
            rows = list(self._sync.xrevrange(key, max=hi, min=lo, count=take))
            rows.reverse()
        else:
            rows = self._sync.xrange(key, min=lo, max=hi, count=take)
        return [_ev_from(eid, fields) for eid, fields in rows]

    def subscribe(self, sid: str) -> asyncio.Queue:
        """从**当前流尾**开始收（历史由调用方自己走 history()，与进程内 bus 同一分工）。

        流尾游标必须**在返回前**钉死：留给 reader 首次被调度时才取，则 `subscribe()` 与
        那次取尾之间落库的事件会被算进「流尾」而永久跳过。`/events` 的走法正是
        subscribe → history → 续推，那批事件既不在已取的历史里、XREAD 又从它之后开始读
        → SSE 静默丢事件（t12 偶发红的真因，不是测试独有的问题）。钉在返回后，语义是
        「至少一次」，重复由前端按 cursor 去重。"""
        q: asyncio.Queue = asyncio.Queue()
        key = STREAM.format(sid)
        tail = self._sync.xrevrange(key, count=1)     # 与 history 同一个同步出口，亚毫秒
        start = tail[0][0] if tail else "0-0"

        async def _reader():
            nonlocal start
            while True:
                try:
                    resp = await self.client.xread({key: start}, block=5000, count=64)
                except Exception:
                    await asyncio.sleep(0.5)
                    continue
                for _, entries in resp:
                    for eid, fields in entries:
                        start = eid
                        q.put_nowait(_ev_from(eid, fields))

        self._readers[q] = asyncio.get_running_loop().create_task(_reader())
        return q

    def unsubscribe(self, sid: str, q: asyncio.Queue):
        t = self._readers.pop(q, None)
        if t:
            t.cancel()

    async def aclose(self):
        if self._flusher:
            self._flusher.cancel()
            self._flusher = None
        for t in self._readers.values():
            t.cancel()
        self._readers.clear()
        await self.client.aclose()
        self._sync.close()
