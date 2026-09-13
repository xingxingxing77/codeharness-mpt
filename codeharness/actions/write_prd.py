"""WritePRD。改造自 actions/write_prd.py + write_prd_an.py：
ActionNode 14 个字段 → 一个 Pydantic schema；instruction 逐条搬进 system prompt；
workspace 落盘走 ArtifactStore；竞品象限图留给前端 mermaid 渲染（不再后端转图）。"""
import json
from pydantic import BaseModel, Field
from codeharness.base.action import BaseAction
from codeharness.schema import Message, Document
from codeharness.const import RepoName, DocName
from codeharness.document_store.artifact_store import ArtifactStore

PRD_SYSTEM_PROMPT = """You are a Product Manager, write a Product Requirement Document (PRD) in json.
Fields and requirements (follow EXACTLY):
- language: "Provide the language used in the project, typically matching the user's requirement language."
- programming_language: "Mainstream programming language. If not specified in the requirements, use Vite, React, MUI, Tailwind CSS."
- original_requirements: "Place the original user's requirements here."
- project_name: 'According to the content of "original_requirements", name the project using snake case style, like "game_2048" or "simple_crm".'
- product_goals: "Provide up to three clear, orthogonal product goals."
- user_stories: "Provide up to 3 to 5 scenario-based user stories."
- competitive_analysis: "Provide 5 to 7 competitive products."
- competitive_quadrant_chart: 'Use mermaid quadrantChart syntax. Distribute scores evenly between 0 and 1'
- requirement_analysis: "Provide a detailed analysis of the requirements."
- requirement_pool: "List down the top-5 requirements with their priority (P0, P1, P2)."  # [["P0","..."],...]
- ui_design_draft: "Provide a simple description of UI elements, functions, style, and layout."
- anything_unclear: "Mention any aspects of the project that are unclear and try to clarify them."
"""

CONTEXT_TEMPLATE = """
### Project Name
{project_name}

### Original Requirements
{requirements}

### Search Information
-
"""


class PRDOutput(BaseModel):
    """= write_prd_an.py NODES 列表的 12 字段（REFINED_* 系列属增量场景，字段同构）"""
    language: str = "en_us"
    programming_language: str = ""
    original_requirements: str = ""
    project_name: str = ""
    product_goals: list[str] = Field(default_factory=list)
    user_stories: list[str] = Field(default_factory=list)
    competitive_analysis: list[str] = Field(default_factory=list)
    competitive_quadrant_chart: str = ""
    requirement_analysis: str = ""
    requirement_pool: list[list[str]] = Field(default_factory=list)
    ui_design_draft: str = ""
    anything_unclear: str = ""


class IssueType(BaseModel):
    """= WP_ISSUE_TYPE_NODE（write_prd_an.py:171-186）"""
    issue_type: str  # BUG / REQUIREMENT
    reason: str = ""


class IsRelative(BaseModel):
    """= WP_IS_RELATIVE_NODE"""
    is_relative: str  # YES / NO
    reason: str = ""


class WritePRD(BaseAction):
    output_schema = PRDOutput

    async def run(self, msg: Message) -> Message:
        from codeharness.report import docs_block
        async with docs_block("prd", role="PM") as rep:
            prd: PRDOutput = await self.llm.structured(PRDOutput).ainvoke(
                f"{self.prefix}\n\n{CONTEXT_TEMPLATE.format(project_name='', requirements=msg.content)}")
            await rep.content(prd.model_dump_json())
        store = ArtifactStore(prd.project_name or "project")
        doc = await store.save(RepoName.PRD, Document(filename=DocName.PRD, content=prd.model_dump_json()))
        await store.save(RepoName.PRD, Document(filename=DocName.PRD_MD, content=json.dumps(
            prd.model_dump(), ensure_ascii=False, indent=2)))
        return Message(content=f"PRD 已完成: {doc.root_relative_path}", role="assistant",
                       cause_by=self.name, sent_from="PM",
                       instruct_content=prd.model_dump(), instruct_schema="PRDOutput")
