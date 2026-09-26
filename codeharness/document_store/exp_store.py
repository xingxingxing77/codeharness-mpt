"""经验池存储：`doc_type="exp"` 切片，落在 R9 的同一个 collection 上（不再自建 codeharness_exp）。

同一个 (user, action_tag, input_sig) 只有一条经验：点 id 由三者派生（`exp_point_id`），
同租户内重复入库是覆盖而不是堆积；租户不在派生里时会**跨租户互相覆盖**（C4，见该函数注释）。
这个派生 id 同时就是 Redis 命中计数的键（见 exp_pool/manager）。
S5.3 起 search 返回**候选列表**而不是单条最优——命中计数要按经验 id 逐个查，
且判定阈值要的是 [0,1] 的 dense 余弦：RRF 融合分是名次分不是相似度，不能拿来跟 0.9 比。

P0-3: 租户隔离——从 CURRENT_USER ContextVar 取 user_id，避免"default"恒值。
"""
import uuid

from codeharness.document_store.embed_split import clamp_query, split_for_embedding
from codeharness.document_store.qdrant_store import Point, QdrantStore
from codeharness.provider.gateway import LLMGateway


def exp_point_id(action_tag: str, input_sig: str, user_id: str) -> str:
    """点 id = 经验 id = 命中计数键。同一 (user, tag, req) 三处派生同一个 UUID，谁都不用记映射。

    ⚠ **租户必须在派生里**（C4 查出的真缺陷）：只由 (tag, req) 派生时，两个用户教同一条经验会算出
    **同一个 id**——读侧的 `user_id` filter 挡住了越权可见，写侧却仍是后写的把先写的**静默覆盖**。
    实测（`s5 t31②`）：alice 先写、bob 后写同一条，集合里只剩 1 条且归 bob，alice 连自己教的都召不回。
    所以「同一 (tag, req) 只有一条」的口径是**每租户一条**。
    ⚠ 换派生式的**真实后果**（实测，不是推断）：老点不会变哑、也不会消失，而是与新点**并存成两条**
    ——召回按 payload 过滤而非 id，同一条经验会连着出现两次（旧答案仍在候选里）。本仓不做迁移清洗
    （同 C12「老记录 total_cost 变哑」的先例），要清就按 `doc_type="exp"` + `user_id` 一次 `delete_scope`。"""
    return str(uuid.uuid5(uuid.NAMESPACE_OID, f"exp:{user_id}:{action_tag}:{input_sig}"))


class ExpStore:
    def __init__(self, embeddings=None, user_id: str | None = None, store: QdrantStore | None = None):
        from codeharness.runtime import CURRENT_USER
        
        self.store = store or QdrantStore()
        self.embeddings = embeddings or LLMGateway.embeddings()
        # P0-3: 从 CURRENT_USER 取 user_id，未设置时 fallback 到 "default"
        self.user_id = user_id or CURRENT_USER.get("default")

    async def save(self, action_tag: str, input_sig: str, output: str, score: float = 0.0):
        """写一条经验。**签名先过读侧那道界（`clamp_query`）再进出口**——这是 R2（09-26 审查）的修法。

        原先写侧用 `split_for_embedding([input_sig])[0]`、读侧用 `clamp_query(query)`：对同一份签名，
        `_cut` 的首块在**含换行**时只到第一个换行（可能短短一行），而 `clamp_query` 给的是 `text[:h]`
        ——两者长度能差一个量级 ⇒ **写进去的向量与查它时的向量不是同一个东西，那份经验永远命中不了自己**
        （缓存阈值 0.9 比的正是这条腿的余弦）。经验池里「文档就是查询」，两侧必须同源。

        为什么是「先 clamp 再 split」而不是直接把出口换成 `clamp_query`：`clamp_query` 保证 ≤h，
        `split_for_embedding` 于是原样通过——**出口仍在链上**（C27 的 t8 盯的就是「每条入库路都过出口」，
        它会把没走出口的文本判红），而两侧算的就是同一段文本了。
        短签名（≤ 上限，绝大多数）逐字节不变：`clamp_query` 原样返回、`split` 也原样返回。

        读侧为何不反过来改成 `split`（那样也能同源）：`clamp_query` 的 docstring 写着理由——读侧要的是
        「一个问句对一个向量」，而且 C35 的 t38 钉的是实发长度恰好等于上限，切成首块会短于上限。
        谁都不动对方的界，只在**签名这一个接缝**上让两边对齐。
        """
        # C27：入库文本一律过唯一出口（端点超长会**静默**截断，读数见 `embed_split` 模块头）。
        # 经验这一条的单位是**一个点**（`exp_point_id` 同时是 Redis 命中计数的键，切成 N 个点会把计数
        # 打散），所以先 clamp 成一段 ≤ 上限的文本，再交给出口——出口原样放行，只有一处。
        sig = clamp_query(input_sig)
        dense = await self.embeddings.aembed_query(split_for_embedding([sig])[0])
        await self.store.write([Point(
            id=exp_point_id(action_tag, input_sig, self.user_id),
            text=input_sig, dense=list(dense), doc_type="exp", user_id=self.user_id,
            extra={"action_tag": action_tag, "output": output, "score": score})])

    async def search(self, action_tag: str, query: str, k: int = 2) -> list[dict]:
        """dense 余弦召回（经验复用问的是"是不是同一个问题"，不是词法覆盖），按分数降序；
        向量近但动作不同的经验不是同一条经验，action_tag 收窄留在本件做。"""
        # C35：这里的 query 是**用户原话**（`manager.py:69` 传 req），可以是几千字——
        # 与 `LongTermMemory.recall` 共用同一个出口截，不各抄一份
        dense = await self.embeddings.aembed_query(clamp_query(query))
        hits = await self.store.search(query, list(dense), k=k * 4, hybrid=False,
                                       doc_type="exp", user_id=self.user_id)
        out = [{"id": str(h.id), "action_tag": h.payload.get("action_tag"),
                "input": h.payload.get("text"), "output": h.payload.get("output"),
                "score": h.score}
               for h in hits if h.payload.get("action_tag") == action_tag]
        return out[:k]
