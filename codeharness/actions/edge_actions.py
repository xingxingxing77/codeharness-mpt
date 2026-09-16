"""ExtractReadMe（源 actions/extract_readme.py：README 四要素提取）。
源面把它写进 GraphRepository——本件转 N2 示例件时补上这条入库腿（接线台账 #13，B5）。
其余五只（WriteDocstring/WriteDesignReview/WriteReview/AnalyzeRequirements/GenerateQuestions）
2026-09-16 对账后删除：源侧消费者只有自身 fire CLI 与 tests（判定表 §一 edge_actions 行改判），
留下就是把源的死分支复制成我的死分支（§一 第二条纪律，先例 summarizing.py）。"""
from pydantic import BaseModel

from codeharness.base.action import BaseAction
from codeharness.schema import Message


class ExtractReadMe(BaseAction):
    """源 extract_readme.py：从 README 提取 summary/installation/config/usages 四要素"""
    class ReadmeSummary(BaseModel):
        summary: str = ""
        installation: str = ""
        configuration: str = ""
        usages: str = ""

    output_schema = ReadmeSummary

    async def run(self, msg: Message) -> Message:
        s: "ExtractReadMe.ReadmeSummary" = await self._structured(
            f"{self.prefix}\nExtract from README:\n{msg.content[:8000]}", schema=self.ReadmeSummary)
        return Message(content=s.model_dump_json(), role="assistant", cause_by=self.name,
                       instruct_content=s.model_dump(), instruct_schema="ReadmeSummary")
