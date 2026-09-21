"""插话队列 Redis 化（施工4 目标结构第 3 行）：`LIST ch:chat:{sid}`。

投递方（HTTP worker）与消费方（跑图的 worker）可能不是同一个进程——进程内那个
`runtime.ChatQueue` 再快也只救单进程，跨 worker 投进来的插话它根本看不见。drain 用逐条 LPOP：单条原子，两个 worker 同时
drain 也不会把同一条插话喂两遍（LRANGE+DEL 会）。
接口与 codeharness.runtime.ChatQueue 同名同签名（enqueue/drain/pending/remove，同步）。
"""
import json
from uuid import uuid4

import redis

from codeharness.configs.settings import RedisConfig, settings
from codeharness.const import TEAMLEADER_NAME

KEY = "ch:chat:{}"


class RedisChatQueue:
    def __init__(self, sid: str, config: RedisConfig | None = None,
                 default_target: str = TEAMLEADER_NAME, on_change=None):
        self.key = KEY.format(sid)
        self.default_target = default_target
        self.on_change = on_change          # B6：与进程内那台同一个接缝（普通函数，内核零依赖）
        self.r = redis.Redis.from_url((config or settings.redis).to_url(), decode_responses=True)

    def _fire(self, action: str, items: list):
        if self.on_change:
            self.on_change(action, items)

    def enqueue(self, content: str, send_to: str = "") -> str:
        qid = uuid4().hex[:8]
        self.r.rpush(self.key, json.dumps([qid, content, send_to], ensure_ascii=False))
        self._fire("add", [{"id": qid, "content": content, "send_to": send_to}])
        return qid

    def _fields(self, raw: str) -> tuple:
        """一条 LIST 元素 → (qid, content, send_to)。
        B6 之前落库的是两字段 `[content, send_to]`，db0 里现在还能数到两条这样的残留——
        没有 id 就给它一个 `legacy`：至少**不丢消息**（这条通道丢过一次的代价是 C7 整件的由来），
        代价是老那条撤回不了（撤回要 id）。"""
        f = json.loads(raw)
        return (f[0], f[1], f[2]) if len(f) == 3 else ("legacy", f[0], f[1] if len(f) > 1 else "")

    def drain(self) -> list:
        out, taken = [], []
        while (item := self.r.lpop(self.key)) is not None:
            qid, c, s = self._fields(item)
            out.append((c, s))
            taken.append({"id": qid, "content": c, "send_to": s})
        if taken:
            self._fire("drain", taken)
        return out

    def pending(self) -> list[dict]:
        return [{"id": q, "content": c, "send_to": s}
                for q, c, s in (self._fields(x) for x in self.r.lrange(self.key, 0, -1))]

    def remove(self, qid: str) -> bool:
        """按**整条值** LREM：Redis 自己摘那一条，后面的次序原样（不重排）。"""
        for raw in self.r.lrange(self.key, 0, -1):
            if self._fields(raw)[0] == qid:
                if self.r.lrem(self.key, 0, raw):
                    self._fire("remove", [{"id": qid}])
                    return True
        return False
