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
from codeharness.document_store.embed_split import clamp_query, split_for_embedding
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


def screen_dense(hits: list, cfg) -> list[str]:
    """C23 的下限判据，只打在 **dense 腿**上：它的 score 是真余弦、名次也是真名次。
    dense 结果按余弦降序回来 ⇒ 名次就是下标+1，不必另排序。

    为什么不能拿 hybrid 的返回分比阈值：那条腿是服务端 RRF 融合分（名次分），
    `document_store/exp_store.py:7` 的注释就是为同一件事写的——经验池因此只走 dense 才敢跟 0.9 比。
    """
    if cfg.mode == "score":
        return [str(h.id) for h in hits if h.score >= cfg.min_score]
    return [str(h.id) for h in hits[:cfg.max_rank]]


def format_kb_blocks(msgs: list) -> str:
    """把召回回来的切片排成「一行出处 + 正文」的块（C22 那行口径，逐字沿用）。

    单独成函数是因为它从 C24 起有**两个读者**：每轮预取的 `RoleZero._kb_recall`，和模型可主动调的
    `search_knowledge_base`。两处各抄一份，改日就是「预取那条带出处、自主查那条不带」——同一种信息
    两种呈现，而模型归因只认文本。C21 之前入库的老切片没有 `source` ⇒ 标「出处未登记」，
    **不拿文件名格式凑一个假出处**。
    """
    blocks = []
    for m in msgs:
        meta = m.metadata or {}
        src, page = str(meta.get("source") or ""), meta.get("page")
        if not src:
            where = "〔出处未登记〕"       # 老点：承认不知道，比编一个名字诚实
        elif page in (None, ""):
            where = f"〔来自 {src}〕"
        else:
            where = f"〔来自 {src} 第 {page} 页〕"
        blocks.append(f"{where}\n{m.content}")
    return "\n".join(blocks)


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

    async def rerank_scored(self, query: str, hits: list, k: int) -> list[tuple]:
        """精排接缝（C23(b) 补全）。返回 `[(hit, relevance_score)]`，**保持精排给的相关性序**。

        三处旧形状都不对，一并改掉：
          · 路径：`{base_url}/rerank` 是 Xinference/TEI 那一类的约定，百炼给的是
            `/compatible-api/v1/reranks`（复数）——实测单数 **404**。现在 base_url 指到哪个口就打哪个口，
            结尾已是 `/rerank(s)` 的按原样用，否则才补 `/rerank`（两种约定都不必再加配置）。
          · 鉴权：以前**一个头都不发**（`reranker.api_key` 是零读者死字段）⇒ 云端永远 401，
            又被 `except Exception` 吞成「降级」。这正是 C8 判过的那条：白等一跳、静默少一层保护。
          · 顺序：旧代码把结果按 `index` 排序再回来，那等于**把精排的相关性序丢掉、还原成粗排原序**
            ——「重排」是个空壳。现在按返回序映射，只把 `index` 用作回指粗排列表的下标。
        未配置精排（`base_url` 空）或调用失败 → 原序 + 分 `None`，失败只响一声（与 s5 t28/t32 成对）。
        """
        texts = [h.payload["text"] for h in hits]
        cfg = settings.reranker
        if not cfg.base_url or not texts:
            return [(h, None) for h in hits[:k]]
        base = cfg.base_url.rstrip("/")
        url = base if base.endswith(("/rerank", "/reranks")) else f"{base}/rerank"
        headers = {"Authorization": f"Bearer {cfg.api_key}"} if cfg.api_key else {}
        try:
            async with httpx.AsyncClient(timeout=20) as c:
                r = await c.post(url, json={"model": cfg.model, "query": query,
                                            "documents": texts, "top_n": len(texts)},
                                 headers=headers)
                r.raise_for_status()
                body = r.json()
            # 兼容口在顶层给 `results`，DashScope 原生口包在 `output` 里——两个都认，
            # 但不许第三个形状静默落在「解析不到 = 没精排」上：解析不到就抛，走同一声 warning。
            results = body.get("results") or (body.get("output") or {}).get("results")
            if results is None:
                raise ValueError(f"响应里没有 results/output.results：{str(body)[:120]}")
            return [(hits[i["index"]], i.get("relevance_score"))
                    for i in results if 0 <= int(i["index"]) < len(hits)][:k]
        except Exception as e:
            logger.warning(f"精排不可用，已降级为仅粗排（{url}）：{type(e).__name__}: {e}")
            return [(h, None) for h in hits[:k]]

    async def _rerank(self, query: str, hits: list, k: int) -> list:
        """旧签名：只重排、不看分（bge-reranker 吸收自 rag/knowledge.py，降级留痕语义不变）。
        s5 t28/t32 钉的就是这一条——`rerank_scored` 是它的带分版本。"""
        return [h for h, _ in await self.rerank_scored(query, hits, k)]

    @span("memory.recall", as_type="retriever")
    async def recall(self, query: str, k: int = 5) -> list[Message]:
        """新任务开始时检索回填（调用方：RoleZero._think 里 `_retrieve_experience` 的位置）。
        hybrid 粗排 → reranker 在场则精排重排（台账 #11 的吸收点）。
        N9：整条检索链（embedding → Qdrant hybrid → rerank）不在 LangChain callback 面内，
        由 span 装饰器手工成 span——S9「hit-rate@5」的证据来源。

        C23：`settings.recall_floor.mode != "off"` 时先过一道相关性下限——dense 腿取宽候选、按余弦或
        名次砍掉不相关的，**再**交 hybrid 只在留下的里面重排。下限不许打在 hybrid 的返回分上（理由见
        `screen_dense`）。`off` 是今天这条单发路径，行为与判据逐字不变。
        C36：取的是**本条腿那一档**（`for_leg(self.doc_type)`）——记忆腿默认 `off`，因为那根 0.55 只在
        kb 语料上标过，照搬会把记忆砍空（依据与读数在 `configs/settings.py` 的 `memory_mode`）。
        """
        cfg = settings.recall_floor.for_leg(self.doc_type)
        # 读侧上界（C32 起、C35 收成一处）：经典线传的可以是整份收件，不截就是
        # 「按字数白烧额度 + 端点静默保头丢尾」。与经验池那条腿共用 `clamp_query`。
        dense = await self.embeddings.aembed_query(clamp_query(query))
        scope = dict(doc_type=self.doc_type, user_id=self.user_id, project=self.project_id)
        width = k
        if cfg.mode in ("score", "rank"):
            window = k * cfg.oversample
            if cfg.mode == "rank":
                window = max(window, cfg.max_rank)   # 候选窗比名次线还窄 = 那条线静默失效
            probe = await self.store.search(query, list(dense), k=window, hybrid=False, **scope)
            keep = screen_dense(probe, cfg)
            if not keep:
                # 空集合必须在这里就回：`only_ids` 为老是「不加限制」，把它交给 hybrid 反而放行全表。
                if probe:
                    # 砍空是「线设高了」唯一的现场症状（界面上只会显示成知识库里没资料），留一条响的。
                    logger.info(f"{self.doc_type} 召回被相关性下限砍空：dense top={probe[0].score:.4f} "
                                f"未过 {cfg.min_score if cfg.mode == 'score' else cfg.max_rank}"
                                f"（档={cfg.mode}，候选 {len(probe)} 条）")
                return []
            logger.debug(f"{self.doc_type} 召回下限({cfg.mode}) {len(probe)}→{len(keep)} 条")
            hits = await self.store.search(query, list(dense), k=k, only_ids=keep, **scope)
        else:
            if cfg.mode == "rerank":
                # 向精排多要一批候选来打分：`reranker.recall_k` 这个字段从登记起零读者，这是第一次有读者
                width = max(k, settings.reranker.recall_k)
            hits = await self.store.search(query, list(dense), k=width, **scope)
        scored = await self.rerank_scored(query, hits, width)
        if cfg.mode == "rerank":
            usable = [(h, s) for h, s in scored if s is not None]
            if usable and len(usable) == len(scored):
                hits = [h for h, s in usable if s >= cfg.min_score][:k]
                if not hits:
                    logger.info(f"{self.doc_type} 召回被精排下限砍空：最高 relevance="
                                f"{max(s for _, s in usable):.4f} 未过 {cfg.min_score}"
                                f"（档=rerank，候选 {len(usable)} 条）")
            else:
                # 「没精排」与「精排给了分但都不达标」是两件事，不许都写成砍空：前者退回粗排原序，
                # 那一声 warning 已经在 `rerank_scored` 里响过了。
                hits = [h for h, _ in scored][:k]
        else:
            hits = [h for h, _ in scored]
        # C21：payload 里的出处带回来。`Message.metadata` 是全系统现成的那个 dict（不是为这件事新造的字段），
        # 下游要归因就在上面读 `source`/`page`——`role_zero._kb_recall` 给每条切片挂一行
        # `〔来自 文件名 [第 N 页]〕`（C22），模型答完才说得出这段话是哪份文件里的。
        return [Message(content=h.payload["text"], role=h.payload.get("role", "user"),
                        cause_by=h.payload.get("cause_by", ""),
                        sent_from=h.payload.get("sent_from", ""),
                        metadata={key: h.payload[key] for key in ("source", "page")
                                  if h.payload.get(key) not in (None, "")}) for h in hits]

    async def drop(self):
        await self.store.delete_scope(doc_type=self.doc_type, user_id=self.user_id, project=self.project_id)
