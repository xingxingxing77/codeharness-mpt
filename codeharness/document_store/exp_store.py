"""经验池存储：`doc_type="exp"` 切片，落在 R9 的同一个 collection 上（不再自建 codeharness_exp）。

同一个 (action_tag, input_sig) 只有一条经验：点 id 由二者派生（`exp_point_id`），
重复入库是覆盖而不是堆积；这个派生 id 同时就是 Redis 命中计数的键（见 exp_pool/manager）。
S5.3 起 search 返回**候选列表**而不是单条最优——命中计数要按经验 id 逐个查，
且判定阈值要的是 [0,1] 的 dense 余弦：RRF 融合分是名次分不是相似度，不能拿来跟 0.9 比。

P0-3: 租户隔离——从 CURRENT_USER ContextVar 取 user_id，避免"default"恒值。
"""
import uuid

from codeharness.document_store.qdrant_store import Point, QdrantStore
from codeharness.provider.gateway import LLMGateway


def exp_point_id(action_tag: str, input_sig: str) -> str:
    """点 id = 经验 id = 命中计数键。同一 (tag, req) 三处派生同一个 UUID，谁都不用记映射。"""
    return str(uuid.uuid5(uuid.NAMESPACE_OID, f"exp:{action_tag}:{input_sig}"))


class ExpStore:
    def __init__(self, embeddings=None, user_id: str | None = None, store: QdrantStore | None = None):
        from codeharness.runtime import CURRENT_USER
        
        self.store = store or QdrantStore()
        self.embeddings = embeddings or LLMGateway.embeddings()
        # P0-3: 从 CURRENT_USER 取 user_id，未设置时 fallback 到 "default"
        self.user_id = user_id or CURRENT_USER.get("default")

    async def save(self, action_tag: str, input_sig: str, output: str, score: float = 0.0):
        dense = await self.embeddings.aembed_query(input_sig)
        await self.store.write([Point(
            id=exp_point_id(action_tag, input_sig),
            text=input_sig, dense=list(dense), doc_type="exp", user_id=self.user_id,
            extra={"action_tag": action_tag, "output": output, "score": score})])

    async def search(self, action_tag: str, query: str, k: int = 2) -> list[dict]:
        """dense 余弦召回（经验复用问的是"是不是同一个问题"，不是词法覆盖），按分数降序；
        向量近但动作不同的经验不是同一条经验，action_tag 收窄留在本件做。"""
        dense = await self.embeddings.aembed_query(query)
        hits = await self.store.search(query, list(dense), k=k * 4, hybrid=False,
                                       doc_type="exp", user_id=self.user_id)
        out = [{"id": str(h.id), "action_tag": h.payload.get("action_tag"),
                "input": h.payload.get("text"), "output": h.payload.get("output"),
                "score": h.score}
               for h in hits if h.payload.get("action_tag") == action_tag]
        return out[:k]
