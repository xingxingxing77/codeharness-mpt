"""ExperienceManager + 命中计数。判定 `重`（源 exp_pool/manager.py 242 行：chroma/bm25 双存储 +
LLM ranker 一律不复制）：

- 存储换 Qdrant（`document_store/exp_store.py`，R9 单集合上的 doc_type="exp" 切片）；
- 排序换 **Redis 命中计数**：候选先按命中数降序、再按相似度降序。源用 LLM judge 打分选经验，
  按判定「真实评测推迟到终验之后」——计数是行为数据（复用过几次），比一次性 LLM 打分诚实；
- Redis 走 `utils/redis.py` 的降级语义：连不上时计数全 0，排序退化为纯相似度，**不抛**。

query_exps 返回 `(Experience, 相似度)` 对：判定阈值（`perfect_judges` 的替身）要拿相似度说话，
源把 chroma 的相关性分数藏在存储对象里，这里摊开在返回值上——接口小、账目清。
"""
from codeharness.exp_pool.schema import (DEFAULT_SIMILARITY_TOP_K, Experience, QueryType)
from codeharness.logs import logger
from codeharness.utils.redis import Redis

HIT_KEY = "exp_hits"          # key = exp_hits:{user_id}:{point_id}，point id 由 (tag, req) 派生


class HitCounter:
    """Redis 命中计数。`get` 失败返回 0、`bump` 失败静默——降级语义与 utils/redis.py 同源。"""

    def __init__(self, redis: Redis | None = None, user_id: str = "default"):
        self.redis = redis or Redis()
        self.user_id = user_id

    def _key(self, exp_id: str) -> str:
        return f"{HIT_KEY}:{self.user_id}:{exp_id}"

    async def get(self, exp_id: str) -> int:
        raw = await self.redis.get(self._key(exp_id))
        try:
            return int(raw) if raw else 0
        except ValueError:
            return 0

    async def bump(self, exp_id: str) -> None:
        await self.redis.set(self._key(exp_id), str(await self.get(exp_id) + 1))


class ExperienceManager:
    def __init__(self, store=None, counter: HitCounter | None = None, user_id: str = "default"):
        if store is None:
            from codeharness.document_store.exp_store import ExpStore
            store = ExpStore(user_id=user_id)
            user_id = store.user_id        # 自建时租户跟存储一致；显式传入 store 则 user_id 由调用方负责
        self.store = store
        self.counter = counter or HitCounter(user_id=user_id)

    async def create_exp(self, exp: Experience) -> None:
        await self.store.save(exp.tag, exp.req, exp.resp)

    async def query_exps(self, req: str, tag: str = "",
                         query_type: QueryType = QueryType.SEMANTIC,
                         k: int = DEFAULT_SIMILARITY_TOP_K) -> list[tuple[Experience, float]]:
        """命中计数排在前，相似度做 tie-break；EXACT 查询保留逐字段相等的源语义。"""
        from codeharness.document_store.exp_store import exp_point_id
        hits = await self.store.search(tag, req, k=k)
        scored: list[tuple[Experience, float, int]] = []
        for h in hits:
            exp = Experience(req=h["input"], resp=h["output"], tag=h["action_tag"])
            if query_type == QueryType.EXACT and exp.req != req:
                continue
            pid = h.get("id") or exp_point_id(exp.tag, exp.req)
            scored.append((exp, h["score"], await self.counter.get(pid)))
        scored.sort(key=lambda t: (-t[2], -t[1]))
        return [(exp, score) for exp, score, _ in scored]

    async def record_hit(self, exp: Experience) -> None:
        """只有真复用的那次才计数：召回了没看上的不计（计了就是把噪声排到前面）。"""
        from codeharness.document_store.exp_store import exp_point_id
        await self.counter.bump(exp_point_id(exp.tag, exp.req))

    async def delete_all_exps(self) -> None:
        if hasattr(self.store, "clear"):
            await self.store.clear()
        else:
            logger.debug("ExpStore 无 clear：集合级清理由自测的 drop() 负责")


_manager: ExperienceManager | None = None


def get_exp_manager() -> ExperienceManager:
    global _manager
    if _manager is None:
        _manager = ExperienceManager()
    return _manager
