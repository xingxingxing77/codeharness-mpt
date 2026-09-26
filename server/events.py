"""每会话事件流：有界历史 + 活跃订阅者队列，SSE 消费。"""
import asyncio
import threading
import time
from collections import deque
from typing import Any, Optional, Union
from pydantic import BaseModel, field_validator

MAX_EVENTS_PER_SESSION = 5000


def cursor_of(seq: int) -> str:
    """定宽补零 ⇒ 字典序 == 数值序。进程内总线的游标形状。"""
    return f"{seq:019d}-0"


def norm_cursor(after: Union[str, int, None]) -> str:
    """把调用方给的 after 归一成游标串。
    int 与「纯数字串」都是改动前的老口径（HTTP 查询参数永远是字符串，所以两种都得吃），
    归一到定宽形状才能比字典序。"""
    if after in (None, "", 0, "0"):
        return ""
    if isinstance(after, int):
        return cursor_of(after)
    s = str(after)
    return cursor_of(int(s)) if s.isdigit() else s


class Event(BaseModel):
    session_id: str
    seq: int
    ts: float
    cursor: str = ""            # 前端去重/续传唯一依据；seq 在 Redis 总线上会溢出 float64
    kind: str = "report"          # report | log | status | ask_human | approval | error
    block: Optional[str] = None
    uuid: Optional[str] = None
    name: Optional[str] = None
    value: Any = None
    role: Optional[str] = None
    extra: Optional[dict] = None

    @field_validator("cursor", mode="after")
    @classmethod
    def _fill_cursor(cls, v: str, info):
        # 本次改动前落库的事件没有这个字段；从 seq 兜底，否则历史回放整段被当最小值丢掉
        return v or cursor_of((info.data or {}).get("seq", 0))


class SessionEventBus:
    def __init__(self, max_events: int = MAX_EVENTS_PER_SESSION):
        self.max_events = max_events
        self._events: dict[str, deque] = {}
        self._counters: dict[str, int] = {}
        # 订阅者：q → 建它时的事件循环。asyncio.Queue **非线程安全**，队列只准在它的
        # loop 线程碰；线程侧 publish 靠 call_soon_threadsafe 转投（见 publish 注释）。
        self._subscribers: dict[str, dict] = {}
        # C63：`LogBridge` 的 loguru sink 在**发日志的那个线程**里跑 broadcast_log→publish
        # （bridges.py:17-20，loguru 未开 enqueue、回调跟调用方线程走），与 loop 线程的
        # publish 并发 ⇒ `_counters += 1` 这个非原子读改写会丢更新：两条事件同 seq/同 cursor，
        # 前端按 `cursor <= lastCursor` 去重就要吞掉一条。Redis 版为此专门有 `_lock`
        # （event_store.py:66 注释原话「loguru sink 线程与 loop 线程都会 publish」），
        # 进程内版（Redis 不可达时的默认落点）此前没有。
        self._lock = threading.Lock()

    def _ensure(self, sid: str):
        if sid not in self._events:
            self._events[sid] = deque(maxlen=self.max_events)
            self._counters[sid] = 0
            self._subscribers[sid] = {}
        return self._events[sid], self._subscribers[sid]

    def publish(self, sid: str, kind: str = "report", **fields) -> Event:
        try:
            cur = asyncio.get_running_loop()
        except RuntimeError:
            cur = None                                    # 线程侧调用（loguru sink 等）
        with self._lock:
            events, subs = self._ensure(sid)
            self._counters[sid] += 1
            seq = self._counters[sid]
            ev = Event(session_id=sid, seq=seq, cursor=cursor_of(seq), ts=time.time(), kind=kind, **fields)
            events.append(ev)
            targets = list(subs.items())
        for q, loop in targets:
            if loop is cur:
                q.put_nowait(ev)                          # 同 loop 快路：立刻可见（既有语义不变）
            else:
                try:
                    loop.call_soon_threadsafe(q.put_nowait, ev)
                except RuntimeError:
                    pass                                  # 订阅者的 loop 已关：随进程收场，丢这条
        return ev

    def history(self, sid: str, after: Union[str, int] = "", before: Union[str, int] = "",
                limit: int = 0) -> list:
        """游标窗口回放。after=下界（不含）、before=**开区间上界**（「加载更早」往回翻用），
        两者都不给就是全量；limit>0 取**窗口尾部** limit 条——不给 before 时上界即流尾，
        也就是「最新一屏」（首屏只吞一屏而不是保留窗口里那 5000 条）。limit=0 不限＝老语义。
        返回始终升序：下一页的 before 用本页首条 cursor，取到空页即到头。"""
        with self._lock:
            events, _ = self._ensure(sid)
            snap = list(events)     # 迭代期间线程侧可能 append：deque 边遍历边改会 RuntimeError，先快照
        a, b = norm_cursor(after), norm_cursor(before)
        out = [ev for ev in snap if (not a or ev.cursor > a) and (not b or ev.cursor < b)]
        return out[-limit:] if limit and limit > 0 else out

    def subscribe(self, sid: str) -> asyncio.Queue:
        _, subs = self._ensure(sid)
        q: asyncio.Queue = asyncio.Queue()
        subs[q] = asyncio.get_running_loop()
        return q

    def unsubscribe(self, sid: str, q: asyncio.Queue):
        self._subscribers.get(sid, {}).pop(q, None)

    def broadcast_log(self, message: str, active_sessions: list):
        for sid in active_sessions:
            self.publish(sid, kind="log", value=message)
