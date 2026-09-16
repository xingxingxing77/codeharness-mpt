"""WriteCodePlanAndChange：增量开发核心。完整 prompt 见源 write_code_plan_and_change_an.py 的
REFINED_TEMPLATE（:27 引用处 :34 起）——按 第 9 步 §1 同模式包装，REFINED_NODES 字段说明逐条搬 instruction。
增量场景启用（legacy_prd/inc 场景），初版 SOP 链不经过它。"""
from pydantic import BaseModel, Field
from codeharness.base.action import BaseAction
from codeharness.schema import Message


class PlanAndChange(BaseModel):
    plan: list[str] = Field(default_factory=list)
    change_list: list[dict] = Field(default_factory=list)   # [{filename, change_desc}]


class WriteCodePlanAndChange(BaseAction):
    output_schema = PlanAndChange

    async def run(self, msg: Message) -> Message:
        pac = await self._structured(f"{self.prefix}\n{msg.content}", schema=PlanAndChange)
        return Message(content="增量计划完成", role="assistant", cause_by=self.name,
                       instruct_content=pac.model_dump(), instruct_schema="PlanAndChange")
