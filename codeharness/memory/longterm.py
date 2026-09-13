"""长期记忆：源 memory/role_zero_memory.py 的语义（溢出入库/新任务检索回填/去重），Chroma → Qdrant。
collection 规划见 docs/参考-判定与范围速查.md §2；维度 1024（bge-m3）。"""
from qdrant_client import AsyncQdrantClient, models


class LongTermMemory:
    def __init__(self, project_id: str, embeddings, llm=None):
        from codeharness.configs.settings import settings
        self.coll = f"codeharness_lt_{project_id}"
        self.client = AsyncQdrantClient(url=settings.qdrant.url)
        self.embeddings = embeddings

    async def ensure_collection(self, dim: int):
        if not await self.client.collection_exists(self.coll):
            await self.client.create_collection(self.coll, vectors_config=models.VectorParams(
                size=dim, distance=models.Distance.COSINE))

    async def overflow(self, msgs: list) -> int:
        """工作记忆溢出时批量入库。调用方：team_graph 的黑板裁剪 / RoleZero think 前。
        对齐 role_zero_memory 的 flush 语义；入库前按 content 去重。"""
        seen, uniq = set(), []
        for m in msgs:
            if m.content not in seen:
                seen.add(m.content)
                uniq.append(m)
        if not uniq:
            return 0
        vecs = await self.embeddings.aembed_documents([m.content for m in uniq])
        await self.ensure_collection(len(vecs[0]))
        await self.client.upsert(self.coll, points=[models.PointStruct(
            id=m.id, vector=v, payload={"content": m.content, "role": m.role,
                                        "cause_by": m.cause_by, "sent_from": m.sent_from})
            for m, v in zip(uniq, vecs) if v])
        return len(uniq)

    async def recall(self, query: str, k: int = 5) -> list:
        """新任务开始时检索回填。调用方：RoleZero._think 第 3 步。"""
        if not await self.client.collection_exists(self.coll):
            return []
        vec = await self.embeddings.aembed_query(query)
        hits = (await self.client.query_points(self.coll, query=vec, limit=k)).points
        from codeharness.schema import Message
        return [Message(id=h.id, content=h.payload["content"], role=h.payload["role"],
                        cause_by=h.payload["cause_by"], sent_from=h.payload.get("sent_from", ""))
                for h in hits]

    async def drop(self):
        if await self.client.collection_exists(self.coll):
            await self.client.delete_collection(self.coll)
