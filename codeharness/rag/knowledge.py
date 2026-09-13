"""知识库：ingest + 粗排精排检索。粗排 Qdrant k=10 → 精排 bge-reranker-v2-m3 top_n=5，
rerank 离线自动降级（见 _rerank）。loader 惰性导入（unstructured/pypdf 未装时 md/txt 仍可用）。"""
import httpx
from qdrant_client import AsyncQdrantClient, models
from codeharness.configs.settings import settings
from codeharness.provider.gateway import LLMGateway


class KnowledgeBase:
    def __init__(self, tenant: str = "default"):
        self.coll = f"codeharness_kb_{tenant}"
        self.client = AsyncQdrantClient(url=settings.qdrant.url)
        self._vstore = None
        from langchain_text_splitters import RecursiveCharacterTextSplitter
        self.splitter = RecursiveCharacterTextSplitter(chunk_size=512, chunk_overlap=64)

    @property
    def vstore(self):
        if self._vstore is None:
            from langchain_qdrant import QdrantVectorStore
            self._vstore = QdrantVectorStore(client=self.client, collection_name=self.coll,
                                             embeddings=LLMGateway.embeddings())
        return self._vstore

    async def _ensure(self):
        if not await self.client.collection_exists(self.coll):
            await self.client.create_collection(self.coll, vectors_config=models.VectorParams(
                size=settings.embedding.dim, distance=models.Distance.COSINE))

    async def ingest(self, file_path: str, meta: dict | None = None) -> int:
        from langchain_community.document_loaders import PyPDFLoader, TextLoader, UnstructuredFileLoader
        loader = (PyPDFLoader(file_path) if file_path.endswith(".pdf")
                  else TextLoader(file_path, encoding="utf-8") if file_path.endswith((".md", ".txt"))
                  else UnstructuredFileLoader(file_path))
        docs = await loader.aload()
        chunks = self.splitter.split_documents(docs)
        for d in chunks:
            d.metadata.update(meta or {})
        await self._ensure()
        await self.vstore.aadd_documents(chunks)
        return len(chunks)

    async def _rerank(self, query: str, texts: list[str]) -> list[str]:
        """bge-reranker /v1/rerank；失败静默降级原序"""
        try:
            async with httpx.AsyncClient(timeout=20) as c:
                r = await c.post(f"{settings.reranker.base_url}/rerank",
                                 json={"model": settings.reranker.model,
                                       "query": query, "documents": texts, "top_n": settings.reranker.top_n})
                r.raise_for_status()
            order = sorted(r.json()["results"], key=lambda x: x["index"])
            return [texts[i["index"]] for i in order]
        except Exception:
            return texts[:settings.reranker.top_n]

    async def retrieve(self, query: str, k: int = 5) -> list[str]:
        hits = await self.vstore.asimilarity_search(query, k=10)       # 粗排
        texts = [d.page_content for d in hits]
        tops = await self._rerank(query, texts)                         # 精排（可降级）
        return [f"[{hits[texts.index(t)].metadata.get('source', '')}] {t}" for t in tops[:k]]
