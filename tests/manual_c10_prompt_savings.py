"""C23 未验⑤：下限开起来，**进 prompt 的 token 到底少了几成**（花真钱，三发锚点）。

行里那句原话：「`avg_prompt_chunks` 只报平均值，没量『进 prompt 的 token 少了几成』——那要跟 C24 的
自主检索一起才算得清」。三件事凑在一起才算得清，所以这个工装一次出三个数：

1. **预取段自己**：同一批 query，`mode=off` 与 `mode=score/0.55` 各排一遍要进 prompt 的那一段
   （`format_kb_blocks` 的产物，界面与模型看到的就是这串），报条数/字符/token 三种量纲的分布；
2. **整发 prompt 的真 token**：挑一个代表问，把「不带预取段 / 带 off 档那段 / 带 0.55 档那段」
   三个 prompt 各发一次**真 StepFun**，读端点回的 `prompt_tokens` —— 本地 tiktoken 不是 StepFun 的
   分词器（`count_output_tokens` 对未知模型退回 cl100k），所以它只配当分布，真数以这里为准；
3. **减掉 C24 那半笔**：预取省下的，会被模型自己再查一次吃回去一点（C24 现证：判别场自发调 1 次、
   带回 281 字两份出处）。这里把「省下」与「回喂」并排印出来，净读数才算成立。

闸（发钱之前判，撞闸 exit 3 印「已花/停在哪一格」；抬闸要人拍）：
  · `EMB_TOKEN_GATE`（默认 20,000）：向量全取 C23 标定那份缓存，正常情况下这一格应该几乎为零；
    真的接近闸值说明缓存键形状对不上（09-25 rerank 那件就是靠这条拦住的），停手来核而不是烧完。
  · `LLM_SPEND_GATE_CNY`（默认 0.05）：三发锚点的钱。

跑法（Qdrant 要在 6333：`docker start codeharness-qdrant`）：
  cd /e/Codeharness && PYTHONPATH=/e/Codeharness:/e/Codeharness/logs PYTHONIOENCODING=utf-8 \\
    PYTHONDONTWRITEBYTECODE=1 REDIS__DB=15 LANGFUSE__ENABLED=0 NO_PROXY=127.0.0.1,localhost,::1 \\
    F:/anaconda/python.exe -B tests/manual_c10_prompt_savings.py

**不算门禁**：挂真端点与真向量库；只碰自己的集合 `c10probe`（跑完 drop），不碰生产集合、不碰 db0。
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

from codeharness.actions.upload_kb import _texts_of  # noqa: E402
from codeharness.configs.settings import settings  # noqa: E402
from codeharness.document_store.qdrant_store import QdrantStore  # noqa: E402
from codeharness.memory.longterm import LongTermMemory, format_kb_blocks  # noqa: E402
from codeharness.provider.gateway import LLMGateway  # noqa: E402
from codeharness.schema import Message  # noqa: E402
import manual_memory_floor_curve as M  # noqa: E402
from manual_memory_floor_curve import (CACHE, CachedEmbeddings, _msafe,  # noqa: E402
                                       _quiet_drop)

SRC = Path("E:/MetaGPT/examples/data/rag_bm/RGB_En")
COLL, USER, PROJ = "c10probe", "u_c10probe", "c10probe"
N_DOCS = int(os.environ.get("C10_DOCS", "200"))
N_Q = int(os.environ.get("C10_QUERIES", "100"))
K = 3                                    # 生产那一档（`role_zero._kb_recall`）
EMB_GATE = int(os.environ.get("EMB_TOKEN_GATE", "20000"))
LLM_GATE = float(os.environ.get("LLM_SPEND_GATE_CNY", "0.05"))
os.environ["EMB_TOKEN_GATE"] = str(EMB_GATE)          # 让 CachedEmbeddings 用同一道闸


def _tokens(text: str) -> int:
    from codeharness.utils.token_counter import count_output_tokens
    return count_output_tokens(string=text, model=settings.llm.model)


async def main() -> int:
    import httpx
    try:
        assert httpx.get(f"{settings.qdrant.url}/healthz", timeout=3).status_code == 200
    except Exception as e:
        sys.exit(f"exit 1：Qdrant 不在线（{settings.qdrant.url}）：{type(e).__name__}——本件要真向量")
    if not CACHE.exists():
        sys.exit(f"exit 1：标定缓存不在（{CACHE}）——没有它这一跑会变成补缓存的名义烧额度")

    # 与 manual_memory_floor_curve 同一条：`longterm.py:207` 的 `rerank_scored` 是**无条件**调的，
    # `.env` 配了精排就会每发 recall 多打一跳（0.8s + 按 query×候选计费的配额）。本件量的是
    # dense 下限对进 prompt 那段的影响，不是精排序 ⇒ 钉灭。（09-25 第一跑忘钉，≥600 发。）
    if settings.reranker.base_url:
        print(f"   钉灭精排腿（原 base_url 非空，len={len(settings.reranker.base_url)}）")
        settings.reranker.base_url = ""

    chunks = [t for t, _ in _texts_of(SRC / "documents.txt")][:N_DOCS]
    rows = json.loads((SRC / "answer.json").read_text(encoding="utf-8"))[:N_Q]
    questions = [str(r["question"]) for r in rows]
    print(f"   kb 池 {len(chunks)} 块（生产切块口径）、查询 {len(questions)} 问、k={K}"
          f" · 集合 {COLL} · 缓存 {CACHE.name}")

    inner = LLMGateway.embeddings()
    emb = CachedEmbeddings(inner)
    kb = LongTermMemory(project_id=PROJ, user_id=USER, embeddings=emb,
                        store=QdrantStore(collection=COLL), doc_type="kb")
    await _quiet_drop(kb)
    # 写入用 **kb 腿自己的口径**（`_texts_of` 的原块直接 `store.write`，与 `manual_recall_floor_curve`
    # 逐字同形状），不走 `LongTermMemory.overflow()`：那条腿会再套一层 `split_for_embedding`（≤1200 字），
    # 把这批 1.1k~1.5k 字的块切成**新字符串** ⇒ 与标定缓存的键不再相同。第一版就为此白买了
    # 23,288 token 的向量（还因为落盘时机错买了两次，共 46,576）。本件量的是 kb 腿进 prompt 那一段，
    # 该用 kb 腿的块。
    import uuid

    from codeharness.document_store.qdrant_store import Point
    vecs = await emb.aembed_documents(chunks)
    await kb.store.write([
        Point(id=str(uuid.uuid5(uuid.NAMESPACE_OID, f"c10probe:{i}")), text=c, dense=v,
              doc_type="kb", user_id=USER, project=PROJ, extra={"chunk_i": i})
        for i, (c, v) in enumerate(zip(chunks, vecs))])
    print(f"   写入 {len(chunks)} 块（kb 腿原块口径）· 实发 {M._spent:,}/{EMB_GATE:,} token"
          f" · 命中/未命中 doc {M._hits['d']}/{M._miss['d']}")

    cfg = settings.recall_floor
    per = {}
    for mode, ms in [("off", None), ("score", cfg.min_score)]:
        cfg.mode, cfg.min_score = mode, ms if ms is not None else cfg.min_score
        blocks, counts = [], []
        for q in questions:
            b = format_kb_blocks(await kb.recall(q, k=K)) or ""
            blocks.append(b)
            counts.append(len(b.splitlines()))
        per[mode] = {"blocks": blocks, "lines": counts}
        print(f"   档 {mode}/{ms}: 平均 {statistics.fmean(counts):.2f} 行/问 · 空段 "
              f"{sum(1 for b in blocks if not b.strip())} 问 · 字符中位 "
              f"{statistics.median(len(b) for b in blocks):.0f}")
    off_b, hi_b = per["off"]["blocks"], per["score"]["blocks"]
    tok_off = [_tokens(b) for b in off_b]
    tok_hi = [_tokens(b) for b in hi_b]
    saved_chars = 1 - statistics.fmean(len(b) for b in hi_b) / max(
        statistics.fmean(len(b) for b in off_b), 1)
    saved_tok = 1 - statistics.fmean(tok_hi) / max(statistics.fmean(tok_off), 1)
    print(f"   预取段自身：off 平均 {statistics.fmean(len(b) for b in off_b):.0f} 字 / "
          f"{statistics.fmean(tok_off):.0f} tok → 0.55 档 {statistics.fmean(len(b) for b in hi_b):.0f} 字"
          f" / {statistics.fmean(tok_hi):.0f} tok ⇒ 省字符 {saved_chars:.1%}、省 token {saved_tok:.1%}"
          f"（本地 tiktoken，仅分布）")

    # —— 真端点锚点：同一问，三个 prompt 各发一发，读端点回的 prompt_tokens
    anchor = questions[0]
    prompt_of = {"no_prefetch": anchor,
                 "off": f"[知识库片段]\n{off_b[0]}\n\n{anchor}",
                 "score_055": f"[知识库片段]\n{hi_b[0]}\n\n{anchor}"}
    cm_costs = []
    # ⚠ 单发必须有上限：09-25 第一版这里用生产档（`max_token=32768`、thinking 模型），
    # 一发 76 个字的输入烧出 **¥1.7329（端点回 pt=25,216 / ct=816,797）**，而「发钱之前判累计」的闸
    # 对单发完全无效。锚点只要 `prompt_tokens` 这一个数，所以把产出钉到 200，并在每发之后再判一次闸
    # （事前闸拦突发，事后闸拦累计）。
    gw = LLMGateway(cfg=settings.llm.model_copy(update={"max_token": 200, "stream": False}))
    for name, p in prompt_of.items():
        spent = gw.cost_manager.get_costs().cost_cny
        if spent >= LLM_GATE:
            print(f"🛑 撞 LLM 闸于 {name} 之前：已花 ¥{spent:.4f}/{LLM_GATE}——停手，抬闸要人拍")
            sys.exit(3)
        msg = await gw.ainvoke(p, tag=f"c10-{name}")
        u = getattr(msg, "usage_metadata", None) or {}
        now = gw.cost_manager.get_costs().cost_cny
        cm_costs.append({"variant": name, "chars": len(p), "prompt_tokens": u.get("input_tokens"),
                         "completion_tokens": u.get("output_tokens"), "cny_after": round(now, 5)})
        print(f"   真端点 {name}: 输入 {len(p)} 字 → prompt_tokens={u.get('input_tokens')} "
              f"ct={u.get('output_tokens')} · 累计 ¥{now:.5f}")
        if now >= LLM_GATE:
            print(f"🛑 单发后累计 ¥{now:.4f} 已越过闸 {LLM_GATE}：停在这里，不续下一发")
            sys.exit(3)
    base = next((c["prompt_tokens"] for c in cm_costs if c["variant"] == "no_prefetch"), 0) or 1
    for c in cm_costs:
        c["over_base_pct"] = round(100 * (c["prompt_tokens"] or 0) / base, 1)
    # 整发口径的分母用现证值：C31 未验① 那场真会话「那个人自己的首轮 prompt 4450 字」
    WHOLE_BASE_CHARS = 4450
    a = next(c for c in cm_costs if c["variant"] == "off")
    b2 = next(c for c in cm_costs if c["variant"] == "score_055")
    print(f"   整发口径（真 token）：预取段占无预取那发的 {100 * (a['prompt_tokens'] - base) / base:.1f}%"
          f"，下限把它压到 {100 * (b2['prompt_tokens'] - base) / base:.1f}%"
          f" ⇒ **整发少 {100 * (a['prompt_tokens'] - b2['prompt_tokens']) / (a['prompt_tokens'] or 1):.1f}%**"
          f"（预取段自身省 {saved_tok:.1%}）")
    snap = gw.cost_manager.get_costs()
    print(f"   本跑真模型：{len(cm_costs)} 发 · pt={gw.cost_manager.total_prompt_tokens} "
          f"ct={gw.cost_manager.total_completion_tokens} cost_cny=¥{snap.cost_cny:.5f}/闸 {LLM_GATE}"
          f" · embedding 实发 {M._spent:,}/{EMB_GATE:,} token")

    out = ROOT / "storage" / "benchmark" / f"prompt_floor_savings_{_msafe(settings.embedding.model)}.json"
    out.write_text(json.dumps({"k": K, "queries": len(questions), "pool": len(chunks),
                               "prefetch_lines_avg": {m: round(statistics.fmean(v["lines"]), 2)
                                                      for m, v in per.items()},
                               "prefetch_chars_avg": {m: round(statistics.fmean(
                                   len(x) for x in v["blocks"]), 1) for m, v in per.items()},
                               "prefetch_tokens_avg_local": {"off": round(statistics.fmean(tok_off), 1),
                                                             "score": round(statistics.fmean(tok_hi), 1)},
                               "saved_fraction_chars": round(saved_chars, 4),
                               "saved_fraction_tokens_local": round(saved_tok, 4),
                               "real_prompt_tokens": cm_costs,
                               "spent": {"llm_cny": snap.cost_cny, "emb_tokens": M._spent}},
                              ensure_ascii=False, indent=1), encoding="utf-8")
    await _quiet_drop(kb)
    print(f"✅ C23⑤ 落盘 {out.name}（集合 {COLL} 已 drop）")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
