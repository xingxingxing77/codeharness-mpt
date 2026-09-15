"""Redis 薄壳。判定 `复`（源 utils/redis.py 63 行），只把配置来源换成 R7 的 pydantic-settings。

降级语义照抄，且是硬要求：连不上只 warning，读写一律返回 None，绝不抛。
没有这个降级，FakeLLM 自测就必须起容器，S1–S6 的门禁全都要挂在一个外部服务上。
"""
from __future__ import annotations

from datetime import timedelta
from typing import Optional

import redis.asyncio as aioredis

from codeharness.configs.settings import RedisConfig, settings
from codeharness.logs import logger


class Redis:
    def __init__(self, config: Optional[RedisConfig] = None):
        self.config = config or settings.redis
        self._client = None

    async def _connect(self, force: bool = False) -> bool:
        if self._client and not force:
            return True
        try:
            # from_url 是惰性的：真正的连接失败发生在下面 get/set 里，被那里的 except 吞掉
            self._client = await aioredis.from_url(
                self.config.to_url(),
                username=self.config.username,
                password=self.config.password,
                db=self.config.db,
            )
            return True
        except Exception as e:
            logger.warning(f"Redis initialization has failed: {e}")
        return False

    async def get(self, key: str) -> Optional[bytes]:
        if not await self._connect() or not key:
            return None
        try:
            return await self._client.get(key)
        except Exception as e:
            logger.warning(f"Redis GET {key} failed: {type(e).__name__}: {e}")
            return None

    async def set(self, key: str, data: str, timeout_sec: Optional[int] = None) -> bool:
        if not await self._connect() or not key:
            return False
        try:
            ex = None if not timeout_sec else timedelta(seconds=timeout_sec)
            await self._client.set(key, data, ex=ex)
            return True
        except Exception as e:
            logger.warning(f"Redis SET {key} failed: {type(e).__name__}: {e}")
            return False

    async def close(self):
        if not self._client:
            return
        await self._client.close()
        self._client = None
