"""数据分析动作族：DataAnalyst / DataInterpreter 的执行单元。
WriteAnalysisCode（源 actions/di/write_analysis_code.py 语义）+ RunPythonCode（第 8 步沙箱）。"""
from codeharness.base.action import BaseAction
from codeharness.schema import Message
from codeharness.tools.sandbox import run_python_code

ANALYSIS_CODE_PROMPT = """You are a data analyst. Write ONE complete Python script (single ```python block) that:
1. Generates or loads the data described in the task (no network required — synthesize deterministic sample data).
2. Performs the requested analysis.
3. `print`s results as fixed-format lines (they will be captured as the execution result).
Task: {task}"""


class WriteAnalysisCode(BaseAction):
    async def run(self, msg: Message) -> Message:
        code = await self._aask(ANALYSIS_CODE_PROMPT.format(task=msg.content))
        from codeharness.actions.write_code import _parse_code
        return Message(content=_parse_code(code), role="assistant", cause_by=self.name, sent_from="DataAnalyst")


class RunPythonCode(BaseAction):
    """执行上一动作产出的代码（msg.content 即代码文本），返回固定格式输出"""
    async def run(self, msg: Message) -> Message:
        result = await run_python_code(msg.content)
        output = result.stdout or result.stderr
        return Message(content=f"[执行结果 rc={result.return_code}]\n{output[:4000]}",
                       role="assistant", cause_by=self.name, sent_from="DataAnalyst")
