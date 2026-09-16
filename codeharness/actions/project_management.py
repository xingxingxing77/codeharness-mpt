"""WriteTasks：设计文档 → 文件级任务列表。= 源 project_management.py(201) 的 `改`：
- 增量迭代照源 :134 `_update_tasks`（旧 tasks 存在走 NEW_REQ 合并，模板逐字源 :36）；
- `_update_requirements`（源 :154）照搬：Required packages 聚合进会话根 requirements.txt——
  这是 QA RunCode 前的依赖声明真源，此前本仓完全没有这一件；
- 源的多设计文件循环/changed_files 记账属 ProjectRepo 制不搬；`_execute_api` 判 `推迟`（同 PRD）。"""
from pydantic import BaseModel, Field

from codeharness.base.action import BaseAction
from codeharness.const import DocName, PACKAGE_REQUIREMENTS_FILENAME, RepoName
from codeharness.document_store.artifact_store import ArtifactStore
from codeharness.logs import logger
from codeharness.schema import Document, Message

NEW_REQ_TEMPLATE = """
### Legacy Content
{old_task}

### New Requirements
{context}
"""

TASKS_SYSTEM_PROMPT = """You are a project manager, write the work breakdown structure in json.
Fields and requirements (follow EXACTLY):
- task_list: list of tasks, each one per file to create, fields:
  - filename: relative path of the file to write, NEVER empty
  - task_id: unique id like "task_001"
  - dependent_task_ids: ids of tasks that must finish first
  - instruction: what to implement in this file
- required_packages: python packages the code needs, one per line, empty if none
- shared_knowledge: domain knowledge every task should know, may be empty
"""

REFINED_TASKS_SYSTEM_PROMPT = """You are a project manager, update the work breakdown structure in json for incremental development.
Fields and requirements (follow EXACTLY):
- task_list: list of tasks; keep still-valid tasks, add/adjust ones required by the new requirements, each one per file:
  - filename: relative path of the file to write, NEVER empty
  - task_id: unique id like "task_001"
  - dependent_task_ids: ids of tasks that must finish first
  - instruction: what to implement in this file
- required_packages: python packages the code needs, one per line, empty if none
- shared_knowledge: domain knowledge every task should know, may be empty
"""


class TaskItem(BaseModel):
    """一条文件级任务。定成具名模型而不是 dict：`list[dict]` 等于没契约，
    structured 不会要求模型填 filename（真模型实测就是缺这个键，装配处 KeyError 崩掉整场会话）。"""

    filename: str = Field(min_length=1)     # 空串会一路传到 WriteCode，往 src 目录本身写
    task_id: str = ""
    dependent_task_ids: list[str] = Field(default_factory=list)
    instruction: str = ""


class TaskList(BaseModel):
    """= project_management_an.py TASK_LIST（task_list + Required packages + Shared Knowledge）"""

    task_list: list[TaskItem] = Field(default_factory=list)
    required_packages: list[str] = Field(default_factory=list)
    shared_knowledge: str = ""


class WriteTasks(BaseAction):
    output_schema = TaskList

    async def run(self, msg: Message) -> Message:
        from codeharness.report import task_block
        from langchain_core.messages import HumanMessage, SystemMessage
        store = ArtifactStore.active()
        design = await store.get(RepoName.DOCS, DocName.DESIGN_JSON) or await store.get(RepoName.DOCS, DocName.DESIGN)
        context = design.content if design else msg.content
        old = await store.get(RepoName.DOCS, DocName.TASKS)

        if old:                                   # 源 :141：旧任务表存在 → REFINED 合并
            logger.info("Requirement update detected: refining existing tasks")
            prompt = NEW_REQ_TEMPLATE.format(old_task=old.content, context=context)
            system = REFINED_TASKS_SYSTEM_PROMPT
        else:
            prompt, system = context, TASKS_SYSTEM_PROMPT

        async with task_block(role="PMManager") as rep:
            tasks: TaskList = await self.llm.structured(TaskList).ainvoke(
                [SystemMessage(content=system), HumanMessage(content=f"{self.prefix}\n{prompt}")], tag=self.name)
            await rep.content(tasks.model_dump_json())

        if not tasks.task_list:
            # 空清单会生成零条 Send：路由不报错，会话以 finished 收场却一行代码都没写（真模型实测）
            raise ValueError(f"WriteTasks 拆不出任何文件级任务，拒绝以「成功」收场。设计文档片段: {context[:200]}")

        await store.save(RepoName.DOCS, Document(filename=DocName.TASKS, content=tasks.model_dump_json()))
        await self._update_requirements(store, tasks)
        files = [t.filename for t in tasks.task_list]
        return Message(content="WBS is completed. " + ", ".join(files), role="assistant",
                       cause_by=self.name, sent_from="PMManager",
                       instruct_content=tasks.model_dump(), instruct_schema="TaskList")

    async def _update_requirements(self, store: ArtifactStore, tasks: TaskList):
        """源 :154：跨轮聚合去重写 requirements.txt（模型偶报空不该把已声明的依赖清掉）。"""
        path = store.root / PACKAGE_REQUIREMENTS_FILENAME
        existing = [p for p in path.read_text(encoding="utf-8").splitlines() if p.strip()] if path.exists() else []
        packages = sorted(set(existing) | set(tasks.required_packages))
        path.write_text("\n".join(packages) + ("\n" if packages else ""), encoding="utf-8")
