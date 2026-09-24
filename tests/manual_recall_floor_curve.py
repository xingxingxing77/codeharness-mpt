"""C23 判据的真读数工装（真 bge-m3 + 真 Qdrant，零现金）：**相关性下限画在哪一条线上，代价是多少**。

为什么这根线必须量而不能拍：`longterm.recall` 走的是服务端 RRF 融合，那条腿的返回分是**名次分不是
相似度**（`document_store/exp_store.py:7` 同一条口径），拿它跟任何阈值比都是自欺；能比的只有 dense 腿
的余弦与名次。而余弦刻度是**embedding 端点的属性**——所以本工装干的事和 C27 量截断窗口是同一类：
在 C20 那把尺子（`storage/benchmark/s20_realvec_baseline.json` 的来源）上扫两档，把默认值钉在读数上。

跑法（Qdrant 得在 6333 —— 重启方式 `docker start codeharness-qdrant`；语料是源项目的 rag_bm，
在 `E:/MetaGPT/examples/data/rag_bm`；向量端点**就指 `.env` 里那台**，不许为省额度换回本机
bge-m3 —— 目录铁律 28：换模型丢的不是钱，是读数的资格）：
  cd /e/Codeharness && PYTHONPATH=/e/Codeharness:/e/Codeharness/logs PYTHONIOENCODING=utf-8 \\
    PYTHONDONTWRITEBYTECODE=1 REDIS__DB=15 LANGFUSE__ENABLED=0 NO_PROXY=127.0.0.1,localhost,::1 \\
    F:/anaconda/python.exe -B tests/manual_recall_floor_curve.py > E:/tmp/ch_c23_curve.out 2>&1

**总量闸装在工装里**（`EMB_TOKEN_GATE`，默认 400000）：按端点回的**真 usage** 累计，不是估算；
撞闸就印「已花 / 停在哪个数据集的哪一批」并非零退出。向量缓存**按模型分文件、键里也带模型名**
（`E:/tmp/c23_emb_cache_<model>.json`）——旧 `c20_emb_cache.json` 的键只有 `kind:sha1(文本)`，
换端点复用同一份文件会让新模型的读数悄悄吃到旧模型的向量，那是标定最坏的一种假绿。

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
向量取自**按模型分键**的缓存 ⇒ 缓存命中时零端点调用、零花费；缺的按生产口径补齐（**这一下真发
云端请求**，所以有闸）。产物落 `storage/benchmark/recall_floor_curve_<model>_k{K}.json`
（该目录整目录被 .gitignore 忽略；文件名带模型，旧 bge-m3 那两份是历史证据，不许覆写）。
"""
import asyncio
import json
import os
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
MODEL = settings.embedding.model


def _msafe(m: str) -> str:
    return m.replace(":", "_").replace("/", "_")


# 缓存按模型分文件、键里也带模型名（见文件头那段）：同文本同键 = 换端点会读到老模型的向量。
CACHE = Path(os.environ.get("EMB_CACHE") or f"E:/tmp/c23_emb_cache_{_msafe(MODEL)}.json")
GATE = int(os.environ.get("EMB_TOKEN_GATE", "400000"))   # 本次标定批的硬闸（token，按真 usage 计）
COLL = "c23curve"
USER = "u_c23curve"
# 默认 5 是为了与 C20 那张基线表同 k 可比；生产那一档（`role_zero._kb_recall` 用的是 k=3）用
# `SWEEP_K=3` 再跑一次——下限与候选窗都随 k 动，只量一个 k 等于替另一个 k 签字。
K = int(os.environ.get("SWEEP_K", "5"))
OVERSAMPLE = 3
DATASETS = ["RGB_En", "simplified_RGB", "simplified_CRUD"]
SCORES = [0.30, 0.35, 0.40, 0.45, 0.50, 0.55, 0.60, 0.65]
RANKS = [1, 2, 3, 4, 5, 6, 7, 9, 12]
OUT_TMPL = "recall_floor_curve_{model}_k{K}.json"
CONCURRENCY = 24

_cache: dict[str, list[float]] = json.loads(CACHE.read_text(encoding="utf-8")) if CACHE.exists() else {}
_spent = 0            # 端点回的 total_tokens 累计（不是估算）


def _key(kind: str, text: str) -> str:
    import hashlib
    return f"{MODEL}/{kind}:{hashlib.sha1(text.encode('utf-8')).hexdigest()}"


async def _embed_batch(emb, texts: list[str], kind: str) -> list[list[float]]:
    """一发嵌批 + 计量。用 `emb.async_client.create` 而不是 `aembed_documents`：同一个
    AsyncOpenAI 客户端、同一个模型、同一份输入形状（langchain 内部调的就是它），差别只是
    **响应里的 usage 拿得到**——闸要读数不要估算。批大小取生产那台对象的 `chunk_size`
    （百炼单批 ≤20，超了整批被拒）。"""
    global _spent
    if _spent >= GATE:
        sys.exit(f"exit 3：撞总量闸 —— 已花 {_spent:,}/{GATE:,} token，停在 [{kind}] 这一批之前。"
                 f"缓存已落 {CACHE}，抬闸要人拍，别改工装。")
    rsp = await emb.async_client.create(model=MODEL, input=texts)
    _spent += int(getattr(rsp.usage, "total_tokens", 0) or 0)
    return [d.embedding for d in rsp.data]


async def embed(emb, texts: list[str], kind: str, batch=None) -> list[list[float]]:
    """命中缓存就不发请求；缺的按生产口径补齐（`batch=None` → 逐条，与线上查询腿一致；
    `batch=0` → 用生产的 `chunk_size`，与四条入库腿一致）。每批落一次盘：中途停不重付。"""
    todo = [t for t in texts if _key(kind, t) not in _cache]
    if todo:
        size = emb.chunk_size if batch == 0 else (batch or 1)
        t0 = time.time()
        for i in range(0, len(todo), size):
            part = todo[i:i + size]
            for t, v in zip(part, await _embed_batch(emb, part, kind)):
                _cache[_key(kind, t)] = [float(x) for x in v]
            CACHE.parent.mkdir(parents=True, exist_ok=True)
            CACHE.write_text(json.dumps(_cache), encoding="utf-8")
            print(f"    emb[{kind}] {min(i + size, len(todo))}/{len(todo)} 累计 "
                  f"{_spent:,}/{GATE:,} token  {time.time() - t0:.0f}s", flush=True)
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
              "embedding": (f"{settings.embedding.model} @ {settings.embedding.base_url} "
                            f"({settings.embedding.dim}d)"),
              "chunking": "生产口径：upload_kb._texts_of → document.py 的 CharacterTextSplitter",
              "gold_rule": f"{SHINGLE}-char shingle 重叠率 >= {GOLD_RATIO}，两侧同做空白归一（C20 定稿口径）",
              "baseline_note": "基线=今天这条路（单发 hybrid、无下限）；判据只看 RGB_En 那栏",
              "noise_floor_hit1_bge_m3_history": 0.0067, "per_dataset": {}}
    for name in DATASETS:
        ds = load(name)
        chunks, rows = ds["chunks"], ds["rows"]
        qtext = [r["question"] for r in rows]
        dv = await embed(emb, chunks, "d", batch=0)
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
        # 复现性第二跑：噪声底是**这套配置**的属性，bge-m3 那轮的 0.0067 不许搬过来当新刻度的尺
        m_base2 = score(await many(baseline), rows)
        # dense 腿单独的质量：直接取同一批 probes 的前 k（零额外请求）——这是 C20 那张表的 dense 栏
        dense_orders = [[local(pid) for pid, _ in p[:K]] for p in probes]
        m_dense = score(dense_orders, rows)
        # 阴性对照：每问的 dense 前 k 配给**下一问**的 gold。只挪 orders、不挪 rows —— 两边同挪一格
        # 等于没挪（第一版就这么自伤过一次，读数与 dense 栏逐字相同才暴露）。尺子在凑命中时这里不为 0
        m_neg = score(dense_orders[1:] + dense_orders[:1], rows)
        run = await sweep(store, pairs, probes, base_orders, rows, local)
        row = {"queries": len(rows), "chunks": len(chunks), "baseline_hybrid": m_base,
               "baseline_hybrid_repeat": m_base2,
               "noise_floor_hit1_abs": round(max(abs(m_base[k] - m_base2[k]) for k in m_base), 4),
               "dense_only": m_dense, "negative_control_shifted_gold": m_neg,
               "score_mode": {}, "rank_mode": {}}
        print(f"  [{name}] {len(chunks)} 块 / {len(rows)} 问可判 gold；基线 {m_base}；"
              f"dense {m_dense}；噪声底 {row['noise_floor_hit1_abs']}；"
              f"阴性对照(错一格) {m_neg}", flush=True)
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
                        "cache_reused": str(CACHE),
                        "embedding_tokens_spent": _spent, "embedding_token_gate": GATE,
                        "gate_hit": _spent >= GATE}
    OUT = (Path(__file__).resolve().parent.parent / "storage" / "benchmark"
           / OUT_TMPL.format(model=_msafe(MODEL), K=K))
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    await store.drop()
    print(f"\n读数落 {OUT}")
    print(f"  清理：临时集合 {COLL} 已 drop（生产集合 {settings.qdrant.collection_prefix} 未碰）")
    print(f"  花费：本次实发 {_spent:,} token / 闸 {GATE:,}（缓存 {CACHE}）")
    print(f"DONE {time.time() - t0:.0f}s")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
