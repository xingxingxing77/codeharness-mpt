"""运行时挂载点：ContextVar 三件套（沿用源项目 SESSION_ID/CURRENT_ROLE 的同款模式）。
内核只读它们；server 在会话任务入口 set——包内保持零 fastapi 依赖。"""
from contextvars import ContextVar
from collections import deque, defaultdict
from pathlib import Path

from codeharness.configs.settings import settings
from codeharness.const import TEAMLEADER_NAME

CURRENT_PROJECT: ContextVar[str] = ContextVar("current_project", default="project")
"""当前会话的项目目录名——ArtifactStore.active() 用它，保证产物落进 session.workspace 同名目录"""

REPORT_SINK: ContextVar[object] = ContextVar("report_sink", default=None)
"""报道槽：sync callable(event_dict)。内核的 report.py 把块事件灌给它；server 装桥→SessionEventBus"""

CHAT_SINK: ContextVar[object] = ContextVar("chat_sink", default=None)
"""插话槽：实现 drain() 的对象。team_graph.route 每轮 drain，把用户插话变成额外 Send（=源 MGXEnv 直聊语义）"""


def session_root(project: str | None = None) -> Path:
    """本会话工作目录 = `workspace_root/{project 或 CURRENT_PROJECT}`，与 server 的 `session.workspace`、
    前端文件树同目录。文件工具、终端 cwd、沙箱 scratch、产物仓全部走这一个出口
    （S4 判 `新`：按会话分配工作目录与执行环境）；S7 接 per-session 容器/配额时只换这一处实现。

    目录名可能是脏数据或 LLM 产出的 project_name，只认 workspace_root 的直接子目录，越界退回默认桶。
    """
    base = Path(settings.workspace_root).resolve()
    root = (base / (project if project is not None else CURRENT_PROJECT.get())).resolve()
    if root.parent != base:
        root = base / "project"
    root.mkdir(parents=True, exist_ok=True)
    return root


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
