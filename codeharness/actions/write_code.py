"""WriteCode。改造自 actions/write_code.py：PROMPT_TEMPLATE(:34-85) 逐字保留；
get_codes(:168) 的"排除自身文件/增量场景旧码置顶"语义保留（build_code_context 搬至
write_code_plan_and_change.py——REFINED_TEMPLATE 的家，本件单向 import，无环）；
EditorReporter 报道经 ArtifactStore.save 的 EDITOR 块承接。
重写分支（接线台账 #4）：源 :119-142 的 `config.inc → REFINED_TEMPLATE`——本仓 inc 等价信号是
**CodingContext 里带了 code_plan_and_change_doc**（FIX_BUG 链的 PlanAndChange 产出），有则 REFINED。"""
from pathlib import Path
from codeharness.actions.write_code_plan_and_change import REFINED_TEMPLATE, build_code_context
from codeharness.base.action import BaseAction
from codeharness.logs import logger
from codeharness.schema import Message, Document, CodingContext
from codeharness.const import BUGFIX_FILENAME, RepoName, DocName
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
## Code: {demo_filename}.js
```javascript
// {demo_filename}.js
...
```

# Instruction: Based on the context, follow "Format example", write code.

## Code: {filename}. Write code with triple quoto, based on the following attentions and context.
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
        if not ctx.filename:
            # 空 filename 不进模型、不抛：真模型第十一处——路由没带 CodingContext 时这里
            # 先烧一次真钱再被产物仓的写拒 ValueError 吹掉整场会话。错误回喂让角色自愈，
            # 断因（watch 缺失/上下文键名漂移）由 s3b t11 的表自洽门禁负责。
            logger.warning("WriteCode 未拿到 filename：上游 Send 缺 CodingContext 上下文")
            return Message(content="[缺少任务上下文] 未收到 filename，本轮不写代码；"
                                    "触发消息需携带 instruct_schema=CodingContext。",
                           role="assistant", cause_by=self.name, sent_from="Engineer")
        store = ArtifactStore.active()
        design = (await store.get(RepoName.DOCS, DocName.DESIGN_JSON)
                  or await store.get(RepoName.DOCS, DocName.DESIGN)) or Document(content="")
        tasks = (await store.get(RepoName.DOCS, DocName.TASKS)) or Document(content="")
        # 源 :119-142：三路上下文（上一轮跑测 stderr / code_summary 复盘存档 / bugfix 工单）+
        # 场景判据。本仓 output 命名对齐源 test_{code_filename}.json（RunCode 出口同步，两处必须一致）。
        logs = ""
        test_out = await store.get(RepoName.TEST_OUTPUTS, f"test_{ctx.filename}.json")
        if test_out:
            from codeharness.schema import RunCodeResult
            logs = RunCodeResult.model_validate_json(test_out.content).stderr
        summary_doc = await store.get(RepoName.DOCS, DocName.CODE_SUMMARY)
        feedback = ""
        bugfix = await store.get(RepoName.DOCS, BUGFIX_FILENAME)
        if bugfix:
            feedback = bugfix.content
            # C102：这里**不删**。删除是「这份工单本次已消费」的记号，只能打在产物落盘之后——
            # 原先读到就删，而模型调用是下面那句 `rsp = await self._aask(prompt)`，那一发一失败
            # （超时／端点挂／落盘 raise）工单就永久没了：下次重试没有工单，
            # `write_code_plan_and_change.py:146` 的 issue_doc 也跟着读不到。
            # 与 C97 同族——别拿已被删的证据去兜底，失败要留一声可 grep 的。
        pac = ctx.code_plan_and_change_doc
        refined = bool(pac and pac.content.strip())          # 本仓的源 config.inc 等价信号（见文件头）
        # 源 :119-126：增量计划在场或带工单 → use_inc 上下文（目标旧码置顶）；新建只带其他文件
        others = await build_code_context(store, ctx.filename, use_inc=refined or bool(feedback))
        if refined:
            requirement = await store.get(RepoName.DOCS, DocName.REQUIREMENT)
            prompt = REFINED_TEMPLATE.format(
                user_requirement=requirement.content if requirement else "",
                code_plan_and_change=pac.content, design=design.content, task=tasks.content,
                code=others, logs=logs, summary_log=summary_doc.content if summary_doc else "",
                feedback=feedback,
                filename=ctx.filename, demo_filename=Path(ctx.filename).stem)
        else:
            prompt = PROMPT_TEMPLATE.format(design=design.content, task=tasks.content, code=others,
                                            logs=logs, summary_log=summary_doc.content if summary_doc else "",
                                            feedback=feedback,
                                            filename=ctx.filename, demo_filename=Path(ctx.filename).stem)
        try:
            # C177：产出是要落盘的代码正文 ⇒ 这一笔不上打字机行（09-29 现证：整段 jsx 714 片 / 8002 字
            # 落 `stream-act` 刷屏）。记账与 span 照旧，界面进度由 `ToolCall` 卡承担。
            rsp = await self._aask(prompt, no_stream=True)
            code = _parse_code(rsp)                              # 见文末工具函数
            code_doc = await store.save(RepoName.SRC, Document(filename=ctx.filename, content=code))
        except Exception as e:
            if bugfix:      # 工单还在 DOCS：留一声可 grep 的，别让人以为「重跑一次也没有了」
                logger.warning(f"{ctx.filename} 写码失败，BUGFIX 工单未消费、仍留在 DOCS 供重试："
                               f"{type(e).__name__}: {str(e)[:160]}")
            raise           # 只加这一声：不吞异常、不改控制流（Action 异常回喂自愈那套语义照旧）
        if bugfix:
            # 源 :163「防止冲突」的语义在**成功之后**才落地：产物已落盘，这份工单才算真被消费掉。
            (store.root / RepoName.DOCS / BUGFIX_FILENAME).unlink(missing_ok=True)
        return Message(content=f"已写 {ctx.filename}（{len(code)} 字符）", role="assistant",
                       cause_by=self.name, sent_from="Engineer",
                       instruct_content={"filename": ctx.filename}, instruct_schema="WriteCodeOutput")


def _parse_code(text: str) -> str:
    """= 源 utils/common.py CodeParser.parse_code：取最后一个 ``` 围栏体"""
    import re
    blocks = re.findall(r"```[a-zA-Z]*\n(.*?)```", text, re.DOTALL)
    return blocks[-1].strip() if blocks else text.strip()
