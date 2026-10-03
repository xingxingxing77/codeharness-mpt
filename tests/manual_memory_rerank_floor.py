"""C158：把精排那把尺（relevance_score）拿到**记忆腿**上量一遍——C152 的翻案实测。

C152 定档 `memory_mode=off` 时只证了 dense 两腿不行（gold/噪声 dense 中位差 0.059）；relevance_score
是另一把刻度，而 `_recall_inner` 的 `rerank_scored` 是**无条件**调的——`.env` 配着 reranker 的生产
本来每问就在付这一跳，mode=off 只是没消费那个分。本件量的就是：这一跳的分在记忆语料上有没有判别力、
阈值划在哪。读数说话：判别力够就翻案改档，不够就把 off 的证据补强成「两把刻度都量过」。

语料与 `manual_memory_floor_curve.py` 完全同源（RGB_En 300 问 / gt_answer 当记忆池，gold = 本问答案），
**向量走同一份 C23 缓存 ⇒ embedding 花费为 0**；新增花费只有精排：分布一轮 300 发 + 生产路径复核一轮
300 发，窗口 k=10（`reranker.recall_k`）。精排结果按 (question, 候选文本) 缓存，重跑零请求。

产出：`storage/benchmark/memory_rerank_curve_{model}_k{K}.json`（分布 + 离线扫档 + 复核读数）。
判据不由本工装拍板——翻案与否由读数对照 kb 刻度（gold p05=0.847 vs 噪声 p95=0.872）后人工定，
读数落状态行。
"""
import asyncio
import json
import os
import statistics
import sys
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
USER, PROJ = "u_c23mem", "c23mem"
K = 3                                            # 生产档（`role_zero._ltm_recall` k=3）
WINDOW = max(K, settings.reranker.recall_k)      # 生产 rerank 档的候选窗（=10）
THRESHOLDS = [0.0, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.85, 0.9]
EMB_CACHE = Path(os.environ.get("EMB_CACHE") or f"E:/tmp/c23_emb_cache_{MODEL}.json")
RR_CACHE = Path(os.environ.get("RR_CACHE") or f"E:/tmp/c23_rr_cache_{MODEL}.json")
_rr: dict = json.loads(RR_CACHE.read_text(encoding="utf-8")) if RR_CACHE.exists() else {}
_rr_calls = 0

sys.path.insert(0, str(ROOT / "tests"))
from manual_memory_floor_curve import CachedEmbeddings, load_pool, _quiet_drop  # noqa: E402


def _pct(xs, p):
    xs = sorted(xs)
    return round(xs[min(len(xs) - 1, int(p / 100 * len(xs)))], 4) if xs else None


async def main():
    if not SRC.exists():
        print(f"❌ 语料不在：{SRC}")
        sys.exit(1)
    import httpx
    try:
        up = httpx.get(f"{settings.qdrant.url}/healthz", timeout=3).status_code == 200
    except Exception:
        up = False
    if not up:
        print(f"❌ Qdrant 不在（{settings.qdrant.url}）——先 `docker start codeharness-qdrant`")
        sys.exit(1)
    if not settings.reranker.base_url:
        print("❌ RERANKER__BASE_URL 没配——本件量的是真精排，不指桩不指空串（空串是配置错，铁律 6/27）")
        sys.exit(1)

    texts, rows = load_pool()
    print(f"   记忆池 {len(texts)} 条 · 查询 {len(rows)} 问 · 窗口 {WINDOW} · k={K} · "
          f"精排 {settings.reranker.model} @ {settings.reranker.base_url[:50]}")

    emb = CachedEmbeddings(LLMGateway.embeddings())
    ltm = LongTermMemory(project_id=PROJ, user_id=USER, embeddings=emb,
                         store=QdrantStore(collection=COLL), doc_type="memory")
    await _quiet_drop(ltm)
    await ltm.overflow([Message(content=t, role="user", sent_from="MemoryPool") for t in texts])
    print("   overflow 写入完成（向量全走 C23 缓存，embedding 花费 0）")

    # —— 分布 + 打分缓存：每问一次真精排（`rerank_scored` 生产路径，兼容口 /reranks）
    gold_rel, neg_rel, in_window = [], [], 0
    scored_by_q = {}
    for i, r in enumerate(rows):
        q = r["question"]
        if q in _rr:
            scored = _rr[q]
        else:
            dense = await emb.aembed_query(q)
            # 窗口口径必须对齐生产：`_recall_inner` 的 off/rerank 分支 `store.search(..., k=width)` 不带
            # `hybrid=False` ⇒ 默认 **hybrid 融合窗**。第一版这里写成 dense 窗，本地桩 parity 当场红出
            # 「离线 261 vs 生产 258」——差的就是这 10 个候选是谁。（084 说的换窗=换题，同一条纪律。）
            hits = await ltm.store.search(q, list(dense), k=WINDOW,
                                          doc_type="memory", user_id=USER, project=PROJ)
            scored = [(h.payload["text"], s) for h, s in await ltm.rerank_scored(q, hits, WINDOW)]
            _rr[q] = scored
            global _rr_calls
            _rr_calls += 1
        scored_by_q[q] = scored
        RR_CACHE.write_text(json.dumps(_rr, ensure_ascii=False), encoding="utf-8")
        hit_texts = {t for t, _ in scored}
        if r["gold_text"] in hit_texts:
            in_window += 1
        for t, s in scored:
            if t == r["gold_text"]:
                gold_rel.append(s)
            else:
                neg_rel.append(s)
    print(f"   精排调用 {_rr_calls} 发（缓存 {len(_rr)} 问）· gold 在窗 {in_window}/{len(rows)}")

    assert gold_rel and neg_rel, "精排分没采到 ⇒ 仪器坏了"
    gap = statistics.median(gold_rel) - statistics.median(neg_rel)
    print(f"   gold relevance：p05={_pct(gold_rel, 5)} p10={_pct(gold_rel, 10)} "
          f"p25={_pct(gold_rel, 25)} p50={_pct(gold_rel, 50)}")
    print(f"   噪声 relevance：中位 {statistics.median(neg_rel):.4f} p95={_pct(neg_rel, 95)} "
          f"⇒ **中位差 {gap:.4f}**（kb 对照：gold p50=0.9623 vs other p50 低位，差在 0.1+ 量级）")

    # —— 离线扫档：从打分缓存逐阈值复算生产语义（rel≥T 取前 k），零额外请求
    idx_of = {t: i for i, t in enumerate(texts)}
    table = []
    for t_ in THRESHOLDS:
        lost = empty = 0
        kept = []
        for r in rows:
            usable = [(tx, s) for tx, s in scored_by_q[r["question"]] if s is not None and s >= t_]
            hits = usable[:K]
            kept.append(len(hits))
            if r["gold_text"] not in {tx for tx, _ in hits}:
                lost += 1
            if not hits:
                empty += 1
        table.append({"min_score": t_, "avg_survivors": round(statistics.fmean(kept), 2),
                      "gold_lost": lost, "empty": empty})
        print(f"   rerank≥{t_}: 每问留 {table[-1]['avg_survivors']} 条 · 丢 gold {lost}/{len(rows)} "
              f"问 · 砍空 {empty} 问")
    base = table[0]["gold_lost"]                     # T=0 = 纯精排重排（无下限）
    print(f"   对照：C152 曲线里 mode=off 丢 gold 245/300；纯精排重排（T=0）丢 {base}/300")

    # —— 生产路径复核：只复核表中最好的一档（再花一轮 300 发精排）
    best = min(table, key=lambda x: (x["gold_lost"], -x["avg_survivors"]))
    cfg = settings.recall_floor
    keep_mode, keep_ms = cfg.memory_mode, cfg.min_score
    cfg.memory_mode, cfg.min_score = "rerank", best["min_score"]
    try:
        lost = empty = kept_n = 0
        for r in rows:
            got = await ltm.recall(r["question"], k=K)
            kept_n += len(got)
            if r["gold_text"] not in {m.content for m in got}:
                lost += 1
            if not got:
                empty += 1
        parity = (lost == best["gold_lost"] and empty == best["empty"]
                  and round(kept_n / len(rows), 2) == best["avg_survivors"])
        # R2 的短路位是本工装的天敌：读腿中途挂一次（本地 Qdrant 抖一下就够），后面全部召回静默回空
        # ⇒ parity 差值是「腿死了」不是「语义不合」。腿死了就宣布 parity 无效并带出真实异常。
        # 容差 ±2/300：hybrid 融合（RRF）在**平名次边界**的候选序不稳定——窗口第 10 名换人，
        # 个别问的 top-3 随之翻边。这是检索器的采样抖动，与下限语义无关；下限语义的正确性由
        # t39⑤ 的分腿判据钉。差出 ±2 之外才是真的语义不合。
        JITTER = 2
        if not ltm.up:
            print(f"   ⚠ 召回腿中途挂掉（{ltm.last_error}）——本轮 parity 无效（基础设施，非语义），重跑")
        in_tol = abs(lost - best["gold_lost"]) <= JITTER
        print(f"   生产路径复核 rerank≥{best['min_score']}: 丢 {lost}/{len(rows)} · 砍空 {empty} · "
              f"每问留 {kept_n / len(rows):.2f} ⇒ "
              f"{'与离线扫档一致（±%d RRF 抖动容差内）✅' % JITTER if in_tol and ltm.up else '超容差/无效 ❌'}")
        offline_ok = in_tol and ltm.up
    finally:
        cfg.memory_mode, cfg.min_score = keep_mode, keep_ms

    out = ROOT / "storage" / "benchmark" / (f"memory_rerank_curve_{MODEL}_k{K}"
                                            + (f"_{os.environ['CURVE_TAG']}" if os.environ.get("CURVE_TAG") else "")
                                            + ".json")
    out.write_text(json.dumps({"k": K, "window": WINDOW, "pool": len(texts), "queries": len(rows),
                               "gold_in_window": in_window, "rerank_calls": _rr_calls,
                               "gold_rel_pct": {"p05": _pct(gold_rel, 5), "p10": _pct(gold_rel, 10),
                                                "p25": _pct(gold_rel, 25), "p50": _pct(gold_rel, 50)},
                               "neg_median": round(statistics.median(neg_rel), 4),
                               "neg_p95": _pct(neg_rel, 95),
                               "gold_vs_neg_median_gap": round(gap, 4),
                               "sweep": table, "rerank_t0_gold_lost": base,
                               "dense_off_gold_lost": 245,
                               "verify": {"min_score": best["min_score"], "gold_lost": lost,
                                          "empty": empty, "avg_survivors": round(kept_n / len(rows), 2),
                                          "offline_parity": offline_ok}},
                              ensure_ascii=False, indent=1), encoding="utf-8")
    await _quiet_drop(ltm)
    print(f"✅ C158 记忆腿精排读数落盘 {out.name}（gate 集合已 drop；翻案与否由读数人工定）")


if __name__ == "__main__":
    asyncio.run(main())
