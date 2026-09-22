"""工具召回（C6：词法粗筛 + 可选模型精排，两级都在**当前名册**上算）。

要不要它：见 `docs/对照3-工具调用.md` §六-5 与 PLAN §4 C6 行。现场读数是——名册 18 只、
18 条描述全量进 prompt 实测 1455 字符，而召回漏一次模型就会吐「未知命令」被回喂、多烧一整发。
所以 `min_tools` 默认 30：**今天的生产路径逐字不变**，名册真长大了才开始裁 prompt。

源 `metagpt/tools/tool_recommend.py` 的三条实测教训都在这一个文件里避开：
1. 它 think 主路径**从没跑过**：`roles/di/role_zero.py:110` 构造时 `force=True`、`:219` 调用又不传
   context/plan，两个短路条件同时命中（`tool_recommend.py:96-99`）。⇒ 本模块没有 force 开关，
   `query` 是必填位置参数，调用点（`roles/role_zero.py` 的 `_think`）必须交出本轮任务文本。
2. 它召回为空直接 `return []`（`tool_recommend.py:102-103`）——prompt 里一个命令都不剩。
   ⇒ 这里空集/异常/裁过头一律**回全量**并留 warning。
3. 它把 LLM 幻觉出的工具名交给 `validate_tool_names` **静默丢弃**（`tool_registry.py:157-158`）。
   ⇒ 这里先过滤成真名，精排返回空也按召回失败处理（不拿「模型说了个不存在的名字」当结论）。

分词与 `document_store/qdrant_store.py:31` 共用同一个 `TOKEN_RE`（英文数字连词、中文按单字）：
两腿一套分词，不会出现「知识库认得这个词而工具召回认不得」那种漂。
"""
import json
import math
import re
from collections import Counter

from codeharness.configs.settings import settings
from codeharness.logs import logger

_RE: re.Pattern | None = None


def _token_re() -> re.Pattern:
    """分词口径**与知识库那条腿同源**（`document_store/qdrant_store.py:31` 的 `TOKEN_RE`）：
    英文数字连词、中文按单字，两腿不会一个认得这个词而另一个不认得。

    为什么现取而不是模块级 import：`qdrant_store` 会拖进 `qdrant-client`，而本模块在
    角色装配的导入链上（`roles/role_zero.py`）——一个纯字符串切分不该把向量库依赖装进每条会话的启动路径。
    """
    global _RE
    if _RE is None:
        from codeharness.document_store.qdrant_store import TOKEN_RE

        _RE = TOKEN_RE
    return _RE


def _tokens(text: str) -> list[str]:
    return _token_re().findall((text or "").lower())


def _scores(query: str, docs: dict[str, list[str]]) -> dict[str, float]:
    """tf-idf 粗筛：`idf = log(1 + (N-df+0.5)/(df+0.5))`、`tf = 1+log(n)`（与 `sparse_from_text` 同口径）。

    ponytail: 没做文档长度归一（BM25 的 b/k1），量级在几十条描述上影响很小；
    升级路径 = 补长度归一，或直接换 bge-m3 的 sparse 权重（那时只改这一个函数，同 qdrant 那处的注记）。
    """
    n = len(docs)
    q = Counter(_tokens(query))
    df = Counter(term for terms in docs.values() for term in set(terms) if term in q)
    out: dict[str, float] = {}
    for name, terms in docs.items():
        tf = Counter(terms)
        out[name] = sum((1 + math.log(c)) * math.log(1 + (n - df[t] + 0.5) / (df[t] + 0.5))
                        for t, c in tf.items() if t in q)
    return out


def recall(query: str, names: dict[str, str], topk: int) -> list[str]:
    """词法粗筛。`names`: 工具名 -> 给模型看的描述文本。返回按分数降序的名字（**0 分的不进**）。

    与源的另一处差别：源 `np.argsort(scores)[::-1][:topk]` 无阈值，全 0 分也照样凑满 top_k，
    于是「一个词都没命中」与「命中得很好」长得一样。这里 0 分即出局，交出去的是真命中数。
    """
    scored = _scores(query, {k: _tokens(v) for k, v in names.items()})
    return [k for k, _ in sorted(scored.items(), key=lambda kv: (-kv[1], kv[0])) if scored[k] > 0][:topk]


RANK_PROMPT = """## 本轮任务
{query}

## 候选工具（名字: 说明）
{candidates}

从候选里挑出真正有助于完成本轮任务的工具，最多 {topk} 个，只列名字。
认为没有一个合适就返回空列表。输出一个 JSON 数组，不要解释：
```json
["工具名", ...]
```"""


async def rank(query: str, candidates: list[str], names: dict[str, str], llm, topk: int) -> list[str]:
    """模型精排。**默认关**（`TOOL_RECALL__USE_LLM`）：形状与降级照 `memory/longterm.py:_rerank`——
    关掉/不可用/不按格式，一律回粗筛原序前 topk 条并留 warning，绝不抛。

    代价要写明白：开一级精排 = 每轮 think 多发一次调用。本机实测 `step-3.5-flash` 一句 ¥0.12–0.27
    （thinking 计进 completion），所以默认不配；真模型读数按 ADR-02 单独批一次，不在门禁里跑。
    """
    if llm is None or not candidates:
        return candidates[:topk]
    block = "\n".join(f"- {c}: {names.get(c, '')[:160]}" for c in candidates)
    try:
        raw = await llm.aask(RANK_PROMPT.format(query=query[:500], candidates=block, topk=topk),
                             tag="tool_recall_rank")
        picked = json.loads(raw[raw.index("["):raw.rindex("]") + 1])
    except Exception as exc:                       # 不按格式=不可用，降级不抛（与精排服务那一条同纪律）
        logger.warning(f"工具精排不可用，已退回词法粗筛：{type(exc).__name__}: {exc}")
        return candidates[:topk]
    # 幻觉名字先过滤成真名；全被滤掉按召回失败处理（源的 validate 是静默 warning 丢弃）
    hit = [c for c in candidates if c in set(picked)]
    if not hit:
        logger.warning(f"工具精排返回的名字没有一个在候选里（{picked}），已退回词法粗筛")
        return candidates[:topk]
    return hit[:topk]


async def coarse_hybrid(query: str, docs: dict[str, str], topk: int, emb=None) -> list[str]:
    """粗筛 = 词法腿 ∪ 语义腿，**RRF 融合**（`1/(60+名次)`，与 `qdrant_store` 的 dense+sparse 同形状）。

    实测理由（18 只名册、14 条真实口吻 query、top-6）：词法腿漏 `scroll_down`
    （中文任务 vs 英文工具名），语义腿漏 `terminal_command`（「跑一下 pytest」离描述里的词义太远），
    **两腿各自的 miss 正好被对方覆盖** ⇒ 任何一条腿单跑都是 13/14，融合才 14/14。
    语义腿不可用（服务没起/报错）时**只用词法腿**并留话，与 `longterm._rerank` 同一条降级纪律。
    """
    lex = recall(query, docs, topk)
    dense = await _dense_rank(query, docs, topk, emb)
    if not dense:
        return lex
    if not lex:
        return dense
    fused: dict[str, float] = {}
    for names in (lex, dense):
        for rank, name in enumerate(names):
            fused[name] = fused.get(name, 0.0) + 1.0 / (60 + rank)
    return sorted(fused, key=lambda n: (-fused[n], n))


# 语义腿的文档向量按「名册签名」缓存：签名变了必须整份重算（源 `tool_recommend.py:202-206` 的坑
# 就是 `__init__` 里建好就不失效——招人的那条线一涨名册，召回还在按老册子打分）。
_VEC_CACHE: dict[str, list[tuple[str, list[float]]]] = {}


def _roster_key(docs: dict[str, str]) -> str:
    import hashlib

    blob = "\n".join(f"{k}\f{docs[k]}" for k in sorted(docs))
    return hashlib.sha1(blob.encode("utf-8")).hexdigest()


async def _dense_rank(query: str, docs: dict[str, str], topk: int, emb) -> list[str]:
    """bge-m3 dense 召回；`emb` 为空或任何异常都回空表（= 这一腿不出场，融合函数自己退词法）。"""
    if emb is None or not docs:
        return []
    import math

    key = _roster_key(docs)
    try:
        if key not in _VEC_CACHE:
            names = list(docs)
            vecs = await emb.aembed_documents([docs[n] for n in names])
            _VEC_CACHE.clear()                       # 只留当前名册那一份，招人来人往不攒旧向量
            _VEC_CACHE[key] = list(zip(names, vecs))
        qv = await emb.aembed_query(query)
        qn = math.sqrt(sum(v * v for v in qv)) or 1.0
        scored = []
        for name, dv in _VEC_CACHE[key]:
            dn = math.sqrt(sum(v * v for v in dv)) or 1.0
            scored.append((sum(a * b for a, b in zip(qv, dv)) / (qn * dn), name))
        return [n for _, n in sorted(scored, key=lambda x: (-x[0], x[1]))][:topk]
    except Exception as exc:
        logger.warning(f"工具召回的语义腿不可用，本跳只用词法腿：{type(exc).__name__}: {exc}")
        return []


async def select_for_prompt(tools: dict, query: str, llm=None) -> dict:
    """`_think` 唯一的工具块出口：返回**要进 prompt 的工具子集**（name -> tool）。

    三档参数在 `TOOL_RECALL__*`。名册没过长线（`min_tools`）就原样返回同一个对象——
    今天生产路径因此逐字不变，包括返回对象身份（判据 s4 钉这一格，见 `tests/s4_tools.py` 的 C6 五组）。
    """
    cfg = settings.tool_recall
    if len(tools) <= cfg.min_tools or not (query or "").strip():
        return tools
    names = {n: (t.description or "") for n, t in tools.items()}
    # 粗筛的文档必须带工具名：只喂 description 时「write note.txt」这种英文任务一个词都命中不了
    # （18 条描述里没几句写着 write/read/search 这几个字），实测退化成整段回全量——等于机制没通电。
    docs = {n: f"{n}: {d}" for n, d in names.items()}
    emb = None
    if cfg.semantic:
        from codeharness.provider.gateway import LLMGateway
        emb = LLMGateway.embeddings()
    coarse = await coarse_hybrid(query, docs, cfg.recall_topk, emb)
    if not coarse:                                  # 一个词都没命中：宁可全量，也不让模型看不见命令
        logger.warning(f"工具召回零命中（query 前 40 字={query[:40]!r}），prompt 保留全量 {len(tools)} 只")
        return tools
    if len(coarse) < min(cfg.topk, 3):
        # 下限管的是**粗筛**：词面只凑到一两只，说明这次任务没匹配上名册，裁下去就是把工具藏起来
        # （实测「跑一下 pytest 看结果」粗筛只有 1 只且不含终端）。精排挑得少不算问题，见 t41。
        logger.warning(f"工具召回薄命中（粗筛 {len(coarse)} 只 < 下限 {min(cfg.topk, 3)}），"
                       f"判为不可用，prompt 保留全量 {len(tools)} 只")
        return tools
    # 精排只信两件事：名字是真的、结果非空（都在 `rank()` 里判并降级）。它挑中 2 只是合法结论，
    # 不再补一条「砍太狠就回全量」的下限——那条下限会把「这轮只用到 2 只」的正常判断也抹成回退。
    fine = await rank(query, coarse, names, llm if cfg.use_llm else None, cfg.topk)
    picked = {n: tools[n] for n in fine if n in tools}
    # 常驻集**不参与裁剪**：读文件/写文件/终端这四个是「几乎每轮都要」的地基工具。
    # 为什么不靠 tune 召回把它们捞回来：实测 `'跑一下 pytest 看结果'` 里 `execute_shell_async` 排第 8、
    # `terminal_command` 排第 11，而两者分数同为 0.537——bge-m3 在 18 条短描述上分不开，
    # 抬 `topk` 去接一枚硬币是脆弱的，往描述里塞触发词则是教测试答题。地基工具让召回裁掉，
    # 代价是模型凭常识猜对名字也要被「未知命令」回喂（多烧一发）⇒ 这一格风险不该由召回承担。
    for n in (x.strip() for x in cfg.always.split(",")):
        if n in tools:
            picked.setdefault(n, tools[n])
    return picked
