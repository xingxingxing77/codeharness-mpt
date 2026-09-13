"""WriteDesign。改造自 actions/design_api.py + design_api_an.py：
5 个字段（Implementation approach/Project name/File list/Data structures and interfaces/
Program call flow）→ 一个 Pydantic schema，instruction 逐条搬运；mermaid 图留给前端渲染。"""
from pydantic import BaseModel, Field
from codeharness.base.action import BaseAction
from codeharness.schema import Message, Document
from codeharness.const import RepoName, DocName
from codeharness.document_store.artifact_store import ArtifactStore

DESIGN_SYSTEM_PROMPT = """You are an architect, write a system design in json.
Fields and requirements (follow EXACTLY):
- implementation_approach: "Analyze the difficult points of the requirements, select the appropriate open-source framework."
- project_name: "The project name with underline"
- file_list: "Only need relative paths. Succinctly designate the correct entry file for your project based on the programming language: use main.js for JavaScript, main.py for Python, and so on for other languages."
- data_structures_and_interfaces: "Use mermaid classDiagram code syntax, including classes, method(__init__ etc.) and functions with type"
- program_call_flow: "Use mermaid sequenceDiagram code syntax, including sequence of calls between classes and functions"
"""


class DesignOutput(BaseModel):
    """= design_api_an.py DESIGN_API_NODE 的 5 字段（REFINED_* 属增量场景，字段同构）"""
    implementation_approach: str = ""
    project_name: str = ""
    file_list: list[str] = Field(default_factory=list)
    data_structures_and_interfaces: str = ""      # mermaid classDiagram
    program_call_flow: str = ""                   # mermaid sequenceDiagram


class WriteDesign(BaseAction):
    output_schema = DesignOutput

    async def run(self, msg: Message) -> Message:
        from codeharness.report import docs_block
        async with docs_block("design", role="Architect") as rep:
            design: DesignOutput = await self.llm.structured(DesignOutput).ainvoke(
                f"{self.prefix}\n\nPRD:\n{msg.content}\n"
                f"（instruct_content 附加信息：{msg.instruct_content or '无'}）")
            await rep.content(design.data_structures_and_interfaces)
        store = ArtifactStore.active()
        md = (f"# {design.project_name} System Design\n\n"
              f"## Implementation approach\n{design.implementation_approach}\n\n"
              f"## File list\n" + "\n".join(f"- {f}" for f in design.file_list) + "\n\n"
              f"## Data structures and interfaces\n```mermaid\n{design.data_structures_and_interfaces}\n```\n\n"
              f"## Program call flow\n```mermaid\n{design.program_call_flow}\n```\n")
        await store.save(RepoName.DOCS, Document(filename=DocName.DESIGN, content=md))
        return Message(content=f"系统设计已完成: {DocName.DESIGN}（文件清单: {', '.join(design.file_list)}）",
                       role="assistant", cause_by=self.name, sent_from="Architect",
                       instruct_content=design.model_dump(), instruct_schema="DesignOutput")
