"""本地精排桩（C159 的开发工装）：ollama bge-m3 余弦打分，喂 `LongTermMemory.rerank_scored`。

为什么存在：云端精排（qwen3.7-text-rerank）按 query×候选 计费且配额有限（10-03 现证会跑干），
而开发要的是「真 HTTP 往返 + 真 `rerank_scored` 解析」的那条链，不是某个特定模型的分数——
铁律 22 的本地桩纪律照搬到精排腿上：起一个 127.0.0.1 的 Xinference 形状桩，打分用本机 ollama
的 bge-m3（`/api/embed` 批量，余弦），零额度、零外网。

跑法（另一条命令后台起）：
  PYTHONIOENCODING=utf-8 F:/anaconda/python.exe -B tests/local_rerank_stub.py [--port 9998]

⚠ 刻度声明：本桩回的是 **bi-encoder 余弦**（bge-m3），不是 cross-encoder 的 relevance——
  排序质量天然低于真精排，**只用于开发/机制验证，不许当任何验收口径**（铁律 28 的开发豁免
  由用户 10-03 令「开发的时候使用 ollama 的」授权，验收仍指 `.env` 云端）。C36 那条「换端点
  必重量」在这里同样成立：本地桩的分数线与云端精排的分数线不可比。
"""
import argparse
import math

from fastapi import FastAPI
from pydantic import BaseModel

from codeharness.logs import logger

OLLAMA = "http://127.0.0.1:11434/api/embed"
EMBED_MODEL = "bge-m3"

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


@app.post("/rerank")
@app.post("/reranks")
async def rerank(req: RerankReq):
    import httpx
    texts = [req.query] + list(req.documents)
    async with httpx.AsyncClient(timeout=60) as c:
        r = await c.post(OLLAMA, json={"model": EMBED_MODEL, "input": texts})
        r.raise_for_status()
        embs = r.json()["embeddings"]
    q, docs = embs[0], embs[1:]
    scored = sorted(({"index": i, "relevance_score": round(_cos(q, d), 6)}
                     for i, d in enumerate(docs)),
                    key=lambda x: x["relevance_score"], reverse=True)
    top_n = req.top_n or len(scored)
    return {"results": scored[:top_n], "count": len(scored)}


if __name__ == "__main__":
    import uvicorn
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=9998)
    args = ap.parse_args()
    logger.warning(f"本地精排桩起在 :{args.port}（bge-m3 余弦，开发用，不当验收口径）")
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")
