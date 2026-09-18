"""长期记忆：源 `memory/role_zero_memory.py`(201) 的「溢出入库 / 新任务检索回填 / 去重」语义，
底座换成 R9 的 `QdrantStore`（named vectors hybrid + payload 多租户），不再自己建 collection。

`doc_type="memory"` 是与知识库(kb)、经验池(exp) 共用一个 collection 时的切片键；
`project` 决定召回范围（跨会话、同项目可召回），`user_id` 是租户边界。

recall 带**精排接缝**（`_rerank`，自 `rag/knowledge.py` 吸收而来——那个上层壳零运行时消费者，
接线台账 #11 判吸收删除；RAG 上半截的检索编排由本件 + exp_pool 承担，对齐源：本 checkout 的
MetaVectorStore 线同样没接线）。精排离线降级为粗排原序，但留 warning（docs P1）。
"""
import uuid

import httpx

from codeharness.configs.settings import settings
from codeharness.document_store.qdrant_store import Point, QdrantStore
from codeharness.logs import logger
from codeharness.observability import span
from codeharness.schema import Message


def _pid(scope: str, content: str) -> str:
    """同一作用域内同内容 = 同一个点：重复入库天然幂等，源 flush 的语义要的就是这个。
    已知天花板：id 只由内容派生，所以"幂等"是**整点覆盖**——同内容换元数据重写，元数据以后写的为准。"""
    return str(uuid.uuid5(uuid.NAMESPACE_OID, f"{scope}:{content}"))


class LongTermMemory:
    def __init__(self, project_id: str = "", embeddings=None, user_id: str = "default",
                 session_id: str = "", store: QdrantStore | None = None):
        self.store = store or QdrantStore()
        self.embeddings = embeddings
        self._project = project_id
        self.user_id = user_id
        self.session_id = session_id

    @property
    def project_id(self) -> str:
        """留空则取当前会话目录名（与 session_root 同一接缝）：组队时未必已进会话上下文。"""
        if self._project:
            return self._project
        from codeharness.runtime import CURRENT_PROJECT
        return CURRENT_PROJECT.get()

    @span("memory.overflow", as_type="embedding")
    async def overflow(self, msgs: list[Message]) -> int:
        """工作记忆溢出时批量入库（调用方：RoleZero._compress）。入库前按 content 去重。"""
        seen, uniq = set(), []
        for m in msgs:
            if m.content not in seen:
                seen.add(m.content)
                uniq.append(m)
        if not uniq:
            return 0
        vecs = await self.embeddings.aembed_documents([m.content for m in uniq])
        scope = f"{self.user_id}/{self.project_id}"
        return await self.store.write([
            Point(id=_pid(scope, m.content), text=m.content, dense=list(v), doc_type="memory",
                  user_id=self.user_id, session_id=self.session_id, project=self.project_id,
                  extra={"role": m.role, "cause_by": m.cause_by, "sent_from": m.sent_from})
            for m, v in zip(uniq, vecs) if v])

    async def _rerank(self, query: str, hits: list, k: int) -> list:
        """bge-reranker /v1/rerank（吸收自 rag/knowledge.py，源语义与降级留痕不变）：
        粗排命中的文本送精排重排，失败即粗排原序前 k 条；未配置精排服务则直接原序。"""
        texts = [h.payload["text"] for h in hits]
        if not settings.reranker.base_url or not texts:
            return hits[:k]
        try:
            async with httpx.AsyncClient(timeout=20) as c:
                r = await c.post(f"{settings.reranker.base_url}/rerank",
                                 json={"model": settings.reranker.model,
                                       "query": query, "documents": texts, "top_n": len(texts)})
                r.raise_for_status()
            order = sorted(r.json()["results"], key=lambda x: x["index"])
            return [hits[i["index"]] for i in order][:k]
        except Exception as e:
            logger.warning(f"精排不可用，已降级为仅粗排（{settings.reranker.base_url}）："
                           f"{type(e).__name__}: {e}")
            return hits[:k]

    @span("memory.recall", as_type="retriever")
    async def recall(self, query: str, k: int = 5) -> list[Message]:
        """新任务开始时检索回填（调用方：RoleZero._think 里 `_retrieve_experience` 的位置）。
        hybrid 粗排 → reranker 在场则精排重排（台账 #11 的吸收点）。
        N9：整条检索链（embedding → Qdrant hybrid → rerank）不在 LangChain callback 面内，
        由 span 装饰器手工成 span——S9「hit-rate@5」的证据来源。"""
        dense = await self.embeddings.aembed_query(query)
        hits = await self.store.search(query, list(dense), k=k, doc_type="memory",
                                      user_id=self.user_id, project=self.project_id)
        hits = await self._rerank(query, hits, k)
        return [Message(content=h.payload["text"], role=h.payload.get("role", "user"),
                        cause_by=h.payload.get("cause_by", ""),
                        sent_from=h.payload.get("sent_from", "")) for h in hits]

    async def drop(self):
        await self.store.delete_scope(doc_type="memory", user_id=self.user_id, project=self.project_id)
