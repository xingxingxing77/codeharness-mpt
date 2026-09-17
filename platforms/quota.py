"""限流（N5 拆分后保留的一半，施工4 目标结构第 6 行）：`INCR` + `EXPIRE` 固定窗。

key=(scope, window)——多 worker 共享请求数配额，进程内计数器失效的老问题就此了断。
⚠ 做的是限流，**不是金额预算**（§零：预算强制作废）；超限在 HTTP 入口 429，
不进图、不在内核判。
"""
import time

import redis

from codeharness.configs.settings import RedisConfig, settings


class Quota:
    def __init__(self, config: RedisConfig | None = None):
        self.r = redis.Redis.from_url((config or settings.redis).to_url(), decode_responses=True)

    def allow(self, scope: str, limit: int, window_sec: int = 60) -> bool:
        """固定窗计数。INCR 与 EXPIRE 之间崩了会留一个永不过期的键——下一窗照旧超限，
        偏保守不偏放行，可接受（ponytail 上限；升级路径=Lua 单原子）。"""
        key = f"ch:quota:{scope}:{int(time.time() // window_sec)}"
        n = self.r.incr(key)
        if n == 1:
            self.r.expire(key, window_sec)
        return n <= limit
