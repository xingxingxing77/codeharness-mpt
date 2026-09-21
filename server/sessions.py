"""会话注册表 + JSON 持久化。字段一字不差对齐 types.ts:1-15；状态机含 stopping（源枚举漏项已补）。"""
import json
import time
import uuid
from enum import Enum
from typing import Optional
from pydantic import BaseModel, Field
from server.settings import WORKSPACE_ROOT, SESSIONS_FILE


class SessionStatus(str, Enum):
    created = "created"
    running = "running"
    awaiting_human = "awaiting_human"
    stopping = "stopping"          # 源 runner 在用但枚举缺失（源项目 bug），前端已认知——补上
    finished = "finished"
    stopped = "stopped"
    failed = "failed"


class Session(BaseModel):
    id: str
    idea: str
    project_name: str
    n_round: int = 5
    paradigm: str = "classic"       # classic=经典 SOP 线（默认）；dynamic=RoleZero 线（S9.1 对照，runner._prepare 分流）
    sop: str = ""                   # 非空=按 N7 模板装配（9.3 扩展线入口，ext_api.register_template 登记）
    user_id: str = "default"        # N1 账号边界：auth 关恒 default；开=创建者，list/get 按它隔离
    status: SessionStatus = SessionStatus.created
    llm_override: dict = Field(default_factory=dict)
    # 装配出口回填（runner._prepare）：roles=这场次真实的角色节点名，entry_role=插话空目标时给谁。
    # 前端直聊下拉只渲染这两个值——原先硬编码的名单与装配对不上，追问会被 route 静默丢掉。
    roles: list[str] = Field(default_factory=list)
    # 现场招进来的成员（C1-③）：[{name, profile, goal, constraints, tools[]}]。
    # `roles` 只是节点名清单（前端下拉/校验吃它），装配要吃**完整 profile**，所以另开一个字段。
    # ⚠ 新增这个字段必须同批登记 `platforms/session_store.py::_JSON_FIELDS`，
    # 否则 Redis 那台 store 会 `str(list)` 落库、回读成字符串，两台 store 从此不同形。
    role_defs: list[dict] = Field(default_factory=list)
    entry_role: str = ""
    # 工具审批的会话级免审档：readonly | workspace_write | full_access（判定表见
    # codeharness/tools/_approval.py）。新建默认最保守的 readonly——只读面免审，
    # 任何写文件 / 执行 / 联网都要人批一次；嫌烦可在 composer 那枚 chip 上切档。
    permission: str = "readonly"
    workspace: str = ""
    error: str = ""
    # B5 会话目标（口径 2026-09-20 定）：**单条**目标、由用户在建会话时填。
    # 完成**只有用户点确认这一条路**（`goal_done_at` 非空＝已确认），模型没有任何写它的出口。
    # 两次变更都进事件流（`kind:"goal"`，见 `server/api/sessions.py`），这样活流与回放同源。
    # 参照系那套 phase（active/paused/blocked/complete）里 paused/blocked 需要轮次驱动与
    # 受阻判定，本仓没有生产者 → 按「口径只要求进行中/已完成」落，不预先摆两个死值。
    goal: str = ""
    goal_done_at: str = ""
    cost: dict = Field(default_factory=dict)
    created_at: str = ""
    started_at: str = ""
    finished_at: str = ""
    archived: bool = False          # 侧栏「归档」：非破坏性隐藏，可撤销
    pinned: bool = False            # 置顶排在列表最前


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


class SessionStore:
    def __init__(self, path=None):
        self.path = path or SESSIONS_FILE
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._sessions: dict = {}
        if self.path.exists():
            for item in json.loads(self.path.read_text(encoding="utf-8")):
                s = Session(**item)
                if s.status in (SessionStatus.running, SessionStatus.awaiting_human):
                    s.status = SessionStatus.stopped      # 服务重启自愈
                self._sessions[s.id] = s
            self._persist()

    def _persist(self):
        data = [s.model_dump() for s in self._sessions.values()]
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.path)

    def create(self, idea: str, n_round: int = 5,
               project_name: str = "", llm_override: Optional[dict] = None,
               paradigm: str = "classic", sop: str = "", user_id: str = "default",
               permission: str = "readonly", goal: str = "") -> Session:
        sid = uuid.uuid4().hex[:8]
        name = project_name or sid
        s = Session(id=sid, idea=idea, project_name=name, n_round=n_round,
                    paradigm=paradigm, sop=sop, user_id=user_id, permission=permission,  # B5：goal 走同一条 create 通路（建会话时就带上），默认空串=没有目标
                    goal=goal,
                    llm_override=llm_override or {},
                    workspace=str(WORKSPACE_ROOT / name),      # 产物目录=会话目录（前端文件树读这里）
                    created_at=_now())
        self._sessions[sid] = s
        self._persist()
        return s

    def get(self, sid: str) -> Optional[Session]:
        return self._sessions.get(sid)

    def list(self) -> list:
        # 两个方向没法塞进一次 sort：先按创建时间倒序，再按「未置顶」稳定排一次，
        # 组内顺序不受影响。
        by_recency = sorted(self._sessions.values(), key=lambda s: s.created_at, reverse=True)
        return sorted(by_recency, key=lambda s: not s.pinned)

    def delete(self, sid: str) -> bool:
        if sid not in self._sessions:
            return False
        del self._sessions[sid]
        self._persist()
        return True

    def update(self, sid: str, persist: bool = True, **fields) -> Session:
        data = self._sessions[sid].model_dump()
        data.update(fields)
        self._sessions[sid] = Session(**data)
        if persist:
            self._persist()
        return self._sessions[sid]

    def set_cost(self, sid: str, cost: dict, persist: bool = False):
        """persist=True：崩了也能从 sessions.json 拿到最近一次落盘的账（S9 双跑靠它）。
        ponytail: 每次合流重写整份 JSON，量级到几百会话时换 S7 的 Redis 会话态。"""
        self.update(sid, persist=persist, cost=cost)
