"""C23 判据的真读数工装（真 bge-m3 + 真 Qdrant，零现金）：**相关性下限画在哪一条线上，代价是多少**。

为什么这根线必须量而不能拍：`longterm.recall` 走的是服务端 RRF 融合，那条腿的返回分是**名次分不是
相似度**（`document_store/exp_store.py:7` 同一条口径），拿它跟任何阈值比都是自欺；能比的只有 dense 腿
的余弦与名次。而余弦刻度是**embedding 端点的属性**——所以本工装干的事和 C27 量截断窗口是同一类：
在 C20 那把尺子（`storage/benchmark/s20_realvec_baseline.json` 的来源）上扫两档，把默认值钉在读数上。

跑法（两条服务得先在：ollama 的 `bge-m3` 在 11434；Qdrant 在 6333 —— 重启方式
`docker start codeharness-qdrant`；语料是源项目的 rag_bm，在 `E:/MetaGPT/examples/data/rag_bm`）：
  cd /e/Codeharness && PYTHONPATH=/e/Codeharness:/e/Codeharness/logs PYTHONIOENCODING=utf-8 \\
    PYTHONDONTWRITEBYTECODE=1 REDIS__DB=15 LANGFUSE__ENABLED=0 \\
    EMBEDDING__BASE_URL=http://localhost:11434/v1 EMBEDDING__API_KEY=ollama EMBEDDING__MODEL=bge-m3 \\
    F:/anaconda/python.exe -B tests/manual_recall_floor_curve.py > E:/tmp/ch_c23_curve.out 2>&1

**这一份不算门禁**：挂在真服务上，服务或语料缺席直接 exit 1 说「没跑成」——不写「打印跳过 + return 0」
那种形状（s9 那张假向量表能长期顶着结论，就是因为绿和跳过在末行里长得一样，C20 复量后已推翻）。

量什么：
  · 基线 = 今天这条路（单发 hybrid、无下限）。三个数据集各出一张表，判据只看 **RGB_En** 那一栏
    （C20 实测唯一有判别力的：300 问、chance hit@5=0.009、同码重跑噪声底 hit@1=0.0067）；
    另两栏作「别把已饱和的那格改坏」的旁证。
  · (a) 档 `score`：dense 先取 k×oversample 当候选窗，余弦 < 线 的出局，剩下的交 hybrid 重排。
    每档报 hit@1/@3/@5、MRR、**每问平均留下几块**（这是收益）、砍空率、以及
    `gold_lost_vs_base`（基线 top-5 里有 gold、这一档却没有——下限真正的代价）。
  · (c) 档 `rank`：同一个窗按 dense 原始名次 ≤ 线 出局。名次是序数，换模型不必重量。
  · dense 分数分布：按名次的 p10/p50/p90，以及「gold 在窗内那批问里 gold 的最高分」的 p05/p10/p25/p50
    ——后者就是 `RECALL_FLOOR__MIN_SCORE` 的**标定上界**依据：超过 p05 就开始吃掉真相关那条。

只碰自己的临时集合 `c23curve`（跑完 drop），不碰生产 collection、不碰 Redis、不碰 `.env`。
向量取自缓存（`E:/tmp/c20_emb_cache.json`，C20 那一轮攒的）⇒ 缓存命中时零端点调用；缓存不在就现嵌，
慢但不改结论。产物落 `storage/benchmark/recall_floor_curve.json`（该目录整目录被 .gitignore 忽略）。
"""
import asyncio
import json
import statistics
import sys
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import httpx                                                       # noqa: E402
from s9_benchmark import GOLD_RATIO, SHINGLE, _norm, _shingles     # noqa: E402

from codeharness.actions.upload_kb import _texts_of                # noqa: E402
from codeharness.configs.settings import settings                  # noqa: E402
from codeharness.document_store.qdrant_store import Point, QdrantStore   # noqa: E402
from codeharness.provider.gateway import LLMGateway                # noqa: E402

SRC = Path("E:/MetaGPT/examples/data/rag_bm")
CACHE = Path("E:/tmp/c20_emb_cache.json")
COLL = "c23curve"
USER = "u_c23curve"
# 默认 5 是为了与 C20 那张基线表同 k 可比；生产那一档（`role_zero._kb_recall` 用的是 k=3）用
# `SWEEP_K=3` 再跑一次——下限与候选窗都随 k 动，只量一个 k 等于替另一个 k 签字。
K = int(__import__("os").environ.get("SWEEP_K", "5"))
OVERSAMPLE = 3
DATASETS = ["RGB_En", "simplified_RGB", "simplified_CRUD"]
SCORES = [0.30, 0.35, 0.40, 0.45, 0.50, 0.55, 0.60, 0.65]
RANKS = [1, 2, 3, 4, 5, 6, 7, 9, 12]
OUT_TMPL = "recall_floor_curve_k{K}.json"
CONCURRENCY = 24

_cache: dict[str, list[float]] = json.loads(CACHE.read_text(encoding="utf-8")) if CACHE.exists() else {}


def _key(kind: str, text: str) -> str:
    import hashlib
    return f"{kind}:{hashlib.sha1(text.encode('utf-8')).hexdigest()}"


async def embed(emb, texts: list[str], kind: str, batch=32) -> list[list[float]]:
    """命中缓存就不发请求；缺的按生产口径补齐（查询侧逐条 `aembed_query`，与线上一致）。"""
    todo = [t for t in texts if _key(kind, t) not in _cache]
    if todo:
        t0 = time.time()
        if batch:
            for i in range(0, len(todo), batch):
                got = await emb.aembed_documents(todo[i:i + batch])
                for t, v in zip(todo[i:i + batch], got):
                    _cache[_key(kind, t)] = [float(x) for x in v]
                print(f"    emb[{kind}] {min(i + batch, len(todo))}/{len(todo)} "
                      f"{time.time() - t0:.0f}s", flush=True)
        else:
            for i, t in enumerate(todo):
                _cache[_key(kind, t)] = [float(x) for x in await emb.aembed_query(t)]
                if (i + 1) % 50 == 0:
                    print(f"    emb[{kind}] {i + 1}/{len(todo)} {time.time() - t0:.0f}s", flush=True)
        CACHE.parent.mkdir(parents=True, exist_ok=True)
        CACHE.write_text(json.dumps(_cache), encoding="utf-8")
    return [[float(x) for x in _cache[_key(kind, t)]] for t in texts]


def load(name: str) -> dict:
    """生产切块 + gold 判定（两侧同做空白归一，即 C20 定稿那一口径），与基线表同构。"""
    chunks = [t for t, _ in _texts_of(SRC / name / "documents.txt")]
    gts = json.loads((SRC / name / "answer.json").read_text(encoding="utf-8"))
    inv, goldset = {}, set()
    for i, c in enumerate(chunks):
        for s in _shingles(_norm(c)):
            inv.setdefault(s, []).append(i)
            goldset.add(s)
    rows = []
    for g in gts:
        gs = _shingles(_norm(g["gt_reference"]))
        n = max(len(gs), 1)
        cnt = {}
        for s in gs:
            for i in inv.get(s, ()):
                cnt[i] = cnt.get(i, 0) + 1
        gold = {i for i, c in cnt.items() if c >= GOLD_RATIO * n}
        if gold:
            rows.append({"question": str(g["question"]), "gold": gold})
    return {"chunks": chunks, "rows": rows,
            "shingle": SHINGLE, "gold_ratio": GOLD_RATIO, "cov": len(goldset)}


def score(orders: list[list[int]], rows: list[dict]) -> dict:
    hits, rr = {1: 0, 3: 0, 5: 0}, 0.0
    for seq, r in zip(orders, rows):
        g = r["gold"]
        for k in hits:
            hits[k] += int(any(i in g for i in seq[:k]))
        rr += next((1.0 / (j + 1) for j, i in enumerate(seq) if i in g), 0.0)
    n = len(rows)
    return {f"hit@{k}": round(hits[k] / n, 4) for k in (1, 3, 5)} | {"mrr": round(rr / n, 4)}


def pct(vals, p):
    v = sorted(vals)
    return round(v[min(int(len(v) * p), len(v) - 1)], 4) if v else None


async def services_or_die(emb) -> None:
    if not SRC.exists():
        sys.exit(f"exit 1：语料不在（{SRC}）——本工装没有它量不出下限，跳过等于没跑")
    try:
        assert httpx.get(f"{settings.qdrant.url}/healthz", timeout=3).status_code == 200
    except Exception as e:
        sys.exit(f"exit 1：Qdrant 不在线（{settings.qdrant.url}）：{type(e).__name__}: {e}")
    v = [float(x) for x in await emb.aembed_query("真 embedding 探活")]
    if len(v) != settings.embedding.dim or not any(v):
        sys.exit(f"exit 1：embedding 端点假在线（{settings.embedding.base_url}"
                 f"/{settings.embedding.model}，dim={len(v)}）")
    print(f"  服务：Qdrant {settings.qdrant.url} 在线；{settings.embedding.model} "
          f"{len(v)} 维（语料 {SRC}）")


async def sweep(store, pairs, probes, base_orders, rows, pid2i_local):
    """一整个档（score 或 rank 的一个参数）在同一批问题上跑一遍，出质量 + 代价两组数。"""

    async def one(sel):
        orders, kept_n, prompt_n, empty, lost = [], [], [], 0, 0
        sem = asyncio.Semaphore(CONCURRENCY)

        async def q(i):
            keep = sel(probes[i])
            if not keep:
                return keep, []
            async with sem:
                pts = await store.search(pairs[i][0], pairs[i][1], k=K, only_ids=keep,
                                         doc_type="kb", user_id=USER)
            return keep, [pid2i_local(str(p.id)) for p in pts]

        res = await asyncio.gather(*(q(i) for i in range(len(pairs))))
        for (keep, seq), seq_base, r in zip(res, base_orders, rows):
            kept_n.append(len(keep))
            if keep:
                prompt_n.append(len(seq))
            else:
                empty += 1
            gold_base = any(i in r["gold"] for i in seq_base)
            gold_now = any(i in r["gold"] for i in seq)
            if gold_base and not gold_now:
                lost += 1
            orders.append(seq)
        return score(orders, rows) | {
            "avg_survivors": round(statistics.fmean(kept_n), 2),
            "avg_prompt_chunks": round(statistics.fmean(prompt_n), 2),
            "empty_rate": round(empty / len(rows), 4),
            "gold_lost_vs_base": round(lost / len(rows), 4)}

    return one


async def main() -> int:
    t0 = time.time()
    emb = LLMGateway.embeddings()
    await services_or_die(emb)
    store = QdrantStore(collection=COLL)
    await store.drop()
    store._ready = False
    report = {"metric": "C23 相关性下限：dense 先筛 → hybrid 只在留下的里面重排（两档扫描）",
              "collection": COLL, "k": K, "oversample": OVERSAMPLE,
              "embedding": f"{settings.embedding.model} (ollama, {settings.embedding.dim}d)",
              "chunking": "生产口径：upload_kb._texts_of → document.py 的 CharacterTextSplitter",
              "gold_rule": f"{SHINGLE}-char shingle 重叠率 >= {GOLD_RATIO}，两侧同做空白归一（C20 定稿口径）",
              "baseline_note": "基线=今天这条路（单发 hybrid、无下限）；判据只看 RGB_En 那栏",
              "noise_floor_hit1": 0.0067, "per_dataset": {}}
    for name in DATASETS:
        ds = load(name)
        chunks, rows = ds["chunks"], ds["rows"]
        qtext = [r["question"] for r in rows]
        dv = await embed(emb, chunks, "d")
        qv = await embed(emb, qtext, "q", batch=None)
        pid2i, pts = {}, []
        for i, (c, v) in enumerate(zip(chunks, dv)):
            pid = str(uuid.uuid5(uuid.NAMESPACE_OID, f"c23curve:{name}:{i}"))
            pid2i[pid] = i
            pts.append(Point(id=pid, text=c, dense=v, doc_type="kb", user_id=USER,
                             extra={"chunk_i": i}))
        await store.write(pts)      # 三个数据集同表累积——与 C20 基线同形状
        # 来自别的数据集的块不是本集的 gold：折成本集下标会造假命中，这里统一记 -1
        local = lambda pid: pid2i.get(pid, -1)
        scope = dict(doc_type="kb", user_id=USER)
        pairs = list(zip(qtext, qv))

        async def many(fn):
            sem = asyncio.Semaphore(CONCURRENCY)

            async def one(i):
                async with sem:
                    return await fn(i)
            return await asyncio.gather(*(one(i) for i in range(len(pairs))))

        probes = [[(str(p.id), float(p.score)) for p in got] for got in await many(
            lambda i: store.search(pairs[i][0], pairs[i][1], k=K * OVERSAMPLE, hybrid=False, **scope))]
        async def baseline(i):
            return [local(str(p.id)) for p in
                    await store.search(pairs[i][0], pairs[i][1], k=K, **scope)]

        base_orders = await many(baseline)
        m_base = score(base_orders, rows)
        run = await sweep(store, pairs, probes, base_orders, rows, local)
        row = {"queries": len(rows), "chunks": len(chunks), "baseline_hybrid": m_base,
               "score_mode": {}, "rank_mode": {}}
        print(f"  [{name}] {len(chunks)} 块 / {len(rows)} 问可判 gold；基线 {m_base}", flush=True)
        for th in SCORES:
            row["score_mode"][str(th)] = await run(
                lambda p, th=th: [i for i, s in p if s >= th])
            print(f"    score>={th}: {row['score_mode'][str(th)]}", flush=True)
        for n in RANKS:
            row["rank_mode"][str(n)] = await run(
                lambda p, n=n: [i for i, _ in p[:n]])
            print(f"    rank<={n}: {row['rank_mode'][str(n)]}", flush=True)
        by_rank = {i + 1: [] for i in range(K * OVERSAMPLE)}
        gold_best, gold_outside = [], 0
        for probe, r in zip(probes, rows):
            for j, (pid, s) in enumerate(probe):
                by_rank[j + 1].append(s)
            gs = [s for pid, s in probe if local(pid) in r["gold"]]
            if gs:
                gold_best.append(max(gs))
            else:
                gold_outside += 1        # gold 不在窗内：任何下限都救不回来，那是窗宽的账
        row["dense_score_by_rank"] = {str(j): {"p10": pct(v, .1), "p50": pct(v, .5),
                                              "p90": pct(v, .9), "n": len(v)}
                                     for j, v in by_rank.items()}
        row["gold_best_dense_score"] = {"p05": pct(gold_best, .05), "p10": pct(gold_best, .10),
                                        "p25": pct(gold_best, .25), "p50": pct(gold_best, .50),
                                        "queries_with_gold_in_window": len(gold_best),
                                        "queries_without_gold_in_window": gold_outside}
        report["per_dataset"][name] = row
        print(f"    gold 在窗内那批的最高 dense 分：{row['gold_best_dense_score']}", flush=True)
    report["totals"] = {"datasets": len(DATASETS), "duration_sec": round(time.time() - t0, 1),
                        "script": "tests/manual_recall_floor_curve.py",
                        "cache_reused": str(CACHE)}
    OUT = Path(__file__).resolve().parent.parent / "storage" / "benchmark" / OUT_TMPL.format(K=K)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    await store.drop()
    print(f"\n读数落 {OUT}")
    print(f"  清理：临时集合 {COLL} 已 drop（生产集合 {settings.qdrant.collection_prefix} 未碰）")
    print(f"DONE {time.time() - t0:.0f}s")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
