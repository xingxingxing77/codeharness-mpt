"""边缘 Action 六件（源 actions/ 同名文件的功能对齐，prompt 语义逐条对应）：
WriteDocstring / WriteDesignReview / WriteReview / ExtractReadMe / AnalyzeRequirements / GenerateQuestions"""
from typing import ClassVar
from pydantic import BaseModel, Field
from codeharness.base.action import BaseAction
from codeharness.schema import Message


class WriteDocstring(BaseAction):
    """源 write_docstring.py：为代码补 docstring（输入代码 → 输出带 docstring 的代码）"""
    DOCSTRING_PROMPT: ClassVar[str] = """Add Google-style docstrings (module/class/function, keep code logic unchanged)
to the following code. Output the full code in ONE ```python block:\n{code}"""

    async def run(self, msg: Message) -> Message:
        rsp = await self._aask(self.DOCSTRING_PROMPT.format(code=msg.content))
        from codeharness.actions.write_code import _parse_code
        return Message(content=_parse_code(rsp), role="assistant", cause_by=self.name)


class WriteDesignReview(BaseAction):
    """源 design_api_review.py：对系统设计文档做评审反馈"""
    PROMPT: ClassVar[str] = """Given the following system design:
{design}

As an architect, review it and provide clear, detailed feedback and suggestions."""

    async def run(self, msg: Message) -> Message:
        review = await self._aask(self.PROMPT.format(design=msg.content))
        return Message(content=review, role="assistant", cause_by=self.name, sent_from="Architect")


class WriteReview(BaseAction):
    """源 write_review.py：通用文档/代码评审"""
    PROMPT: ClassVar[str] = """Review the following content. Output: 1) 总体评价 2) 优点 3) 问题清单（按严重度）
4) 修改建议。{extra}Content:\n{content}"""

    async def run(self, msg: Message) -> Message:
        review = await self._aask(self.PROMPT.format(content=msg.content, extra=""))
        return Message(content=review, role="assistant", cause_by=self.name)


class ExtractReadMe(BaseAction):
    """源 extract_readme.py：从 README 提取 summary/installation/config/usages 四要素
    （源入 graph 仓库，此处结构化返回）"""
    class ReadmeSummary(BaseModel):
        summary: str = ""
        installation: str = ""
        configuration: str = ""
        usages: str = ""

    output_schema = ReadmeSummary

    async def run(self, msg: Message) -> Message:
        s: "ExtractReadMe.ReadmeSummary" = await self.llm.structured(self.ReadmeSummary).ainvoke(
            f"{self.prefix}\nExtract from README:\n{msg.content[:8000]}")
        return Message(content=s.model_dump_json(), role="assistant", cause_by=self.name,
                       instruct_content=s.model_dump(), instruct_schema="ReadmeSummary")


class AnalyzeRequirements(BaseAction):
    """源 analyze_requirements.py：需求 → 分析后的需求要点清单"""
    class RequirementAnalysis(BaseModel):
        analysis: list[str] = Field(default_factory=list)

    output_schema = RequirementAnalysis

    async def run(self, msg: Message) -> Message:
        r: "AnalyzeRequirements.RequirementAnalysis" = await self.llm.structured(
            self.RequirementAnalysis).ainvoke(
            f"{self.prefix}\nAnalyze the requirement into actionable points:\n{msg.content}")
        return Message(content="\n".join(r.analysis), role="assistant", cause_by=self.name,
                       instruct_content=r.model_dump(), instruct_schema="RequirementAnalysis")


class GenerateQuestions(BaseAction):
    """源 generate_questions.py（QUESTIONS node instruction 逐字）：基于上下文挖掘追问"""
    class Questions(BaseModel):
        questions: list[str] = Field(default_factory=list)

    output_schema = Questions

    async def run(self, msg: Message) -> Message:
        instruction = ("Task: Refer to the context to further inquire about the details that interest you, "
                       "within a word limit of 150 words. Please provide the specific details you would like "
                       "to inquire about here")
        q: "GenerateQuestions.Questions" = await self.llm.structured(self.Questions).ainvoke(
            f"{instruction}\n\nContext:\n{msg.content[:6000]}")
        return Message(content="\n".join(q.questions), role="assistant", cause_by=self.name,
                       instruct_content=q.model_dump(), instruct_schema="Questions")
