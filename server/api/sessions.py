"""Session REST + SSE（端点集=client.ts 全集）。N1：全部路由过 `current_user` 依赖——
auth 关恒 "default"（现状行为），开时校验 Bearer 并按 user 隔离（越权一律 404 不泄露存在性）。"""
import asyncio
from pathlib import Path
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, field_validator
from server.auth import current_user
from server.sessions import SessionStatus

router = APIRouter(prefix="/api/sessions", tags=["sessions"])


class CreateSessionReq(BaseModel):
    idea: str = Field(min_length=1)
    project_name: str = ""
    n_round: int = 5
    paradigm: str = "classic"       # classic|dynamic（S9.1 对照）|react（9.2 策略曲线）；其余值 422
    sop: str = ""                   # N7 模板名（9.3 扩展线入口）；非空时 create 即校验，别让拼错拖到 start 才炸
    llm: dict = Field(default_factory=dict)

    @field_validator("paradigm")
    @classmethod
    def _paradigm(cls, v: str) -> str:
        if v not in ("classic", "dynamic", "react"):
            raise ValueError("paradigm 只能是 classic、dynamic 或 react")
        return v

    @field_validator("sop")
    @classmethod
    def _sop_exists(cls, v: str) -> str:
        if v:
            from codeharness.sop.builder import get_template
            try:
                get_template(v)
            except KeyError as e:
                raise ValueError(str(e))
        return v

    @field_validator("project_name")
    @classmethod
    def _single_dir_name(cls, v: str) -> str:
        # 名字直接拼成 workspace/{name}：会话目录、产物仓与前端文件树的根都是它，带分隔就能越界
        if v and Path(v).name != v:
            raise ValueError("project_name 不能包含路径分隔")
        return v


class ChatReq(BaseModel):
    content: str = Field(min_length=1)
    send_to: str = ""          # 空=TeamLeader；角色名=直聊（InputCard 的目标选择）


class HumanInputReq(BaseModel):
    content: str = Field(min_length=1)


def _get(request: Request, name: str):
    return getattr(request.app.state, name)


@router.get("")
def list_sessions(request: Request, user: str = Depends(current_user)):
    from codeharness.configs.settings import settings
    items = _get(request, "store").list()
    if settings.platform.auth_enabled:
        items = [s for s in items if s.user_id == user]
    return [s.model_dump() for s in items]


@router.post("")
async def create_session(req: CreateSessionReq, request: Request, user: str = Depends(current_user)):
    # N5 保留的一半：请求数限流在 HTTP 入口判（429），不进图、不在内核判；
    # quota 未装配（进程内默认）=直通。⚠ 这是限流不是金额预算（§零）。N1：配额按 user 分桶。
    q = getattr(request.app.state, "quota", None)
    if q is not None:
        from codeharness.configs.settings import settings
        if not q.allow(f"create:{user}", settings.platform.create_per_min,
                       settings.platform.rate_window_sec):
            raise HTTPException(429, "建会话过于频繁，稍后再试")
    name = req.project_name.strip()
    # N1：同 project_name 跨用户 = 产物目录冲突（workspace/{name} 平铺），create 即 409——
    # 别让两家的文件树互相覆盖才在浏览器里现形。同用户重名沿用现状（多场同名合法）。
    from codeharness.configs.settings import settings
    if name and settings.platform.auth_enabled:
        for s in _get(request, "store").list():
            if s.project_name == name and s.user_id != user:
                raise HTTPException(409, f"项目名 {name!r} 已被其他用户占用")
    s = _get(request, "store").create(idea=req.idea, n_round=req.n_round,
                                      project_name=name, llm_override=req.llm,
                                      paradigm=req.paradigm, sop=req.sop, user_id=user)
    _get(request, "bus").publish(s.id, kind="status", value={"status": s.status, "message": "created"})
    return s.model_dump()


@router.get("/{sid}")
def get_session(sid: str, request: Request, user: str = Depends(current_user)):
    s = _owned(request, sid, user)
    return s.model_dump()


def _owned(request: Request, sid: str, user: str):
    """N1：取会话 + 归属校验（越权 404）。auth 关时 user 恒 "default"、不比对。"""
    from codeharness.configs.settings import settings
    s = _get(request, "store").get(sid)
    if not s:
        raise HTTPException(404, f"session {sid} not found")
    if settings.platform.auth_enabled and s.user_id != user:
        raise HTTPException(404, f"session {sid} not found")
    return s


@router.get("/{sid}/graph")
def session_graph(sid: str, request: Request, user: str = Depends(current_user)):
    """N6 编排可视化：节点与订阅边取自真实装配对象，不是手绘示意图。

    ⚠ LangGraph 静态图只有 `__start__→router→__end__` 两条边——角色路由是运行期 Send，
    图对象自己照不出来。这条架构的真实接线在每个角色的 watch 订阅表里（黑板-路由-订阅）。
    三种范式各按**自己的装配**现采（9.3 收口：此前 dynamic/sop 会话画的也是 classic 表——
    「画的就是跑的」只对默认线成立，扩展线在这里是不可见的）：
      sop 非空 → N7 模板装配（ext_api.register_template 的扩展线从这里可见）；
      dynamic  → dynamic_assembly（RoleZero 三角色）；否则 → classic 兜底。
    门禁 s8 t4 钉「节点集与 watch 边必须出自真装配」。"""
    s = _owned(request, sid, user)
    from codeharness.team import _default_agents, _make_llm
    rtable: dict = {}                     # 装配路由表（RoleZero 无 watch，动态/模板线从这里兜底）
    if getattr(s, "sop", ""):
        from codeharness.sop.builder import get_template
        tpl = get_template(s.sop)
        agents = tpl.build_agents(_make_llm())
        rtable = {k: list(v) for k, v in tpl.edges.items()}
    elif getattr(s, "paradigm", "classic") == "dynamic":
        from codeharness.team import dynamic_assembly
        agents, dyn = dynamic_assembly(_make_llm())
        rtable = {k: list(v) for k, v in dyn.items()}
    else:
        agents = _default_agents()
    lines = ["flowchart LR", "  start_([需求])", '  router{{"route · 黑板"}}', "  stop_([结束])",
             "  start_ --> router", "  router --> stop_"]
    for i, (name, ag) in enumerate(agents.items()):
        lines.append(f'  a{i}["{name}"]')
        tags = sorted(getattr(ag, "watch", None)
                      or (t for t, roles in rtable.items() if name in roles))
        for tag in tags:
            lines.append(f"  router -.->|{tag}| a{i}")
    return {"mermaid": "\n".join(lines)}


@router.post("/{sid}/start")
async def start_session(sid: str, request: Request, user: str = Depends(current_user)):
    store, runner = _get(request, "store"), _get(request, "runner")
    s = _owned(request, sid, user)
    # is_running 是 worker 本地视角；多进程部署下「已在跑」的真源是 store 状态
    # （启动残态由 heal_running 自愈，running/awaiting_human 必有人在跑）。
    if runner.is_running(sid) or s.status in (SessionStatus.running, SessionStatus.awaiting_human):
        raise HTTPException(409, "session already running")
    if not (_get(request, "llm_defaults") or {}).get("api_key"):
        raise HTTPException(400, f"LLM 未配置：{_get(request, 'llm_problem') or '缺少 api_key'}")
    q = getattr(request.app.state, "quota", None)
    if q is not None:
        # 并发会话数配额（多 worker 共享计数，进程内计数器失效的老问题）
        from codeharness.configs.settings import settings
        running = sum(1 for x in store.list() if str(x.status.value) == "running")
        if running >= settings.platform.max_concurrent:
            raise HTTPException(429, f"并发会话已达上限 {settings.platform.max_concurrent}")
    runner.start(s)
    return {"ok": True}


@router.post("/{sid}/stop")
async def stop_session(sid: str, request: Request, user: str = Depends(current_user)):
    _owned(request, sid, user)
    stopped = await _get(request, "runner").stop(sid)
    return {"ok": True, "stopped": stopped}


@router.post("/{sid}/chat")
async def chat(sid: str, req: ChatReq, request: Request, user: str = Depends(current_user)):
    _owned(request, sid, user)
    runner = _get(request, "runner")
    if not runner.is_running(sid):
        raise HTTPException(409, "session is not running")
    if not runner.enqueue_chat(sid, req.content, req.send_to or ""):
        raise HTTPException(409, "session chat queue unavailable")
    return {"ok": True}


@router.post("/{sid}/human-input")
async def human_input(sid: str, req: HumanInputReq, request: Request, user: str = Depends(current_user)):
    _owned(request, sid, user)
    return {"ok": _get(request, "runner").answer_human(sid, req.content)}


@router.get("/{sid}/events")
async def events(sid: str, request: Request, after: int = 0, user: str = Depends(current_user)):
    bus, store = _get(request, "bus"), _get(request, "store")
    _owned(request, sid, user)

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


@router.get("/{sid}/trace")
def session_trace(sid: str, request: Request, user: str = Depends(current_user)):
    """N4 数据层：每笔 LLM 调用的节点/token 增量/时刻（ch:trace:{sid}）。
    trace 未装配（进程内默认）回空表——前端 N4 面板是 S8 剩余项，先攒数据。"""
    _owned(request, sid, user)
    tr = _get(request, "runner").trace
    return {"spans": tr.spans(sid) if tr else []}


@router.get("/{sid}/events/history")
def events_history(sid: str, request: Request, after: int = 0, user: str = Depends(current_user)):
    """有界 JSON 回放：`/events` 是给浏览器 EventSource 的无界活流，**任何要读完再走的
    消费方都必须用这条**——冒烟脚本挂死两场的根因就是拿普通 GET 读无限流（TestClient 的
    transport 会把应用跑到底才返回，`while True` 永不返回）。事后审计、S9 采集、断线重连
    补历史，证据都从这里拿。"""
    bus, store = _get(request, "bus"), _get(request, "store")
    _owned(request, sid, user)
    return {"events": [e.model_dump() for e in bus.history(sid, after)]}
