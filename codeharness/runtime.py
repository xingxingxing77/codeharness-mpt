"""运行时挂载点：ContextVar 三件套（沿用源项目 SESSION_ID/CURRENT_ROLE 的同款模式）。
内核只读它们；server 在会话任务入口 set——包内保持零 fastapi 依赖。"""
from contextvars import ContextVar
from collections import deque, defaultdict
from codeharness.const import TEAMLEADER_NAME

CURRENT_PROJECT: ContextVar[str] = ContextVar("current_project", default="project")
"""当前会话的项目目录名——ArtifactStore.active() 用它，保证产物落进 session.workspace 同名目录"""

REPORT_SINK: ContextVar[object] = ContextVar("report_sink", default=None)
"""报道槽：sync callable(event_dict)。内核的 report.py 把块事件灌给它；server 装桥→SessionEventBus"""

CHAT_SINK: ContextVar[object] = ContextVar("chat_sink", default=None)
"""插话槽：实现 drain() 的对象。team_graph.route 每轮 drain，把用户插话变成额外 Send（=源 MGXEnv 直聊语义）"""


class ChatQueue:
    """每会话一个。runner 持有并暴露 enqueue；route 每轮 drain。"""

    def __init__(self, default_target: str = TEAMLEADER_NAME):
        self.default_target = default_target
        self._q: dict[str, deque] = defaultdict(deque)

    def enqueue(self, content: str, send_to: str = ""):
        self._q["main"].append((content, send_to))

    def drain(self) -> list[tuple[str, str]]:
        out = list(self._q["main"])
        self._q["main"].clear()
        return out
