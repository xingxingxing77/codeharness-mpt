"""WritePRDReview。源 actions/write_prd_review.py（31 行）逐字语义：PM 视角评审 PRD 并给反馈。"""
from codeharness.base.action import BaseAction
from codeharness.schema import Message

PRD_REVIEW_PROMPT_TEMPLATE = """
Given the following Product Requirement Document (PRD):
{prd}

As a project manager, please review it and provide your feedback and suggestions.
"""


class WritePRDReview(BaseAction):
    async def run(self, msg: Message) -> Message:
        review = await self._aask(PRD_REVIEW_PROMPT_TEMPLATE.format(prd=msg.content))
        return Message(content=review, role="assistant", cause_by=self.name, sent_from="PMManager")
