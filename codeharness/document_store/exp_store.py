"""经验池存储：`doc_type="exp"` 切片，落在 R9 的同一个 collection 上（不再自建 codeharness_exp）。

同一个 (action_tag, input_sig) 只有一条经验：点 id 由二者派生，重复入库是覆盖而不是堆积。
命中计数与排序到 S5.3 随 `exp_pool` 闭环一起做（那时才有人真读写 hit 数）。
"""
import uuid

from codeharness.document_store.qdrant_store import Point, QdrantStore
from codeharness.provider.gateway import LLMGateway


class ExpStore:
    def __init__(self, embeddings=None, user_id: str = "default", store: QdrantStore | None = None):
        self.store = store or QdrantStore()
        self.embeddings = embeddings or LLMGateway.embeddings()
        self.user_id = user_id

    async def save(self, action_tag: str, input_sig: str, output: str, score: float = 0.0):
        dense = await self.embeddings.aembed_query(input_sig)
        await self.store.write([Point(
            id=str(uuid.uuid5(uuid.NAMESPACE_OID, f"exp:{action_tag}:{input_sig}")),
            text=input_sig, dense=list(dense), doc_type="exp", user_id=self.user_id,
            extra={"action_tag": action_tag, "output": output, "score": score})])

    async def search(self, action_tag: str, query: str, k: int = 3) -> dict | None:
        """hybrid 召回后按 action_tag 收窄：向量近但动作不同的经验不是同一条经验。"""
        dense = await self.embeddings.aembed_query(query)
        hits = await self.store.search(query, list(dense), k=k * 4, doc_type="exp", user_id=self.user_id)
        for h in hits:
            if h.payload.get("action_tag") == action_tag:
                return {**h.payload, "score": h.score}
        return None
