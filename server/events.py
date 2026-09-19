"""每会话事件流：有界历史 + 活跃订阅者队列，SSE 消费。"""
import asyncio
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
    kind: str = "report"          # report | log | status | ask_human | error
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
        self._subscribers: dict[str, set] = {}

    def _ensure(self, sid: str):
        if sid not in self._events:
            self._events[sid] = deque(maxlen=self.max_events)
            self._counters[sid] = 0
            self._subscribers[sid] = set()
        return self._events[sid], self._subscribers[sid]

    def publish(self, sid: str, kind: str = "report", **fields) -> Event:
        events, subs = self._ensure(sid)
        self._counters[sid] += 1
        seq = self._counters[sid]
        ev = Event(session_id=sid, seq=seq, cursor=cursor_of(seq), ts=time.time(), kind=kind, **fields)
        events.append(ev)
        for q in list(subs):
            q.put_nowait(ev)
        return ev

    def history(self, sid: str, after: Union[str, int] = "") -> list:
        """after 是上一次交付的游标（旧调用方传 int 也吃）。空 = 全给。"""
        events, _ = self._ensure(sid)
        a = norm_cursor(after)
        return [ev for ev in events if not a or ev.cursor > a]

    def subscribe(self, sid: str) -> asyncio.Queue:
        _, subs = self._ensure(sid)
        q: asyncio.Queue = asyncio.Queue()
        subs.add(q)
        return q

    def unsubscribe(self, sid: str, q: asyncio.Queue):
        self._subscribers.get(sid, set()).discard(q)

    def broadcast_log(self, message: str, active_sessions: list):
        for sid in active_sessions:
            self.publish(sid, kind="log", value=message)
