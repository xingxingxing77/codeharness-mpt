"""WriteDesign。= 源 design_api.py(279) + design_api_an.py 的 `改`：
- 字段集照源 NODES（PROJECT_NAME 在源里被注释移出，项目名生成归 WritePRD）；
  instruction **逐字**取自 design_api_an.py:12-95，REFINED_* 是增量变体的另一套原文；
- 增量迭代（源 :146：旧 design 存在则走 REFINED 合并，NEW_REQ_TEMPLATE 逐字）；
- 落盘换 ArtifactStore：`design.json`（机器真源，源 system_design.json 的等价）+ `design.md`（人读）
  + 两图 `.mmd`（方案 C：不后端渲染，S8 前端消费；对应源 mermaid_to_file 的 DATA_API_DESIGN/SEQ_FLOW 两族产物）；
- 源的多 PRD 文件循环与 changed_files 记账属 ProjectRepo 制，本仓单 PRD 会话制不搬；
- `_execute_api` 同 WritePRD 判 `推迟`（per-session 边界，S7 后按会话内口径重写）。"""
from pydantic import BaseModel, Field

from codeharness.base.action import BaseAction
from codeharness.const import DocName, RepoName
from codeharness.document_store.artifact_store import ArtifactStore
from codeharness.logs import logger
from codeharness.schema import Document, Message

NEW_REQ_TEMPLATE = """
### Legacy Content
{old_design}

### New Requirements
{context}
"""

# instruction 逐字：design_api_an.py:14/34/50/69/87
DESIGN_SYSTEM_PROMPT = """You are an architect, write a system design in json.
Fields and requirements (follow EXACTLY):
- implementation_approach: "Analyze the difficult points of the requirements, select the appropriate open-source framework."
- file_list: "Only need relative paths. Succinctly designate the correct entry file for your project based on the programming language: use main.js for JavaScript, main.py for Python, and so on for other languages."
- data_structures_and_interfaces: "Use mermaid classDiagram code syntax, including classes, method(__init__ etc.) and functions with type annotations, CLEARLY MARK the RELATIONSHIPS between classes, and comply with PEP8 standards. The data structures SHOULD BE VERY DETAILED and the API should be comprehensive with a complete design."
- program_call_flow: "Use sequenceDiagram code syntax, COMPLETE and VERY DETAILED, using CLASSES AND API DEFINED ABOVE accurately, covering the CRUD AND INIT of each object, SYNTAX MUST BE CORRECT."
- anything_unclear: "Mention unclear project aspects, then try to clarify it."
"""

# 逐字：design_api_an.py:21/41/59/77 的 REFINED_* 变体（Anything UNCLEAR 无 refined 档，共用措辞）
REFINED_DESIGN_SYSTEM_PROMPT = """You are an architect, update a system design in json for incremental development.
Fields and requirements (follow EXACTLY):
- implementation_approach: "Update and extend the original implementation approach to reflect the evolving challenges and requirements due to incremental development. Outline the steps involved in the implementation process with the detailed strategies."
- file_list: "Update and expand the original file list including only relative paths. Up to 2 files can be added.Ensure that the refined file list reflects the evolving structure of the project."
- data_structures_and_interfaces: "Update and extend the existing mermaid classDiagram code syntax to incorporate new classes, methods (including __init__), and functions with precise type annotations. Delineate additional relationships between classes, ensuring clarity and adherence to PEP8 standards.Retain content that is not related to incremental development but important for consistency and clarity."
- program_call_flow: "Extend the existing sequenceDiagram code syntax with detailed information, accurately covering theCRUD and initialization of each object. Ensure correct syntax usage and reflect the incremental changes introduced by the classes and API defined above. Retain content that is not related to incremental development but important for consistency and clarity."
- anything_unclear: "Mention unclear project aspects, then try to clarify it."
"""


class DesignOutput(BaseModel):
    """= DESIGN_API_NODE 五字段（REFINED_DESIGN_NODE 字段同构，只差 prompt 措辞）"""

    implementation_approach: str = ""
    file_list: list[str] = Field(default_factory=list)
    data_structures_and_interfaces: str = ""      # mermaid classDiagram
    program_call_flow: str = ""                   # mermaid sequenceDiagram
    anything_unclear: str = ""


class WriteDesign(BaseAction):
    output_schema = DesignOutput

    async def run(self, msg: Message) -> Message:
        from codeharness.report import docs_block
        from langchain_core.messages import HumanMessage, SystemMessage
        store = ArtifactStore.active()
        prd_doc = await store.get(RepoName.PRD, DocName.PRD)     # 源 :186：上下文读 PRD 文件，不是消息转述
        context = prd_doc.content if prd_doc else msg.content
        old = await store.get(RepoName.DOCS, DocName.DESIGN_JSON)

        if old:
            logger.info("Requirement update detected: refining existing system design")
            prompt, system = (NEW_REQ_TEMPLATE.format(old_design=old.content, context=context),
                              REFINED_DESIGN_SYSTEM_PROMPT)
        else:
            prompt, system = context, DESIGN_SYSTEM_PROMPT

        async with docs_block("design", role="Architect") as rep:
            design: DesignOutput = await self.llm.structured(DesignOutput).ainvoke(
                [SystemMessage(content=system), HumanMessage(content=f"{self.prefix}\n{prompt}")],
                tag=self.name)
            await rep.content(design.model_dump_json())

        await store.save(RepoName.DOCS, Document(filename=DocName.DESIGN_JSON,
                                                 content=design.model_dump_json()))
        await store.save(RepoName.DOCS, Document(filename=DocName.DESIGN, content=self._markdown(design)))
        for name, chart in (("data_api_design.mmd", design.data_structures_and_interfaces),
                            ("seq_flow.mmd", design.program_call_flow)):
            if chart:                                           # 方案 C：源 mermaid_to_file 的落盘半边
                await store.save(RepoName.RESOURCES, Document(filename=name, content=chart))

        return Message(content="Designing is complete. " + DocName.DESIGN_JSON + "\n" + DocName.DESIGN,
                       role="assistant", cause_by=self.name, sent_from="Architect",
                       instruct_content=design.model_dump(), instruct_schema="WriteDesignOutput")

    def _markdown(self, d: DesignOutput) -> str:
        return ("## Implementation approach\n" + d.implementation_approach + "\n\n"
                "## File list\n" + "\n".join(f"- {f}" for f in d.file_list) + "\n\n"
                "## Data structures and interfaces\n```mermaid\n" + d.data_structures_and_interfaces + "\n```\n\n"
                "## Program call flow\n```mermaid\n" + d.program_call_flow + "\n```\n\n"
                "## Anything UNCLEAR\n" + d.anything_unclear + "\n")
