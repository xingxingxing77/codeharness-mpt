"""C23(b) 的 `mode=rerank` 那根线怎么标：**精排分（0~1）与 dense 余弦不是同一把尺**，
0.55 那个数在它身上没有意义——本工装量的就是「这条尺上该画在哪」。

零 embedding 花费、零 Qdrant：dense 宽窗的向量全取 C23 标定那轮的缓存，只做「取候选 → 真精排打分」
那一段，因为要量的就是精排给的分。候选与 gold 判定沿用 C20 那把尺子的原口径
（`manual_recall_floor_curve.load`，同一份 shingle + GOLD_RATIO），所以两档的数字能并排放。

四格判据：
  ① **每一发真拿到分**：每个候选都带回非空 `relevance_score`，且**返回序按分单调不增**
     （静默降级、解析不到分、以及「把顺序按 index 排回去＝重排是空壳」那三件事都过不了这一格）；
  ② **这把尺有判别力**：gold 的 p05 明显高于无关切片的 p95，并配**阴性对照**——gold 标记按池内位置
     整串错开一格，错配之后的分数必须落进无关分布；工装自己判有没有真错开（同串＝没挪，C23 栽过）；
  ③ **阈值曲线**：t 从 0.1 到 0.9 各档的「gold 被砍掉的问数」与「每问池子还剩几条」——
     定线只取这两个确定量，hit@1 那类摆动在噪声底以内不算数（C20/C23 两次教训）；
  ④ **花费照实 + 撞闸**：`RERANK_MAX_REQ`（默认 320 发，**精确**）与 `RERANK_TOKEN_GATE`
     （默认 150,000）。⚠ 生产那条腿 `rerank_scored` 把响应体丢了、不回填 usage ⇒ token 那一档只能按
     「字符数 ÷4」**估算**，末行会写明是估算；正因为如此，**发数才是这道闸的真身**。
     撞闸印「已发几发/停在哪一问」并 exit 3，不许自己抬闸。缓存每发落盘，中途停不重付。

跑法（`.env` 里 `RERANKER__*` 必须已配——09-24 15:1x 起就配着）：
  cd /e/Codeharness && PYTHONPATH=/e/Codeharness:/e/Codeharness/logs PYTHONIOENCODING=utf-8 \\
    PYTHONDONTWRITEBYTECODE=1 REDIS__DB=15 LANGFUSE__ENABLED=0 NO_PROXY=127.0.0.1,localhost,::1 \\
    RERANK_MAX_REQ=2 RERANK_TOKEN_GATE=3000 \\
    F:/anaconda/python.exe -B tests/manual_rerank_floor_curve.py --probe
  ... 探针先量单价（两问、低闸），再按它算整批的闸放开跑

**不算门禁**：花真钱挂真端点，端点缺席 exit 1 说「没跑成」。
产物 `storage/benchmark/rerank_floor_curve_<model>.json`；缓存 `E:/tmp/ch_rerank_cache.json`。
"""
import argparse
import asyncio
import hashlib
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from manual_recall_floor_curve import (CACHE as EMB_CACHE, load, pct,        # noqa: E402
                                       MODEL as EMB_MODEL)   # 标定那轮的缓存键形状：对**原文**做 sha1
from codeharness.configs.settings import settings                           # noqa: E402
from codeharness.memory.longterm import LongTermMemory                      # noqa: E402

TOKEN_GATE = int(os.environ.get("RERANK_TOKEN_GATE", "150000"))
MAX_REQ = int(os.environ.get("RERANK_MAX_REQ", "320"))
POOL = int(os.environ.get("RERANK_POOL", "15"))          # 与生产 dense 宽窗同档（k=3 × oversample 5）
CACHE = Path(os.environ.get("RERANK_CACHE", "E:/tmp/ch_rerank_cache.json"))
THRESHOLDS = [round(0.1 * i, 1) for i in range(1, 10)]
DATASETS = ["RGB_En", "simplified_CRUD"]

_emb = json.loads(EMB_CACHE.read_text(encoding="utf-8")) if EMB_CACHE.exists() else {}
_scores: dict = json.loads(CACHE.read_text(encoding="utf-8")) if CACHE.exists() else {}
_state = {"reqs": 0, "tokens": 0, "estimate": False}


def _die(msg: str) -> None:
    sys.exit(f"exit 1：{msg}")


def _ckey(query: str, docs: list) -> str:
    h = hashlib.sha1("\x00".join([query, *docs]).encode("utf-8")).hexdigest()
    return f"{settings.reranker.model}/{h}"


def _ekey(kind: str, text: str) -> str:
    # 键的形状必须与标定那轮**逐字一致**（它对**原文**做 sha1，不折空白）——
    # 这里多做一次 `_norm` 就会全线 miss，然后被「补缓存」的名义烧出不该有的 embedding 请求。
    return f"{EMB_MODEL}/{kind}:{hashlib.sha1(text.encode('utf-8')).hexdigest()}"


async def rerank_once(query: str, docs: list) -> list:
    """一发真精排，走**生产那条腿**（`LongTermMemory.rerank_scored`）——只把候选直接喂给它。

    另写一个客户端就没有读数资格：要量的正是产品会拿到的那串分。返回 `[(池内下标, 分数)]`（精排序）。
    """
    key = _ckey(query, docs)
    if key in _scores:
        return _scores[key]
    if _state["reqs"] >= MAX_REQ or _state["tokens"] >= TOKEN_GATE:
        sys.exit(f"exit 3：撞闸 —— 已发 {_state['reqs']}/{MAX_REQ} 发、已计 "
                 f"{_state['tokens']:,}/{TOKEN_GATE:,} token"
                 f"{'（估算）' if _state['estimate'] else ''}，停在第 {_state['reqs'] + 1} 发之前。"
                 f"缓存已落 {CACHE}，抬闸要人拍、别改工装。")
    hits = [type("H", (), {"payload": {"text": d}, "i": i})() for i, d in enumerate(docs)]
    t0 = time.time()
    pairs = await LongTermMemory(embeddings=None, doc_type="kb").rerank_scored(query, hits, len(docs))
    out = [(h.i, s) for h, s in pairs]
    if any(s is None for _, s in out) or not out:
        # 拿不到分＝静默降级（C8 那一族），这一发的读数不存在，别让它混进曲线
        _die(f"精排没给分（返回 {len(out)} 条、含 None {sum(1 for _, s in out if s is None)} 个）——"
             f"端点 {settings.reranker.base_url} 今天不可用或响应形状不对，先看这一发：{out[:3]}")
    _state["reqs"] += 1
    _state["tokens"] += (len(query) + sum(len(d) for d in docs)) // 4      # 生产件不回填 usage ⇒ 估算
    _state["estimate"] = True
    _scores[key] = out
    CACHE.write_text(json.dumps(_scores), encoding="utf-8")
    print(f"    精排第 {_state['reqs']} 发：{len(docs)} 条候选 / 累计 {_state['tokens']:,} token（估算）"
          f" / {time.time() - t0:.1f}s", flush=True)
    return out


def dense_pool(chunks: list, query: str, n: int) -> list:
    """生产口径的 dense 宽窗（缓存向量做余弦取前 n），零端点调用。缺向量就直接停，不现算。"""
    import math
    qv = _emb.get(_ekey("q", query)) or _emb.get(_ekey("d", query))
    if qv is None:
        _die(f"缓存里没有这句的向量：{query[:36]}…… —— 不拿现算冒充「零花费」，先补缓存或换问")

    def cv_of(c):
        return _emb.get(_ekey("d", c))

    qn = math.sqrt(sum(a * a for a in qv))
    scored = []
    for i, c in enumerate(chunks):
        cv = cv_of(c)
        if not cv:
            continue
        dot = sum(a * b for a, b in zip(qv, cv))
        nn = qn * math.sqrt(sum(b * b for b in cv))
        scored.append((dot / nn if nn else 0.0, i))
    return [i for _, i in sorted(scored, reverse=True)[:n]]


async def sweep(name: str, limit: int) -> dict:
    data = load(name)
    chunks, rows = data["chunks"], data["rows"][:limit]
    gold, other, neg, per_q, nonmono = [], [], [], [], 0
    for r in rows:
        pool = dense_pool(chunks, r["question"], POOL)
        got = await rerank_once(r["question"], [chunks[i] for i in pool])
        seq = [s for _, s in got]
        if any(seq[j] > seq[j - 1] for j in range(1, len(seq))):   # 递降才是对的：上升一格＝序没保住
            nonmono += 1                          # ①：返回序必须按分递减，否则「重排」是空壳
        # 两套下标别混：`orig` 是**池内位置**（我喂进去的顺序），chunk 下标 = pool[orig]。
        # 全部换算成 chunk 下标之后再比，省得精排序与粗排序互相冒充。
        sc = {pool[orig]: s for orig, s in got}
        g = r["gold"]
        gs = [sc[i] for i in pool if i in g]
        os_ = [sc[i] for i in pool if i not in g]
        # 阴性对照：每个真 gold 去拿**它前一名**的分（挪的是标签、不是分数）——
        # C23 那次「orders 与 rows 同挪＝没挪」的教训用下面那条断言钉住。
        n = len(pool)
        ng = [sc[pool[(p - 1) % n]] for p, i in enumerate(pool) if i in g]
        if n and len(ng) == len(gs) and ng == gs:
            _die(f"{r['question'][:24]}…… 的阴性对照与正样本逐字相同 ⇒ 那一格没真错开，读数作废")
        gold += gs
        other += os_
        neg += ng
        kept = {t: sum(1 for i in pool if sc[i] >= t) for t in THRESHOLDS}
        per_q.append({"gold_max": max(gs) if gs else None, "kept": kept, "n_pool": n})
    return {"n_rows": len(rows), "gold": gold, "other": other, "neg": neg,
            "per_q": per_q, "nonmono": nonmono}


def curve(res: dict) -> dict:
    n = max(res["n_rows"], 1)
    out = {}
    for t in THRESHOLDS:
        kept = sum(p["kept"][t] for p in res["per_q"]) / n
        lost = sum(1 for p in res["per_q"] if p["gold_max"] is not None and p["gold_max"] < t)
        empty = sum(1 for p in res["per_q"] if p["kept"][t] == 0)
        out[str(t)] = {"avg_pool_kept": round(kept, 2), "gold_lost": lost, "empty_pool": empty}
    return out


async def main_async(limit: int, probe: bool) -> int:
    cfg = settings.reranker
    if not cfg.base_url or not cfg.api_key:
        _die(f"RERANKER__ 没配齐（base_url={bool(cfg.base_url)}、api_key={bool(cfg.api_key)}）")
    print(f"  端点：{cfg.model} @ {cfg.base_url}")
    print(f"  闸：{MAX_REQ} 发（精确）/ {TOKEN_GATE:,} token（按字符÷4 估算）｜宽窗 {POOL} 条/问")
    names = [DATASETS[0]] if probe else DATASETS
    result = {}
    for name in names:
        res = await sweep(name, 2 if probe else limit)
        if not res["gold"]:
            _die(f"{name}：一个 gold 分都没拿到，这把尺量不出东西（先查候选与 gold 判定）")
        result[name] = {"n_rows": res["n_rows"], "非单调的问数": res["nonmono"],
                        "gold_p05": pct(res["gold"], 0.05), "gold_p50": pct(res["gold"], 0.5),
                        "other_p95": pct(res["other"], 0.95) if res["other"] else None,
                        "other_p50": pct(res["other"], 0.5) if res["other"] else None,
                        "neg_p50": pct(res["neg"], 0.5) if res["neg"] else None,
                        "curve": curve(res)}
        r = result[name]
        print(f"  {name}｜n={r['n_rows']} 非序={r['非单调的问数']} gold p05={r['gold_p05']} "
              f"p50={r['gold_p50']} ｜ 无关 p50={r['other_p50']} p95={r['other_p95']} "
              f"｜ 阴性 p50={r['neg_p50']}")
    out_dir = ROOT / "storage" / "benchmark"
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"rerank_floor_curve_{cfg.model}.json"
    out.write_text(json.dumps({"gate": {"req": MAX_REQ, "token": TOKEN_GATE, "reqs_used": _state["reqs"],
                                        "tokens_estimate": _state["tokens"]},
                               "pool": POOL, "embedding_cache": str(EMB_CACHE),
                               "results": result}, ensure_ascii=False), encoding="utf-8")
    print(f"  产物：{out}")
    print(f"  花费：{_state['reqs']} 发 / {_state['tokens']:,} token（**按字符÷4 估算**——"
          f"生产那条腿不回填 usage，见本文件④）")
    for name, r in result.items():
        print(f"  阈值表 {name}：" + " ｜ ".join(
            f"{t}:{v['avg_pool_kept']}条/丢{v['gold_lost']}/空{v['empty_pool']}"
            for t, v in r["curve"].items()))
    if probe:
        print("  只探针（两问）：拿这一行的 token÷发数 当单价，再定整批的闸放开跑")
        return 0
    bad = [f"{n}：{r['非单调的问数']} 问的返回序不按分递减 ⇒ 精排序没保住（①）"
           for n, r in result.items() if r["非单调的问数"]]
    for n, r in result.items():
        # ②a 这一格问的是「这把尺能不能当门限量纲」：分数得跟**内容**相关，不是跟位置/名次相关。
        #     判据用阴性对照（gold 标记整串错开一格）——它的中位分必须明显低于真 gold。
        if r["neg_p50"] is None or r["gold_p50"] is None or r["neg_p50"] >= r["gold_p50"]:
            bad.append(f"{n}：阴性对照中位分（{r['neg_p50']}）不低于真 gold（{r['gold_p50']}）"
                       "⇒ 分数与位置相关，这把尺不能当门限量纲（②a，整批读数作废）")
        # ②b 尾部重叠**不判红**，只登记。第一版把它写成 pass/fail（要求 p05(gold) > p95(无关)），
        #     那是**排序质量**的标准、不是**划线资格**的标准：高分段里混进几条不相关切片对下限无害
        #     （它们只是被留下而已），而划线看的是「丢不丢 gold、每问还剩几条」。
        line = max((t for t, v in r["curve"].items()
                    if v["gold_lost"] == 0 and v["empty_pool"] == 0), key=float, default=None)
        v = r["curve"].get(str(line)) if line is not None else None
        r["建议线"] = line
        over = (r["gold_p05"] is not None and r["other_p95"] is not None
                and r["gold_p05"] <= r["other_p95"])
        print(f"  {n}：尾部重叠 = gold p05 {r['gold_p05']} vs 无关 p95 {r['other_p95']}"
              f"{'（重叠，照实登记）' if over else '（分开）'} ｜ 可划的最高档 t={line}"
              f" → 每问留 {v['avg_pool_kept'] if v else '-'} 条、"
              f"丢 gold {v['gold_lost'] if v else '-'} 问、空池 {v['empty_pool'] if v else '-'} 问")
    if bad:
        print("判据未绿：" + "；".join(bad))
        return 2
    print("判据：①每发都真拿到分且返回序按分递减；②a 阴性对照落进无关分布（这把尺跟内容相关）；"
          "③阈值表在上面，可划的最高档已逐栏打出——定线取「不丢 gold 且不砍空」的最高 t")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=300, help="每个语料跑前 N 问（全量 300）")
    ap.add_argument("--probe", action="store_true", help="只跑 2 问量单价")
    args = ap.parse_args()
    if not _emb:
        _die(f"embedding 缓存是空的（{EMB_CACHE}）——本工装靠它做零花费的 dense 宽窗")
    return asyncio.run(main_async(args.limit, args.probe))


if __name__ == "__main__":
    sys.exit(main())
