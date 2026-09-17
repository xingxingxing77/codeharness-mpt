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
from langgraph.types import Command
from server.bridges import SESSION_ID
from server.sessions import Session, SessionStatus


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _seeded_ledger(saved: dict):
    """进程重启后的 resume 路径：账本从会话记录里上次落盘的快照起算。
    不播种的话新账本从 0 起，终态快照会把历史用量整段覆盖掉（合流断点之二）。"""
    from codeharness.provider.cost import CostManager
    cm = CostManager()
    cm.total_prompt_tokens = int(saved.get("total_prompt_tokens", 0) or 0)
    cm.total_completion_tokens = int(saved.get("total_completion_tokens", 0) or 0)
    cm.total_cost = float(saved.get("total_cost", 0) or 0)
    return cm


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
        self._last_span: dict[str, tuple] = {}     # sid -> (pt, ct, cost) 上次累计值，span 取增量

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
        if self._ctl is not None and s is not None and s.status in (
                SessionStatus.running, SessionStatus.awaiting_human):
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
        from codeharness.team import prepare_project
        project = self.projects[sid] = session.project_name or sid
        self.costs.setdefault(sid, _seeded_ledger(session.cost or {}))
        team, config, _init = prepare_project(session.idea, project, checkpointer=await self._saver(),
                                              cost_manager=self.costs[sid])
        # 重建出来的图只用于 resume：init 不能再喂一遍，否则等于重开一个线程
        self.graphs[sid] = (team, config)
        return self.graphs[sid]

    def answer_human(self, sid: str, content: str) -> bool:
        if not self.store.get(sid):
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
            session = self.store.update(sid, status=SessionStatus.finished, finished_at=_now())
            self._publish_status(session, "run completed")
            self._forget(sid, terminal=True)
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
        """装/卸四个 ContextVar。token 只能是局部变量——挂在 self 上会被并发会话互相覆盖，
        随后 reset 到别人 context 里创建的 token 直接 ValueError。"""
        from codeharness.runtime import CURRENT_PROJECT, REPORT_SINK, CHAT_SINK
        pairs = (
            (SESSION_ID, SESSION_ID.set(sid)),
            (CURRENT_PROJECT, CURRENT_PROJECT.set(self.projects.get(sid, sid))),
            (REPORT_SINK, REPORT_SINK.set(self._make_sink(sid))),
            (CHAT_SINK, CHAT_SINK.set(self.chats.get(sid))),
        )
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

    def _fail(self, sid: str, exc: Exception):
        message = f"{type(exc).__name__}: {exc}"
        session = self.store.update(sid, status=SessionStatus.failed, error=message, finished_at=_now())
        self.bus.publish(sid, kind="error", value=traceback.format_exc(limit=6))
        self._publish_status(session, message)
        self._forget(sid, terminal=True)

    # ---- 主流程 -------------------------------------------------------------
    def _make_chat(self, sid: str):
        """插话队列的装配出口：进程内 ChatQueue（默认）或 RedisChatQueue（S7 flag 开时注入工厂）。"""
        if self.chat_factory:
            return self.chat_factory(sid)
        from codeharness.runtime import ChatQueue
        return ChatQueue()

    async def _run(self, session: Session):
        sid = session.id
        from codeharness.provider.cost import CostManager

        chat = self.chats[sid] = self._make_chat(sid)
        cost_manager = self.costs[sid] = CostManager()
        project = self.projects[sid] = session.project_name or sid
        try:
            from codeharness.team import prepare_project
            # 账本必须由 runner 建、传进图：图内各角色共用它，runner 上报时读的就是这同一个实例
            team, config, init = prepare_project(
                session.idea, project, checkpointer=await self._saver(), cost_manager=cost_manager)
            self.graphs[sid] = (team, config)        # 快路径；丢了也能从 checkpointer 重建

            with self._session_ctx(sid):
                session = self.store.update(sid, status=SessionStatus.running, started_at=_now())
                self._publish_status(session, "team started")
                async for ev in team.astream_events(init, config, version="v2"):
                    self._translate(sid, ev)

            session = self.store.update(sid, status=SessionStatus.finished, finished_at=_now())
            self._publish_status(session, "run completed")
            self._forget(sid, terminal=True)
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
        c = cm.get_costs()
        cost = {"total_cost": round(c.total_cost, 4),
                "total_prompt_tokens": c.total_prompt_tokens,
                "total_completion_tokens": c.total_completion_tokens}
        self.store.set_cost(sid, cost, persist=True)
        status = str(session.status.value if hasattr(session.status, "value") else session.status)
        self.bus.publish(sid, kind="status",
                         value={"status": status, "error": session.error,
                                "cost": cost, "message": ""})

    def _trace_span(self, sid: str, node: str):
        """N4 数据层：每笔 LLM 调用记一条 span（节点/token 增量/时刻）→ ch:trace:{sid}。
        trace 未注入（进程内默认）= 零开销直通。"""
        if self.trace is None:
            return
        cm = self.costs.get(sid)
        if cm is None:
            return
        cur = (cm.total_prompt_tokens, cm.total_completion_tokens, round(cm.total_cost, 6))
        prev = self._last_span.get(sid, (0, 0, 0.0))
        self._last_span[sid] = cur
        self.trace.record(sid, {"node": node, "pt": cur[0] - prev[0], "ct": cur[1] - prev[1],
                                "cost": round(cur[2] - prev[2], 6), "ts": time.time()})

    # ---- astream_events 翻译（LLM 用量合流与打字机、interrupt，其余块走报道槽） ----
    def _translate(self, sid: str, ev: dict):
        kind = ev.get("event", "")
        if kind == "on_chat_model_end":
            self._sync_cost(sid)
            # 打字机流块（stream-{node}）到此收口，否则跑完了光标还在闪（S8 终验现形）
            node = ev.get("metadata", {}).get("langgraph_node", "")
            self.bus.publish(sid, kind="report", block="Thought", uuid=f"stream-{node}",
                             name="end_marker", value=None, role=node)
            self._trace_span(sid, node)
        elif kind == "on_chat_model_stream":
            # structured 输出不进这里做打字机（内核 Thought 块整段上屏）；这里只兜底裸文本流
            chunk = ev["data"]["chunk"]
            if getattr(chunk, "content", ""):
                node = ev.get("metadata", {}).get("langgraph_node", "")
                self.bus.publish(sid, kind="report", block="Thought",
                                 uuid=f"stream-{node}", name="content",
                                 value=chunk.content, role=node)
        elif kind == "on_interrupt":
            q = ev.get("value")
            question = q[0].get("question", "") if isinstance(q, list) and q else str(q)
            self.store.update(sid, status=SessionStatus.awaiting_human)
            self.bus.publish(sid, kind="ask_human", value=question)
            self._publish_status(self.store.get(sid), "awaiting human")

    def _publish_status(self, session: Session, message: str = ""):
        """用量快照只读：token 与成本照实报，没有任何预算上限字段。"""
        cost = {}
        cm = self.costs.get(session.id)
        if cm is not None:
            c = cm.get_costs()
            cost = {"total_cost": round(c.total_cost, 4),
                    "total_prompt_tokens": c.total_prompt_tokens,
                    "total_completion_tokens": c.total_completion_tokens}
            self.store.set_cost(session.id, cost, persist=True)
        self.bus.publish(session.id, kind="status",
                         value={"status": str(session.status.value if hasattr(session.status, "value")
                                               else session.status),
                                "error": session.error, "cost": cost, "message": message})
