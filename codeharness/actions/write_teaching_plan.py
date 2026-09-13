"""WriteTeachingPlan：源 actions/write_teaching_plan.py 语义——可配置教学语言/母语，
分部（part by part）产出教学计划。"""
from pydantic import BaseModel, Field
from codeharness.base.action import BaseAction
from codeharness.schema import Message, Document
from codeharness.const import RepoName
from codeharness.document_store.artifact_store import ArtifactStore

TEACHING_PLAN_PROMPT = """You are a professional {teaching_language} teacher named {name}, native language {native_language}.
Write a teaching plan for "{topic}" — part by part: learning objectives, prerequisite check, lesson outline,
practice exercises, homework, and assessment criteria. Constraints: {constraints}"""


class TeachingPlan(BaseModel):
    """= 源教学计划的结构化骨架"""
    topic: str = ""
    learning_objectives: list[str] = Field(default_factory=list)
    outline: list[str] = Field(default_factory=list)
    homework: str = ""


class WriteTeachingPlan(BaseAction):
    output_schema = TeachingPlan

    async def run(self, msg: Message) -> Message:
        p: TeachingPlan = await self.llm.structured(TeachingPlan).ainvoke(
            TEACHING_PLAN_PROMPT.format(teaching_language="English", name="Lily",
                                        native_language="Chinese", topic=msg.content,
                                        constraints="writing in English"))
        doc = await ArtifactStore.active().save(
            RepoName.RESOURCES, Document(filename="teaching_plan.md", content=p.model_dump_json()))
        return Message(content=f"教学计划已完成: {doc.root_relative_path}", role="assistant",
                       cause_by=self.name, sent_from="Lily",
                       instruct_content=p.model_dump(), instruct_schema="TeachingPlan")
