"""模型目录：把已配置端点的 `GET {base_url}/models` 摊平成前端可选列表。

只回名字——前端能改的也只有 `model` 一个字段（SSRF 说明见 `codeharness.team._make_llm`）。
端点侧不支持 /models 或网络不通时不报错：返回 `ok=false` + 当前模型，前端据此把
模型位退化成只读文本，不画一个点开是空的箭头。"""
import time

from fastapi import APIRouter, Depends, Request

from server.auth import current_user

router = APIRouter(prefix="/api/models", tags=["models"])

_TTL_SEC = 30.0
_cache: dict = {"at": 0.0, "body": None}


@router.get("")
async def list_models(request: Request, user: str = Depends(current_user)):
    d = getattr(request.app.state, "llm_defaults", None) or {}
    current = str(d.get("model") or "")
    now = time.monotonic()
    if _cache["body"] is not None and now - _cache["at"] < _TTL_SEC:
        return {**_cache["body"], "current": current}
    body = {"models": [current] if current else [], "ok": False, "error": "端点未配置"}
    if d.get("base_url") and d.get("api_key"):
        try:
            import httpx
            url = d["base_url"].rstrip("/") + "/models"
            async with httpx.AsyncClient(timeout=5.0) as c:
                r = await c.get(url, headers={"Authorization": f"Bearer {d['api_key']}"})
                r.raise_for_status()
                ids = sorted({str(m["id"]) for m in (r.json().get("data") or []) if m.get("id")})
            # ollama 给的是 `名字:latest`，配置里写的是不带后缀的那个：不归一，
            # 菜单的勾选永远落不到当前模型上（选中它反而变成一次真覆盖）。
            if current:
                ids = [current if i == f"{current}:latest" else i for i in ids]
            if ids:
                body = {"models": ids, "ok": True, "error": ""}
        except Exception as e:                       # 端点不配合不是故障：退化成只读
            body["error"] = f"{type(e).__name__}: {e}"
    _cache.update(at=now, body=body)
    return {**body, "current": current}
