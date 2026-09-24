"""SessionRunner：会话 ↔ LangGraph 团队图。三件事：
1) 会话任务入口装四个 ContextVar（SESSION_ID/CURRENT_PROJECT/REPORT_SINK/CHAT_SINK）——
   内核的报道与插话因此零依赖 web 层；
2) astream_events 里只翻译两件事：LLM token（Thought 打字机）与 interrupt（ask_human）；
   其余块事件全部来自内核报道槽（report.py），此处只做 sink→bus 转发；
3) 人工回答 = 同 graph 实例 + 同 thread_id 的 Command(resume)，且必须重装同一套 ContextVar。"""
import asyncio
import json
import time
import traceback
from contextlib import contextmanager
from codeharness.logs import logger
from langgraph.types import Command
from server.bridges import SESSION_ID
from server.sessions import Session, SessionStatus


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _seeded_ledger(saved: dict):
    """进程重启后的 resume 路径：账本从会话记录里上次落盘的快照起算。
    不播种的话新账本从 0 起，终态快照会把历史用量整段覆盖掉（合流断点之二）。

    ⚠ 只读 `cost_usd`/`cost_cny` 两个键。老记录里那个单一 `total_cost` 是**混币种脏数**
    （美元行与人民币行加在同一个 float 上），C12 拍板不留兼容字段，所以这里直接不读——
    读它等于把错口径继续带进新快照。"""
    from codeharness.provider.cost import CostManager
    cm = CostManager()
    cm.total_prompt_tokens = int(saved.get("total_prompt_tokens", 0) or 0)
    cm.total_completion_tokens = int(saved.get("total_completion_tokens", 0) or 0)
    cm.cost_usd = float(saved.get("cost_usd", 0) or 0)
    cm.cost_cny = float(saved.get("cost_cny", 0) or 0)
    return cm


def cost_snapshot(cm) -> dict:
    """成本快照的唯一出口（`_sync_cost` 与 `_publish_status` 两处必须同形，
    否则 SSE 那一路与 GET 那一路读到的字段集会漂）。币种分两桶，**不相加**。"""
    c = cm.get_costs()
    # 两个「无效调用」计数不住在 `Costs` 里（那是钱与 token 的计量口径，字段集有结构守卫钉着），
    # 它们是 manager 上的观测项；快照这份 dict 才是被持久化、被列表出口带出去的那一份。
    return {"cost_usd": round(c.cost_usd, 6), "cost_cny": round(c.cost_cny, 6),
            "total_prompt_tokens": c.total_prompt_tokens,
            "total_completion_tokens": c.total_completion_tokens,
            "truncated_calls": getattr(cm, "truncated_calls", 0),
            "unknown_command_calls": getattr(cm, "unknown_command_calls", 0),
            "empty_output_calls": getattr(cm, "empty_output_calls", 0)}


class SessionRunner:
    def __init__(self, store, bus, llm_defaults: dict | None = None, chat_factory=None):
        self.store, self.bus = store, bus
        self.tasks: dict[str, asyncio.Task] = {}
        self.graphs: dict[str, tuple] = {}        # sid -> (graph, config)——resume 用
        self.chats: dict[str, object] = {}        # sid -> ChatQueue
        self.costs: dict[str, object] = {}        # sid -> CostManager（图内共用的那一个账本）
        self.projects: dict[str, str] = {}        # sid -> 产物目录名
        self._closers: set = set()                # 散会收壳的后台任务，握住引用防被 GC 半路回收
        self._ck = None                            # 进程级 checkpointer（懒建）
        # S7 双实现注入点（默认全 None=进程内旧路，feature flag 在 lifespan 装配）：
        self.chat_factory = chat_factory           # (sid) -> ChatQueue 同接口对象（Redis LIST 版）
        self.trace = None                          # platforms.trace.TraceStore；on_chat_model_end 记 span
        self._ctl = None                           # 跨 worker stop 的发布端（redis 模式才有）
        self._ctl_task = None
        self._last_span: dict[str, tuple] = {}     # sid -> (pt, ct, cost_usd, cost_cny) 上次累计值，span 取增量
        self._trunc_reported: dict[str, int] = {}   # sid -> 已报过的截断笔数（B8），防长跑 resume 刷同一条提示
        # (sid, run_id) -> [派发时刻, 首 token 时刻]。键用 run_id 不用节点名：同一节点在一跑里
        # 会被调多次（n_round 循环），按节点名会把复用/并发的调用串成一条。
        self._call_t0: dict[tuple[str, str], list] = {}

    # ---- 生命周期（契约：start/stop） --------------------------------------
    def start(self, session: Session):
        if self.is_running(session.id):
            raise RuntimeError(f"session {session.id} already running")
        self.tasks[session.id] = asyncio.create_task(self._run(session))

    def is_running(self, sid: str) -> bool:
        t = self.tasks.get(sid)
        return t is not None and not t.done()

    async def stop(self, sid: str) -> bool:
        t = self.tasks.get(sid)
        if t and not t.done():
            self.store.update(sid, status=SessionStatus.stopping)
            t.cancel()
            return True
        # 本 worker 没有这个 task：会话可能跑在别的 worker 上（多进程部署）。
        # 控制通道 PUBLISH ch:ctl，持任务的那个 worker 收到自己 cancel（施工4 目标结构第 7 行）。
        # ⚠ 只在 store 说「可能在跑」时才转发——否则对 created/finished 会话点停止会把它
        # 误标 stopping 卡死（多 worker 冒烟实测：stopped:true 但没人会来收尾）。
        s = self.store.get(sid)
        if s is not None and s.status == SessionStatus.awaiting_human:
            # 停在断点时**全集群都没有活任务**：`_run` 已收尾，`_resume` 一旦起来状态就变
            # running 了（:226）。这条分支原先走下面的转发，而转发的结果是把会话钉死在
            # stopping——没有任何 worker 会有任务去取消它。就地落 stopped，并散会
            # （terminal=True：收 graphs/shell/editor，放弃这个断点就是放弃这场）。
            session = self.store.update(sid, status=SessionStatus.stopped, finished_at=_now())
            self._publish_status(session, "stopped by user")
            self._forget(sid, terminal=True)
            return True
        if self._ctl is not None and s is not None and s.status == SessionStatus.running:
            self.store.update(sid, status=SessionStatus.stopping)
            await self._ctl.publish("ch:ctl", json.dumps({"cmd": "stop", "sid": sid}))
            return True
        return False

    def enable_redis_control(self):
        """redis 模式 lifespan 调一次：订阅 ch:ctl，替别的 worker 取消它手上的任务。"""
        import redis.asyncio as aioredis
        from codeharness.configs.settings import settings

        self._ctl = aioredis.from_url(settings.redis.to_url(), decode_responses=True)

        async def _listen():
            pubsub = self._ctl.pubsub()
            await pubsub.subscribe("ch:ctl")
            try:
                async for msg in pubsub.listen():
                    if msg.get("type") != "message":
                        continue
                    try:
                        cmd = json.loads(msg["data"])
                    except Exception:
                        continue
                    if cmd.get("cmd") == "stop":
                        t = self.tasks.get(cmd.get("sid"))
                        if t and not t.done():
                            t.cancel()
            except asyncio.CancelledError:
                raise
            except Exception:
                return      # 停机时连接被关是预期路径，不留孤儿任务栈

        self._ctl_task = asyncio.get_running_loop().create_task(_listen())

    # ---- 插话（恢复被删除的 /chat 功能） ------------------------------------
    def enqueue_chat(self, sid: str, content: str, send_to: str = "") -> bool:
        chat = self.chats.get(sid)
        if not chat:
            return False
        chat.enqueue(content, send_to)
        return True

    # ---- 人工回答（interrupt → resume） -------------------------------------
    async def _ensure_graph(self, sid: str):
        """取 (graph, config)。进程内字典命中是快路径；**未命中则按会话记录重建**——
        断点已落持久化 checkpointer，靠 thread_id 就能续上，不必是创建它的那个对象。"""
        packed = self.graphs.get(sid)
        if packed:
            return packed

        session = self.store.get(sid)
        if not session:
            return None
        project = self.projects[sid] = session.project_name or sid
        self.costs.setdefault(sid, _seeded_ledger(session.cost or {}))
        team, config, _init = await self._prepare(session, project, self.costs[sid])
        # 重建出来的图只用于 resume：init 不能再喂一遍，否则等于重开一个线程
        self.graphs[sid] = (team, config)
        return self.graphs[sid]

    async def _prepare(self, session, project: str, cost_manager):
        """按会话三态装配三件套：sop=N7 模板线（9.3 扩展入口）；dynamic=S9.1 对照的 RoleZero 线；
        classic=默认经典线。resume 重建路径走同一函数——两张表（组队 × 路由）不会再各长各的
        （第十一处教训）。thread_id 必须带会话唯一值：多会话共用模板不能在 checkpointer 里串台。"""
        from codeharness.const import RequirementTag
        from codeharness.environment.team_graph import SOP
        from codeharness.team import prepare_project, _make_llm, classic_team
        # 一次装配只建一个网关：会话的 llm_override 就在这里落地。经典线原先走
        # prepare_project 的 agents=None 兜底，而 _default_agents 会另建一个不认
        # override 的网关——所以三条线都显式组队。
        llm = _make_llm(cost_manager, getattr(session, "llm_override", None))
        if getattr(session, "sop", ""):
            from codeharness.sop.builder import build_team_from_template, get_template
            edges = get_template(session.sop).edges
            team, config, init = build_team_from_template(session.sop, llm,
                                                          checkpointer=await self._saver(),
                                                          idea=session.idea, thread_id=project)
            names = sorted({str(r) for roles in edges.values() for r in roles})
            entry = str(next(iter(edges.get(RequirementTag.USER_REQUIREMENT) or []), ""))
        else:
            sop = None
            paradigm = getattr(session, "paradigm", "classic")
            if paradigm == "dynamic":
                from codeharness.team import dynamic_assembly
                agents, sop = dynamic_assembly(llm)
                # C1-③ 现场招的成员在**建图之前**并进装配：LangGraph 的节点集 compile 时就定死，
                # 所以生效点是「下一次起跑/续跑」，不是运行中热插（API 侧同判：只有 dynamic 线能招人，
                # classic/react 线没人会点名新节点，招进来就是个醒不过来的死节点）。
                for d in getattr(session, "role_defs", None) or []:
                    from codeharness.team import build_hired_role
                    agents[d["name"]] = build_hired_role(d, llm)
                from codeharness.team import sync_roster
                sync_roster(agents)           # 队长的 {team_info} 得看见新成员，否则点名点不到
            elif paradigm == "react":                     # 9.2 策略曲线第三腿：经典队形×REACT 循环
                from codeharness.team import react_assembly
                agents = react_assembly(llm)
            else:
                agents = classic_team(llm)
            team, config, init = prepare_project(session.idea, project, agents=agents,
                                                 checkpointer=await self._saver(),
                                                 cost_manager=cost_manager, sop=sop)
            names = sorted(agents)
            # 插话默认目标 = 真正订阅 USER_REQUIREMENT 的那个节点（dynamic 用自己的表，
            # classic/react 走 prepare_project 里的 team_graph.SOP）
            route = sop or SOP
            entry = next((str(r) for r in (route.get(RequirementTag.USER_REQUIREMENT) or [])
                          if r in agents), "")
        # 装配出口回填：前端直聊下拉与 /chat 的目标校验都读这两个值。原先前端硬编码
        # ProductManager/Engineer2/DataAnalyst——一个都不在装配里，追问会被 route 静默丢掉。
        if names != list(session.roles) or entry != session.entry_role:
            self.store.update(session.id, roles=names, entry_role=entry)
        session.roles, session.entry_role = names, entry
        chat = self.chats.get(session.id)
        if chat is not None and entry:
            chat.default_target = entry                 # 空目标也要落在真节点上
        # N9：全链路 trace 的唯一注入点（_run 与 _resume 都从这里拿 config）。
        # 节点内裸 model.ainvoke()/tool.ainvoke() 靠 langchain-core 的 var_child_runnable_config
        # 继承，网关与节点里**不得**再传一次——同一 handler 既显式又继承会双 span。
        from codeharness.observability import callbacks
        config["callbacks"] = callbacks()
        return team, config, init

    async def _ckpt_page(self, graph, config: dict, limit: int, before: dict | None = None) -> dict:
        """取一页超步摘要（新→旧），多取一条探 `has_more`——翻页口径与 B2 的事件回放同判。

        ⚠ `before` 必须走 **关键字参数**（开区间上界）。把 `checkpoint_id` 塞进 config 是另一个
        语义：「从那个点往回看，**含它自己**」（闭区间），翻页会重复吐同一条——B2 那轮踩过同族形状。
        这一层刻意**不带 values**：`aget_state_history` 会把每份 state 反序列化出来
        （langgraph 的接口就这样），但只在这一格内存里活着，出页即丢。真数据层实测最单个
        thread 有 711 份、单份最大 62 KB，整份跟着列表回给浏览器等于一次请求搬几十 MB。
        `writes` 这一格 09-25 删掉了，别当「实测为空」：本机 langgraph 的 `snap.metadata` 只有
        `parents / source / step` 三个键（两节点小图现证，见 `plan/team-runtime.md` C11 行末），
        旧写法 `sorted(writes) if isinstance(writes, dict) else []` 于是**永远**给前端一个空数组——
        那是个装死的字段，而界面还写了「空则退回 tasks」的兜底，看着像有两条数据源。
        这一步里谁干活由 `tasks` 给，那才是真值。"""
        out: list[dict] = []
        async for snap in graph.aget_state_history(config, before=before, limit=limit + 1):
            md = snap.metadata or {}
            conf = (snap.config or {}).get("configurable") or {}
            out.append({"checkpoint_id": conf.get("checkpoint_id") or "",
                        "step": md.get("step"), "source": md.get("source"),
                        "ts": str(snap.created_at or ""), "next": list(snap.next or ()),
                        "tasks": [getattr(t, "name", "") for t in (snap.tasks or ())]})
        has_more = len(out) > limit
        page = out[:limit]
        return {"checkpoints": page, "has_more": has_more,
                "next_before": page[-1]["checkpoint_id"] if (page and has_more) else ""}

    def answer_human(self, sid: str, content: str) -> bool:
        if not self.store.get(sid):
            return False
        if self.is_running(sid):
            # _run 与 _resume 共用同一个 tasks[sid] 槽：抢槽会让先结束的一方 pop 掉另一方的
            # 引用（is_running 误报 False、stop() 取消错对象、跨 worker 停止失配）。
            # 判据用活任务视角而不是 store.status：实测 interrupt 收尾时 _run 会把
            # awaiting_human 覆写成 finished（tests/s17::t1 读数），状态在这儿不可信。
            return False
        self.tasks[sid] = asyncio.create_task(self._resume(sid, content))
        return True

    @staticmethod
    async def _pending(graph, config) -> bool:
        """该 thread 是否停在待恢复处（interrupt 未答）。

        ⚠ 必须用 `aget_state`：异步 saver 不支持同步 `get_state`，一调就抛
        NotImplementedError。取不到状态时按"是"处理——让 resume 自己决定，
        比误拒用户回答要好。"""
        try:
            state = await graph.aget_state(config)
        except Exception:
            return True
        nxt = getattr(state, "next", None)
        return bool(nxt) if nxt is not None else True

    async def _resume(self, sid, content):
        """必须重装同一套 ContextVar：create_task 复制的是 HTTP 请求的 context，
        不重装则内核 report.py 拿不到 sink，人工回答之后的产物块会被静默丢弃。"""
        packed = await self._ensure_graph(sid)
        if not packed:
            return
        graph, config = packed
        if not await self._pending(graph, config):
            return                                   # 没停在待恢复点，别把会话误标成 finished
        chat = self.chats.get(sid) or self._make_chat(sid)
        self.chats[sid] = chat
        self.store.update(sid, status=SessionStatus.running)
        try:
            with self._session_ctx(sid):
                async for ev in graph.astream_events(Command(resume=content), config, version="v2"):
                    self._translate(sid, ev)
            await self._settle(sid)
        except asyncio.CancelledError:
            session = self.store.update(sid, status=SessionStatus.stopped, finished_at=_now())
            self._publish_status(session, "stopped by user")
            self._forget(sid, terminal=True)
            raise
        except Exception as exc:
            self._fail(sid, exc)
        finally:
            self.tasks.pop(sid, None)

    # ---- checkpointer（进程级共用一个文件型 saver） --------------------------
    async def _saver(self):
        """断点必须落盘：内存型 saver 一重启就丢，interrupt 的会话再也续不上。

        不可用时退回内存型并照常跑通单进程流程——存储后端故障不该让平台起不来。
        必须是协程：工厂内部要拿运行中的事件循环来建 AsyncSqliteSaver。"""
        if self._ck is None:
            from codeharness.environment.checkpoint import default_checkpoint_path, make_checkpointer
            from server.settings import WORKSPACE_ROOT
            self._ck = await make_checkpointer(default_checkpoint_path(WORKSPACE_ROOT))
        return self._ck

    # ---- 报道槽 → 总线（内核块的唯一通道） ----------------------------------
    def _make_sink(self, sid: str):
        bus = self.bus

        def sink(event: dict):                    # 普通函数（桥接铁律）
            bus.publish(sid, kind="report", **event)

        return sink

    @contextmanager
    def _session_ctx(self, sid: str):
        """装/卸六个 ContextVar。token 只能是局部变量——挂在 self 上会被并发会话互相覆盖，
        随后 reset 到别人 context 里创建的 token 直接 ValueError。

        批次36 多装两个：PERMISSION（工具审批的会话级免审档）与 APPROVAL_IO（待批通道，
        内核 gate 只认它的 sid/decision/request 三个口，因此内核保持零 server / 零 redis 依赖）。

        N9：同时套一层 Langfuse 的会话属性（session_id/user/tags）——两个 astream 循环共用的
        唯一上下文口，OTel 上下文按 asyncio task 隔离，并发会话不串；未开启时是 nullcontext。"""
        from contextlib import ExitStack
        from codeharness.runtime import (CURRENT_PROJECT, REPORT_SINK, CHAT_SINK, CURRENT_USER,
                                         APPROVAL_IO, PERMISSION)
        from codeharness.observability import session_attributes
        from platforms.approval_store import ApprovalStore
        session = self.store.get(sid)
        pairs = (
            (SESSION_ID, SESSION_ID.set(sid)),
            (CURRENT_PROJECT, CURRENT_PROJECT.set(self.projects.get(sid, sid))),
            (REPORT_SINK, REPORT_SINK.set(self._make_sink(sid))),
            (CHAT_SINK, CHAT_SINK.set(self.chats.get(sid))),
            # N1：user_id 贯穿进内核（记忆/经验池的切片键从这里兜底），auth 关恒 "default"
            (CURRENT_USER, CURRENT_USER.set(getattr(session, "user_id", "default")
                                            if session else "default")),
            # 取不到会话时按最严的 readonly（多问一次，不是放行一切）
            (PERMISSION, PERMISSION.set(getattr(session, "permission", "") or "readonly")),
            (APPROVAL_IO, APPROVAL_IO.set(ApprovalStore(sid))),
        )
        with ExitStack() as stack:
            stack.enter_context(session_attributes(self.store.get(sid), self.projects.get(sid, sid)))
            try:
                yield
            finally:
                for var, tok in reversed(pairs):
                    var.reset(tok)

    def _forget(self, sid: str, terminal: bool):
        """散会才清 graphs（断点已落 checkpointer 才安全）；awaiting_human 时必须留着供 resume。"""
        self.chats.pop(sid, None)
        self.costs.pop(sid, None)
        self._last_span.pop(sid, None)
        self._trunc_reported.pop(sid, None)
        # 中断的调用不会走到 on_chat_model_end，在途表必须在这里扫干净，否则永久留着
        for k in [k for k in self._call_t0 if k[0] == sid]:
            self._call_t0.pop(k, None)
        if terminal:
            self.graphs.pop(sid, None)
            project = self.projects.pop(sid, None)
            if project:
                # 常驻 shell 按会话登记，散会不收就是每会话漏一个 cmd.exe
                from codeharness.tools.libs.terminal import close_terminal
                task = asyncio.create_task(close_terminal(project))
                self._closers.add(task)
                task.add_done_callback(self._closers.discard)
                # Editor 视图态（current_file/行窗）与会话同生命周期——B3 命令面登记后必收，
                # 否则字典按会话名只增不减
                from codeharness.tools.libs.editor_tools import close_editor
                close_editor(project)

    async def _interrupt_payload(self, sid: str):
        """活图视角问「这个 thread 是不是停在 interrupt 上」，是则回它的 payload（ask_human 的问题或待批项）。

        为什么必须问活图：`astream_events(v2)` 在 langgraph 1.2.11 下**不发** interrupt 事件
        （实测事件名只有 `on_chain_start/stream/end`）。旧代码把落态押在 `_translate` 的
        `on_interrupt` 分支上，那条分支永不触发 → 停在待批处的会话被写成 `finished`、
        `graphs` 被 pop、审批卡也进不了活流（前端只在切会话时 GET 一次，用户看不到新卡）。
        A4 真模型那批（PLAN §2）取到的读数就是这条。graphs 已被清掉时按「没停」走 finished——
        那种情况线程里没有待恢复的断点，误报 awaiting_human 反而会让 start/stop 卡住。"""
        packed = self.graphs.get(sid)
        if not packed:
            return None
        graph, config = packed
        try:
            state = await graph.aget_state(config)
        except Exception:
            return None
        for task in getattr(state, "tasks", None) or ():
            for intr in getattr(task, "interrupts", None) or ():
                return intr.value
        return None

    def _park(self, sid: str, payload):
        """停在待人工处的统一落态：`awaiting_human` + 把卡推给活流 + graph 留着供 resume。"""
        self.store.update(sid, status=SessionStatus.awaiting_human)
        item = payload.get("approval") if isinstance(payload, dict) else None
        if item:
            # 待批项是内核 gate 用 HSETNX 登记过的那条，这里只推给界面，不重复登记
            self.bus.publish(sid, kind="approval", name="requested", value=item)
        else:
            question = payload.get("question", "") if isinstance(payload, dict) else str(payload)
            self.bus.publish(sid, kind="ask_human", value=question)
        self._publish_status(self.store.get(sid), "awaiting human input")
        self._forget(sid, terminal=False)

    async def _settle(self, sid: str):
        """事件流收口时的落态——**停在待人工处就不许写 finished**。

        旧写法（A 项那次）读 `store.status == awaiting_human` 来判，而那个字段本来就是本函数要写的东西：
        真正置它的那条路（`on_interrupt`）在生产里从不触发，于是判据恒假。现在改问活图。
        两条 teardown（`_run`/`_resume`）共用本出口，不再各写一遍。"""
        payload = await self._interrupt_payload(sid)
        if payload is not None:
            self._park(sid, payload)
            return
        session = self.store.update(sid, status=SessionStatus.finished, finished_at=_now())
        self._publish_max_tokens(sid)
        self._publish_status(session, "run completed")
        self._forget(sid, terminal=True)

    def _publish_max_tokens(self, sid: str):
        """B8：这一跑里有几步被输出上限截断——有就发一条 `turn/end`，形状照参照系
        （`conversation-nodes/turn-max-tokens.ts:42` 读的是 `turn/end` 的 `reason.kind==='max-tokens'`）。

        两个口径差写清楚，别当成「和参照系一模一样」：
        ① 位置：参照系也是把提示锚在轮尾（closing Assistant 与 turn-tail 之间），不是贴在截断那一步；
        ② 计数：它是「这一轮至少有一步撞了上限」的聚合，我们这里是一跑收口说一次——resume 过的长跑
           靠 `_trunc_reported` 记「上次报到第几笔」，新增的截断才会再冒一条，不会三跑刷三条。

        为什么不在 `_translate` 的 `on_chat_model_end` 上顺手发：那条钩子在**最伤的那种截断**上收不到信号
        （JSON 被切半→解析失败→走 repair，`data.output` 里没有 finish_reason；活体 classic 线三次
        length 收尾，零条提示）。每笔调用落账时都带着自己的 response_metadata，记账口是唯一不漏的形状。
        空 content 的 length 收尾走的是另一条已可见的路：`gateway.structured` 抛 ValueError → `_fail`
        发 error 行，文案自己写着「模型输出被 max_token=… 截断」。"""
        cm = self.costs.get(sid)
        n = getattr(cm, "truncated_calls", 0) or 0
        if n > self._trunc_reported.get(sid, 0):
            self._trunc_reported[sid] = n
            self.bus.publish(sid, kind="turn", name="end", value={"reason": {"kind": "max-tokens"}})

    def _fail(self, sid: str, exc: Exception):
        message = f"{type(exc).__name__}: {exc}"
        # 可 grep 的告警（C18②）：这是会话被打成 failed 的唯一出口。事件流是喂界面的，日志才是运维
        # grep 的对象——缺这一行时「点了允许然后整场死了」在日志里零痕迹（告警与重试是两回事：
        # 连接类失败本就由 `_retryable` 重发过，重发用尽之后必须留下响）。
        logger.error(f"[session-failed] sid={sid} {message}")
        session = self.store.update(sid, status=SessionStatus.failed, error=message, finished_at=_now())
        self.bus.publish(sid, kind="error", value=traceback.format_exc(limit=6))
        self._publish_status(session, message)
        self._forget(sid, terminal=True)

    # ---- 主流程 -------------------------------------------------------------
    def _make_chat(self, sid: str):
        """插话队列的装配出口：进程内 ChatQueue（默认）或 RedisChatQueue（S7 flag 开时注入工厂）。

        B6：装一个 `on_change` 接缝再交出去——「谁在队列里」只有队列自己知道
        （HTTP 线程投、图里 route 每轮取），别处猜都只能猜成第二个游标（§9 第 5 条当年写的
        「若需界面可见，接缝应做在 ChatQueue」就是这个口子）。工厂签名不动（它只认 sid），
        所以在这里补装属性，两台同名同属性。"""
        if self.chat_factory:
            chat = self.chat_factory(sid)
        else:
            from codeharness.runtime import ChatQueue
            chat = ChatQueue()
        chat.on_change = self._chat_notifier(sid)
        return chat

    def _chat_notifier(self, sid: str):
        def notify(action: str, items: list):        # 普通函数（桥接铁律，同 _make_sink）
            self.bus.publish(sid, kind="queue", name=action, value={"items": items})
        return notify

    async def _run(self, session: Session):
        sid = session.id
        from codeharness.provider.cost import CostManager

        chat = self.chats[sid] = self._make_chat(sid)
        cost_manager = self.costs[sid] = CostManager()
        project = self.projects[sid] = session.project_name or sid
        try:
            # 账本必须由 runner 建、传进图：图内各角色的 LLM 共用这一个实例，runner 上报时读的就是这同一个
            team, config, init = await self._prepare(session, project, cost_manager)
            self.graphs[sid] = (team, config)        # 快路径；丢了也能从 checkpointer 重建

            with self._session_ctx(sid):
                session = self.store.update(sid, status=SessionStatus.running, started_at=_now())
                self._publish_status(session, "team started")
                async for ev in team.astream_events(init, config, version="v2"):
                    self._translate(sid, ev)

            await self._settle(sid)
        except asyncio.CancelledError:                    # 必须 re-raise，否则僵尸协程
            session = self.store.update(sid, status=SessionStatus.stopped, finished_at=_now())
            self._publish_status(session, "stopped by user")
            self._forget(sid, terminal=True)
            raise
        except Exception as exc:
            self._fail(sid, exc)
        finally:
            self.tasks.pop(sid, None)

    def _sync_cost(self, sid: str):
        """每笔 LLM 调用落账后把账本合进会话态与事件流（GET 轮询与 SSE 各读一头）。
        ⚠ 读的是 `self.costs[sid]`——必须与图内 gateway 持同一个实例，双账本正是恒 0 的根因；
        快照可能比最后一笔晚到一步（on_chat_model_end 的回调先于网关落账），终态快照兜底。"""
        cm = self.costs.get(sid)
        session = self.store.get(sid)
        if cm is None or session is None:
            return
        cost = cost_snapshot(cm)
        self.store.set_cost(sid, cost, persist=True)
        status = str(session.status.value if hasattr(session.status, "value") else session.status)
        self.bus.publish(sid, kind="status",
                         value={"status": status, "error": session.error,
                                "cost": cost, "message": ""})

    def _trace_span(self, sid: str, node: str, slot=None):
        """N4 数据层：每笔 LLM 调用记一条 span（节点/token 增量/两桶成本增量/时刻）→ ch:trace:{sid}。
        `t0` 是派发时刻、`ft` 是首 token 时刻，来自 `_translate` 按 run_id 攒的在途表；
        采不到就是 null——前端据此**不显示**读数，而不是显示一个假的 0。
        trace 未注入（进程内默认）= 零开销直通。"""
        if self.trace is None:
            return
        cm = self.costs.get(sid)
        if cm is None:
            return
        t0, ft = slot if slot else (None, None)
        cur = (cm.total_prompt_tokens, cm.total_completion_tokens,
               round(cm.cost_usd, 6), round(cm.cost_cny, 6))
        prev = self._last_span.get(sid, (0, 0, 0.0, 0.0))
        self._last_span[sid] = cur
        # 一笔调用只会有一个币种在动（一个模型一套价），但两桶都发出去：
        # 前端据此各标各符号，而不是把两个数加回一列「成本」——那正是 C12 修掉的形状。
        self.trace.record(sid, {"node": node, "pt": cur[0] - prev[0], "ct": cur[1] - prev[1],
                                "cost_usd": round(cur[2] - prev[2], 6),
                                "cost_cny": round(cur[3] - prev[3], 6), "ts": time.time(),
                                "t0": t0, "ft": ft})

    # ---- astream_events 翻译（LLM 用量合流与打字机、interrupt，其余块走报道槽） ----
    def _translate(self, sid: str, ev: dict):
        kind = ev.get("event", "")
        rid = str(ev.get("run_id") or "")
        if kind == "on_chat_model_start":
            if rid:
                self._call_t0[(sid, rid)] = [time.time(), None]
        elif kind == "on_chat_model_end":
            self._sync_cost(sid)
            # 打字机流块（stream-{node}）到此收口，否则跑完了光标还在闪（S8 终验现形）
            node = ev.get("metadata", {}).get("langgraph_node", "")
            self.bus.publish(sid, kind="report", block="Thought", uuid=f"stream-{node}",
                             name="end_marker", value=None, role=node)
            self._trace_span(sid, node, self._call_t0.pop((sid, rid), None) if rid else None)
        elif kind == "on_chat_model_stream":
            # structured 输出不进这里做打字机（内核 Thought 块整段上屏）；这里只兜底裸文本流
            chunk = ev["data"]["chunk"]
            if getattr(chunk, "content", ""):
                node = ev.get("metadata", {}).get("langgraph_node", "")
                slot = self._call_t0.get((sid, rid)) if rid else None
                # 首 token 只认第一个有内容的分片；没采到就是 None，不拿收口时刻凑一个假 TTFT。
                if slot is not None and slot[1] is None:
                    slot[1] = time.time()
                self.bus.publish(sid, kind="report", block="Thought",
                                 uuid=f"stream-{node}", name="content",
                                 value=chunk.content, role=node)
        # 注意：这里**没有** `on_interrupt` 分支。旧实现有一条，靠它置 `awaiting_human` 并把审批卡
        # 推进活流——实测 langgraph 1.2.11 的 `astream_events(v2)` 只发 `on_chain_start/stream/end`，
        # 根本没有 interrupt 事件（A4 真模型那批取到的读数，PLAN §2 A4 行），那条分支永不触发，
        # 于是停在待批处的会话被 `_settle` 无条件写成 finished、graphs 被 pop、活流里也看不到卡。
        # 落态与推卡统一改问活图：`_interrupt_payload()` → `_park()`。

    def _publish_status(self, session: Session, message: str = ""):
        """用量快照只读：token 与成本照实报，没有任何预算上限字段。"""
        cost = {}
        cm = self.costs.get(session.id)
        if cm is not None:
            cost = cost_snapshot(cm)
            self.store.set_cost(session.id, cost, persist=True)
        self.bus.publish(session.id, kind="status",
                         value={"status": str(session.status.value if hasattr(session.status, "value")
                                               else session.status),
                                "error": session.error, "cost": cost, "message": message})
