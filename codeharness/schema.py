"""
框架的核心数据结构/模型定义模块，
它是整个项目的"数据骨架"，定义了框架中流转和持久化的各种核心数据模型

来源：metagpt/schema.py 逐符号对齐——SerializationMixin(:72) / SimpleMessage(:133) /
Document(:138) / Documents(:194) / Resource(:224) / Message(:232) / UserMessage(:419) /
SystemMessage(:429) / AIMessage(:439) / Task(:457) / TaskResult(:480) / Plan(:496) /
MessageQueue(:713) / BaseContext(:789) / 六上下文(:797-857)。
新栈自有：TeamState / Command / instruct_schema 字段。

两处**有意偏差**（改动会波及整个 Action 层，故显式登记）：
1. `instruct_content` 用 `dict + instruct_schema` 承载，不用源的"动态造 BaseModel 类"——源那套依赖
   `action_node.create_model_class` + `import_class`（属判 `重` 的 985 行自研引擎）。序列化后仍可无损
   还原，因为模式名保存在 `instruct_schema`，不需要 `{"class","module","value"}` 三件套。
2. `RunCodeResult.return_code` 是新增字段：源靠 `"Ran N tests ... OK"` 正则判通过，对 pytest 无效。
   源的 `summary/stdout/stderr` 三字段全部保留。
"""
import asyncio
import json
import os
import uuid
from abc import ABC
from asyncio import Queue, QueueEmpty, wait_for
from datetime import datetime
from json import JSONDecodeError
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set, Type, TypeVar, Union

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, create_model, field_serializer, field_validator

from codeharness.const import (
    AGENT,
    MESSAGE_ROUTE_CAUSE_BY,
    MESSAGE_ROUTE_FROM,
    MESSAGE_ROUTE_TO,
    MESSAGE_ROUTE_TO_ALL,
    SERDESER_PATH,
    SYSTEM_DESIGN_FILE_REPO,
    TASK_FILE_REPO,
    USER_REQUIREMENT,
)


# ---------------- 序列化（两层分工，与源一致） ----------------
# base/base_serialization.py : BaseSerialization —— 多态类型标签 + extra="forbid"（与存储后端正交）
# 本文件                     : SerializationMixin —— 只负责"存到哪个文件"
# 判定表 R4 只取代后者（checkpointer）；前者必须保留，否则"字段声明基类、值实为子类"会静默丢字段。
from codeharness.base.base_serialization import BaseSerialization


class SerializationMixin(BaseSerialization):
    """源 schema.py:72。落盘路径规则照源：`SERDESER_PATH/<ClassName>.json`。"""

    def serialize(self, file_path: str = None) -> str:
        from codeharness.utils.common import write_json_file
        file_path = file_path or self.get_serialization_path()
        write_json_file(file_path, self.model_dump(), use_fallback=True)
        return file_path

    @classmethod
    def deserialize(cls, file_path: str = None) -> Optional["SerializationMixin"]:
        from codeharness.utils.common import read_json_file
        file_path = file_path or cls.get_serialization_path()
        if not Path(file_path).exists():
            return None
        return cls.model_validate(read_json_file(file_path))

    @classmethod
    def get_serialization_path(cls) -> str:
        return str(SERDESER_PATH / f"{cls.__qualname__}.json")


class SimpleMessage(BaseModel):
    content: str
    role: str


# ---------------- 产物模型（源 schema.py:138-221） ----------------
class Document(BaseModel):
    """一个产物文件：相对路径 + 正文。WritePRD/WriteCode 等全部产物用它。

    ⚠ 与 `codeharness/document.py` 里的 `Document` **同名不同物**：那个是文档仓子系统
    （带 `path`/`from_path`/`persist`），这个是 Action 的产物载体。源项目同样是两套。"""

    root_path: str = ""
    filename: str = ""
    content: str = ""

    def get_meta(self) -> "Document":
        """只带 root_path/filename 的元信息副本。"""
        return Document(root_path=self.root_path, filename=self.filename)

    @property
    def root_relative_path(self):
        return os.path.join(self.root_path, self.filename)

    def __str__(self):
        return self.content

    __repr__ = __str__

    @classmethod
    async def load(cls, filename: Union[str, Path], project_path: Optional[Union[str, Path]] = None) -> Optional["Document"]:
        if not filename or not Path(filename).exists():
            return None
        content = await asyncio.to_thread(Path(filename).read_text, encoding="utf-8")
        doc = cls(content=content, filename=str(filename))
        if project_path and Path(filename).is_relative_to(project_path):
            doc.root_path = Path(filename).relative_to(project_path).parent
            doc.filename = Path(filename).name
        return doc


class Documents(BaseModel):
    docs: Dict[str, Document] = Field(default_factory=dict)

    @classmethod
    def from_iterable(cls, documents: Iterable[Document]) -> "Documents":
        return Documents(docs={doc.filename: doc for doc in documents})

    def to_instruct_content(self) -> dict:
        """源是 `to_action_output()`→ActionOutput（判 `重`）；新栈用 dict + instruct_schema 承载。"""
        return {k: d.content for k, d in self.docs.items()}


class Resource(BaseModel):
    """`Message.parse_resources` 的返回元素（源 :224）。"""

    resource_type: str  # the type of resource
    value: str          # a string type of resource content
    description: str    # explanation


# ---------------- 短名归一（cause_by / sent_from / send_to 专用） ----------------
def _tag(val: Any) -> str:
    """把 类对象 / 实例 / 字符串 统一归一成**短类名**。

    ⚠ 故意不调 `utils.common.any_to_str`：那份复制件的 `get_class_name` 返回
    `module.ClassName` 全限定名（common.py:390），而新栈的 `RequirementTag` 与
    `Action.name` 全部是短名。两套混用不会报错，只会让 watch 订阅静默失配——
    这正是本项目最难查的一类 bug。S6 逐字复制源 Action 时务必统一走这里。"""
    if isinstance(val, str):
        return val
    if isinstance(val, type):
        return val.__name__
    if val is None or val == "":
        return ""
    return type(val).__name__


def _tag_set(val: Any) -> set:
    """镜像源 `any_to_str_set`（common.py:405）：**标量归一成单元素集合**，
    容器才逐个映射。直接对 str 做集合推导会把 `"self"` 拆成 `{'s','e','l','f'}`——
    这是个静默的路由失效，写在这里就是为了不再踩。

    源有一处不对称，这里照搬：`check_send_to`（validator）里空值落 `{<all>}`，
    而 `__setattr__` 里 `m.send_to = set()` 保持空集，语义是"不给任何人"。"""
    if isinstance(val, dict):
        return {_tag(i) for i in val.values()}
    if isinstance(val, (list, set, tuple)):
        return {_tag(i) for i in val}
    return {_tag(val)}


# ---------------- 消息（源 schema.py:232-416） ----------------
class Message(BaseModel):
    """全系统唯一通信载体：谁说的（sent_from）、说什么（content）、
    由哪个动作产生（cause_by）、发给谁（send_to）。"""

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
        """两条路径都收：dict（新栈主路径）或 BaseModel（源路径 / create_instruct_value 产物）。"""
        if ic is None:
            return None
        return ic.model_dump() if isinstance(ic, BaseModel) else ic

    @field_validator("cause_by", mode="before")
    @classmethod
    def check_cause_by(cls, v):
        """⚠ 一致性敏感：**为空才落 USER_REQUIREMENT，已显式给出时绝不覆盖**。
        源在此处有无条件覆盖的问题，会让 watch 订阅整体失效。"""
        if v in (None, ""):
            return USER_REQUIREMENT
        return _tag(v)

    @field_validator("sent_from", mode="before")
    @classmethod
    def check_sent_from(cls, v):
        return _tag(v)

    @field_validator("send_to", mode="before")
    @classmethod
    def check_send_to(cls, v):
        return {MESSAGE_ROUTE_TO_ALL} if not v else _tag_set(v)

    @field_serializer("send_to", mode="plain")
    def ser_send_to(self, v: set) -> list:
        return list(v)

    @field_serializer("instruct_content", mode="plain")
    def ser_instruct_content(self, ic) -> Union[dict, None]:
        """源在此造 {"class","mapping","value"} 以还原动态类；新栈只存 dict，模式名在 instruct_schema。"""
        if ic is None:
            return None
        return ic.model_dump() if isinstance(ic, BaseModel) else ic

    def __init__(self, content: str = "", **data: Any):
        data["content"] = data.get("content", content)
        super().__init__(**data)

    def __setattr__(self, key, val):
        """源 :307：路由三字段允许赋类对象/字符串混用，统一归一成 str 或 set[str]。"""
        if key in (MESSAGE_ROUTE_CAUSE_BY, MESSAGE_ROUTE_FROM):
            val = _tag(val)
        elif key == MESSAGE_ROUTE_TO:
            val = _tag_set(val)      # 不加 <all> 兜底：赋 set() 的语义是"不给任何人"（源同）
        super().__setattr__(key, val)

    def __str__(self):
        if self.instruct_content:
            return f"{self.role}: {self.instruct_content}"
        return f"{self.role}: {self.content}"

    __repr__ = __str__

    def rag_key(self) -> str:
        """For search"""
        return self.content

    def to_dict(self) -> dict:
        """喂 LLM 的标准格式（源 :332）。"""
        return {"role": self.role, "content": self.content}

    def dump(self) -> str:
        return self.model_dump_json(exclude_none=True, warnings=False)

    @staticmethod
    def load(val) -> Optional["Message"]:
        """json 字符串 → Message。id 要显式回写，否则 validator 会造一个新 id。"""
        try:
            m = json.loads(val)
        except JSONDecodeError:
            return None
        mid = m.pop("id", None)
        msg = Message(**m)
        if mid:
            msg.id = mid
        return msg

    async def parse_resources(self, llm, key_descriptions: Dict[str, str] = None) -> Dict:
        """源 :358：从需求文本里列资源（上下文适配能力，后续会迁到 context builder）。"""
        if not self.content:
            return {}
        content = f"## Original Requirement\n```text\n{self.content}\n```\n"
        return_format = (
            "Return a markdown JSON object with:\n"
            '- a "resources" key contain a list of objects. Each object with:\n'
            '  - a "resource_type" key explain the type of resource;\n'
            '  - a "value" key containing a string type of resource content;\n'
            '  - a "description" key explaining why;\n'
        )
        for k, v in (key_descriptions or {}).items():
            return_format += f'- a "{k}" key containing {v};\n'
        return_format += '- a "reason" key explaining why;\n'
        instructions = ['Lists all the resources contained in the "Original Requirement".', return_format]
        from codeharness.utils.common import CodeParser
        rsp = await llm.aask(content, system_msgs=instructions)
        m = json.loads(CodeParser.parse_code(text=rsp, lang="json"))
        m["resources"] = [Resource(**i) for i in m.get("resources", [])]
        return m

    def add_metadata(self, key: str, value: str):
        self.metadata[key] = value

    @staticmethod
    def create_instruct_value(kvs: Dict[str, Any], class_name: str = "") -> BaseModel:
        """源 :396：按 dict 动态造 BaseModel 实例。平时 instruct_content 存 dict 即可，需要模型语义时用。"""
        if not class_name:
            class_name = "DM" + uuid.uuid4().hex[0:8]
        dynamic_class = create_model(class_name, **{k: (v.__class__, ...) for k, v in kvs.items()})
        return dynamic_class.model_validate(kvs)

    def is_user_message(self) -> bool:
        return self.role == "user"

    def is_ai_message(self) -> bool:
        return self.role == "assistant"


class UserMessage(Message):
    """便于支持 OpenAI 的消息。"""

    def __init__(self, content: str, **kwargs):
        kwargs.pop("role", None)
        super().__init__(content=content, role="user", **kwargs)


class SystemMessage(Message):
    def __init__(self, content: str, **kwargs):
        kwargs.pop("role", None)
        super().__init__(content=content, role="system", **kwargs)


class AIMessage(Message):
    def __init__(self, content: str, **kwargs):
        kwargs.pop("role", None)
        super().__init__(content=content, role="assistant", **kwargs)

    def with_agent(self, name: str):
        self.add_metadata(key=AGENT, value=name)
        return self

    @property
    def agent(self) -> str:
        return self.metadata.get(AGENT, "")


# ---------------- 任务与计划（源 schema.py:457-710） ----------------
class Task(BaseModel):
    task_id: str = ""
    dependent_task_ids: list[str] = []      # Tasks prerequisite to this Task
    instruction: str = ""
    task_type: str = ""
    code: str = ""
    result: str = ""
    is_success: bool = False
    is_finished: bool = False
    assignee: str = ""                      # ⚠ 应是 role 的 name（源 append_task 注释）

    def reset(self):
        self.code = ""
        self.result = ""
        self.is_success = False
        self.is_finished = False

    def update_task_result(self, task_result: "TaskResult"):
        self.code = self.code + "\n" + task_result.code
        self.result = self.result + "\n" + task_result.result
        self.is_success = task_result.is_success


class TaskResult(BaseModel):
    """Result of taking a task, with result and is_success required to be filled."""

    code: str = ""
    result: str
    is_success: bool


class Plan(BaseModel):
    """Plan is a sequence of tasks towards a goal.（源 :496）

    `WriteTasks` 与 RoleZero 的运行时底座：拓扑排序、下游级联 reset、当前游标推进都在这里，
    **不要简化成"一个任务列表"**。

    源文件头的 `@register_tool(include_functions=[...])` 未搬：schema 层 import tools 会成环
    （tools → actions → schema）。新栈的工具暴露集中在 S6 的 ext_api/tools.py。
    """

    goal: str = ""
    context: str = ""
    tasks: list[Task] = []
    task_map: dict[str, Task] = {}
    current_task_id: str = ""

    def _topological_sort(self, tasks: list[Task]):
        task_map = {task.task_id: task for task in tasks}
        dependencies = {task.task_id: set(task.dependent_task_ids) for task in tasks}
        sorted_tasks = []
        visited = set()

        def visit(task_id):
            if task_id in visited:
                return
            visited.add(task_id)
            for dependent_id in dependencies.get(task_id, []):
                visit(dependent_id)
            sorted_tasks.append(task_map[task_id])

        for task in tasks:
            visit(task.task_id)
        return sorted_tasks

    def add_tasks(self, tasks: list[Task]):
        """把新任务并入现有计划，保持依赖序。

        1) 无现有任务：拓扑排序后直接作为当前任务列表。
        2) 有现有任务：保留 (task_id, instruction) 相同的前缀，其余用新任务覆盖——
           这让 LLM 重规划时已完成的进度不被抹掉。
        """
        if not tasks:
            return
        new_tasks = self._topological_sort(tasks)
        if not self.tasks:
            self.tasks = new_tasks
        else:
            prefix_length = 0
            for old_task, new_task in zip(self.tasks, new_tasks):
                if old_task.task_id != new_task.task_id or old_task.instruction != new_task.instruction:
                    break
                prefix_length += 1
            self.tasks = self.tasks[:prefix_length] + new_tasks[prefix_length:]
        self._update_current_task()
        self.task_map = {task.task_id: task for task in self.tasks}

    def reset_task(self, task_id: str):
        """按 task_id 重置任务，并**级联重置所有下游任务**。"""
        if task_id in self.task_map:
            task = self.task_map[task_id]
            task.reset()
            for dep_task in self.tasks:
                if task_id in dep_task.dependent_task_ids:
                    # FIXME（源同注）：LLM 造出环依赖时会无限递归
                    self.reset_task(dep_task.task_id)
        self._update_current_task()

    def _replace_task(self, new_task: Task):
        assert new_task.task_id in self.task_map
        self.task_map[new_task.task_id] = new_task
        for i, task in enumerate(self.tasks):
            if task.task_id == new_task.task_id:
                self.tasks[i] = new_task
                break
        for task in self.tasks:
            if new_task.task_id in task.dependent_task_ids:
                self.reset_task(task.task_id)
        self._update_current_task()

    def _append_task(self, new_task: Task):
        if self.has_task_id(new_task.task_id):
            from codeharness.logs import logger
            logger.warning("Task already in current plan, should use replace_task instead. Overwriting the existing task.")
        assert all([self.has_task_id(dep_id) for dep_id in new_task.dependent_task_ids]), "New task has unknown dependencies"
        # 现有任务不依赖新任务，直接排到拓扑序末尾即可
        self.tasks.append(new_task)
        self.task_map[new_task.task_id] = new_task
        self._update_current_task()

    def has_task_id(self, task_id: str) -> bool:
        return task_id in self.task_map

    def _update_current_task(self):
        self.tasks = self._topological_sort(self.tasks)
        self.task_map = {task.task_id: task for task in self.tasks}
        current_task_id = ""
        for task in self.tasks:
            if not task.is_finished:
                current_task_id = task.task_id
                break
        self.current_task_id = current_task_id
        self._report_plan(current_task_id)

    def _report_plan(self, current_task_id: str):
        """源 `TaskReporter().report({...})`。同步发射，走内核报道槽 → 前端 Task 块。"""
        from codeharness.report import BlockType, _emit
        _emit(BlockType.TASK, uuid.uuid4().hex, "object",
              {"tasks": [i.model_dump() for i in self.tasks], "current_task_id": current_task_id})

    @property
    def current_task(self) -> Optional[Task]:
        return self.task_map.get(self.current_task_id, None)

    def finish_current_task(self):
        """完成当前任务：置 is_finished，游标推进到下一个。"""
        if self.current_task_id:
            self.current_task.is_finished = True
            self._update_current_task()

    def finish_all_tasks(self):
        while self.current_task:
            self.finish_current_task()

    def is_plan_finished(self) -> bool:
        return all(task.is_finished for task in self.tasks)

    def get_finished_tasks(self) -> list[Task]:
        """按线性化顺序返回已完成任务。"""
        return [task for task in self.tasks if task.is_finished]

    def append_task(self, task_id: str, dependent_task_ids: list[str], instruction: str, assignee: str, task_type: str = ""):
        """追加新任务。assignee 必须是 role 的 name。"""
        return self._append_task(Task(task_id=task_id, dependent_task_ids=dependent_task_ids,
                                      instruction=instruction, assignee=assignee, task_type=task_type))

    def replace_task(self, task_id: str, new_dependent_task_ids: list[str], new_instruction: str, new_assignee: str):
        return self._replace_task(Task(task_id=task_id, dependent_task_ids=new_dependent_task_ids,
                                       instruction=new_instruction, assignee=new_assignee))


# ---------------- 收件箱队列（源 schema.py:713-782） ----------------
class MessageQueue(BaseModel):
    """Message queue which supports asynchronous updates.

    ⚠ 队列必须是 `PrivateAttr(default_factory=Queue)`：写成类属性 `_queue: Queue = Queue()`
    会让**所有实例共用同一个队列**（此前正是这个写法，两个会话的消息会互串）。"""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    _queue: Queue = PrivateAttr(default_factory=Queue)

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

    async def dump(self) -> str:
        """序列化但**不消费**：取出→再回推。"""
        if self.empty():
            return "[]"
        lst, msgs = [], []
        try:
            while True:
                item = await wait_for(self._queue.get(), timeout=1.0)
                if item is None:
                    break
                msgs.append(item)
                lst.append(item.dump())
                self._queue.task_done()
        except asyncio.TimeoutError:
            pass
        finally:
            for m in msgs:
                self._queue.put_nowait(m)
        return json.dumps(lst, ensure_ascii=False)

    @staticmethod
    def load(data) -> "MessageQueue":
        queue = MessageQueue()
        try:
            for i in json.loads(data):
                msg = Message.load(i)
                if msg:
                    queue.push(msg)
        except JSONDecodeError:
            pass
        return queue


# ---------------- 上下文模型（源 schema.py:789-857） ----------------
T = TypeVar("T", bound=BaseModel)


class BaseContext(SerializationMixin, ABC):
    @classmethod
    def loads(cls: Type[T], val: str) -> Optional[T]:
        try:
            return cls(**json.loads(val))
        except (JSONDecodeError, TypeError, ValueError):
            return None


# 字段名与源逐字对齐（S6 复制 run_code.py / qa_engineer.py 时按这些名字取值）；
# 但一律给默认值——源多处是必填，而新栈 Action 常用 `Model(**(msg.instruct_content or {}))` 构造。
class CodingContext(BaseContext):
    """WriteCode 的输入。"""

    filename: str = ""
    design_doc: Optional[Document] = None
    task_doc: Optional[Document] = None
    code_doc: Optional[Document] = None
    code_plan_and_change_doc: Optional[Document] = None


class TestingContext(BaseContext):
    """WriteTest 的输入。"""

    filename: str = ""
    code_doc: Optional[Document] = None
    test_doc: Optional[Document] = None


class RunCodeContext(BaseContext):
    """RunCode 的输入。源字段名是 `working_directory`（不是 working_dir）。"""

    mode: str = "script"
    code: Optional[str] = None
    code_filename: str = ""
    test_code: Optional[str] = None
    test_filename: str = ""
    command: List[str] = Field(default_factory=list)
    working_directory: str = ""
    additional_python_paths: List[str] = Field(default_factory=list)
    output_filename: Optional[str] = None
    output: Optional[str] = None


class RunCodeResult(BaseContext):
    """RunCode 的输出。summary/stdout/stderr 照源，return_code 为新栈新增。"""

    summary: str = ""
    stdout: str = ""
    stderr: str = ""
    return_code: int = 0


class CodeSummarizeContext(BaseModel):
    design_filename: str = ""
    task_filename: str = ""
    codes_filenames: List[str] = Field(default_factory=list)
    reason: str = ""

    @staticmethod
    def loads(filenames: List) -> "CodeSummarizeContext":
        ctx = CodeSummarizeContext()
        for filename in filenames:
            if Path(filename).is_relative_to(SYSTEM_DESIGN_FILE_REPO):
                ctx.design_filename = str(filename)
                continue
            if Path(filename).is_relative_to(TASK_FILE_REPO):
                ctx.task_filename = str(filename)
                continue
        return ctx

    def __hash__(self):
        return hash((self.design_filename, self.task_filename))


class CodePlanAndChangeContext(BaseModel):
    requirement: str = ""
    issue: str = ""
    prd_filename: str = ""
    design_filename: str = ""
    task_filename: str = ""
    code_plan_filename: str = ""          # 新栈自有：装配 WriteCode 的底稿
    changed_filenames: List[str] = []     # 新栈自有


# ---------------- 团队图状态与命令（新栈自有） ----------------
class TeamState(BaseModel):
    """图状态的一部分（TypedDict 版在 environment/team_graph.py 内定义）。"""

    round: int = 0
    debug_rounds: int = 0
    finished: bool = False


class Command(BaseModel):
    """RoleZero 单条命令（prompts/di/role_zero.py 契约）。"""

    command_name: str
    args: Dict[str, Any] = Field(default_factory=dict)

# mermaid class view
class UMLClassMeta(BaseModel):
    name: str = ""
    visibility: str = ""

    @staticmethod
    def name_to_visibility(name: str) -> str:
        if name == "__init__":
            return "+"
        if name.startswith("__"):
            return "-"
        elif name.startswith("_"):
            return "#"
        return "+"


class UMLClassAttribute(UMLClassMeta):
    value_type: str = ""
    default_value: str = ""

    def get_mermaid(self, align=1) -> str:
        content = "".join(["\t" for i in range(align)]) + self.visibility
        if self.value_type:
            content += self.value_type.replace(" ", "") + " "
        name = self.name.split(":", 1)[1] if ":" in self.name else self.name
        content += name
        if self.default_value:
            content += "="
            if self.value_type not in ["str", "string", "String"]:
                content += self.default_value
            else:
                content += '"' + self.default_value.replace('"', "") + '"'
        # if self.abstraction:
        #     content += "*"
        # if self.static:
        #     content += "$"
        return content


class UMLClassMethod(UMLClassMeta):
    args: List[UMLClassAttribute] = Field(default_factory=list)
    return_type: str = ""

    def get_mermaid(self, align=1) -> str:
        content = "".join(["\t" for i in range(align)]) + self.visibility
        name = self.name.split(":", 1)[1] if ":" in self.name else self.name
        content += name + "(" + ",".join([v.get_mermaid(align=0) for v in self.args]) + ")"
        if self.return_type:
            content += " " + self.return_type.replace(" ", "")
        # if self.abstraction:
        #     content += "*"
        # if self.static:
        #     content += "$"
        return content


class UMLClassView(UMLClassMeta):
    attributes: List[UMLClassAttribute] = Field(default_factory=list)
    methods: List[UMLClassMethod] = Field(default_factory=list)

    def get_mermaid(self, align=1) -> str:
        content = "".join(["\t" for i in range(align)]) + "class " + self.name + "{\n"
        for v in self.attributes:
            content += v.get_mermaid(align=align + 1) + "\n"
        for v in self.methods:
            content += v.get_mermaid(align=align + 1) + "\n"
        content += "".join(["\t" for i in range(align)]) + "}\n"
        return content

    @classmethod
    def load_dot_class_info(cls, dot_class_info) -> "UMLClassView":
        visibility = UMLClassView.name_to_visibility(dot_class_info.name)
        class_view = cls(name=dot_class_info.name, visibility=visibility)
        for i in dot_class_info.attributes.values():
            visibility = UMLClassAttribute.name_to_visibility(i.name)
            attr = UMLClassAttribute(name=i.name, visibility=visibility, value_type=i.type_, default_value=i.default_)
            class_view.attributes.append(attr)
        for i in dot_class_info.methods.values():
            visibility = UMLClassMethod.name_to_visibility(i.name)
            method = UMLClassMethod(name=i.name, visibility=visibility, return_type=i.return_args.type_)
            for j in i.args:
                arg = UMLClassAttribute(name=j.name, value_type=j.type_, default_value=j.default_)
                method.args.append(arg)
            method.return_type = i.return_args.type_
            class_view.methods.append(method)
        return class_view
