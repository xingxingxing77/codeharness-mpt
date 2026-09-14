"""DebugError：修复回路核心。prompt 逐字搬运源 :22-47；通过判定 = 源 :60 的 "Ran N tests ... OK" 正则。"""
import re

from codeharness.base.action import BaseAction
from codeharness.schema import Document, Message, RunCodeContext
from codeharness.const import RepoName
from codeharness.document_store.artifact_store import ArtifactStore

PROMPT_TEMPLATE = """
NOTICE
1. Role: You are a Development Engineer or QA engineer;
2. Task: You received this message from another Development Engineer or QA engineer who ran or tested your code.
Based on the message, first, figure out your own role, i.e. Engineer or QaEngineer,
then rewrite the development code or the test code based on your role, the error, and the summary, such that all bugs are fixed and the code performs well.
Attention: Use '##' to split sections, not '#', and '## <SECTION_NAME>' SHOULD WRITE BEFORE the test case or script and triple quotes.
The message is as follows:
# Legacy Code
```python
{code}
```
---
# Unit Test Code
```python
{test_code}
```
---
# Console logs
```text
{logs}
```
---
Now you should start rewriting the code:
## file name of the code to rewrite: Write code with triple quote. Do your best to implement THIS IN ONLY ONE FILE.
"""


class DebugError(BaseAction):
    async def run(self, msg: Message) -> Message:
        ctx = RunCodeContext(**(msg.instruct_content or {}))
        store = ArtifactStore.active()
        output = await store.get(RepoName.TEST_OUTPUTS, ctx.output_filename)
        code_doc = await store.get(RepoName.SRC, ctx.code_filename)
        test_doc = await store.get(RepoName.TESTS, ctx.test_filename)
        if not (output and code_doc and test_doc):
            return Message(content="缺少修复上下文", role="assistant", cause_by=self.name)
        from codeharness.schema import RunCodeResult
        detail = RunCodeResult.model_validate_json(output.content)
        if detail.return_code == 0 or re.search(r"Ran (\d+) tests in ([\d.]+)s\n\nOK", detail.stderr):
            return Message(content="已通过，无需修复", role="assistant", cause_by=self.name)
        rsp = await self._aask(PROMPT_TEMPLATE.format(code=code_doc.content,
                                                      test_code=test_doc.content,
                                                      logs=(detail.stderr + "\n" + detail.stdout)[:4000]))
        from codeharness.actions.write_code import _parse_code
        fixed = _parse_code(rsp)
        await store.save(RepoName.SRC, Document(filename=ctx.code_filename, content=fixed))
        return Message(content=f"已修复 {ctx.code_filename}，请重跑测试", role="assistant",
                       cause_by=self.name, sent_from="Engineer",
                       instruct_content={"code_filename": ctx.code_filename,
                                         "test_filename": ctx.test_filename,
                                         "output_filename": ctx.output_filename},
                       instruct_schema="DebugOutput")
