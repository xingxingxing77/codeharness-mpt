"""经验池存储：collection codeharness_exp（collection 规划见参考速查）。"""
import uuid as _u
from qdrant_client import AsyncQdrantClient, models
from codeharness.configs.settings import settings
from codeharness.provider.gateway import LLMGateway


class ExpStore:
    def __init__(self):
        self.coll = "codeharness_exp"
        self.client = AsyncQdrantClient(url=settings.qdrant.url)
        self.embeddings = LLMGateway.embeddings()

    async def save(self, action_tag: str, input_sig: str, output: str, score: float = 0.0):
        vec = await self.embeddings.aembed_query(input_sig)
        if not await self.client.collection_exists(self.coll):
            await self.client.create_collection(self.coll, vectors_config=models.VectorParams(
                size=len(vec), distance=models.Distance.COSINE))
        await self.client.upsert(self.coll, points=[models.PointStruct(
            id=_u.uuid4().hex, vector=vec, payload={"action_tag": action_tag,
                                                    "input_sig": input_sig, "output": output, "score": score})])

    async def search(self, action_tag: str, query: str) -> dict | None:
        if not await self.client.collection_exists(self.coll):
            return None
        vec = await self.embeddings.aembed_query(query)
        hits = (await self.client.query_points(self.coll, query=vec, limit=1)).points
        return hits[0].payload | {"score": hits[0].score} if hits else None
