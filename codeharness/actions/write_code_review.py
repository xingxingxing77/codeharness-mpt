"""WriteCodeReview。改造自 actions/write_code_review.py：PROMPT_TEMPLATE/EXAMPLE_AND_INSTRUCTION/
FORMAT_EXAMPLE/REWRITE_CODE_TEMPLATE 四段 prompt 逐字搬运（:27-138）。两段流程：评审→LBTM 则重写。"""
import re
from codeharness.base.action import BaseAction
from codeharness.schema import Message, Document, CodingContext
from codeharness.const import RepoName
from codeharness.document_store.artifact_store import ArtifactStore

PROMPT_TEMPLATE = """
# System
Role: You are a professional software engineer, and your main task is to review and revise the code. You need to ensure that the code conforms to the google-style standards, is elegantly designed and modularized, easy to read and maintain.
Language: Please use the same language as the user requirement, but the title and code should be still in English. For example, if the user speaks Chinese, the specific text of your answer should also be in Chinese.
ATTENTION: Use '##' to SPLIT SECTIONS, not '#'. Output format carefully referenced "Format example".

# Context
{context}

-----

## Code to be Reviewed: {filename}
```Code
{code}
```
"""

EXAMPLE_AND_INSTRUCTION = """

{format_example}


# Instruction: Based on the actual code, follow one of the "Code Review Format example".
- Note the code filename should be `{filename}`. Return the only ONE file `{filename}` under review.

## Code Review: Ordered List. Based on the "Code to be Reviewed", provide key, clear, concise, and specific answer. If any answer is no, explain how to fix it step by step.
1. Is the code implemented as per the requirements? If not, how to achieve it? Analyse it step by step.
2. Is the code logic completely correct? If there are errors, please indicate how to correct them.
3. Does the existing code follow the "Data structures and interfaces"?
4. Are all functions implemented? If there is no implementation, please indicate how to achieve it step by step.
5. Have all necessary pre-dependencies been imported? If not, indicate which ones need to be imported
6. Are methods from other files being reused correctly?

## Actions: Ordered List. Things that should be done after CR, such as implementing class A and function B

## Code Review Result: str. If the code doesn't have bugs, we don't need to rewrite it, so answer LGTM and stop. ONLY ANSWER LGTM/LBTM.
LGTM/LBTM

"""

REWRITE_CODE_TEMPLATE = """
# Instruction: rewrite the `{filename}` based on the Code Review and Actions
## Rewrite Code: CodeBlock. If it still has some bugs, rewrite {filename} using a Markdown code block, with the filename docstring preceding the code block. Do your utmost to optimize THIS SINGLE FILE. Return all completed codes and prohibit the return of unfinished codes.
```python
## {filename}
...
```
"""


class WriteCodeReview(BaseAction):
    async def run(self, msg: Message) -> Message:
        ctx = CodingContext(**(msg.instruct_content or {}))
        store = ArtifactStore.active()
        code_doc = await store.get(RepoName.SRC, ctx.filename)
        if not code_doc:
            return Message(content=f"待评审文件不存在: {ctx.filename}", role="assistant", cause_by=self.name)
        review_rsp = await self._aask(PROMPT_TEMPLATE.format(
            context="", filename=ctx.filename, code=code_doc.content)
            + EXAMPLE_AND_INSTRUCTION.format(format_example="", filename=ctx.filename))
        lgtm = bool(re.search(r"LGTM", review_rsp.split("Code Review Result")[-1]))
        if lgtm:
            return Message(content=f"Code Review 通过（LGTM）: {ctx.filename}", role="assistant",
                           cause_by=self.name, sent_from="Engineer",
                           instruct_content={"filename": ctx.filename, "review": "LGTM"},
                           instruct_schema="WriteCodeReviewOutput")
        rewrite_rsp = await self._aask(REWRITE_CODE_TEMPLATE.format(filename=ctx.filename) +
                                       f"\n# Code Review 与 Actions\n{review_rsp}")
        from codeharness.actions.write_code import _parse_code
        fixed = _parse_code(rewrite_rsp)
        await store.save(RepoName.SRC, Document(filename=ctx.filename, content=fixed))
        return Message(content=f"Code Review 后已重写: {ctx.filename}（{len(fixed)} 字符）", role="assistant",
                       cause_by=self.name, sent_from="Engineer",
                       instruct_content={"filename": ctx.filename, "review": "LBTM→rewritten"},
                       instruct_schema="WriteCodeReviewOutput")
