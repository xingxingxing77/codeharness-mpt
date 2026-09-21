"""运行时挂载点：ContextVar 三件套（沿用源项目 SESSION_ID/CURRENT_ROLE 的同款模式）。
内核只读它们；server 在会话任务入口 set——包内保持零 fastapi 依赖。"""
from contextvars import ContextVar
from queue import Empty as QueueEmpty, Queue
from pathlib import Path

from codeharness.configs.settings import settings
from codeharness.const import TEAMLEADER_NAME

CURRENT_PROJECT: ContextVar[str] = ContextVar("current_project", default="project")
"""当前会话的项目目录名——ArtifactStore.active() 用它，保证产物落进 session.workspace 同名目录"""

REPORT_SINK: ContextVar[object] = ContextVar("report_sink", default=None)
"""报道槽：sync callable(event_dict)。内核的 report.py 把块事件灌给它；server 装桥→SessionEventBus"""

CHAT_SINK: ContextVar[object] = ContextVar("chat_sink", default=None)
"""插话槽：实现 drain() 的对象。team_graph.route 每轮 drain，把用户插话变成额外 Send（=源 MGXEnv 直聊语义）"""

CURRENT_USER: ContextVar[str] = ContextVar("current_user", default="default")
"""N1 账号边界：当前会话的创建者。server 在 _session_ctx 装载；记忆/经验池的 user_id 切片键
在调用方没显式给时从这里兜底——auth 关恒 "default"，qdrant payload 与既有行为逐字节兼容"""

PERMISSION: ContextVar[str] = ContextVar("permission", default="readonly")
"""工具审批的会话级免审档（readonly|workspace_write|full_access）。server 在 _session_ctx 装载；
缺省取最严的 readonly——忘了装的后果是「多问一次」，不是「放行一切」。
中途切档从**下一个节点边界**起生效（跑图任务已持有旧值，resume 时新建任务才读到新值）。"""

APPROVAL_IO: ContextVar[object] = ContextVar("approval_io", default=None)
"""待批通道：实现 `decision(aid)` / `request(item)` 的对象（server 注入 platforms.approval_store 适配器）。
没装 = 内核直跑图（门禁与离线测试路径），此时 gate 一律放行——没有可问的人，挂起只会永久卡住。"""


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
    """每会话一个。runner 持有并暴露 enqueue；route 每轮 drain。

    ⚠ 这里必须是**逐条取走**的队列，不能是「快照 + 清空」（C7 修的正是这个）：
    旧写法 `out = list(deque)` 再 `deque.clear()`，这两步之间从 HTTP 线程 append 进来的那条
    会被 clear 一起抹掉——用户点「追问」，消息没了、日志里一个字都没有。
    `queue.Queue` 的 `put_nowait`/`get_nowait` 自带锁，取走即消费，没有那个窗口。
    接口与 `platforms/chat_queue.RedisChatQueue` 同名同签名（enqueue/drain，同步）。"""

    def __init__(self, default_target: str = TEAMLEADER_NAME):
        self.default_target = default_target
        self._q: Queue = Queue()

    def enqueue(self, content: str, send_to: str = ""):
        self._q.put_nowait((content, send_to))

    def drain(self) -> list[tuple[str, str]]:
        out = []
        while True:
            try:
                out.append(self._q.get_nowait())
            except QueueEmpty:
                return out
