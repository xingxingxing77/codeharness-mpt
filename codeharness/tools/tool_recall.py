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
    coarse = await _in_thread(recall, query, {n: f"{n}: {d}" for n, d in names.items()}, cfg.recall_topk)
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
    return {n: tools[n] for n in fine if n in tools}


async def _in_thread(fn, *args):
    """纯 CPU 的一级放线程池：与仓内其它「同步计算别占事件循环」的写法一致（18~几十条文本，
    当前量级其实用不上，但 think 每轮都调，别在事件循环里做词频统计）。"""
    import asyncio

    return await asyncio.get_running_loop().run_in_executor(None, fn, *args)
