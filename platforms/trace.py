"""trace（N4 数据层，施工4 目标结构第 8 行）：`ZSET ch:trace:{sid}` + 每笔 span JSON。

runner 在 on_chat_model_end 处记每笔 LLM 调用的节点/token 增量/时刻——
出 P95 与 token 归因的数据源。前端 N4 面板是 S8 剩余项（等这里攒出真数据）。
同步低频写（每 LLM 调用一次），与 store 同一姿势。
"""
import json
import time

import redis

from codeharness.configs.settings import RedisConfig, settings

KEY = "ch:trace:{}"


class TraceStore:
    def __init__(self, config: RedisConfig | None = None):
        self.r = redis.Redis.from_url((config or settings.redis).to_url(), decode_responses=True)

    def record(self, sid: str, span: dict):
        self.r.zadd(KEY.format(sid), {json.dumps(span, ensure_ascii=False): time.time()})

    def spans(self, sid: str) -> list:
        return [json.loads(m) for m in self.r.zrange(KEY.format(sid), 0, -1)]
