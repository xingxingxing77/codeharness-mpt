"""知识库：ingest + hybrid 粗排 + rerank 精排。

底座是 R9 的 `QdrantStore`（`doc_type="kb"` 切片），不再用 `langchain_qdrant.QdrantVectorStore`：
那层把 payload 结构固定成自己的口径，多租户 filter 与 sparse 向量都塞不进去。
切块仍用 LangChain 的 splitter（它只管文本），向量与召回走本仓的接缝。

粗排 `settings.reranker.recall_k`(10) → 精排 `top_n`(5)；精排服务离线时降级为仅粗排，
**但降级会打 warning**——此前是静默 return，精排长期缺席也不会被发现（docs P1）。
"""
import uuid

import httpx
from codeharness.configs.settings import settings
from codeharness.document_store.qdrant_store import Point, QdrantStore
from codeharness.logs import logger
from codeharness.provider.gateway import LLMGateway


class KnowledgeBase:
    def __init__(self, tenant: str = "default", embeddings=None, store: QdrantStore | None = None,
                 project: str = ""):
        self.store = store or QdrantStore()
        self.embeddings = embeddings or LLMGateway.embeddings()
        self.tenant = tenant
        self.project = project
        from langchain_text_splitters import RecursiveCharacterTextSplitter
        self.splitter = RecursiveCharacterTextSplitter(chunk_size=512, chunk_overlap=64)

    async def ingest(self, file_path: str, meta: dict | None = None) -> int:
        from langchain_community.document_loaders import PyPDFLoader, TextLoader, UnstructuredFileLoader
        loader = (PyPDFLoader(file_path) if file_path.endswith(".pdf")
                  else TextLoader(file_path, encoding="utf-8") if file_path.endswith((".md", ".txt"))
                  else UnstructuredFileLoader(file_path))
        docs = await loader.aload()
        chunks = self.splitter.split_documents(docs)
        if not chunks:
            return 0
        texts = [d.page_content for d in chunks]
        vecs = await self.embeddings.aembed_documents(texts)
        return await self.store.write([
            Point(id=str(uuid.uuid5(uuid.NAMESPACE_OID, f"kb:{self.tenant}:{d.metadata.get('source', file_path)}:{t}")),
                  text=t, dense=list(v), doc_type="kb", user_id=self.tenant, project=self.project,
                  extra={"source": d.metadata.get("source", file_path), **({"meta": meta} if meta else {})})
            for d, t, v in zip(chunks, texts, vecs) if v])

    async def _rerank(self, query: str, texts: list[str]) -> list[str]:
        """bge-reranker /v1/rerank；失败降级为粗排原序，但必须留痕。"""
        try:
            async with httpx.AsyncClient(timeout=20) as c:
                r = await c.post(f"{settings.reranker.base_url}/rerank",
                                 json={"model": settings.reranker.model,
                                       "query": query, "documents": texts, "top_n": settings.reranker.top_n})
                r.raise_for_status()
            order = sorted(r.json()["results"], key=lambda x: x["index"])
            return [texts[i["index"]] for i in order]
        except Exception as e:
            logger.warning(f"精排不可用，已降级为仅粗排（{settings.reranker.base_url}）："
                           f"{type(e).__name__}: {e}")
            return texts[:settings.reranker.top_n]

    async def retrieve(self, query: str, k: int = 5, hybrid: bool = True) -> list[str]:
        """粗排 recall_k 条 → 精排 top_n 条。hybrid=False 只给 S5 门禁做对照用。"""
        if not query or not k:
            return []
        dense = await self.embeddings.aembed_query(query)
        hits = await self.store.search(query, list(dense), k=settings.reranker.recall_k,
                                      hybrid=hybrid, doc_type="kb", user_id=self.tenant,
                                      **({"project": self.project} if self.project else {}))
        texts = [h.payload["text"] for h in hits]
        if not texts:
            return []
        tops = await self._rerank(query, texts)
        src = {h.payload["text"]: h.payload.get("source", "") for h in hits}
        return [f"[{src.get(t, '')}] {t}" for t in tops[:k]]
