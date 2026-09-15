"""长期记忆：源 `memory/role_zero_memory.py`(201) 的「溢出入库 / 新任务检索回填 / 去重」语义，
底座换成 R9 的 `QdrantStore`（named vectors hybrid + payload 多租户），不再自己建 collection。

`doc_type="memory"` 是与知识库(kb)、经验池(exp) 共用一个 collection 时的切片键；
`project` 决定召回范围（跨会话、同项目可召回），`user_id` 是租户边界。
"""
import uuid

from codeharness.document_store.qdrant_store import Point, QdrantStore
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

    async def recall(self, query: str, k: int = 5) -> list[Message]:
        """新任务开始时检索回填（调用方：RoleZero._think 里 `_retrieve_experience` 的位置）。"""
        dense = await self.embeddings.aembed_query(query)
        hits = await self.store.search(query, list(dense), k=k, doc_type="memory",
                                      user_id=self.user_id, project=self.project_id)
        return [Message(content=h.payload["text"], role=h.payload.get("role", "user"),
                        cause_by=h.payload.get("cause_by", ""),
                        sent_from=h.payload.get("sent_from", "")) for h in hits]

    async def drop(self):
        await self.store.delete_scope(doc_type="memory", user_id=self.user_id, project=self.project_id)
