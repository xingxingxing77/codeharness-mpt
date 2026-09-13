"""Session REST + SSE（端点集=client.ts 全集）。"""
import asyncio
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from server.sessions import SessionStatus

router = APIRouter(prefix="/api/sessions", tags=["sessions"])


class CreateSessionReq(BaseModel):
    idea: str = Field(min_length=1)
    project_name: str = ""
    investment: float = 3.0
    n_round: int = 5
    llm: dict = Field(default_factory=dict)


class ChatReq(BaseModel):
    content: str = Field(min_length=1)
    send_to: str = ""          # 空=TeamLeader；角色名=直聊（InputCard 的目标选择）


class HumanInputReq(BaseModel):
    content: str = Field(min_length=1)


def _get(request: Request, name: str):
    return getattr(request.app.state, name)


@router.get("")
def list_sessions(request: Request):
    return [s.model_dump() for s in _get(request, "store").list()]


@router.post("")
async def create_session(req: CreateSessionReq, request: Request):
    s = _get(request, "store").create(idea=req.idea, investment=req.investment, n_round=req.n_round,
                                      project_name=req.project_name.strip(), llm_override=req.llm)
    _get(request, "bus").publish(s.id, kind="status", value={"status": s.status, "message": "created"})
    return s.model_dump()


@router.get("/{sid}")
def get_session(sid: str, request: Request):
    s = _get(request, "store").get(sid)
    if not s:
        raise HTTPException(404, f"session {sid} not found")
    return s.model_dump()


@router.post("/{sid}/start")
async def start_session(sid: str, request: Request):
    store, runner = _get(request, "store"), _get(request, "runner")
    s = store.get(sid)
    if not s:
        raise HTTPException(404, f"session {sid} not found")
    if runner.is_running(sid):
        raise HTTPException(409, "session already running")
    if not (_get(request, "llm_defaults") or {}).get("api_key"):
        raise HTTPException(400, f"LLM 未配置：{_get(request, 'llm_problem') or '缺少 api_key'}")
    runner.start(s)
    return {"ok": True}


@router.post("/{sid}/stop")
async def stop_session(sid: str, request: Request):
    if not _get(request, "store").get(sid):
        raise HTTPException(404, f"session {sid} not found")
    stopped = await _get(request, "runner").stop(sid)
    return {"ok": True, "stopped": stopped}


@router.post("/{sid}/chat")
async def chat(sid: str, req: ChatReq, request: Request):
    runner = _get(request, "runner")
    if not runner.is_running(sid):
        raise HTTPException(409, "session is not running")
    if not runner.enqueue_chat(sid, req.content, req.send_to or ""):
        raise HTTPException(409, "session chat queue unavailable")
    return {"ok": True}


@router.post("/{sid}/human-input")
async def human_input(sid: str, req: HumanInputReq, request: Request):
    return {"ok": _get(request, "runner").answer_human(sid, req.content)}


@router.get("/{sid}/events")
async def events(sid: str, request: Request, after: int = 0):
    bus, store = _get(request, "bus"), _get(request, "store")
    if not store.get(sid):
        raise HTTPException(404, f"session {sid} not found")

    async def gen():
        q = bus.subscribe(sid)
        try:
            for ev in bus.history(sid, after):
                yield f"data: {ev.model_dump_json()}\n\n"
            while True:
                try:
                    ev = await asyncio.wait_for(q.get(), timeout=15)
                    yield f"data: {ev.model_dump_json()}\n\n"
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
        except asyncio.CancelledError:            # 前端断开
            pass
        finally:
            bus.unsubscribe(sid, q)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no",
                                      "Connection": "keep-alive"})
