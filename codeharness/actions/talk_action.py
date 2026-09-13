"""TalkAction：源 actions/talk_action.py 语义——带角色 desc 原则与历史的纯对话动作。
Sales/CustomerService/Assistant 的对话本体；CustomerService 可选注入知识库上下文。"""
from typing import Optional
from codeharness.base.action import BaseAction
from codeharness.schema import Message


class TalkAction(BaseAction):
    kb_context: Optional[str] = None      # CustomerService：FAQ/规则库片段（第 8 步 KnowledgeBase.retrieve）

    async def run(self, msg: Message) -> Message:
        system = [self.prefix]
        if self.kb_context:
            system.append(f"[Rules & FAQs]\n{self.kb_context}")
        reply = await self.llm.aask(msg.content, system_msgs=system, tag=self.name)
        return Message(content=reply, role="assistant", cause_by=self.name)
