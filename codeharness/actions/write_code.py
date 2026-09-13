"""WriteCode。改造自 actions/write_code.py：PROMPT_TEMPLATE(:34-85) 逐字保留；
get_codes(:168) 的"排除自身文件"语义保留；EditorReporter 换 editor_block 报道。"""
from pathlib import Path
from codeharness.base.action import BaseAction
from codeharness.schema import Message, Document, CodingContext
from codeharness.const import RepoName, DocName
from codeharness.document_store.artifact_store import ArtifactStore

PROMPT_TEMPLATE = """
NOTICE
Role: You are a professional engineer; the main goal is to write google-style, elegant, modular, easy to read and maintain code
Language: Please use the same language as the user requirement, but the title and code should be still in English. For example, if the user speaks Chinese, the specific text of your answer should also be in Chinese.
ATTENTION: Use '##' to SPLIT SECTIONS, not '#'. Output format carefully referenced "Format example".

# Context
## Design
{design}

## Task
{task}

## Legacy Code
{code}

## Debug logs
```text
{logs}

{summary_log}
```

## Bug Feedback logs
```text
{feedback}
```

# Format example
## Code: {demo_filename}.py
```python
## {demo_filename}.py
...
```

# Instruction: Based on the context, follow "Format example", write code.

## Code: {filename}. Write code with triple quote, based on the following attentions and context.
1. Only One file: do your best to implement THIS ONLY ONE FILE.
2. COMPLETE CODE: Your code will be part of the entire project, so please implement complete, reliable, reusable code snippets.
3. Set default value: If there is any setting, ALWAYS SET A DEFAULT VALUE, ALWAYS USE STRONG TYPE AND EXPLICIT VARIABLE. AVOID circular import.
4. Follow design: YOU MUST FOLLOW "Data structures and interfaces". DONT CHANGE ANY DESIGN. Do not use public member functions that do not exist in your design.
5. CAREFULLY CHECK THAT YOU DONT MISS ANY NECESSARY CLASS/FUNCTION IN THIS FILE.
6. Before using a external variable/module, make sure you import it first.
7. Write out EVERY CODE DETAIL, DON'T LEAVE TODO.

"""


class WriteCode(BaseAction):
    async def run(self, msg: Message) -> Message:
        ctx = CodingContext(**(msg.instruct_content or {}))
        store = ArtifactStore.active()
        design = (await store.get(RepoName.DOCS, DocName.DESIGN)) or Document(content="")
        tasks = (await store.get(RepoName.DOCS, DocName.TASKS)) or Document(content="")
        # 源 get_codes(:168)：同项目其他文件的代码作为上下文，排除当前文件
        others = "\n".join(f"### File Name: `{f}`\n```\n{(await store.get(RepoName.SRC, f)).content}\n```\n"
                           for f in store.all_files(RepoName.SRC) if f != ctx.filename)
        prompt = PROMPT_TEMPLATE.format(design=design.content, task=tasks.content, code=others,
                                        logs="", summary_log="", feedback="",
                                        filename=ctx.filename, demo_filename=Path(ctx.filename).stem)
        rsp = await self._aask(prompt)
        code = _parse_code(rsp)                                  # 见文末工具函数
        code_doc = await store.save(RepoName.SRC, Document(filename=ctx.filename, content=code))
        return Message(content=f"已写 {ctx.filename}（{len(code)} 字符）", role="assistant",
                       cause_by=self.name, sent_from="Engineer",
                       instruct_content={"filename": ctx.filename}, instruct_schema="WriteCodeOutput")


def _parse_code(text: str) -> str:
    """= 源 utils/common.py CodeParser.parse_code：取最后一个 ``` 围栏体"""
    import re
    blocks = re.findall(r"```[a-zA-Z]*\n(.*?)```", text, re.DOTALL)
    return blocks[-1].strip() if blocks else text.strip()
