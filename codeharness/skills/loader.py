"""SkillAction：源 metagpt/skills（Semantic Kernel 形态）的功能等价物——
skill = 一个 prompt 模板文件（{{$input}} 占位符）+ 装配器。源 skills/{WriterSkill,SummarizeSkill}
已按同构落位（writer/summarize），新增技能 = 建目录放 prompt 文件。"""
from pathlib import Path
from pydantic import PrivateAttr
from codeharness.base.action import BaseAction
from codeharness.schema import Message

SKILLS_DIR = Path(__file__).parent
INPUT_MARK = "{{$input}}"


class SkillAction(BaseAction):
    """从 skill 目录加载 prompt 并执行（对齐源 skills 的 KernelFunction 语义）"""
    _prompt_template: str = PrivateAttr(default="")

    def __init__(self, skill_name: str, **data):
        super().__init__(**data)
        self.name = f"{skill_name}Skill"
        self._prompt_template = (SKILLS_DIR / skill_name / "prompt.md").read_text(encoding="utf-8")

    async def run(self, msg: Message) -> Message:
        prompt = self._prompt_template.replace(INPUT_MARK, msg.content)
        reply = await self.llm.aask(prompt, system_msgs=[self.prefix] if self.prefix else None,
                                    tag=self.name)
        return Message(content=reply, role="assistant", cause_by=self.name)


AVAILABLE_SKILLS = ["writer", "summarize"]
