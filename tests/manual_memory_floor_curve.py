"""C23 未验①：把 0.55 这根线拿到**记忆腿**（`doc_type="memory"`）上量一遍。

为什么 kb 的读数不能借给它（`plan/rag-knowledge.md` C23 行 未验边界①原话）：尺子灌的是 rag_bm 语料的
`doc_type="kb"`，「同一条闸同一份实现，但**语料形状不同**」。kb 那侧标出来的线是：gold 切片的 dense
分 p05=0.647、0.55 档每问留 4.98 条、丢 gold 1~2/300（300 问、k=5）。记忆腿的形状差得很远：
生产写进去的是**一条消息的碎点**（`longterm.overflow`，≤1200 字），典型是「用户问 X，答 Y」里 Y 那种
十几个字的断语，而不是 256 字的文档块 —— 短文本与问句的余弦天然低，**误伤风险全在这一侧**。

语料（不合成、不叫模型写，避免拿模型脾气当判据）：`RGB_En/answer.json` 的 300 条 `gt_answer`
**当记忆池**、`question` 当查询，gold = 该问自己的那条答案片段。这是这把尺能构造出的**最难**的形状
（问句与答案零词面重叠），所以读数偏乐观的方向只有一个：真记忆的字数通常比 11 字的答案多。
本机真记忆有多少可量：**`BRAIN_MEMORY:*` 键数 0**（09-25 现取，见台账），没有真实语料能替它。

三件事一起出：
- **判别力**（阴性对照）：gold 指针整串错开一格 ⇒ 那批「假 gold」的分必须显著低于真 gold，
  否则这把尺在记忆形状上根本没判别力，后面的划线全部作废；
- **划线**：`mode=off` 与 `score/0.30…0.65` 各扫一遍，走**生产那条 `LongTermMemory.recall`**
  （不是另写一份筛子），报每问留下几条、丢 gold 几问、砍空几问；
- **分布**：gold 在 dense 宽候选窗里的分位（p05/p10/p25/p50），与 kb 腿的 p05=0.647 同列对照。

入库走生产写入路径（`LongTermMemory.overflow`），向量按 C23 标定那份缓存复用：
**键形状必须逐字一致**（`{MODEL}/{kind}:{sha1(文本)}`），命中就不发请求；缺的才发，且按端点回的
真 `usage.total_tokens` 计。**总量闸 `EMB_TOKEN_GATE`（默认 60,000）装在发请求之前**，撞闸印
「已花/停在哪一批」并 exit 3 —— 抬闸要人拍，别改工装。
"""
import asyncio
import hashlib
import json
import os
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from codeharness.configs.settings import settings  # noqa: E402
from codeharness.document_store.qdrant_store import QdrantStore  # noqa: E402
from codeharness.memory.longterm import LongTermMemory  # noqa: E402
from codeharness.provider.gateway import LLMGateway  # noqa: E402
from codeharness.schema import Message  # noqa: E402

SRC = Path("E:/MetaGPT/examples/data/rag_bm/RGB_En/answer.json")
MODEL = settings.embedding.model
COLL = os.environ.get("MEM_COLL", "c23mem")
USER = "u_c23mem"
PROJ = "c23mem"
K = int(os.environ.get("SWEEP_K", "3"))         # 生产那一档（`role_zero._ltm_recall` 用 k=3）
SCORES = [0.30, 0.35, 0.40, 0.45, 0.50, 0.55, 0.60, 0.65]
GATE = int(os.environ.get("EMB_TOKEN_GATE", "60000"))


def _msafe(m):
    return m.replace(":", "_").replace("/", "_")


CACHE = Path(os.environ.get("EMB_CACHE") or f"E:/tmp/c23_emb_cache_{_msafe(MODEL)}.json")
_cache: dict = json.loads(CACHE.read_text(encoding="utf-8")) if CACHE.exists() else {}
_spent = 0
_hits = {"d": 0, "q": 0}
_miss = {"d": 0, "q": 0}


def _key(kind, text):
    return f"{MODEL}/{kind}:{hashlib.sha1(text.encode('utf-8')).hexdigest()}"


class CachedEmbeddings:
    """生产那台向量端点 + C23 标定缓存。命中零请求；缺的按生产口径补齐并计量撞闸。

    为什么不直接给 `LongTermMemory` 塞真 `gateway.embeddings()`：那条腿每问都要重嵌一次，
    八档扫描就是 2,400 发 —— 省的不是钱，是「拿补缓存的名义烧出不该有的请求」（09-25 rerank
    那件就是这么防的）。
    """

    def __init__(self, inner):
        self.inner = inner

    async def _fetch(self, texts, kind):
        global _spent
        emb = self.inner
        size = emb.chunk_size
        for i in range(0, len(texts), size):
            if _spent >= GATE:
                print(f"🛑 撞总量闸：已花 {_spent:,}/{GATE:,} token，停在 [{kind}] 第 {i} 条之前"
                      f"（缓存已落 {CACHE}，抬闸要人拍）")
                sys.exit(3)
            part = texts[i:i + size]
            rsp = await emb.async_client.create(model=MODEL, input=part)
            _spent += int(getattr(rsp.usage, "total_tokens", 0) or 0)
            for t, d in zip(part, rsp.data):
                _cache[_key(kind, t)] = [float(x) for x in d.embedding]
            # **每批落一次盘**（与 `manual_recall_floor_curve.embed()` 同一条纪律）：撞闸退出、被 timeout
            # 打掉、 Ctrl-C 都不该让已付过费的向量蒸发。09-25 第一版把落盘写在循环外，撞闸后重跑
            # 把同一批 23,288 token 又买了一遍——省的不是钱，是「白烧两次还看不出来」。
            CACHE.parent.mkdir(parents=True, exist_ok=True)
            CACHE.write_text(json.dumps(_cache), encoding="utf-8")
        return [_cache[_key(kind, t)] for t in texts]

    async def _todo(self, texts, kind):
        miss = [t for t in dict.fromkeys(texts) if _key(kind, t) not in _cache]
        _hits[kind] += len(texts) - len([t for t in texts if _key(kind, t) in _cache])
        _miss[kind] += len([t for t in texts if _key(kind, t) not in _cache])
        if miss:
            await self._fetch(miss, kind)

    async def aembed_documents(self, texts):
        await self._todo(texts, "d")
        return [_cache[_key("d", t)] for t in texts]

    async def aembed_query(self, text):
        await self._todo([text], "q")
        return _cache[_key("q", text)]


async def _quiet_drop(ltm) -> None:
    """集合还不存在时 `delete_scope` 是 404（`qdrant_store.py:148` 不判存在性，`drop()` 判）——
    本工装第一次跑就是「先清后写」，所以清一发不存在的东西要当正常。这条形状另记台账留账。"""
    if await ltm.store.client.collection_exists(ltm.store.collection):
        await ltm.drop()


def load_pool():
    """记忆池 = 300 条 gt_answer（每条一个「记忆片段」）；查询 = 同一行的 question。"""
    gts = json.loads(SRC.read_text(encoding="utf-8"))
    pool, rows = [], []
    for g in gts:
        ans = str(g.get("gt_answer") or "").strip()
        q = str(g.get("question") or "").strip()
        if not ans or not q:
            continue
        pool.append(ans)
        rows.append({"question": q, "gold_text": ans})
    uniq = {i: t for i, t in enumerate(pool)}            # 同文重复只留一份（点 id 由内容派生）
    gold_of = {}
    for i, t in uniq.items():
        gold_of.setdefault(t, i)
    for r in rows:
        r["gold"] = gold_of[r["gold_text"]]
    return list(uniq.values()), rows


async def main():
    if not SRC.exists():
        print(f"❌ 语料不在：{SRC}（E:/MetaGPT 是源仓，没有它这格没有读数）")
        sys.exit(1)
    import httpx
    try:
        up = httpx.get(f"{settings.qdrant.url}/healthz", timeout=3).status_code == 200
    except Exception:
        up = False
    if not up:
        print(f"❌ Qdrant 不在（{settings.qdrant.url}）——本件要真向量真库，不起替身：先 "
              f"`docker start codeharness-qdrant`")
        sys.exit(1)

    # ⚠ 精排那一腿必须钉灭（09-25 现证）：`LongTermMemory.recall` 里 `rerank_scored` 是**无条件**
    # 调的——`mode=score` 不选精排档，只要 `.env` 配了 `RERANKER__BASE_URL`，每次 recall 就照打一发
    # （多一跳 0.8s + 按 query×候选 计费的配额）。本件第一跑没钉它，跑到 score/0.3 才停手，
    # 期间 ≥600 发 recall 各自打了一发精排。读数要的是 dense 分，不是精排序，所以这里置空。
    if settings.reranker.base_url:
        print(f"   钉灭精排腿（原 base_url={settings.reranker.base_url[:60]}）——"
              f"`recall` 里那一发是无条件的，不钉就是拿配额买一个本件不量的东西")
        settings.reranker.base_url = ""

    texts, rows = load_pool()
    print(f"   记忆池 {len(texts)} 条（字数中位 {statistics.median(len(t) for t in texts):.0f}）、"
          f"查询 {len(rows)} 问 · 集合 {COLL} · k={K}")

    inner = LLMGateway.embeddings()
    emb = CachedEmbeddings(inner)
    ltm = LongTermMemory(project_id=PROJ, user_id=USER, embeddings=emb,
                         store=QdrantStore(collection=COLL), doc_type="memory")
    await _quiet_drop(ltm)
    t0 = time.time()
    n = await ltm.overflow([Message(content=t, role="user", sent_from="MemoryPool") for t in texts])
    print(f"   overflow 写入 {n} 点（生产路径：`split_for_embedding` + 内容派生点 id），"
          f"{time.time() - t0:.1f}s · 实发 {_spent:,}/{GATE:,} token · 命中/未命中 "
          f"doc {_hits['d']}/{_miss['d']}")

    # —— 分布 + 判别力对照：dense 宽候选窗（与 `recall` 里那一发同形状）
    # 对照取**同窗内的非 gold 候选**（不是「整串错开一格」：那一版只有 25 发命中窗，样本又小又偏，
    # 拿它比 117 发的中位是把两批不同总体当同尺度用 —— 09-25 第一版就错在这）。
    window = K * settings.recall_floor.oversample
    gold_scores, neg_scores, in_window = [], [], 0
    for r in rows:
        dense = await emb.aembed_query(r["question"])
        probe = await ltm.store.search(r["question"], list(dense), k=window, hybrid=False,
                                       doc_type="memory", user_id=USER, project=PROJ)
        for h in probe:
            if h.payload["text"] == r["gold_text"]:
                in_window += 1
                gold_scores.append(h.score)
            else:
                neg_scores.append(h.score)

    def _pct(xs, p):
        xs = sorted(xs)
        return round(xs[min(len(xs) - 1, int(p / 100 * len(xs)))], 4) if xs else None

    assert gold_scores and neg_scores, "两批分都没采到 ⇒ 仪器坏了，后面的划线不作数"
    gap = statistics.median(gold_scores) - statistics.median(neg_scores)
    print(f"   gold 在宽候选窗里 {in_window}/{len(rows)} 问（{in_window / len(rows):.0%}）；窗内分位 "
          f"p05={_pct(gold_scores, 5)} p10={_pct(gold_scores, 10)} p25={_pct(gold_scores, 25)} "
          f"p50={_pct(gold_scores, 50)}")
    print(f"   同窗内非 gold 候选 {len(neg_scores)} 条：中位 {statistics.median(neg_scores):.4f}、"
          f"p95={_pct(neg_scores, 95)} ⇒ **gold 与噪声的中位差 {gap:.4f}**"
          f"（kb 腿那侧的对照是「无关文档 0.1472 vs 相关 0.6222」，差 0.47）")
    if gap < 0.10:
        print("   ⚠ 这把尺在记忆形状上判别力不足（中位差 <0.10）：下面的表照出，但**它不是「换个阈值就行」"
              "的证据**，是「dense 分这一量纲在短文本记忆上不成立」的证据")

    # —— 划线：走生产那条 `recall`，逐档改 `settings.recall_floor`
    cfg = settings.recall_floor
    idx_of = {t: i for i, t in enumerate(texts)}
    table = []
    for mode, ms in [("off", None)] + [("score", s) for s in SCORES]:
        cfg.mode = mode
        cfg.min_score = ms if ms is not None else cfg.min_score
        kept, lost, empty = [], 0, 0
        for r in rows:
            got = await ltm.recall(r["question"], k=K)
            ids = {idx_of[m.content] for m in got if m.content in idx_of}
            kept.append(len(got))
            if r["gold"] not in ids:
                lost += 1
            if not got:
                empty += 1
        table.append({"mode": mode, "min_score": ms,
                      "avg_survivors": round(statistics.fmean(kept), 2),
                      "gold_lost": lost, "empty": empty})
        print(f"   {mode}/{ms}: 每问留 {table[-1]['avg_survivors']} 条 · 丢 gold {lost}/{len(rows)} "
              f"问 · 砍空 {empty} 问")
    cfg.mode, cfg.min_score = "score", 0.55

    print(f"   实发 embedding {_spent:,}/{GATE:,} token（缓存命中 doc {_hits['d']} / query "
          f"{_hits['q']}）")
    out = ROOT / "storage" / "benchmark" / f"memory_floor_curve_{_msafe(MODEL)}_k{K}.json"
    out.write_text(json.dumps({"k": K, "pool": len(texts), "queries": len(rows),
                               "gold_in_window": in_window,
                               "gold_dense_pct": {"p05": _pct(gold_scores, 5),
                                                  "p10": _pct(gold_scores, 10),
                                                  "p25": _pct(gold_scores, 25),
                                                  "p50": _pct(gold_scores, 50)},
                               "neg_median": round(statistics.median(neg_scores), 4),
                               "neg_n": len(neg_scores), "gold_vs_neg_median_gap": round(gap, 4),
                               "sweep": table, "spent_tokens": _spent,
                               "kb_reference": {"p05": 0.647, "avg_survivors_k5_at_055": 4.98,
                                                "gold_lost_per_300_at_055": "1~2"}},
                              ensure_ascii=False, indent=1), encoding="utf-8")
    await _quiet_drop(ltm)
    print(f"✅ C23① 记忆腿读数落盘 {out.name}（gate 集合已 drop）")


if __name__ == "__main__":
    asyncio.run(main())
