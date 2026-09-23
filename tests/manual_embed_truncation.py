"""C27 判据 ③④ 的真读数工装（真 bge-m3 + 真 Qdrant，零现金）：**尾部可达**与**上限在链上生效**。

跑法（两条服务得先在：ollama 的 `bge-m3` 在 11434；Qdrant 在 6333 —— 重启方式
`docker start codeharness-qdrant`）：
  cd /e/Codeharness && PYTHONPATH=/e/Codeharness:/e/Codeharness/logs PYTHONIOENCODING=utf-8 \\
    PYTHONDONTWRITEBYTECODE=1 F:/anaconda/python.exe -B tests/manual_embed_truncation.py

**这一份不算门禁**：它挂在真服务上，服务缺席时直接 exit 1 说「没跑成」。故意不写「打印跳过 +
return 0」那种形状——s9 那张假向量表能长期顶着「hybrid 远好过 dense」的结论，就是因为绿和跳过
在末行里长得一样（C20 用真 embedding 复量后已推翻）。

三格各量什么：
  A 真文档复量截断点。原读数（`E:/tmp/ch_trunc2.out`）是**重复串前缀**量的，PLAN 明写「3600 是
    观测上限不是安全值，落地前用一份真文档复量」——这里就用真文档（仓里 docs/*.md 拼出的中文散文）
    重扫：同一份文本的全文与前缀各发一次，找**向量逐维完全相同**的最小前缀长度（不是「变弱」）。
  B 尾部可达的正负对照。**必须 dense-only**：词法腿（`qdrant_store.sparse_from_text`）是本地从
    全文算的，用 hybrid 量就是让词法腿把整条大点顶进 top-k，测的是词法不是向量——这正是这个洞
    至今没被归因的原因（「按原文关键词问得到、换个说法问不到」）。同一条 2 万字文档（尾部放一句
    只有尾部才有的事实）+ 12 条无关干扰项，灌两个租户：`u_new` 走改后的出口（碎成 N 块）、
    `u_old` 按改前写法整条一个点。只用那句尾部事实提问：新点必须命中、整条点必须**不**命中。
    少了对照组，这格会被「向量确实算出来了」那种假绿顶过去。
  C 上限生效曲线 + 越界退化。同一份 12,000 字文档在 H=400/1200/2400/3200 下各切几块、逐块是否 ≤H、
    字丢不丢（首块**实发端点**四次，四种 H 下向量必须逐维相同——顺带证端点幂等）；再拿一块越过 A
    量到的窗口的文本发端点，证明它照样被截（逐维相同）——这条就是 `EMBEDDING__MAX_CHARS`
    那个上界 validator 的实测依据，不是装饰。

只碰自己的临时集合 `c27reach`（跑完 drop），不碰生产 collection、不碰 Redis、不碰 `.env`。
"""
import asyncio
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from codeharness.configs.settings import EMBEDDING_OBSERVED_TRUNCATION_CHARS, settings       # noqa: E402
from codeharness.document_store.embed_split import split_for_embedding                        # noqa: E402
from codeharness.document_store.qdrant_store import Point, QdrantStore                        # noqa: E402
from codeharness.memory.longterm import point_id                                              # noqa: E402
from codeharness.provider.gateway import LLMGateway                                           # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
COLL = "c27reach"
DOC_TYPE = "kb"
TAIL_FACT = "青隼站下雨的傍晚，三号月台会亮起三十七盏灯。"
TAIL_QUERY = "青隼站雨天傍晚月台亮几盏灯"
FILLER_MIN = 20000
NOISE = ["季度对账的口径改成按自然月", "网关压缩阈值默认关闭", "会话工作区落盘按项目分目录",
         "工具名册超过三十只才开始裁", "审批默认档是只读", "台账耗时列取真值",
         "SSE 游标改字符串避开精度丢事件", "经验池命中计数按经验 id 逐个查", "点 id 派生必须带租户",
         "召回精排未配置时直接原序", "断线横幅取三态流状态", "工具行摘要只取首行"]


def _cos(a, b) -> float:
    n = sum(x * y for x, y in zip(a, b))
    d = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    return n / d if d else 0.0


def _filler() -> str:
    """真中文文本（仓里 docs/*.md 拼出来的），不是 `E:/tmp/ch_trunc2.py` 那种重复串前缀。"""
    parts, n = [], 0
    for p in sorted((ROOT / "docs").glob("*.md")):
        t = p.read_text(encoding="utf-8")
        parts.append(t)
        n += len(t)
        if n >= FILLER_MIN:
            break
    text = "\n".join(parts)
    assert "青隼" not in text, "干扰文本里撞了尾部事实，这格就没有区分力了"
    return text


async def _services_or_die(emb):
    import httpx
    try:
        v = await emb.aembed_query("探活")
        assert len(v) == settings.embedding.dim, f"维度不对：{len(v)} ≠ {settings.embedding.dim}"
    except Exception as e:
        sys.exit(f"真 embedding 端点不可用（{settings.embedding.base_url} / {settings.embedding.model}）："
                 f"{type(e).__name__}: {e} —— 这格没跑成，不许当通过")
    try:
        assert httpx.get(f"{settings.qdrant.url}/healthz", timeout=5).status_code == 200
    except Exception as e:
        sys.exit(f"Qdrant 不可用（{settings.qdrant.url}）：{type(e).__name__}: {e}"
                 f" —— 起法 `docker start codeharness-qdrant`；这格没跑成，不许当通过")


async def _first_equal_prefix(emb, text: str, lo: int, hi: int, step: int) -> int:
    """最小的 L：`embed(text) == embed(text[:L])` 逐维相同（= 端点一个字都没看 L 之后的内容）。"""
    full = await emb.aembed_query(text)
    for L in range(lo, min(hi, len(text)) + 1, step):
        if await emb.aembed_query(text[:L]) == full:
            return L
    return 0


async def a_window(emb) -> int:
    doc = _filler()
    hi = min(len(doc), EMBEDDING_OBSERVED_TRUNCATION_CHARS * 2)
    first = await _first_equal_prefix(emb, doc, 200, hi, 200)
    assert first, (f"A 没量到等值点（文档 {len(doc)} 字、前缀扫到 {hi} 字）——"
                   f"要么这台端点不截断，要么它按 token 截不给整字等值；先弄清再收口")
    print(f"  A 真文档（{len(doc)} 字中文散文）复量：全文向量与 `doc[:{first}]` **逐维完全相同**"
          f" ⇒ 本机 bge-m3 的有效窗口 ≈{first} 字（重复串那次的观测是 3600）")
    return first


async def b_tail_reachable(store, emb, window: int):
    doc = _filler()[:FILLER_MIN] + TAIL_FACT
    assert len(doc) > window * 3, f"文档 {len(doc)} 字，尾部离窗口太近，这格测不出东西"
    noise = [f"{s}（编号 {i}）" for i, s in enumerate(NOISE)]
    qv = await emb.aembed_query(TAIL_QUERY)

    async def ingest(user: str, texts: list[str]):
        vecs = await emb.aembed_documents(texts)
        await store.write([Point(id=point_id(user, t), text=t, dense=list(v), doc_type=DOC_TYPE,
                                 user_id=user) for t, v in zip(texts, vecs) if v])
        return len([1 for v in vecs if v])

    n_new = await ingest("u_new", split_for_embedding([doc] + noise))
    n_old = await ingest("u_old", [doc] + noise)

    async def probe(user: str):
        hits = await store.search(TAIL_QUERY, qv, k=5, hybrid=False, doc_type=DOC_TYPE, user_id=user)
        texts = [h.payload["text"] for h in hits]
        sims = []
        for t in texts:
            sims.append(_cos(qv, await emb.aembed_query(t)))
        rank = next((i + 1 for i, t in enumerate(texts) if TAIL_FACT in t), 0)
        return rank, (max(sims) if sims else 0.0)

    (new_rank, new_sim) = await probe("u_new")
    (old_rank, old_sim) = await probe("u_old")
    assert new_rank, f"B 正例落空：改后碎成 {n_new} 块，用尾部事实问却不在 top-5（最佳 cos {new_sim:.3f}）"
    assert not old_rank, (f"B 对照组失效：改前那种「整条一个点」也命中了 top-5（rank={old_rank}）"
                          f" —— 干扰项太少或查询串了词，这格没有区分力，别拿它当读数")
    print(f"  B dense-only + {len(noise)} 条干扰项：改后 {n_new} 块 → 尾部事实命中 top-{new_rank}"
          f"（cos {new_sim:.3f}）；改前整条 {n_old} 点 → top-5 **不命中**（最佳 cos {old_sim:.3f}）"
          f" ⇒ 尾部以前进不了向量")


async def c_cap(store, emb, window: int):
    doc = _filler()[:12000]
    curve, first_v = [], None
    for h in (400, settings.embedding.max_chars, 2400, EMBEDDING_OBSERVED_TRUNCATION_CHARS):
        ch = split_for_embedding([doc], max_chars=h)
        assert all(0 < len(c) <= h for c in ch), f"C：H={h} 却产出了 {max(map(len, ch))} 字的块"
        # 守恒的准确说法：**字**一个不丢、顺序不变；被丢的只有跨块边界上的换行符
        # （`document.py` 那个 splitter 也是 `keep_separator=False`，同一口径）。
        assert "".join(ch).replace("\n", "") == doc.replace("\n", ""), f"C：H={h} 切完吞字了"
        # 曲线由**真端点**量：把首块原样发一次，四种 H 下必须逐维相同——
        # 既证这台端点幂等（改 H 不影响已有块的向量），也证切的是「尾巴」不是把前缀重排。
        v = await emb.aembed_query(ch[0])
        if first_v is None:
            first_v = v
        assert v == first_v, f"C：H={h} 下首块向量变了 ⇒ 端点不幂等，切块把已有内容挪了位置"
        curve.append(f"H={h}→{len(ch)}块")
    over = doc[:window + 400]
    equal_at = await _first_equal_prefix(emb, over, max(200, window - 200), window + 400, 50)
    assert equal_at, (f"C 越界对照没成立：{len(over)} 字的块找不到「与某段前缀逐维相同」的前缀"
                      f"（窗口 {window} 字）—— 窗口量歪了，A/B 两格的读数也要重取")
    assert equal_at <= EMBEDDING_OBSERVED_TRUNCATION_CHARS, \
        f"C：越过窗口的块在 {equal_at} 字才等值，超过观测截断点 {EMBEDDING_OBSERVED_TRUNCATION_CHARS}"
    print(f"  C 上限生效曲线（12,000 字真文档、端点实发）{' '.join(curve)}：逐块 ≤H、字一个不丢，"
          f"且首块在四种 H 下**向量逐维相同**；一块 {len(over)} 字（越过 A 量到的 {window} 字窗口）"
          f"与它的前 {equal_at} 字**逐维相同** ⇒ 上限比端点窗口还大就等于没设，validator 那条上界有实测依据")


async def main():
    emb = LLMGateway.embeddings()
    await _services_or_die(emb)
    store = QdrantStore(collection=COLL)
    try:
        await store.drop()
        window = await a_window(emb)
        await b_tail_reachable(store, emb, window)
        await c_cap(store, emb, window)
        print(f"\nC27 判据 ③④ 真读数到手（bge-m3 + Qdrant，端点窗口 {window} 字，"
              f"默认上限 {settings.embedding.max_chars} 字，validator 上界 "
              f"{EMBEDDING_OBSERVED_TRUNCATION_CHARS} 字）")
    finally:
        await store.drop()
        print(f"  清理：临时集合 {COLL} 已 drop（生产集合 {settings.qdrant.collection_prefix} 未碰）")


if __name__ == "__main__":
    asyncio.run(main())
