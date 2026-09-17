"""S9 门禁（N8）：把源的评测资产变成离线检索质量回归门禁（施工4 §9.2 第一件）。

数据集 = 源 `examples/data/rag_bm`（simplified_CRUD / simplified_RGB / RGB_En，源 load_dataset
的 DatasetInfo 三元组语义，但源 `rag/benchmark/base.py` 的指标件不搬——它绑 llama_index evaluator
与 jieba/evaluate，且 `mean_reciprocal_rank` 自带 NameError（:119 引用未定义的 `text`，从未跑通过）。
本件按指标**意图**重写，两处偏差显式入册：
  ①切块 = 定长 1024 字符、0 重叠（源 SentenceSplitter(chunk_size=1024, chunk_overlap=0) 的
    字符等价物；中文按 token 切与按字切近似，偏差不改变"gold 区域"判定）；
  ②gold 判定 = 20 字符 shingle 重叠率 ≥ 0.1（源 recall/hit_rate 的 `node.text in doc` 逐字包含
    对手工整理的 gt_reference 几乎恒假——引用文本与原文有手工增删，逐字匹配是死口径）。

可复现性（S5.2 复测口径的收口）：Qdrant **本地模式**（path=，进程内暴力精确检索，warning 原文
"Local mode performs exact (brute-force) search"）——HNSW 近似漂移与 IDF 集合漂移在这里都不存在，
零外部服务、零 docker，门禁永远可跑。t3 把"两次跑逐条一致"钉成断言；server 模式要同性质，
需给 query_points 传 SearchParams(exact=True)（Prefetch 在 client 1.19 不收该参数，留待真需要时）。

跑法：
  cd /e/Codeharness && PYTHONPATH=/e/Codeharness PYTHONIOENCODING=utf-8 F:/anaconda/python.exe tests/s9_benchmark.py
"""
import asyncio
import json
import shutil
import tempfile
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC_RAG_BM = ROOT.parent / "MetaGPT" / "examples" / "data" / "rag_bm"
CHUNK = 1024          # 源 SentenceSplitter chunk_size
GOLD_RATIO = 0.1      # shingle 重叠率超过此值 = 该块属于 gold 区域
SHINGLE = 20
MAX_Q = 20            # 每数据集最多取前 20 问（确定性截断；RGB_En 300 问全跑没必要）
K = 5


class HashEmbeddings:
    """确定性 bag-of-chars 假 embedding（与 s5 门禁同一只，10 行不值得抽公共件）：
    离线、可复现，dense 只看得见字符重叠——正是 hybrid 对照实验要利用的真实缺陷。"""

    dim = 64

    def _v(self, text: str) -> list[float]:
        v = [0.0] * self.dim
        for ch in text:
            v[ord(ch) % self.dim] += 1.0
        n = sum(x * x for x in v) ** 0.5 or 1.0
        return [x / n for x in v]

    async def aembed_documents(self, texts):
        return [self._v(t) for t in texts]

    async def aembed_query(self, text):
        return self._v(text)


def _norm(s) -> str:
    """gt_reference 有手工整理痕迹：首尾多 `"` 与 `,`、句中多空格；RGB_En 里还是 list（多参考文）。
    归一后拼接——gold 判定只关心字符 run 的重叠，跨参考文的接缝不产生合法 shingle。"""
    if isinstance(s, list):
        s = "\n".join(str(x) for x in s)
    return "".join(str(s).split()).strip('",')


def _shingles(s: str) -> set[str]:
    return {s[i:i + SHINGLE] for i in range(0, max(len(s) - SHINGLE, 1))}


def load_dataset() -> tuple[dict[str, dict], dict[str, str]]:
    """源 load_dataset 语义的本地化：目录即数据集（dataset_info.json 的 RGB_EN 键与
    实际目录 RGB_En 大小写不一致——以目录为准，不背这口锅）。
    gold 推导不出的问题/数据集如实剔除并记录原因（RGB_En 的 gt_reference 是答案参照不是
    原文摘录，与 documents.txt 零 shingle 重叠——检索 gold 从文本上不可推导，不是阈值问题）。"""
    if not SRC_RAG_BM.exists():
        return {}, {}
    out, skipped = {}, {}
    for d in sorted(SRC_RAG_BM.iterdir()):
        docs_f, gt_f = d / "documents.txt", d / "answer.json"
        if not (docs_f.exists() and gt_f.exists()):
            continue
        text = docs_f.read_text(encoding="utf-8")
        chunks = [text[i:i + CHUNK] for i in range(0, len(text), CHUNK)]
        gts = json.loads(gt_f.read_text(encoding="utf-8"))[:MAX_Q]
        chunk_sh = [_shingles(c) for c in chunks]
        golds, kept = [], []
        for g in gts:
            gs = _shingles(_norm(g["gt_reference"]))
            gold = ({i for i, cs in enumerate(chunk_sh)
                     if len(gs & cs) / max(len(gs), 1) >= GOLD_RATIO} if gs else set())
            if gold:
                golds.append(gold)
                kept.append(g["question"])
        if len(kept) == len(gts) and kept:
            out[d.name] = {"chunks": chunks, "questions": kept, "golds": golds}
        else:
            skipped[d.name] = (f"gold 推导失败 {len(gts) - len(kept)}/{len(gts)} 问"
                               if kept else "gt_reference 与 documents.txt 零重叠（非原文摘录）")
    return out, skipped


async def _run_modes(store, emb, ds) -> dict:
    """同一 collection 上两种模式各查一遍，返回 hit@k 与 MRR（MRR 按 gold 集合首个命中计，
    源同名函数有 NameError，此处按意图重写）。"""
    res = {}
    for mode, hyb in (("dense_only", False), ("hybrid", True)):
        hits, rr = {1: 0, 3: 0, 5: 0}, 0.0
        orders = []
        for q, gold in zip(ds["questions"], ds["golds"]):
            dense = await emb.aembed_query(q)
            pts = await store.search(q, dense, k=K, hybrid=hyb, doc_type="kb", user_id="u_s9bench")
            ids = [p.payload["chunk_i"] for p in pts]
            orders.append(ids)
            for k in hits:
                hits[k] += int(any(i in gold for i in ids[:k]))
            rr += next((1.0 / (r + 1) for r, i in enumerate(ids) if i in gold), 0.0)
        n = len(ds["questions"])
        res[mode] = {"hit@1": hits[1] / n, "hit@3": hits[3] / n, "hit@5": hits[5] / n,
                     "mrr": rr / n, "_orders": orders, "_n": n, "_hits": hits}
    return res


async def _bench():
    from codeharness.document_store.qdrant_store import Point, QdrantStore
    tmp = tempfile.mkdtemp(prefix="s9bench_")
    try:
        client = _local_client(tmp)
        st = QdrantStore(collection="s9_bench", client=client)
        emb = HashEmbeddings()
        ds_map, skipped = load_dataset()
        table = {"dataset": "source rag_bm (E:/MetaGPT/examples/data/rag_bm)",
                 "chunking": f"fixed {CHUNK} chars, overlap 0", "k": K,
                 "embedding": "hash-fake(64d, bag-of-chars)",
                 "backend": "qdrant local mode (exact brute-force)",
                 "gold_rule": f"{SHINGLE}-char shingle overlap >= {GOLD_RATIO}",
                 "skipped_datasets": skipped, "per_dataset": {}}
        for name, ds in ds_map.items():
            await st.write([Point(id=uuid.uuid5(uuid.NAMESPACE_OID, f"{name}:{i}"),
                                  text=c, dense=v, doc_type="kb", user_id="u_s9bench",
                                  extra={"chunk_i": i})
                            for (i, c), v in zip(enumerate(ds["chunks"]),
                                                 await emb.aembed_documents(ds["chunks"])) if v])
            r = await _run_modes(st, emb, ds)
            d, h = r["dense_only"], r["hybrid"]
            table["per_dataset"][name] = {
                "chunks": len(ds["chunks"]), "queries": d["_n"],
                "dense_only": {k: d[k] for k in ("hit@1", "hit@3", "hit@5", "mrr")},
                "hybrid": {k: h[k] for k in ("hit@1", "hit@3", "hit@5", "mrr")}}
            # ② 回收本数据集，下一数据集独占 collection（gold 判定是按数据集切的，混装会互相稀释）
            await st.delete_scope(doc_type="kb", user_id="u_s9bench")
            print(f"  {name}: {len(ds['chunks'])} 块 {d['_n']} 问  "
                  f"hit@1 {d['hit@1']:.2f}→{h['hit@1']:.2f}  mrr {d['mrr']:.2f}→{h['mrr']:.2f}")

            # ③ 复现性：同一 collection 再查一遍，两轮 id 序列逐条一致（本地模式暴力检索的硬承诺）
            await st.write([Point(id=uuid.uuid5(uuid.NAMESPACE_OID, f"{name}:{i}"),
                                  text=c, dense=v, doc_type="kb", user_id="u_s9bench",
                                  extra={"chunk_i": i})
                            for (i, c), v in zip(enumerate(ds["chunks"]),
                                                 await emb.aembed_documents(ds["chunks"])) if v])
            r2 = await _run_modes(st, emb, ds)
            assert r2["hybrid"]["_orders"] == r["hybrid"]["_orders"], \
                f"{name} 两轮检索结果不一致——复现性破了（server 模式需钉 exact=True）"
            await st.delete_scope(doc_type="kb", user_id="u_s9bench")

        n_all = sum(v["queries"] for v in table["per_dataset"].values())
        table["total"] = {"queries": n_all}
        out = ROOT / "storage" / "benchmark"
        out.mkdir(parents=True, exist_ok=True)
        (out / "s9_retrieval_gate.json").write_text(
            json.dumps(table, ensure_ascii=False, indent=2), encoding="utf-8")
        return table
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        try:
            await client.close()
        except Exception:
            pass


def _local_client(path: str):
    """qdrant 本地模式：进程内暴力精确检索（天然可复现），check_compatibility=False 防版本探测
    线程吊死退出（s3b t14 教训）。"""
    from qdrant_client import AsyncQdrantClient
    return AsyncQdrantClient(path=path, check_compatibility=False)


def t1_dataset_load():
    ds, skipped = load_dataset()
    if not ds:
        print("  t1 跳过（源 rag_bm 数据集不在本机）")
        return None
    total_c = sum(len(v["chunks"]) for v in ds.values())
    total_q = sum(len(v["questions"]) for v in ds.values())
    assert total_c > 0 and total_q > 0 and all(any(g for g in v["golds"]) for v in ds.values()), \
        "数据集为空或没有任何 gold 块——shingle 阈值/归一化口径漂了"
    note = f"；剔除 {len(skipped)} 组：{skipped}" if skipped else ""
    print(f"  t1 源数据集就位：{len(ds)} 组 {total_c} 块 {total_q} 问，gold 判定全部非空{note}")
    return ds


def t2_hybrid_vs_dense_gate():
    table = asyncio.run(_bench())
    tot_d = [table["per_dataset"][n]["dense_only"] for n in table["per_dataset"]]
    tot_h = [table["per_dataset"][n]["hybrid"] for n in table["per_dataset"]]
    agg = lambda rows, key: sum(r[key] for r in rows) / len(rows)   # noqa: E731
    d5, h5 = agg(tot_d, "hit@5"), agg(tot_h, "hit@5")
    d1, h1 = agg(tot_d, "hit@1"), agg(tot_h, "hit@1")
    dm, hm = agg(tot_d, "mrr"), agg(tot_h, "mrr")
    # 门禁：hybrid 不退步 + 至少一项严格更好（融合真的干了活，不是白装）
    assert h5 >= d5 and hm >= dm, f"hybrid 退步：{json.dumps(table['per_dataset'], ensure_ascii=False)}"
    assert h1 > d1 or hm > dm, "hybrid 与 dense-only 完全打平——RRF 融合这一路没起作用"
    print(f"  t2 hybrid≥dense 全dataset：hit@1 {d1:.2f}→{h1:.2f}  hit@5 {d5:.2f}→{h5:.2f}  "
          f"mrr {dm:.2f}→{hm:.2f}；表已存 storage/benchmark/s9_retrieval_gate.json")


def main():
    t1_dataset_load()
    t2_hybrid_vs_dense_gate()
    print("\ns9_benchmark: 2/2 全绿（N8 检索质量门禁，零外部服务）")


if __name__ == "__main__":
    main()
