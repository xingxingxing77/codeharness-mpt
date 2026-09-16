"""RunCode：执行测试命令 + **LLM 复盘**（源 run_code.py 语义：跑完必须过一遍模型，产出
File To Rewrite / Status / Send To 三段，DebugError 与路由靠它）。执行本体在第 8 步
sandbox.run_context（additional_python_paths→PYTHONPATH、workdir、超时按进程树杀都在那）。
源 mode=text 的 in-process exec 判 `推迟`：本仓沙箱纪律是只走子进程，text 面随 2d 数据系工具再议。"""
import sys

from codeharness.base.action import BaseAction
from codeharness.const import MESSAGE_ROUTE_TO_SELF, RepoName
from codeharness.document_store.artifact_store import ArtifactStore
from codeharness.logs import logger
from codeharness.schema import Document, Message, RunCodeContext, RunCodeResult
from codeharness.tools.sandbox import run_context

PROMPT_TEMPLATE = """
Role: You are a senior development and qa engineer, your role is summarize the code running result.
If the running result does not include an error, you should explicitly approve the result.
On the other hand, if the running result indicates some error, you should point out which part, the development code or the test code, produces the error,
and give specific instructions on fixing the errors. Here is the code info:
{context}
Now you should begin your analysis
---
## instruction:
Please summarize the cause of the errors and give correction instruction
## File To Rewrite:
Determine the ONE file to rewrite in order to fix the error, for example, xyz.py, or test_xyz.py
## Status:
Determine if all of the code works fine, if so write PASS, else FAIL,
WRITE ONLY ONE WORD, PASS OR FAIL, IN THIS SECTION
## Send To:
Please write NoOne if there are no errors, Engineer if the errors are due to problematic development codes, else QaEngineer,
WRITE ONLY ONE WORD, NoOne OR Engineer OR QaEngineer, IN THIS SECTION.
---
You should fill in necessary instruction, status, send to, and finally return all content between the --- segment line.
"""

TEMPLATE_CONTEXT = """
## Development Code File Name
{code_file_name}
## Development Code
```python
{code}
```
## Test File Name
{test_file_name}
## Test Code
```python
{test_code}
```
## Running Command
{command}
## Running Output
standard output: 
```text
{outs}
```
standard errors: 
```text
{errs}
```
"""


class RunCode(BaseAction):
    async def run(self, msg: Message) -> Message:
        ctx = RunCodeContext(**(msg.instruct_content or {}))
        store = ArtifactStore.active()
        ctx.working_directory = ctx.working_directory or str(store.root)
        # sys.executable：保证用当前解释器（PATH 上的 python 可能没装 pytest）
        ctx.command = ctx.command or [sys.executable, "-m", "pytest", "tests", "-x", "--tb=short"]
        logger.info(f"Running {' '.join(ctx.command)}")
        result = await run_context(ctx)
        # pytest 报告在 stdout、unittest 在 stderr——合并存档；ok 以 return_code 为准（格式无关，
        # 源对 pytest 恒不匹配的 "Ran N tests OK" 正则判 `弃`，不复活）
        combined = RunCodeResult(stdout=result.stdout, stderr=(result.stderr + "\n" + result.stdout).strip(),
                                 return_code=result.return_code)
        code_doc = await store.get(RepoName.SRC, ctx.code_filename)
        test_doc = await store.get(RepoName.TESTS, ctx.test_filename)
        try:                                    # 复盘是增值段：模型挂了不能把跑完的测试结果也吞了
            combined.summary = await self._aask(PROMPT_TEMPLATE.format(context=TEMPLATE_CONTEXT.format(
                code_file_name=ctx.code_filename, code=(code_doc.content if code_doc else ctx.code or ""),
                test_file_name=ctx.test_filename, test_code=(test_doc.content if test_doc else ctx.test_code or ""),
                command=" ".join(ctx.command), outs=combined.stdout[:500], errs=combined.stderr[:10000])))
        except Exception as e:
            logger.warning(f"RunCode 复盘失败，按无摘要存档: {type(e).__name__}: {e}")
        out_name = ctx.output_filename or f"output_{ctx.test_filename or 'run'}.json"
        await store.save(RepoName.TEST_OUTPUTS, Document(filename=out_name, content=combined.model_dump_json()))
        ok = combined.return_code == 0
        return Message(content=(f"测试通过\n{combined.summary}" if ok
                                else f"测试失败:\n{combined.stderr[:3000]}\n{combined.summary}"),
                       role="assistant", cause_by=self.name, sent_from="QA",
                       send_to={MESSAGE_ROUTE_TO_SELF} if ok else set(),
                       instruct_content={"output_filename": out_name, "ok": ok,     # DebugError 修复回路透传
                                         "code_filename": ctx.code_filename,
                                         "test_filename": ctx.test_filename},
                       instruct_schema="RunCodeOutput")
