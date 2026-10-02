"""每会话事件流：有界历史 + 活跃订阅者队列，SSE 消费。"""
import asyncio
import os
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any, Optional, Union
from pydantic import BaseModel, field_validator

from codeharness.logs import logger

# 每会话保留窗口。5000 是拍的；15000 有数：只读 db0 的 `XINFO STREAM.entries-added`（含被
# `XADD MAXLEN` 裁掉那截）量到真实发数 max=12346、被 5000 窗口裁掉全局 35%（那 5 场丢 23%~59%）。
# 取 15000 = 覆盖到真实 max 且留一倍余量，仍守 `MAX_SSE_QUEUE < 本值`（否则丢的最旧补不回，
# t24④ 钉这条）。代价：进程内档每场顶格 ≈15000×1475B≈21MB、Redis 档 ≈3.6MB/场（全账 `plan/platform-infra.md` §1.10）。
MAX_EVENTS_PER_SESSION = 15000

# C90/C103：每条 SSE 连接的订阅队列上界，**两条 bus 共用这一个数**。原先只定义在
# `platforms/event_store.py:33`（Redis 那台用），进程内那台一直是无界 `asyncio.Queue()`——
# 而**无 Redis 才是默认档**（`PLATFORM__USE_REDIS=0` 时 `create_app` 就用它），C90 的判据
# `s7 t21` 又只测 Redis bus ⇒ 盲区。挪到这里是因为依赖方向是 `event_store -> server.events`
# （它 import 本文件的 Event/MAX_EVENTS_PER_SESSION/norm_cursor），反向 import 会成环。
# 满档语义：丢**最旧**（游标语义下旧事件重连走 `/events/history` 补），每丢满 500 条留一行
# 可 grep 的 `[sse-drop]`。要「一条不丢」得上 Redis 消费组（XREADGROUP + PEL），SSE 不值得。
MAX_SSE_QUEUE = 4096
_DROP_LOG_EVERY = 500


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
    def __init__(self, max_events: int = MAX_EVENTS_PER_SESSION, spill_dir=None):
        self.max_events = max_events
        # C115：终态会话的 ring 溢出到这下面的 `{sid}.jsonl` 冷档，然后从内存卸掉。
        # 为什么必须先有冷档才许卸：§1.12 的 D 臂现证**这条路上 ring 就是唯一的历史源**
        # （无 Redis 档没有第二本账），裸卸等于把那场的补回窗口压成 0。所以 `spill_dir=None`
        # 时 `retire()` 什么都不做——宁可留内存，不丢历史。
        self.spill_dir = Path(spill_dir) if spill_dir else None
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
        # C103：队列 → 该连接已丢条数。键是 Queue 对象本身（与 `_subscribers` 同一把锁保护），
        # `unsubscribe` 时一起摘，别让断开的连接一直占着计数。
        self._dropped: dict = {}

    def _ensure(self, sid: str):
        if sid not in self._events:
            self._events[sid] = deque(maxlen=self.max_events)
            self._counters[sid] = 0
            self._subscribers[sid] = {}
            self._load_spill(sid, self._events[sid])     # C115：ring 是冷档的缓存，不是第二本账
        return self._events[sid], self._subscribers[sid]

    def _spill_path(self, sid: str):
        return self.spill_dir / f"{sid}.jsonl" if self.spill_dir else None

    def _load_spill(self, sid: str, into: deque):
        """把冷档读回 ring，并把 seq 计数器播到尾——**少了这一步，重开的会话会从 seq 1 重新发**，
        前端按 `cursor <= lastCursor` 去重就会把新一场整段吞掉（与「SSE seq 精度丢事件」同族：
        游标必须是同一本账上的连续尾）。"""
        p = self._spill_path(sid)
        if p is None or not p.exists():
            return
        last = 0
        with p.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    ev = Event.model_validate_json(line)
                except Exception:                         # 半截/坏行：跳过这一行，别让一场历史拖死服务
                    logger.warning(f"[ring-spill] {sid} 冷档有一行读不动，跳过")
                    continue
                into.append(ev)
                last = max(last, ev.seq)
        self._counters[sid] = max(self._counters.get(sid, 0), last)

    def retire(self, sid: str) -> bool:
        """C115：一场跑到终态后把它的 ring 落冷档、再从内存卸掉。返回**是否真卸了**。

        为什么要这件（§1.8 现证）：`_events[sid]` 从前全仓没有任何 per-session 驱逐 ⇒ 进程活得越久、
        开过的场越多，常驻只增不减（8 场 56.7 MB，那 8 场早已 finished）。
        三条不许破的边界，全部有判据钉着（`tests/s7_platform.py` t25）：
          ① 有订阅者就**不卸**——有人正在看这条流，卸了下一屏就缺；
          ② 卸之前必须先写成冷档，写失败就原样留着（历史比内存值钱）；
          ③ 没有 `spill_dir` 就整个不卸（无 Redis 档 ring 是唯一历史源，§1.12 D 臂那条）。
        ponytail: 冷档只留 `max_events` 条（ring 本身就是上界），一场 ≈15000 行、约 3 MB；
        按会话数线性占**磁盘**，升级路径是按 user/project 分目录 + 保留期清理，不在本件。
        """
        if self.spill_dir is None:
            return False
        with self._lock:                                  # 写盘排在锁里：publish 用的是同一把锁，
            if self._subscribers.get(sid):                # 于是「边写边被 append」不会发生
                return False
            events = self._events.get(sid)
            if not events:
                return False
            n = len(events)
            p = self._spill_path(sid)
            tmp = p.with_suffix(".jsonl.tmp")
            try:
                self.spill_dir.mkdir(parents=True, exist_ok=True)
                with tmp.open("w", encoding="utf-8") as f:
                    for ev in events:
                        f.write(ev.model_dump_json() + "\n")
                os.replace(tmp, p)                        # 原子替换：读者只会看到整档
            except OSError as exc:
                # C118（并发探针现证，`spill_concurrent_probe`：NAIVE 对照 1610 次读里 1586 次残缺、
                # ATOMIC 0 次残缺，但写者 40 轮里多次撞 PermissionError [WinError 5]）：
                # Windows 上目标档**正被人打开读**时 `os.replace` 会失败。三件事按顺序做完：
                #   ① 清掉自己刚写的 .tmp——不然每撞一次就在盘上漏一个整档大小的孤儿文件（正对着 C115 想收的账反着漏）；
                #   ② ring 原样留着——pop 排在 replace 之后，所以抛穿发生在「丢历史」之前，这条顺序是保命的；
                #   ③ 返回 False 而不抛穿——省内存这件事是旁路，别把散会/落态那条主路掀了
                #      （runner 那侧的 try/except 仍保留：两处各自独立成立，谁都不假设对方一定吞得下）。
                try:
                    tmp.unlink(missing_ok=True)
                except OSError as exc2:
                    logger.warning(f"[ring-retire] {sid} 落档失败、临时档也没删掉（下次同路径会覆盖）：{exc2}")
                logger.warning(f"[ring-retire] {sid} 落冷档失败，ring 原样留着（历史没丢）："
                               f"{type(exc).__name__}: {exc}")
                return False
            self._events.pop(sid, None)
            self._counters.pop(sid, None)
            self._subscribers.pop(sid, None)
        logger.info(f"[ring-retire] {sid} 的 {n} 条事件已落冷档并卸出内存")
        return True

    def discard(self, sid: str):
        """会话被删：ring 与冷档一起走，不留孤儿文件。"""
        with self._lock:
            self._events.pop(sid, None)
            self._counters.pop(sid, None)
            self._subscribers.pop(sid, None)
        p = self._spill_path(sid)
        if p is not None:
            try:
                p.unlink()
            except FileNotFoundError:
                pass

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
                self._offer(sid, q, ev)                   # 同 loop 快路：立刻可见（既有语义不变）
            else:
                try:
                    loop.call_soon_threadsafe(self._offer, sid, q, ev)
                except RuntimeError:
                    pass                                  # 订阅者的 loop 已关：随进程收场，丢这条
        return ev

    def _offer(self, sid: str, q: asyncio.Queue, ev):
        """C103：有界投递——满了丢**最旧**，并按连接计数留一行可 grep 的 `[sse-drop]`。

        与 Redis 版 `_reader`（`platforms/event_store.py` 的 C90 那段）同形状、同前缀、同计数节奏，
        两条 bus 从此共用 `MAX_SSE_QUEUE` 这一个数。丢最旧的依据是游标语义：旧事件重连时由
        `/events/history` 补回来，而 SSE 的实时面只关心最新的——要「一条不丢」得上消费组，不值得。
        ⚠ 只准在队列自己的 loop 线程上跑（`asyncio.Queue` 非线程安全）：上面同 loop 直调、
        线程侧经 `call_soon_threadsafe` 转投，正是 C63 定的那条形状，别改回跨线程直接 `put_nowait`。
        """
        try:
            q.put_nowait(ev)
            return
        except asyncio.QueueFull:
            pass
        try:                                            # 丢最旧：腾一个位置出来
            q.get_nowait()
        except asyncio.QueueEmpty:
            pass
        with self._lock:                                # 计数与 `_subscribers` 同一条锁
            n = self._dropped.get(q, 0) + 1
            self._dropped[q] = n
        if n % _DROP_LOG_EVERY == 1:                    # 每 500 条喊一次，静默丢不许
            logger.warning(f"[sse-drop] {sid} 进程内订阅队列消费端太慢，已丢最旧 {n} 条"
                           f"（队列上界 {MAX_SSE_QUEUE}，重连走 /events/history 补）")
        try:
            q.put_nowait(ev)
        except asyncio.QueueFull:                       # 别的连接在同一 loop 上抢了刚腾的位置
            pass

    def history(self, sid: str, after: Union[str, int] = "", before: Union[str, int] = "",
                limit: int = 0) -> list:
        """游标窗口回放。after=下界（不含）、before=**开区间上界**（「加载更早」往回翻用），
        两者都不给就是全量；limit>0 取**窗口尾部** limit 条——不给 before 时上界即流尾，
        也就是「最新一屏」（首屏只吞一屏而不是保留窗口里那 `MAX_EVENTS_PER_SESSION` 条）。limit=0 不限＝老语义。
        返回始终升序：下一页的 before 用本页首条 cursor，取到空页即到头。"""
        with self._lock:
            events, _ = self._ensure(sid)
            snap = list(events)     # 迭代期间线程侧可能 append：deque 边遍历边改会 RuntimeError，先快照
        a, b = norm_cursor(after), norm_cursor(before)
        out = [ev for ev in snap if (not a or ev.cursor > a) and (not b or ev.cursor < b)]
        return out[-limit:] if limit and limit > 0 else out

    async def subscribe(self, sid: str) -> asyncio.Queue:
        """C90：改 async 与 Redis 版 `RedisEventBus.subscribe` **同形**——调用方（/events 路由）
        服务两种 bus，接口必须一致。本实现无 I/O，async 只是形状。"""
        _, subs = self._ensure(sid)
        q: asyncio.Queue = asyncio.Queue(maxsize=MAX_SSE_QUEUE)
        subs[q] = asyncio.get_running_loop()
        with self._lock:
            self._dropped[q] = 0
        return q

    def unsubscribe(self, sid: str, q: asyncio.Queue):
        self._subscribers.get(sid, {}).pop(q, None)
        with self._lock:            # C103：连接走了就把丢包计数一起带走，别留在字典里
            self._dropped.pop(q, None)

    def broadcast_log(self, message: str, active_sessions: list):
        for sid in active_sessions:
            self.publish(sid, kind="log", value=message)
