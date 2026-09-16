"""SummarizeCode。= 源 summarize_code.py(123) 的 `改`：PROMPT_TEMPLATE/FORMAT_EXAMPLE **逐字**，
上下文改从产物仓取（源 i_context 的 design/task/filenames 由 per-session 单文件口径代替，
CodeSummarizeContext 多项目字段不搬）；源的 tenacity 重试不在此层重复——gateway._acall 统一收口。"""
from codeharness.base.action import BaseAction
from codeharness.const import DocName, RepoName
from codeharness.document_store.artifact_store import ArtifactStore
from codeharness.logs import logger
from codeharness.schema import Document, Message
from codeharness.utils.common import get_markdown_code_block_type

PROMPT_TEMPLATE = """
NOTICE
Role: You are a professional software engineer, and your main task is to review the code.
Language: Please use the same language as the user requirement, but the title and code should be still in English. For example, if the user speaks Chinese, the specific text of your answer should also be in Chinese.
ATTENTION: Use '##' to SPLIT SECTIONS, not '#'. Output format carefully referenced "Format example".

-----
# System Design
```text
{system_design}
```
-----
# Task
```text
{task}
```
-----
{code_blocks}

## Code Review All: Please read all historical files and find possible bugs in the files, such as unimplemented functions, calling errors, unreferences, etc.

## Call flow: mermaid code, based on the implemented function, use mermaid to draw a complete call chain

## Summary: Summary based on the implementation of historical files

## TODOs: Python dict[str, str], write down the list of files that need to be modified and the reasons. We will modify them later.

"""

FORMAT_EXAMPLE = """

## Code Review All

### a.py
- It fulfills less of xxx requirements...
- Field yyy is not given...
-...

### b.py
...

### c.py
...

## Call flow
```mermaid
flowchart TB
    c1-->a2
    subgraph one
    a1-->a2
    end
    subgraph two
    b1-->b2
    end
    subgraph three
    c1-->c2
    end
```

## Summary
- a.py:...
- b.py:...
- c.py:...
- ...

## TODOs
{
    "a.py": "implement requirement xxx...",
}

"""


class SummarizeCode(BaseAction):
    async def run(self, msg: Message) -> Message:
        store = ArtifactStore.active()
        design = await store.get(RepoName.DOCS, DocName.DESIGN_JSON) or await store.get(RepoName.DOCS, DocName.DESIGN)
        tasks = await store.get(RepoName.DOCS, DocName.TASKS)
        code_blocks = []
        for f in store.all_files(RepoName.SRC):
            doc = await store.get(RepoName.SRC, f)
            if doc:
                code_blocks.append(f"```{get_markdown_code_block_type(f)}\n{doc.content}\n```\n---\n")
        logger.info("Summarize code..")
        summary = await self._aask(PROMPT_TEMPLATE.format(
            system_design=design.content if design else "",
            task=tasks.content if tasks else "",
            code_blocks="\n".join(code_blocks)))
        await store.save(RepoName.DOCS, Document(filename=DocName.CODE_SUMMARY, content=summary))
        return Message(content=summary, role="assistant", cause_by=self.name, sent_from="Engineer")
