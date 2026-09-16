"""WritePRD。= 源 write_prd.py(325) + write_prd_an.py 的 `改`：
三分支业务流照源（bugfix / 新建 / 需求增量），上下层换成新栈：
- ActionNode → `structured`（判定节点的 instruction **逐字**取自 write_prd_an.py:171-186）；
- ProjectRepo/FileRepository → ArtifactStore（产物名一律引 const，禁止内联字面量）；
- `mermaid_to_file` 后端渲染 → **方案 C**（施工4 拍板，2026-09-16）：象限图以 `.mmd` 落 resources，
  渲染与导出在前端（S8/N6）；
- DocsReporter(enable_llm_stream) → docs_block（本仓统一用块上下文管理器，不改回类名形态——
  施工3 批2 第 4 条「两者选一但必须显式」，选的是改调用点）；
- `_execute_api`（register_tool 的独立 API 形态）判 `推迟`：它的出口是 output_pathname 任意路径，
  与 per-session 强制边界（S7）冲突；N2 扩展点要工具面时再按会话内口径重写。
源里 workspace 改名（_rename_workspace + rename_root）在 per-session 目录制下没有对应物：
project_name 只进产物字段与报道，不动会话目录。"""
import json

from pydantic import BaseModel, Field

from codeharness.base.action import BaseAction
from codeharness.const import BUGFIX_FILENAME, DocName, RepoName, RequirementTag
from codeharness.document_store.artifact_store import ArtifactStore
from codeharness.logs import logger
from codeharness.schema import Document, Message

CONTEXT_TEMPLATE = """
### Project Name
{project_name}

### Original Requirements
{requirements}

### Search Information
-
"""

NEW_REQ_TEMPLATE = """
### Legacy Content
{old_prd}

### New Requirements
{requirements}
"""

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


class PRDOutput(BaseModel):
    """= write_prd_an.py NODES/REFINED_NODES 的 12 字段（REFINED_* 与 WRITE_PRD_* 字段同构，共用本 schema）"""

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
    """= WP_ISSUE_TYPE_NODE（write_prd_an.py:171）；instruction 逐字进 field description，
    structured 走 JSON schema，模型看到的就是源那句话。"""

    issue_type: str = Field(description="Answer BUG/REQUIREMENT. If it is a bugfix, answer BUG, otherwise answer Requirement")
    reason: str = Field(default="", description="Explain the reasoning process from question to answer")


class IsRelative(BaseModel):
    """= WP_IS_RELATIVE_NODE（write_prd_an.py:178）"""

    is_relative: str = Field(description="Answer YES/NO. If the requirement is related to the old PRD, answer YES, otherwise NO")
    reason: str = Field(default="", description="Explain the reasoning process from question to answer")


class WritePRD(BaseAction):
    """源 docstring 的三情形照抄语义：Bugfix / New requirement / Requirement update。"""

    output_schema = PRDOutput

    async def run(self, msg: Message) -> Message:
        store = ArtifactStore.active()
        old = await store.get(RepoName.PRD, DocName.PRD)

        if store.all_files(RepoName.SRC) and await self._is_bugfix(msg.content):
            return await self._handle_bugfix(store, msg)

        if old and await self._is_related(msg.content, old):
            logger.info(f"Requirement update detected: {msg.content[:80]}")
            prd = await self._merge(store, msg, old)
            changed = [DocName.PRD]
        else:
            logger.info(f"New requirement detected: {msg.content[:80]}")
            prd = await self._new_prd(store, msg)
            changed = [DocName.PRD]

        return Message(content="PRD is completed. " + "\n".join(changed), role="assistant",
                       cause_by=self.name, sent_from="PM",
                       instruct_content=prd.model_dump(), instruct_schema="WritePRDOutput")

    async def _is_bugfix(self, context: str) -> bool:
        """源 :238：只在已有代码产物时才问模型（无代码就没有"修"的对象）——省一次真调用，照源条件。"""
        it: IssueType = await self.llm.structured(IssueType).ainvoke(
            f"{self.prefix}\n{context}", tag=self.name)
        return it.issue_type.upper() == "BUG"

    async def _handle_bugfix(self, store: ArtifactStore, msg: Message) -> Message:
        """源 :191：新 issue 落 docs/bugfix → cause_by=FixBug 指名给 Engineer（本仓走 SOP 的 FIX_BUG 边）。"""
        await store.save(RepoName.DOCS, Document(filename=BUGFIX_FILENAME, content=msg.content))
        return Message(content=f"A new issue is received: {BUGFIX_FILENAME}", role="assistant",
                       cause_by=RequirementTag.FIX_BUG, sent_from="PM",
                       instruct_content={"issue_filename": BUGFIX_FILENAME},
                       instruct_schema="IssueDetail")

    async def _is_related(self, requirement: str, old_prd: Document) -> bool:
        rel: IsRelative = await self.llm.structured(IsRelative).ainvoke(
            f"{self.prefix}\n{NEW_REQ_TEMPLATE.format(old_prd=old_prd.content, requirements=requirement)}",
            tag=self.name)
        return rel.is_relative.upper() == "YES"

    def _ask(self, schema, user_prompt: str):
        """system=PRD_SYSTEM_PROMPT、user=业务上下文：structured 吃消息列表，
        system prompt 不再像旧版那样只定义不消费（死资产）。"""
        from langchain_core.messages import HumanMessage, SystemMessage
        return self.llm.structured(schema).ainvoke(
            [SystemMessage(content=PRD_SYSTEM_PROMPT),
             HumanMessage(content=f"{self.prefix}\n{user_prompt}")], tag=self.name)

    async def _new_prd(self, store: ArtifactStore, msg: Message) -> PRDOutput:
        from codeharness.report import docs_block
        async with docs_block("prd", role="PM") as rep:
            prd: PRDOutput = await self._ask(
                PRDOutput, CONTEXT_TEMPLATE.format(project_name="", requirements=msg.content))
            await rep.content(prd.model_dump_json())
        await self._save(store, prd)
        return prd

    async def _merge(self, store: ArtifactStore, msg: Message, old: Document) -> PRDOutput:
        """源 _merge(:254) + _update_prd：REFINED_PRD 用同一组字段、NEW_REQ_TEMPLATE 做底。"""
        from codeharness.report import docs_block
        async with docs_block("prd-update", role="PM") as rep:
            refined: PRDOutput = await self._ask(
                PRDOutput, NEW_REQ_TEMPLATE.format(old_prd=old.content, requirements=msg.content))
            await rep.content(refined.model_dump_json())
        await self._save(store, refined)
        return refined

    async def _save(self, store: ArtifactStore, prd: PRDOutput):
        """PRD json + 人读 md（源 save_pdf 的 C 方案替身）+ 象限图 `.mmd`（不渲染，前端消费）。"""
        await store.save(RepoName.PRD, Document(filename=DocName.PRD, content=prd.model_dump_json()))
        await store.save(RepoName.PRD, Document(filename=DocName.PRD_MD, content=json.dumps(
            prd.model_dump(), ensure_ascii=False, indent=2)))
        if prd.competitive_quadrant_chart:      # 源 :273 _save_competitive_analysis 的落盘半边
            await store.save(RepoName.RESOURCES, Document(
                filename="competitive_analysis.mmd", content=prd.competitive_quadrant_chart))
