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
from codeharness.document_store.embed_split import split_for_embedding
from codeharness.document_store.qdrant_store import Point, QdrantStore
from codeharness.logs import logger
from codeharness.observability import span
from codeharness.schema import Message


def point_id(scope: str, content: str) -> str:
    """同一作用域内同内容 = 同一个点：重复入库天然幂等，源 flush 的语义要的就是这个。
    已知天花板：id 只由内容派生，所以"幂等"是**整点覆盖**——同内容换元数据重写，元数据以后写的为准。

    C3 起知识库摄取（`actions/upload_kb.py`）也走这一个出口：两条入库路共用一条
    幂等规则，才不会「重新上传同一份 FAQ 就多出 N 份切片」。"""
    return str(uuid.uuid5(uuid.NAMESPACE_OID, f"{scope}:{content}"))


class LongTermMemory:
    def __init__(self, project_id: str = "", embeddings=None, user_id: str = "",
                 session_id: str = "", store: QdrantStore | None = None,
                 doc_type: str = "memory"):
        self.store = store or QdrantStore()
        self.embeddings = embeddings
        self._project = project_id
        self._user = user_id
        self.session_id = session_id
        # 同一个类当两条切片的读者：`memory`=角色自己的历史，`kb`=用户上传的知识库（C3）。
        # 知识库那条只被 `recall` 读——它是人上传的文档，不是记忆溢出写进去的。
        self.doc_type = doc_type

    @property
    def user_id(self) -> str:
        """N1：调用方没显式给就从 CURRENT_USER 兜底——auth 关恒 "default"，
        qdrant payload 与既有行为逐字节兼容。"""
        if self._user:
            return self._user
        from codeharness.runtime import CURRENT_USER
        return CURRENT_USER.get() or "default"

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
        # C27：记忆腿**也切**。这条腿以前是四条入库路里最坏的——`read_file` 单次可回 2 万字符，
        # `_compress` 把整条消息原样灌进来，而端点会把超长输入静默截断（只有前 ~3 千字进得了向量，
        # 读数与口径见 `document_store/embed_split.py` 模块头）。一条消息 → N 个切片点，每点带自己
        # 那段原文 + 这条消息的元数据。代价照 C4/C12 先例写在头里：**长消息的老整条点会与新碎点
        # 并存**（点 id 由内容派生），dev 不做迁移清洗，要清按 `doc_type` + `user_id` 一次 `delete_scope`。
        pairs = [(m, c) for m in uniq for c in split_for_embedding([m.content])]
        vecs = await self.embeddings.aembed_documents([c for _, c in pairs])
        # C4 同族审（09-22）：派生式必须带 doc_type。这个类一次被建两个实例（`team.py:23-24`：
        # memory 与 kb 各一条），scope 只到 user/project 时同一段文本在两条切片上算出**同一个点 id**，
        # 于是 `role.kb.overflow(...)` 一旦被人调用就会顶掉记忆那条（payload 说是 kb、id 却是 memory 的）。
        # 配方与 `actions/upload_kb.py:70` 对齐（它从 C3 起就含 doc_type）。代价照 C4/C12 先例：
        # 改造前的老点不会变哑也不会消失，只是与新点并存成两条（读侧按 payload filter，不看 id），
        # dev 数据不做迁移清洗。
        scope = f"{self.doc_type}/{self.user_id}/{self.project_id}"
        return await self.store.write([
            Point(id=point_id(scope, c), text=c, dense=list(v), doc_type=self.doc_type,
                  user_id=self.user_id, session_id=self.session_id, project=self.project_id,
                  extra={"role": m.role, "cause_by": m.cause_by, "sent_from": m.sent_from})
            for (m, c), v in zip(pairs, vecs) if v])

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
        hits = await self.store.search(query, list(dense), k=k, doc_type=self.doc_type,
                                      user_id=self.user_id, project=self.project_id)
        hits = await self._rerank(query, hits, k)
        return [Message(content=h.payload["text"], role=h.payload.get("role", "user"),
                        cause_by=h.payload.get("cause_by", ""),
                        sent_from=h.payload.get("sent_from", "")) for h in hits]

    async def drop(self):
        await self.store.delete_scope(doc_type=self.doc_type, user_id=self.user_id, project=self.project_id)
