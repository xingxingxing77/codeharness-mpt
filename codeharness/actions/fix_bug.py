"""FixBug。源 actions/fix_bug.py（13 行）逐字语义：**无实现细节的纯 tag 类**——
Bugfix 场景的消息路由标记（WritePRD._handle_bugfix 产出 cause_by=FixBug 的消息）。
tag 常量已在 const.RequirementTag.FIX_BUG；本文件保留类形态以对齐源结构，Engineer watch 它。"""
from codeharness.base.action import BaseAction
from codeharness.schema import Message
from codeharness.const import RequirementTag


class FixBug(BaseAction):
    """Fix bug action without any implementation details（源注释逐字）——路由标记，不执行逻辑"""

    async def run(self, msg: Message) -> Message:
        # 源项目该 Action 从不真正执行（被 watch 后由 Engineer 的 WriteCodePlanAndChange 接手）
        return Message(content=f"Bugfix 需求已受理: {msg.content[:80]}", role="assistant",
                       cause_by=RequirementTag.FIX_BUG, sent_from="Engineer")
