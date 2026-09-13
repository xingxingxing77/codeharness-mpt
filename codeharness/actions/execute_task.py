"""ExecuteTask。源 actions/execute_task.py（19 行）逐字语义：占位 Action（run: pass）——
源项目用于 ProjectManager 的任务执行占位，实际执行由 Engineer 的任务循环承担。"""
from codeharness.base.action import BaseAction
from codeharness.schema import Message


class ExecuteTask(BaseAction):
    async def run(self, *args, **kwargs):
        pass        # 源 :18 同款：占位无实现

