"""WriteCodeReview。= 源 write_code_review.py(315) 的 `改`：四段 prompt 常量由构建脚本自源**逐字节摘取**
（第一版手抄漏了 FORMAT_EXAMPLE 整段、REWRITE 模板少了 js 分支——教训：逐字件不手抄，用脚本搬）；
业务流照源 `run`：k 轮 评审→(LBTM 就地重写)→复审，`k = settings.code_validate_k_times`；
上下层换成新栈：ActionNode/ProjectRepo → ArtifactStore + `build_code_context`（与 WriteCode 共用），
EditorReporter → store.save 自带 Editor 块报道。源的"改完只回内存、由 Role 落盘"改为本件每轮即存
——per-session 产物是崩溃续跑的凭据，这处偏离记录在施工3。"""
from codeharness.base.action import BaseAction
from codeharness.configs.settings import settings
from codeharness.const import DocName, RepoName
from codeharness.document_store.artifact_store import ArtifactStore
from codeharness.logs import logger
from codeharness.schema import CodingContext, Document, Message
from codeharness.actions.write_code import _parse_code, build_code_context
from codeharness.utils.common import CodeParser

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

FORMAT_EXAMPLE = """
-----

# Code Review Format example 1
## Code Review: {filename}
1. No, we should fix the logic of class A due to ...
2. ...
3. ...
4. No, function B is not implemented, ...
5. ...
6. ...

## Actions
1. Fix the `handle_events` method to update the game state only if a move is successful.
   ```python
   def handle_events(self):
       for event in pygame.event.get():
           if event.type == pygame.QUIT:
               return False
           if event.type == pygame.KEYDOWN:
               moved = False
               if event.key == pygame.K_UP:
                   moved = self.game.move('UP')
               elif event.key == pygame.K_DOWN:
                   moved = self.game.move('DOWN')
               elif event.key == pygame.K_LEFT:
                   moved = self.game.move('LEFT')
               elif event.key == pygame.K_RIGHT:
                   moved = self.game.move('RIGHT')
               if moved:
                   # Update the game state only if a move was successful
                   self.render()
       return True
   ```
2. Implement function B

## Code Review Result
LBTM

-----

# Code Review Format example 2
## Code Review: {filename}
1. Yes.
2. Yes.
3. Yes.
4. Yes.
5. Yes.
6. Yes.

## Actions
pass

## Code Review Result
LGTM

-----
"""

REWRITE_CODE_TEMPLATE = """
# Instruction: rewrite the `{filename}` based on the Code Review and Actions
## Rewrite Code: CodeBlock. If it still has some bugs, rewrite {filename} using a Markdown code block, with the filename docstring preceding the code block. Do your utmost to optimize THIS SINGLE FILE. Return all completed codes and prohibit the return of unfinished codes.
```python
## {filename}
...
```
or
```javascript
// {filename}
...
```
"""

class WriteCodeReview(BaseAction):
    async def run(self, msg: Message) -> Message:
        ctx = CodingContext(**(msg.instruct_content or {}))
        if not ctx.filename:
            return Message(content="[缺少任务上下文] WriteCodeReview 未收到 filename，本轮跳过。",
                           role="assistant", cause_by=self.name, sent_from="Engineer")
        store = ArtifactStore.active()
        code_doc = await store.get(RepoName.SRC, ctx.filename)
        if not code_doc:
            return Message(content=f"待评审文件不存在: {ctx.filename}", role="assistant", cause_by=self.name)
        design = (await store.get(RepoName.DOCS, DocName.DESIGN_JSON)
                  or await store.get(RepoName.DOCS, DocName.DESIGN))
        tasks = await store.get(RepoName.DOCS, DocName.TASKS)
        k = max(1, settings.code_validate_k_times)
        iterative = code_doc.content
        for i in range(k):
            context = "\n".join([
                "## System Design\n" + (design.content if design else ""),
                "## Task\n" + (tasks.content if tasks else ""),
                "## Code Files\n" + await build_code_context(store, ctx.filename),
            ])
            context_prompt = PROMPT_TEMPLATE.format(context=context, code=iterative, filename=ctx.filename)
            logger.info(f"Code review and rewrite {ctx.filename}: {i + 1}/{k}")
            cr_rsp = await self._aask(context_prompt + EXAMPLE_AND_INSTRUCTION.format(
                format_example=FORMAT_EXAMPLE.format(filename=ctx.filename), filename=ctx.filename))
            result = CodeParser.parse_block("Code Review Result", cr_rsp)
            if "LGTM" in result:
                if iterative != code_doc.content:          # 前几轮重写过的内容要落住（源由 Role 收尾存，本件即存）
                    await store.save(RepoName.SRC, Document(filename=ctx.filename, content=iterative))
                return Message(content=f"Code Review 通过（LGTM 第{i + 1}轮）: {ctx.filename}",
                               role="assistant", cause_by=self.name, sent_from="Engineer",
                               instruct_content={"filename": ctx.filename, "review": "LGTM"},
                               instruct_schema="WriteCodeReviewOutput")
            rewrite_rsp = await self._aask(
                f"{context_prompt}\n{cr_rsp}\n" + REWRITE_CODE_TEMPLATE.format(filename=ctx.filename))
            fixed = _parse_code(rewrite_rsp)
            iterative = fixed or iterative                 # 解析不出代码就保留上一版，不写空
            await store.save(RepoName.SRC, Document(filename=ctx.filename, content=iterative))
        return Message(content=f"Code Review {k} 轮后仍 LBTM，已按最后一轮意见重写: {ctx.filename}",
                       role="assistant", cause_by=self.name, sent_from="Engineer",
                       instruct_content={"filename": ctx.filename, "review": "LBTM->rewritten"},
                       instruct_schema="WriteCodeReviewOutput")
