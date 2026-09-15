"""SessionRunner：会话 ↔ LangGraph 团队图。三件事：
1) 会话任务入口装四个 ContextVar（SESSION_ID/CURRENT_PROJECT/REPORT_SINK/CHAT_SINK）——
   内核的报道与插话因此零依赖 web 层；
2) astream_events 里只翻译两件事：LLM token（Thought 打字机）与 interrupt（ask_human）；
   其余块事件全部来自内核报道槽（report.py），此处只做 sink→bus 转发；
3) 人工回答 = 同 graph 实例 + 同 thread_id 的 Command(resume)，且必须重装同一套 ContextVar。"""
import asyncio
import time
import traceback
from contextlib import contextmanager
from langgraph.types import Command
from server.bridges import SESSION_ID
from server.sessions import Session, SessionStatus


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


class SessionRunner:
    def __init__(self, store, bus, llm_defaults: dict | None = None):
        self.store, self.bus = store, bus
        self.tasks: dict[str, asyncio.Task] = {}
        self.graphs: dict[str, tuple] = {}        # sid -> (graph, config)——resume 用
        self.chats: dict[str, object] = {}        # sid -> ChatQueue
        self.costs: dict[str, object] = {}        # sid -> CostManager（图内共用的那一个账本）
        self.projects: dict[str, str] = {}        # sid -> 产物目录名
        self._closers: set = set()                # 散会收壳的后台任务，握住引用防被 GC 半路回收
        self._ck = None                            # 进程级 checkpointer（懒建）

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
        if not t or t.done():
            return False
        self.store.update(sid, status=SessionStatus.stopping)
        t.cancel()
        return True

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
        from codeharness.provider.cost import CostManager
        from codeharness.team import prepare_project
        project = self.projects[sid] = session.project_name or sid
        self.costs.setdefault(sid, CostManager())
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
        from codeharness.runtime import ChatQueue
        packed = await self._ensure_graph(sid)
        if not packed:
            return
        graph, config = packed
        if not await self._pending(graph, config):
            return                                   # 没停在待恢复点，别把会话误标成 finished
        chat = self.chats.get(sid) or ChatQueue()
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
        if terminal:
            self.graphs.pop(sid, None)
            project = self.projects.pop(sid, None)
            if project:
                # 常驻 shell 按会话登记，散会不收就是每会话漏一个 cmd.exe
                from codeharness.tools.libs.terminal import close_terminal
                task = asyncio.create_task(close_terminal(project))
                self._closers.add(task)
                task.add_done_callback(self._closers.discard)

    def _fail(self, sid: str, exc: Exception):
        message = f"{type(exc).__name__}: {exc}"
        session = self.store.update(sid, status=SessionStatus.failed, error=message, finished_at=_now())
        self.bus.publish(sid, kind="error", value=traceback.format_exc(limit=6))
        self._publish_status(session, message)
        self._forget(sid, terminal=True)

    # ---- 主流程 -------------------------------------------------------------
    async def _run(self, session: Session):
        sid = session.id
        from codeharness.runtime import ChatQueue
        from codeharness.provider.cost import CostManager

        chat = self.chats[sid] = ChatQueue()
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

    # ---- astream_events 翻译（只两件事，其余块走报道槽） --------------------
    def _translate(self, sid: str, ev: dict):
        kind = ev.get("event", "")
        if kind == "on_chat_model_stream":
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
            self.store.set_cost(session.id, cost)
        self.bus.publish(session.id, kind="status",
                         value={"status": str(session.status.value if hasattr(session.status, "value")
                                               else session.status),
                                "error": session.error, "cost": cost, "message": message})
