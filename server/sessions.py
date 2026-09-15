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
    status: SessionStatus = SessionStatus.created
    llm_override: dict = Field(default_factory=dict)
    workspace: str = ""
    error: str = ""
    cost: dict = Field(default_factory=dict)
    created_at: str = ""
    started_at: str = ""
    finished_at: str = ""


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
               project_name: str = "", llm_override: Optional[dict] = None) -> Session:
        sid = uuid.uuid4().hex[:8]
        name = project_name or sid
        s = Session(id=sid, idea=idea, project_name=name, n_round=n_round,
                    llm_override=llm_override or {},
                    workspace=str(WORKSPACE_ROOT / name),      # 产物目录=会话目录（前端文件树读这里）
                    created_at=_now())
        self._sessions[sid] = s
        self._persist()
        return s

    def get(self, sid: str) -> Optional[Session]:
        return self._sessions.get(sid)

    def list(self) -> list:
        return sorted(self._sessions.values(), key=lambda s: s.created_at, reverse=True)

    def update(self, sid: str, persist: bool = True, **fields) -> Session:
        data = self._sessions[sid].model_dump()
        data.update(fields)
        self._sessions[sid] = Session(**data)
        if persist:
            self._persist()
        return self._sessions[sid]

    def set_cost(self, sid: str, cost: dict):
        self.update(sid, persist=False, cost=cost)
