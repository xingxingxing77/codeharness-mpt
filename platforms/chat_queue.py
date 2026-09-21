"""插话队列 Redis 化（施工4 目标结构第 3 行）：`LIST ch:chat:{sid}`。

投递方（HTTP worker）与消费方（跑图的 worker）可能不是同一个进程——进程内那个
`runtime.ChatQueue` 再快也只救单进程，跨 worker 投进来的插话它根本看不见。drain 用逐条 LPOP：单条原子，两个 worker 同时
drain 也不会把同一条插话喂两遍（LRANGE+DEL 会）。
接口与 codeharness.runtime.ChatQueue 同名同签名（enqueue/drain，同步）。
"""
import json

import redis

from codeharness.configs.settings import RedisConfig, settings
from codeharness.const import TEAMLEADER_NAME

KEY = "ch:chat:{}"


class RedisChatQueue:
    def __init__(self, sid: str, config: RedisConfig | None = None,
                 default_target: str = TEAMLEADER_NAME):
        self.key = KEY.format(sid)
        self.default_target = default_target
        self.r = redis.Redis.from_url((config or settings.redis).to_url(), decode_responses=True)

    def enqueue(self, content: str, send_to: str = ""):
        self.r.rpush(self.key, json.dumps([content, send_to], ensure_ascii=False))

    def drain(self) -> list:
        out = []
        while (item := self.r.lpop(self.key)) is not None:
            c, s = json.loads(item)
            out.append((c, s))
        return out
