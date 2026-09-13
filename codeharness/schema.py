"""消息与产物模型。来源：metagpt/schema.py 改造（Message/MessageQueue/Document/Documents）
+ 新写（四上下文模型/TeamState/Command）。判定与逐方法说明见 docs/02-第2步-消息模型.md。"""
import json
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional, Set

from pydantic import BaseModel, Field, field_serializer, field_validator

from codeharness.const import MESSAGE_ROUTE_TO_ALL, MESSAGE_ROUTE_TO_SELF, USER_REQUIREMENT


# ---------------- 消息 ----------------
class Message(BaseModel):
    """全系统唯一通信载体。改造自源 schema.py:232-356：
    - instruct_content 从动态类还原简化为 dict + instruct_schema
    - 删 parse_resources / __setattr__（多模态与 any_to_str 体系不搬）"""

    id: str = Field(default="", validate_default=True)
    content: str
    instruct_content: Optional[dict] = Field(default=None, validate_default=True)
    instruct_schema: str = ""
    role: str = "user"
    cause_by: str = Field(default="", validate_default=True)
    sent_from: str = Field(default="", validate_default=True)
    send_to: Set[str] = Field(default={MESSAGE_ROUTE_TO_ALL}, validate_default=True)
    metadata: Dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=datetime.now)

    @field_validator("id", mode="before")
    @classmethod
    def check_id(cls, v):
        return v or uuid.uuid4().hex

    @field_validator("instruct_content", mode="before")
    @classmethod
    def check_instruct_content(cls, ic):
        return ic or None

    @field_validator("cause_by", mode="before")
    @classmethod
    def check_cause_by(cls, v):
        if v in (None, ""):
            return USER_REQUIREMENT
        return v if isinstance(v, str) else v.__name__

    @field_validator("sent_from", mode="before")
    @classmethod
    def check_sent_from(cls, v):
        return v if isinstance(v, str) else (v.__name__ if isinstance(v, type) else str(v or ""))

    @field_validator("send_to", mode="before")
    @classmethod
    def check_send_to(cls, v):
        if not v:
            return {MESSAGE_ROUTE_TO_ALL}
        return {str(x) for x in v}

    @field_serializer("send_to", mode="plain")
    def ser_send_to(self, v: set) -> list:
        return list(v)

    def __init__(self, content: str = "", **data: Any):
        data["content"] = data.get("content", content)
        super().__init__(**data)

    def __str__(self):
        return f"{self.role}: {self.instruct_content or self.content}"

    __repr__ = __str__

    def rag_key(self) -> str:
        return self.content

    def to_dict(self) -> dict:
        """喂 LLM 的标准格式（源 :332）"""
        return {"role": self.role, "content": self.content}

    def dump(self) -> str:
        return self.model_dump_json(exclude_none=True, warnings=False)

    @staticmethod
    def load(val) -> Optional["Message"]:
        try:
            m = json.loads(val)
            mid = m.pop("id", None)
            msg = Message(**m)
            if mid:
                msg.id = mid
            return msg
        except Exception:
            return None


class AIMessage(Message):
    """role 固定 assistant 的便捷子类（替代源 AIMessage；agent 字段不搬，前端用 sent_from）"""

    role: str = "assistant"


# ---------------- 收件箱队列（源 schema.py:713-782 原样复制） ----------------
import asyncio
from asyncio import Queue, QueueEmpty


class MessageQueue(BaseModel):
    model_config = {"arbitrary_types_allowed": True}
    _queue: Queue = Queue()

    def pop(self) -> Optional[Message]:
        try:
            item = self._queue.get_nowait()
            if item:
                self._queue.task_done()
            return item
        except QueueEmpty:
            return None

    def pop_all(self) -> List[Message]:
        ret = []
        while True:
            msg = self.pop()
            if not msg:
                break
            ret.append(msg)
        return ret

    def push(self, msg: Message):
        self._queue.put_nowait(msg)

    def empty(self):
        return self._queue.empty()


# ---------------- 产物模型（源 schema.py:138-221 复制，删 to_action_output） ----------------
import os
from pathlib import Path


class Document(BaseModel):
    """一个产物文件：相对路径 + 正文。WritePRD/WriteCode 等全部产物用它。"""

    root_path: str = ""
    filename: str = ""
    content: str = ""

    def get_meta(self) -> "Document":
        return Document(root_path=self.root_path, filename=self.filename)

    @property
    def root_relative_path(self):
        return os.path.join(self.root_path, self.filename)

    def __str__(self):
        return self.content

    __repr__ = __str__

    @classmethod
    async def load(cls, filename, project_path=None) -> Optional["Document"]:
        path = Path(filename)
        if not filename or not path.exists():
            return None
        content = await asyncio.to_thread(path.read_text, encoding="utf-8")
        doc = cls(content=content, filename=str(filename))
        if project_path and path.is_relative_to(project_path):
            doc.root_path = str(path.relative_to(project_path).parent)
            doc.filename = path.name
        return doc


class Documents(BaseModel):
    docs: Dict[str, Document] = Field(default_factory=dict)

    @classmethod
    def from_iterable(cls, documents) -> "Documents":
        return cls(docs={d.filename: d for d in documents})


# ---------------- 四个上下文模型（12 号册：SOP 回路载体） ----------------
class CodingContext(BaseModel):
    """WriteCode 的输入"""
    filename: str = ""
    design_doc: Optional[Document] = None
    task_doc: Optional[Document] = None
    code_plan_and_change_doc: Optional[Document] = None
    code_doc: Optional[Document] = None


class TestingContext(BaseModel):
    """WriteTest 的输入"""
    filename: str = ""
    code_doc: Optional[Document] = None
    test_doc: Optional[Document] = None


class RunCodeContext(BaseModel):
    """RunCode 的输入（源 qa_engineer.py:92 字段对齐）"""
    command: List[str] = []
    working_dir: str = ""
    code: str = ""
    code_filename: str = ""
    test_filename: str = ""
    output_filename: str = ""


class RunCodeResult(BaseModel):
    """RunCode 的输出"""
    stdout: str = ""
    stderr: str = ""
    return_code: int = 0


class CodePlanAndChangeContext(BaseModel):
    requirement: str = ""
    code_plan_filename: str = ""
    changed_filenames: List[str] = []


# ---------------- 团队图状态与命令 ----------------
class TeamState(BaseModel):
    """impl/05 的图状态以此为准（TypedDict 版在 team_graph.py 内定义）"""
    round: int = 0
    budget_used: float = 0.0
    debug_rounds: int = 0
    finished: bool = False


class Command(BaseModel):
    """RoleZero 单条命令（prompts/di/role_zero.py 契约）"""
    command_name: str
    args: Dict[str, Any] = Field(default_factory=dict)