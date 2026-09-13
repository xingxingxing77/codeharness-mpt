"""SessionRunner：会话 ↔ LangGraph 团队图。三件事：
1) 会话任务入口 set 四个 ContextVar（SESSION_ID/CURRENT_PROJECT/REPORT_SINK/CHAT_SINK）——
   内核的报道与插话因此零依赖 web 层（同源项目 SESSION_ID/CURRENT_ROLE 模式）；
2) astream_events 里只翻译两件事：LLM token（Thought 打字机）与 interrupt（ask_human）；
   其余块事件全部来自内核报道槽（report.py），此处只做 sink→bus 转发；
3) 人工回答 = 同 graph 实例 + 同 thread_id 的 Command(resume)。"""
import asyncio
import time
import traceback
from contextvars import Token
from typing import Optional
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
    def answer_human(self, sid: str, content: str) -> bool:
        packed = self.graphs.get(sid)
        if not packed:
            return False
        graph, config = packed
        self.store.update(sid, status=SessionStatus.running)
        asyncio.create_task(self._resume(sid, graph, config, content))
        return True

    async def _resume(self, sid, graph, config, content):
        try:
            async for ev in graph.astream_events(Command(resume=content), config, version="v2"):
                self._translate(sid, ev)
        except asyncio.CancelledError:
            raise

    # ---- 报道槽 → 总线（内核块的唯一通道） ----------------------------------
    def _make_sink(self, sid: str):
        bus = self.bus

        def sink(event: dict):                    # 普通函数（桥接铁律）
            bus.publish(sid, kind="report", **event)

        return sink

    # ---- 主流程 -------------------------------------------------------------
    async def _run(self, session: Session):
        sid = session.id
        token: Token = SESSION_ID.set(sid)
        from codeharness.runtime import CURRENT_PROJECT, REPORT_SINK, CHAT_SINK, ChatQueue
        from codeharness.provider.cost import CostManager
        from codeharness.configs.settings import settings

        chat = self.chats[sid] = ChatQueue(default_target="Mike")
        cost_manager = CostManager(max_budget=session.investment or settings.max_budget)
        try:
            from codeharness.team import prepare_project
            project = session.project_name or sid
            team, config, init = prepare_project(
                session.idea, project, investment=session.investment, checkpointer=None)
            self.graphs[sid] = (team, config)

            # 四个挂载点：内核据此知道"为谁、报给谁、插话给谁"
            self._project_token = CURRENT_PROJECT.set(project)
            self._sink_token = REPORT_SINK.set(self._make_sink(sid))
            self._chat_token = CHAT_SINK.set(chat)

            session = self.store.update(sid, status=SessionStatus.running, started_at=_now())
            self._publish_status(session, cost_manager, "team started")

            async for ev in team.astream_events(init, config, version="v2"):
                self._translate(sid, ev, cost_manager)

            session = self.store.update(sid, status=SessionStatus.finished, finished_at=_now())
            self._publish_status(session, cost_manager, "run completed")
        except asyncio.CancelledError:                    # 必须 re-raise，否则僵尸协程
            session = self.store.update(sid, status=SessionStatus.stopped, finished_at=_now())
            self._publish_status(session, cost_manager, "stopped by user")
            raise
        except Exception as exc:
            no_money = type(exc).__name__ == "NoMoneyException" or "预算耗尽" in str(exc)
            status = SessionStatus.finished if no_money else SessionStatus.failed
            message = f"预算耗尽: {exc}" if no_money else f"{type(exc).__name__}: {exc}"
            session = self.store.update(sid, status=status, error=message, finished_at=_now())
            self.bus.publish(sid, kind="error", value=traceback.format_exc(limit=6))
            self._publish_status(session, cost_manager, message)
        finally:
            SESSION_ID.reset(token)
            for name in ("_project_token", "_sink_token", "_chat_token"):
                tok = getattr(self, name, None)
                if tok is not None:
                    {"_project_token": CURRENT_PROJECT, "_sink_token": REPORT_SINK,
                     "_chat_token": CHAT_SINK}[name].reset(tok)
            self.tasks.pop(sid, None)

    # ---- astream_events 翻译（只两件事，其余块走报道槽） --------------------
    def _translate(self, sid: str, ev: dict, cm=None):
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

    def _publish_status(self, session: Session, cm, message: str = ""):
        cost = {}
        if cm is not None:
            c = cm.get_costs()
            cost = {"total_cost": round(c.total_cost, 4), "max_budget": c.total_budget,
                    "total_prompt_tokens": c.total_prompt_tokens,
                    "total_completion_tokens": c.total_completion_tokens}
            self.store.set_cost(session.id, cost)
        self.bus.publish(session.id, kind="status",
                         value={"status": str(session.status.value if hasattr(session.status, "value")
                                               else session.status),
                                "error": session.error, "cost": cost, "message": message})
