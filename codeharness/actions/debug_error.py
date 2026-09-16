"""DebugError：QA 侧修复回路。prompt 逐字搬运源 :22-47；通过判定 = return_code（源 :60 的
"Ran N tests ... OK" 正则对 pytest 恒不匹配，判弃不复活）。
写回目录照源 qa_engineer.py:155：**修复产物进 tests/ctx.test_filename**——本件是 QA 修测试；
开发码的问题不归这里，由 RunCode 按复盘 Send To 分诊具名投给 Engineer（run_code.py 的源 :124-148 段）。
回流消息 instruct 用 RunCodeContext 形态（消费方 RunCode 同 schema；extra=forbid 下多一键即炸回路）。"""
import re

from codeharness.base.action import BaseAction
from codeharness.schema import Document, Message, RunCodeContext
from codeharness.const import MESSAGE_ROUTE_TO_SELF, RepoName
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
        # 源 :155：QA 的修复产物写回 **tests/**（此前无条件进 SRC/——修测试的用例把测试码污染进源码目录）
        await store.save(RepoName.TESTS, Document(filename=ctx.test_filename, content=fixed))
        # 回流只带消费方（RunCode 重跑）schema 的键；<self> 环数上限在 team_graph 的 debug_rounds 计
        return Message(content=f"已修复测试 {ctx.test_filename}，请重跑", role="assistant",
                       cause_by=self.name, sent_from="QA",
                       send_to={MESSAGE_ROUTE_TO_SELF},
                       instruct_content={"command": ctx.command, "code_filename": ctx.code_filename,
                                         "test_filename": ctx.test_filename,
                                         "output_filename": ctx.output_filename,
                                         "working_directory": ctx.working_directory},
                       instruct_schema="RunCodeContext")
