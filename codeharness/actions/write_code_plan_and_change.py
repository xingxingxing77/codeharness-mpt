"""WriteCodePlanAndChange：增量/重写链的规划引擎。判定 `改`（源 write_code_plan_and_change_an.py, 240 行）：
- 源 DEVELOPMENT_PLAN/INCREMENTAL_CHANGE 两 ActionNode → structured 字段，**instruction 逐字**进
  field description（WriteDesign/WritePRD 同款口径）；源两段 EXAMPLE（长 diff few-shot）不进 JSON
  schema——structured 吃不了 few-shot，git-diff 标记语义由 REFINED_TEMPLATE 第 5 条 instruction 保留；
- CODE_PLAN_AND_CHANGE_CONTEXT 六段与 REFINED_TEMPLATE **逐字**（构建脚本自源 AST 摘取，t11 值比对钉）；
- ProjectRepo/Document.load → ArtifactStore；get_old_codes 的 "### File Name + 围栏" 形态保留；
- 生产者与消费者（接线台账 #5）：FIX_BUG 工单 → Engineer 先产本件（plans 装配表，team/registry 两张）
  → 输出携带 code_plan_and_change_doc 的 CodingContext → WriteCode 据此走 REFINED 重写分支
  （源 config.inc 是 CLI 旗标，平台无对应物——"重写计划在"即本仓的 inc 等价信号，已注明）。
"""
from pydantic import BaseModel, Field

from codeharness.base.action import BaseAction
from codeharness.const import BUGFIX_FILENAME, DocName, RepoName
from codeharness.document_store.artifact_store import ArtifactStore
from codeharness.logs import logger
from codeharness.schema import Document, Message

DEV_PLAN_INSTRUCTION = "Develop a comprehensive and step-by-step incremental development plan, providing the detail changes to be implemented at each step based on the order of 'Task List'"

INC_CHANGE_INSTRUCTION = 'Write Incremental Change by making a code draft that how to implement incremental development including detailed steps based on the context. Note: Track incremental changes using the marks `+` and `-` to indicate additions and deletions, and ensure compliance with the output format of `git diff`'

CODE_PLAN_AND_CHANGE_CONTEXT = """
## User New Requirements
{requirement}

## Issue
{issue}

## PRD
{prd}

## Design
{design}

## Task
{task}

## Legacy Code
{code}
"""

REFINED_TEMPLATE = """
NOTICE
Role: You are a professional engineer; The main goal is to complete incremental development by combining legacy code and plan and Incremental Change, ensuring the integration of new features.

# Context
## User New Requirements
{user_requirement}

## Code Plan And Change
{code_plan_and_change}

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
## Code: {demo_filename}.js
```javascript
// {demo_filename}.js
...
```

# Instruction: Based on the context, follow "Format example", write or rewrite code.
## Write/Rewrite Code: Only write one file {filename}, write or rewrite complete code using triple quotes based on the following attentions and context.
1. Only One file: do your best to implement THIS ONLY ONE FILE.
2. COMPLETE CODE: Your code will be part of the entire project, so please implement complete, reliable, reusable code snippets.
3. Set default value: If there is any setting, ALWAYS SET A DEFAULT VALUE, ALWAYS USE STRONG TYPE AND EXPLICIT VARIABLE. AVOID circular import.
4. Follow design: YOU MUST FOLLOW "Data structures and interfaces". DONT CHANGE ANY DESIGN. Do not use public member functions that do not exist in your design.
5. Follow Code Plan And Change: If there is any "Incremental Change" that is marked by the git diff format with '+' and '-' symbols, or Legacy Code files contain "{filename} to be rewritten", you must merge it into the code file according to the "Development Plan". 
6. CAREFULLY CHECK THAT YOU DONT MISS ANY NECESSARY CLASS/FUNCTION IN THIS FILE.
7. Before using a external variable/module, make sure you import it first.
8. Write out EVERY CODE DETAIL, DON'T LEAVE TODO.
9. Attention: Retain details that are not related to incremental development but are important for maintaining the consistency and clarity of the old code.
"""

# 源 :218-219 实际赋值进 llm.system_prompt 的只有这半句：赋值行后的第二段字符串在源里是
# 独立表达式语句，没被任何对象吃到——源自身的死分支不复制（§一 第二条纪律同样管 `改` 面）。
SYSTEM_PROMPT = ("You are a professional software engineer, your primary responsibility is to ")

# 源的三处 markdown 头/围栏字面（get_codes :207-211、get_old_codes :237），集中成常量少踩转义
FILE_HEAD = "### File Name: `{file}`\n"
REWRITE_HEAD = "### The name of file to rewrite: `{file}`\n"
FENCED = "```\n{code}\n```\n"


async def build_code_context(store: ArtifactStore, exclude: str, use_inc: bool = False) -> str:
    """源 WriteCode.get_codes(:168)：同项目其他文件作上下文。
    非增量：排除当前文件（源按 task 列表取文件，本仓按 src 实际文件——没写完的任务文件
    本就不在 src，两个口径在增量场景外等价）。增量/工单（源 use_inc=True :196-211）：目标文件
    旧码以 "### The name of file to rewrite" 置顶进上下文，源 main.py 例外跳过——不给 main 塞旧码；
    全量场景（exclude 传空，源 get_old_codes 语义）所有文件都进。
    生成侧（WriteCode）与评审侧（WriteCodeReview）共用一个实现——源也是 review 调 WriteCode 的这个。"""
    parts = []
    for f in store.all_files(RepoName.SRC):
        d = await store.get(RepoName.SRC, f)
        if not d:
            continue
        if f == exclude:
            if use_inc and f != "main.py":       # 源 :200-203
                parts.insert(0, REWRITE_HEAD.format(file=f) + FENCED.format(code=d.content))
            continue
        parts.append(FILE_HEAD.format(file=f) + FENCED.format(code=d.content))
    return "\n".join(parts)


class PlanAndChange(BaseModel):
    """= 源 CODE_PLAN_AND_CHANGE 两节点（字段说明逐字取 instruction）"""

    development_plan: list[str] = Field(default_factory=list, description=DEV_PLAN_INSTRUCTION)
    incremental_change: list[str] = Field(default_factory=list, description=INC_CHANGE_INSTRUCTION)


class WriteCodePlanAndChange(BaseAction):
    output_schema = PlanAndChange

    async def run(self, msg: Message) -> Message:
        """源 :217-232 六段上下文：requirement/issue 按场景取（源 :458-463 工单场景读 issue），
        prd/design/task 从产物仓读，Legacy Code 全量进（源 get_old_codes:234-240）。"""
        store = ArtifactStore.active()
        fic = msg.instruct_content or {}
        issue_doc = await store.get(RepoName.DOCS, fic.get("issue_filename") or BUGFIX_FILENAME)
        req_doc = await store.get(RepoName.DOCS, DocName.REQUIREMENT)
        prd_doc = await store.get(RepoName.PRD, DocName.PRD)
        design = (await store.get(RepoName.DOCS, DocName.DESIGN_JSON)
                  or await store.get(RepoName.DOCS, DocName.DESIGN))
        tasks = await store.get(RepoName.DOCS, DocName.TASKS)
        context = CODE_PLAN_AND_CHANGE_CONTEXT.format(
            requirement=f"```text\n{req_doc.content if req_doc else msg.content}\n```",
            issue=f"```text\n{issue_doc.content if issue_doc else ''}\n```",
            prd=prd_doc.content if prd_doc else "",
            design=design.content if design else "",
            task=tasks.content if tasks else "",
            code=await build_code_context(store, exclude=""),
        )
        logger.info("Writing code plan and change..")
        pac: PlanAndChange = await self._structured(context, schema=PlanAndChange, system=SYSTEM_PROMPT)
        doc_md = ("## Development Plan\n" + "\n".join(pac.development_plan)
                  + "\n\n## Incremental Change\n" + "\n".join(pac.incremental_change) + "\n")
        await store.save(RepoName.DOCS, Document(filename=DocName.CODE_PLAN_AND_CHANGE, content=doc_md))
        # 目标文件：工单点名优先；源按 changed files 排每文件一 todo，本仓单会话单主文件——缺省取现存 src 第一个
        target = fic.get("filename") or (store.all_files(RepoName.SRC) or [""])[0]
        return Message(content=f"增量开发计划完成（{len(pac.development_plan)} 步），目标文件: {target or '(无)'}",
                       role="assistant", cause_by=self.name, sent_from="Engineer",
                       instruct_content={"filename": target,
                                         "code_plan_and_change_doc":
                                             {"filename": DocName.CODE_PLAN_AND_CHANGE,
                                              "root_path": RepoName.DOCS, "content": doc_md}},
                       instruct_schema="CodingContext")
