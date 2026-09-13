"""每会话事件流：有界历史 + 活跃订阅者队列，SSE 消费。"""
import asyncio
import time
from collections import deque
from typing import Any, Optional
from pydantic import BaseModel

MAX_EVENTS_PER_SESSION = 5000


class Event(BaseModel):
    session_id: str
    seq: int
    ts: float
    kind: str = "report"          # report | log | status | ask_human | error
    block: Optional[str] = None
    uuid: Optional[str] = None
    name: Optional[str] = None
    value: Any = None
    role: Optional[str] = None
    extra: Optional[dict] = None


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
        ev = Event(session_id=sid, seq=self._counters[sid], ts=time.time(), kind=kind, **fields)
        events.append(ev)
        for q in list(subs):
            q.put_nowait(ev)
        return ev

    def history(self, sid: str, after_seq: int = 0) -> list:
        events, _ = self._ensure(sid)
        return [ev for ev in events if ev.seq > after_seq]

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
