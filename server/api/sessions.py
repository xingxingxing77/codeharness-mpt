"""Session REST + SSE（端点集=client.ts 全集）。N1：全部路由过 `current_user` 依赖——
auth 关恒 "default"（现状行为），开时校验 Bearer 并按 user 隔离（越权一律 404 不泄露存在性）。"""
import asyncio
from pathlib import Path
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query, Request
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
    permission: str = "readonly"    # 工具审批的免审档（判定表 codeharness/tools/_approval.py）；其余值 422
    goal: str = ""                 # B5：建会话时用户填的目标（口径：只有用户能点完成）

    @field_validator("permission")
    @classmethod
    def _permission(cls, v: str) -> str:
        from codeharness.tools._approval import TIERS
        if v not in TIERS:
            raise ValueError(f"permission 只能是 {'、'.join(TIERS)}")
        return v

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

    @field_validator("llm")
    @classmethod
    def _only_model(cls, v: dict) -> dict:
        """只留 `model`。其余键（尤其 base_url/api_key）存进会话记录会看着像生效，
        而 `team._make_llm` 明确不吃它们——在入口就丢掉，别让记录说谎。"""
        m = str(v.get("model") or "").strip()
        return {"model": m} if m else {}


class ChatReq(BaseModel):
    content: str = Field(min_length=1)
    send_to: str = ""          # 空=TeamLeader；角色名=直聊（InputCard 的目标选择）


class HumanInputReq(BaseModel):
    content: str = Field(min_length=1)


class RoleReq(BaseModel):
    """C1-③ 招人档案。这里只做**形状**校验（少字段就 422，不进内核），
    语义校验（名字合法、工具已注册、档位够不够）在 `codeharness.team.check_role_def` 那一个出口里做。"""
    name: str = Field(min_length=1, max_length=32)
    profile: str = Field(min_length=1)
    goal: str = Field(min_length=1)
    constraints: str = ""
    tools: list[str] = Field(default_factory=list)


class PatchSessionReq(BaseModel):
    """四个都可选：不传的字段保持原值，所以取消归档要显式发 archived=false。"""
    idea: Optional[str] = Field(default=None, min_length=1)
    archived: Optional[bool] = None
    pinned: Optional[bool] = None
    permission: Optional[str] = None        # 会话中途切免审档（composer 那枚 chip）

    @field_validator("permission")
    @classmethod
    def _permission(cls, v: Optional[str]) -> Optional[str]:
        from codeharness.tools._approval import TIERS
        if v is not None and v not in TIERS:
            raise ValueError(f"permission 只能是 {'、'.join(TIERS)}")
        return v


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
                                      paradigm=req.paradigm, sop=req.sop, user_id=user,
                                      permission=req.permission, goal=req.goal.strip())
    _get(request, "bus").publish(s.id, kind="status", value={"status": s.status, "message": "created"})
    return s.model_dump()


@router.get("/{sid}")
def get_session(sid: str, request: Request, user: str = Depends(current_user)):
    s = _owned(request, sid, user)
    return s.model_dump()


@router.patch("/{sid}")
def patch_session(sid: str, req: PatchSessionReq, request: Request, user: str = Depends(current_user)):
    """侧栏的重命名/归档/置顶。只认这三个字段，其余一律不接受。"""
    s = _owned(request, sid, user)
    fields = req.model_dump(exclude_unset=True)   # 没传的字段不动：false 与「未传」必须可区分
    if not fields:
        return s.model_dump()
    return _get(request, "store").update(s.id, **fields).model_dump()


class GoalReq(BaseModel):
    objective: str = Field(min_length=1, max_length=200)

    @field_validator("objective")
    @classmethod
    def _not_blank(cls, v: str) -> str:
        # 只 `min_length=1` 挡不住两个空格：那样会存进一条「有目标但看不见」的目标。
        v = v.strip()
        if not v:
            raise ValueError("目标不能是空白")
        return v


def _goal_changed(request: Request, s, operation: str) -> dict:
    """B5：目标的每次变更进事件流（口径里「变更进新 `kind`」那一格）。
    事件不是真相源——`Session.goal` / `goal_done_at` 才是；这条只让活流与回放看得见「目标变了」，
    形状照参照系 `goal/change`（`operation` ∈ create|edit|complete|clear，
    `packages/goal/goal/src/domain.ts:14-21`；它没有的 paused/blocked 我们也没有生产者）。"""
    _get(request, "bus").publish(s.id, kind="goal", name=operation,
                                value={"objective": s.goal, "done_at": s.goal_done_at})
    return s.model_dump()


@router.post("/{sid}/goal")
def set_goal(sid: str, req: GoalReq, request: Request, user: str = Depends(current_user)):
    """设/改这一场要达成的目标（单条字符串）。改目标会把「已完成」清回去——
    完成是针对**那条**目标点的，换了目标还挂着已完成就是假状态。"""
    s = _owned(request, sid, user)
    updated = _get(request, "store").update(s.id, goal=req.objective, goal_done_at="")
    return _goal_changed(request, updated, "create" if not s.goal else "edit")


@router.post("/{sid}/goal/complete")
def complete_goal(sid: str, request: Request, user: str = Depends(current_user)):
    """完成**只有用户点确认这一条路**（口径 2026-09-20）：内核、模型、runner 都没有写
    `goal_done_at` 的出口，所以这里也不需要防谁。重复点幂等——不刷新时间戳，也不重发事件。"""
    s = _owned(request, sid, user)
    if not s.goal:
        raise HTTPException(422, "这场没有目标可确认完成")
    if s.goal_done_at:
        return s.model_dump()
    from server.sessions import _now
    updated = _get(request, "store").update(s.id, goal_done_at=_now())
    return _goal_changed(request, updated, "complete")


@router.post("/{sid}/goal/clear")
def clear_goal(sid: str, request: Request, user: str = Depends(current_user)):
    s = _owned(request, sid, user)
    updated = _get(request, "store").update(s.id, goal="", goal_done_at="")
    return _goal_changed(request, updated, "clear")


@router.delete("/{sid}")
def delete_session(sid: str, request: Request, user: str = Depends(current_user)):
    s = _owned(request, sid, user)
    if s.status in (SessionStatus.running, SessionStatus.stopping, SessionStatus.awaiting_human):
        # runner 还在往这条记录合流 cost/status，删了会留下往空会话写事件的怪状态
        raise HTTPException(409, "会话进行中，请先停止再删除")
    _get(request, "store").delete(s.id)
    return {"ok": True, "deleted": s.id}


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
    ⚠ react 会话走的也是 classic 兜底，这是**对的**不是漏判：`team.react_assembly` 只在经典
    五角色上翻 `react_mode`（换执行策略，路由表仍是 SOP），队形没变→图没变。别给 react 加分支。
    两个分支的 `_make_llm()` 不传会话 override 同样是对的：这条端点只现采装配画图，
    构造 gateway 不发模型请求（`model` 覆盖只在真跑图时由 runner 传，画图不需要知道选了哪个模型）。
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
    # （启动残态由 heal_running 自愈，running 必有人在跑）。
    if runner.is_running(sid) or s.status == SessionStatus.running:
        raise HTTPException(409, "session already running")
    # awaiting_human 自 09-21 起是**可信的驻留态**（原先被 runner 收尾那句覆写成 finished，
    # 于是这里放行重开、把 checkpointer 里等人回答的那个断点冲掉）。要往前走只有两条：
    # 回答（human-input）或放弃（stop → stopped），都不是「再 start 一次」。
    if s.status == SessionStatus.awaiting_human:
        raise HTTPException(409, "会话停在待人工处：请先回答，或点停止放弃这场")
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
    s = _owned(request, sid, user)
    target = req.send_to or ""
    # 目标必须是这场装配里的真节点。route 对不认识的目标是「不投递、不报错」
    # （team_graph.py:127 无 else），所以在入口挡住并回列出可选项。
    if target and s.roles and target not in s.roles:
        raise HTTPException(422, f"目标角色 {target!r} 不在这场装配里（可选：{'、'.join(s.roles)}）")
    runner = _get(request, "runner")
    if not runner.is_running(sid):
        raise HTTPException(409, "session is not running")
    if not runner.enqueue_chat(sid, req.content, target):
        raise HTTPException(409, "session chat queue unavailable")
    return {"ok": True}


@router.get("/{sid}/queue")
def get_queue(sid: str, request: Request, user: str = Depends(current_user)):
    """B6：看这场此刻排着哪些**还没被取走**的插话（route 每轮取一次，取走就不再出现在这里）。
    队列只在有活任务时存在（`_run` 建、`_forget` 收），所以停着的会话回空列表——
    这不是「队列被清空」的假话，是确实没有下一刻会取走它的图。"""
    s = _owned(request, sid, user)
    chat = _get(request, "runner").chats.get(s.id)
    return {"items": chat.pending() if chat else []}


@router.delete("/{sid}/queue/{qid}")
def drop_queued(sid: str, qid: str, request: Request, user: str = Depends(current_user)):
    """撤回一条（**不重排**：只把那个位置剔掉，后面的次序原样）。
    不在队列里就 404——已经被 route 取走的也 404，那种情况该点是停止或再插一条，
    而不是假装还撤得回来。"""
    s = _owned(request, sid, user)
    chat = _get(request, "runner").chats.get(s.id)
    if not chat or not chat.remove(qid):
        raise HTTPException(404, f"队列里没有 {qid}（可能已被投递，或这场没有在跑）")
    return {"ok": True, "removed": qid}


@router.post("/{sid}/roles")
def hire_role(sid: str, req: RoleReq, request: Request, user: str = Depends(current_user)):
    """C1-③ 现场招人。**生效点是下一次起跑/续跑**（不是运行中热插）：
    LangGraph 的节点集在 compile 时就固定，热插只能重建图 + 换线程，代价大于「等这一跑完」。"""
    s = _owned(request, sid, user)
    if s.sop or s.paradigm != "dynamic":
        # 只有动态线有「队长点名」这条通路。classic/react 线里没有任何边或命令会指向新节点，
        # 招进来就是个醒不过来的死成员——那种节点宁可直接拒绝，也不静默收下。
        raise HTTPException(422, f"只有 paradigm=dynamic 的会话能招人（当前 {s.paradigm!r}"
                                f"{'，且走 sop 模板线' if s.sop else ''}）")
    if s.status == SessionStatus.running:
        raise HTTPException(409, "会话在跑：等它收口或先点停止，再招人（生效点是下一次装配）")
    from codeharness.team import check_role_def, required_tier
    from codeharness.tools._approval import PERMISSION_DEFAULT, TIER_RANK
    try:
        defn = check_role_def(req.model_dump(),
                              taken=[*s.roles, *(str(d.get("name", "")) for d in s.role_defs)])
    except ValueError as e:
        raise HTTPException(422, str(e))
    need = required_tier(defn["tools"])
    have = s.permission if s.permission in TIER_RANK else PERMISSION_DEFAULT
    if TIER_RANK[need] > TIER_RANK[have]:
        # 声明工具走的就是执行期那一条审批门（同一张表 `_approval.TOOL_TIER`），这里不开后门
        raise HTTPException(422, f"这些工具需要 {need} 档，会话现在是 {have} 档："
                                f"{'、'.join(defn['tools'])}。要招就先在 composer 上把档位切到 {need}")
    updated = _get(request, "store").update(sid, role_defs=[*s.role_defs, defn])
    _get(request, "bus").publish(s.id, kind="status",
                                 value={"status": updated.status, "message": f"已招募 {defn['name']}（下一次起跑生效）"})
    return {"ok": True, "role": defn, "takes_effect": "next_start", "roles": updated.roles}


@router.get("/{sid}/roles/draft")
async def draft_role(sid: str, request: Request, user: str = Depends(current_user)):
    """让模型现场写一份档案（决策 #4「profile 现场写」）。**只回草案、不落库**——
    落库必须走 `POST /{sid}/roles`，那里才有重名、工具注册表与档位三道闸。"""
    s = _owned(request, sid, user)
    from codeharness.team import _make_llm, draft_role_profile
    llm = _make_llm(None, getattr(s, "llm_override", None))
    try:
        draft = await draft_role_profile(llm, s.idea, teammates=s.roles)
    except ValueError as e:
        raise HTTPException(502, f"草案不合规矩（模型声明了注册表里没有的工具）：{e}")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(503, f"模型没答上：{type(e).__name__}: {e}")
    return draft


@router.post("/{sid}/human-input")
async def human_input(sid: str, req: HumanInputReq, request: Request, user: str = Depends(current_user)):
    _owned(request, sid, user)
    if not _get(request, "runner").answer_human(sid, req.content):
        # B2：拒收必须是 409。原先 200 + {"ok": false} 到了前端是恒真的信封
        # （store 把整个响应体当布尔用），用户以为回答已送达、卡片就地消失。
        raise HTTPException(409, "会话正在运行或已在恢复中，人工回答未接收")
    return {"ok": True}


@router.get("/{sid}/events")
async def events(sid: str, request: Request, after: str = "", user: str = Depends(current_user)):
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


MAX_STATE_BYTES = 4 * 1024 * 1024     # 单份超步状态的响应上限（实测真会话最大一份 62 KB，留足余量）
DEFAULT_CHECKPOINT_PAGE = 80


def _cfg_at(config: dict, checkpoint_id: str = "") -> dict:
    """把 checkpoint_id 打进 config 的 configurable——回放面全部走 langgraph 的这一个入口。
    ⚠ `checkpoint_id` 是**不透明串**（uuid7 形状），不能当数字排序或比较大小：
    真正的顺序由 saver 按插入序给，接口一律回「新→旧」。"""
    c = dict(config)
    conf = dict(c.get("configurable") or {})
    if checkpoint_id:
        conf["checkpoint_id"] = checkpoint_id
    c["configurable"] = conf
    return c


@router.get("/{sid}/checkpoints")
async def session_checkpoints(sid: str, request: Request, before: str = "",
                              limit: int = Query(DEFAULT_CHECKPOINT_PAGE, ge=1, le=500),
                              user: str = Depends(current_user)):
    """C11 时间旅行·列表：**按 superstep 的历史快照**，新→旧的窗口。

    采集是免费的：AsyncSqliteSaver 每个 superstep 本来就落一份 checkpoint（实测两节点小图
    跑 4 场 ainvoke 出 12 条，`metadata.step` 单调、`source∈{input,loop}`、`next` 给下一步是谁），
    所以这条只读不写、不动 runner 的落盘路径。

    为什么只回摘要不回全量：真数据层实测最单个 thread 有 **711 条**、全库 3513 条 / 32.4 MB，
    整份 state 是黑板 messages 的全量（单份最大 62 KB，且随会话长度线性涨）。列表页把
    每份都反序列化出来就等于把那个库读进内存一遍——`limit+1` 探 `has_more`，
    正文留给 `/{sid}/checkpoints/{checkpoint_id}` 按需取。翻页口径与 B2 的
    `/events/history` 一致：`before`=更早一侧的**开区间**上界（传上一页末条的 checkpoint_id）。
    """
    _owned(request, sid, user)
    packed = await _get(request, "runner")._ensure_graph(sid)
    if not packed:
        # 图重建不出来（会话没装配过 / LLM 未配）。回空但不装死：reason 让界面说清为什么是空的
        return {"checkpoints": [], "has_more": False, "next_before": "", "reason": "无法重建图，读不到超步"}
    graph, config = packed
    before_cfg = _cfg_at(config, before) if before else None   # 只当**上界**用，走 before 关键字（开区间）
    return await _get(request, "runner")._ckpt_page(graph, config, limit, before_cfg)


@router.get("/{sid}/checkpoints/{checkpoint_id}")
async def session_checkpoint_detail(sid: str, checkpoint_id: str, request: Request,
                                    user: str = Depends(current_user)):
    """C11 时间旅行·详情：某一份超步的完整 state（点节点看当时黑板）。

    超过 `MAX_STATE_BYTES` 直接 413 而不是硬塞——前端已按状态码分流（F-E），
    这条的形状与 `/workspace/file` 的预览上限是同一族判据。"""
    import json

    from fastapi.responses import Response

    _owned(request, sid, user)
    packed = await _get(request, "runner")._ensure_graph(sid)
    if not packed:
        raise HTTPException(409, "无法重建图，读不到超步")
    graph, config = packed
    snap = await graph.aget_state(_cfg_at(config, checkpoint_id))
    if getattr(snap, "created_at", None) is None and not (snap.values or {}):
        raise HTTPException(404, "该会话没有这个超步")
    md = snap.metadata or {}
    body = {"checkpoint_id": checkpoint_id, "step": md.get("step"), "source": md.get("source"),
            "ts": str(snap.created_at or ""), "next": list(snap.next or ()),
            "state": snap.values}
    try:
        s = json.dumps(body, ensure_ascii=False, default=str)
    except (TypeError, ValueError) as exc:      # 状态里混进不可序列化对象时别 500
        raise HTTPException(500, f"超步状态序列化失败：{type(exc).__name__}: {exc}") from exc
    if len(s.encode("utf-8")) > MAX_STATE_BYTES:
        raise HTTPException(413, f"这份超步状态超过 {MAX_STATE_BYTES // 1048576}MB 回放上限，"
                                 f"只保留在列表页的摘要里")
    return Response(content=s, media_type="application/json")


@router.get("/{sid}/events/history")
def events_history(sid: str, request: Request, after: str = "", before: str = "",
                   limit: int = Query(0, ge=0, le=1000), user: str = Depends(current_user)):
    """有界 JSON 回放：`/events` 是给浏览器 EventSource 的无界活流，**任何要读完再走的
    消费方都必须用这条**——冒烟脚本挂死两场的根因就是拿普通 GET 读无限流（TestClient 的
    transport 会把应用跑到底才返回，`while True` 永不返回）。事后审计、S9 采集、断线重连
    补历史，证据都从这里拿。

    翻页（B2）：`after`=往前追增量、`before`=往回翻（「加载更早」，**开区间**上界）、
    `limit`=一屏条数（0=不限，即老调用方语义），**始终取窗口尾部** N 条——不给 before
    就是「最新一屏」，首屏因此不必吞下保留窗口里那 5000 条。响应带 `has_more`：
    给了 limit 就多取一条用它判「前面还有没有」，省掉前端为胶囊显隐再打一次请求。
    返回**始终升序**，下一页的 before 用本页首条的 cursor。"""
    bus = _get(request, "bus")
    _owned(request, sid, user)
    evs = bus.history(sid, after, before, limit + 1 if limit else 0)
    has_more = bool(limit) and len(evs) > limit
    return {"events": [e.model_dump() for e in (evs[1:] if has_more else evs)], "has_more": has_more}
