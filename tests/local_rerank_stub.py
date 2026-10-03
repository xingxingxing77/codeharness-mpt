"""本地精排服务（C159/C158 的开发工装）：两种后端，喂 `LongTermMemory.rerank_scored`。

  · `--backend cos`（默认）：ollama bge-m3 余弦打分——**bi-encoder，零模型下载**。C158 实测它在
    记忆语料上没有判别力（中位差 -0.0095），只用于机制/管线验证，不许当质量口径。
  · `--backend ce`：**真 cross-encoder**（`BAAI/bge-reranker-v2-m3`，sentence-transformers，
    HF_ENDPOINT 走 hf-mirror 下载）——本机零额度跑真精排，C158 翻案的实施件就是它。

两种后端都讲 Xinference/兼容口形状（`{model,query,documents,top_n}` → `results[{index,relevance_score}]`），
真 HTTP 往返 + 真 `rerank_scored` 解析（铁律 22 的本地桩纪律）。带 (query,documents) 键的打分缓存：
分布轮与复核轮的窗口相同，命中即零计算（RRF 窗口抖动的少数 miss 才真算）。

⚠ 刻度声明：cos 后端的分数线与 cross-encoder（本机 CE 或云端 qwen3.7）**不可比**（C36「换端点必重量」）；
  `memory_min_score=0.0` 纯重排不需要标定，这正是翻案能脱离云端额度成立的原因。验收口径仍指 `.env`
  云端——本机 CE 是用户 10-03 令「精排额度没了，开发用 ollama 的」授权下的开发/单机部署件。

跑法（另一条命令后台起）：
  HF_ENDPOINT=https://hf-mirror.com PYTHONIOENCODING=utf-8 F:/anaconda/python.exe -B \
    tests/local_rerank_stub.py --port 9998 --backend ce
"""
import argparse
import math

from fastapi import FastAPI
from pydantic import BaseModel

from codeharness.logs import logger

OLLAMA = "http://127.0.0.1:11434/api/embed"
EMBED_MODEL = "bge-m3"
_CE = None            # 惰性加载：进程起来时不占显存/内存，第一发才拉模型
_cache: dict = {}     # (query, documents) → [(index, score)]，上界 8000 条足够一轮标定

app = FastAPI()


class RerankReq(BaseModel):
    model: str = ""
    query: str
    documents: list[str] = []
    top_n: int | None = None


def _cos(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a)) or 1.0
    nb = math.sqrt(sum(y * y for y in b)) or 1.0
    return dot / (na * nb)


def _score(query: str, documents: list[str]) -> list[dict]:
    key = (query, tuple(documents))
    if key in _cache:
        return _cache[key]
    global _CE
    if BACKEND == "ce":
        scores = _CE.predict([(query, d) for d in documents])
        out = sorted(({"index": i, "relevance_score": round(float(s), 6)}
                      for i, s in enumerate(scores)),
                     key=lambda x: x["relevance_score"], reverse=True)
    else:
        import httpx
        texts = [query] + list(documents)
        r = httpx.post(OLLAMA, json={"model": EMBED_MODEL, "input": texts}, timeout=60)
        r.raise_for_status()
        embs = r.json()["embeddings"]
        q, ds = embs[0], embs[1:]
        out = sorted(({"index": i, "relevance_score": round(_cos(q, d), 6)}
                      for i, d in enumerate(ds)),
                     key=lambda x: x["relevance_score"], reverse=True)
    if len(_cache) < 8000:
        _cache[key] = out
    return out


@app.post("/rerank")
@app.post("/reranks")
async def rerank(req: RerankReq):
    scored = _score(req.query, req.documents)
    top_n = req.top_n or len(scored)
    return {"results": scored[:top_n], "count": len(scored)}


if __name__ == "__main__":
    import uvicorn
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=9998)
    ap.add_argument("--backend", choices=["cos", "ce"], default="cos")
    args = ap.parse_args()
    BACKEND = args.backend
    if BACKEND == "ce":
        from sentence_transformers import CrossEncoder
        logger.warning("本地精排（cross-encoder bge-reranker-v2-m3）加载模型中，首发可用即绪")
        _CE = CrossEncoder("BAAI/bge-reranker-v2-m3")
    logger.warning(f"本地精排服务起在 :{args.port} backend={BACKEND}（开发/单机部署用，验收口径仍指云端）")
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")
