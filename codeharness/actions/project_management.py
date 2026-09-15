"""WriteTasks：设计文档 → 文件级任务列表。prompt 结构对齐源 project_management.py 的 TASK_LIST 字段。"""
from pydantic import BaseModel, Field
from codeharness.base.action import BaseAction
from codeharness.schema import Message, Document
from codeharness.const import RepoName, DocName
from codeharness.document_store.artifact_store import ArtifactStore


class TaskItem(BaseModel):
    """一条文件级任务。定成具名模型而不是 dict：`list[dict]` 等于没契约，
    structured 不会要求模型填 filename（真模型实测就是缺这个键，装配处 KeyError 崩掉整场会话）。"""

    filename: str = Field(min_length=1)     # 空串会一路传到 WriteCode，往 src 目录本身写
    task_id: str = ""
    dependent_task_ids: list[str] = Field(default_factory=list)
    instruction: str = ""


class TaskList(BaseModel):
    """= project_management_an.py TASK_LIST"""
    task_list: list[TaskItem] = Field(default_factory=list)


class WriteTasks(BaseAction):
    output_schema = TaskList

    async def run(self, msg: Message) -> Message:
        design = await ArtifactStore.active().get(RepoName.DOCS, DocName.DESIGN)
        tasks: TaskList = await self.llm.structured(TaskList).ainvoke(
            f"{self.prefix}\n按设计文档拆分文件级任务（每文件一条，标注依赖）：\n{design.content if design else msg.content}")
        content = tasks.model_dump_json()
        await ArtifactStore.active().save(RepoName.DOCS, Document(filename=DocName.TASKS, content=content))
        files = [t.filename for t in tasks.task_list]
        if not files:
            # 空清单会生成零条 Send：路由不报错，会话以 finished 收场却一行代码都没写（真模型实测）
            raise ValueError(f"WriteTasks 拆不出任何文件级任务，拒绝以「成功」收场。"
                             f"设计文档片段: {(design.content if design else msg.content)[:200]}")
        return Message(content="任务拆解完成: " + ", ".join(files), role="assistant",
                       cause_by=self.name, sent_from="PMManager",
                       instruct_content=tasks.model_dump(), instruct_schema="TaskList")
