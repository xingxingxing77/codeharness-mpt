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

import redis
import redis.asyncio as aioredis

from codeharness.configs.settings import RedisConfig, settings
from server.events import Event, MAX_EVENTS_PER_SESSION

STREAM = "ch:ev:{}"
_RING_MAX = 20000


def enc_id(seq: int) -> str:
    ms, cnt = divmod(seq, 10**6)
    return f"{ms}-{cnt}"


def dec_id(sid_str: str) -> int:
    ms, cnt = sid_str.split("-", 1)
    return int(ms) * 10**6 + int(cnt)


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
            for sid, ev in batch:
                key = STREAM.format(sid)
                eid = await self.client.xadd(key, {"d": json.dumps(ev.model_dump(), ensure_ascii=False)},
                                             maxlen=self.maxlen, approximate=True)
                ev.seq = dec_id(eid)                    # seq 由服务端承接（XADD id 单调）
            await asyncio.sleep(0.02)                   # 攒批窗口：20ms 对 SSE 无感

    async def flush_now(self):
        """测试/停机用：把 ring 里的积压刷干净（生产靠 flusher 自转）。"""
        while True:
            with self._lock:
                if not self._ring:
                    break
            await asyncio.sleep(0.03)

    def history(self, sid: str, after_seq: int = 0) -> list:
        key = STREAM.format(sid)
        lo = f"({enc_id(after_seq)}" if after_seq else "-"
        out = []
        for eid, fields in self._sync.xrange(key, min=lo):
            ev = Event(**json.loads(dict(fields)["d"]))
            ev.seq = dec_id(eid)
            out.append(ev)
        return out

    def subscribe(self, sid: str) -> asyncio.Queue:
        """从**当前流尾**开始收（历史由调用方自己走 history()，与进程内 bus 同一分工）。"""
        q: asyncio.Queue = asyncio.Queue()
        key = STREAM.format(sid)

        async def _reader():
            tail = await self.client.xrevrange(key, count=1)   # 从当前流尾开始续推
            start = tail[0][0] if tail else "0-0"
            while True:
                try:
                    resp = await self.client.xread({key: start}, block=5000, count=64)
                except Exception:
                    await asyncio.sleep(0.5)
                    continue
                for _, entries in resp:
                    for eid, fields in entries:
                        start = eid
                        d = dict(fields)
                        ev = Event(**json.loads(d["d"]))
                        ev.seq = dec_id(eid)
                        q.put_nowait(ev)

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
