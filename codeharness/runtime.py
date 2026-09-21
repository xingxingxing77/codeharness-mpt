"""运行时挂载点：ContextVar 三件套（沿用源项目 SESSION_ID/CURRENT_ROLE 的同款模式）。
内核只读它们；server 在会话任务入口 set——包内保持零 fastapi 依赖。"""
from contextvars import ContextVar
from collections import deque
from pathlib import Path
from threading import Lock
from uuid import uuid4

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

    ⚠ 这里必须是**取走即消费**的队列，不能留「快照 + 清空」那种两步写法（C7 修的正是这个）：
    旧写法 `out = list(deque)` 再 `deque.clear()`，这两步之间从 HTTP 线程 append 进来的那条
    会被 clear 一起抹掉——用户点「追问」，消息没了、日志里一个字都没有。
    B6 要 `pending()/remove()`（要能看见、能撤回），一把锁包住四个动作就还是同一个接缝：
    drain 与 enqueue 互斥，所以「逐条 popleft」和任何写法一样不会丢。
    接口与 `platforms/chat_queue.RedisChatQueue` 同名同签名（同步）。"""

    def __init__(self, default_target: str = TEAMLEADER_NAME, on_change=None):
        self.default_target = default_target
        # B6 的界面接缝：「谁在队列里」这件事由队列自己知道（入队/撤回/被 route 取走都是它动的），
        # runner 在建队时把这个普通函数注进来 → 内核依旧零 server / 零 redis 依赖（同 APPROVAL_IO 那招）。
        self.on_change = on_change
        self._lock = Lock()
        self._q: deque = deque()          # (qid, content, send_to)；qid 只为「撤回这一条」存在

    def _fire(self, action: str, items: list):
        if self.on_change:
            self.on_change(action, items)

    def enqueue(self, content: str, send_to: str = "") -> str:
        qid = uuid4().hex[:8]
        with self._lock:                       # 整段在锁里：没有「快照与清空之间被插一条」的窗口
            self._q.append((qid, content, send_to))
        self._fire("add", [{"id": qid, "content": content, "send_to": send_to}])
        return qid

    def drain(self) -> list[tuple[str, str]]:
        with self._lock:
            taken = []
            while self._q:
                taken.append(self._q.popleft())   # 取走即消费；两步快照那种形状由 s7 的结构判据盯着
        if taken:
            self._fire("drain", [{"id": i, "content": c, "send_to": s} for i, c, s in taken])
        return [(c, s) for _, c, s in taken]

    def pending(self) -> list[dict]:
        with self._lock:
            return [{"id": i, "content": c, "send_to": s} for i, c, s in self._q]

    def remove(self, qid: str) -> bool:
        """撤回一条还没被取走的插话。**不重排**：只把那一个位置剔掉，后面的次序原样。"""
        with self._lock:
            for n, item in enumerate(self._q):
                if item[0] == qid:
                    del self._q[n]
                    break
            else:
                return False
        self._fire("remove", [{"id": qid}])
        return True
