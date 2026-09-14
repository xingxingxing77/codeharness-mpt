"""RunCode：执行 pytest（源 actions/run_code.py 语义 → 第 8 步 sandbox.run_context）。"""
import sys
from codeharness.base.action import BaseAction
from codeharness.schema import Message, Document, RunCodeContext
from codeharness.const import MESSAGE_ROUTE_TO_SELF, RepoName
from codeharness.document_store.artifact_store import ArtifactStore
from codeharness.tools.sandbox import run_context


class RunCode(BaseAction):
    async def run(self, msg: Message) -> Message:
        ctx = RunCodeContext(**(msg.instruct_content or {}))
        ctx.working_directory = ctx.working_directory or str(ArtifactStore.active().root)
        # sys.executable：保证用当前解释器（PATH 上的 python 可能没装 pytest）
        ctx.command = ctx.command or [sys.executable, "-m", "pytest", "tests", "-x", "--tb=short"]
        result = await run_context(ctx)
        # pytest 报告在 stdout、unittest 在 stderr——合并存档；ok 以 return_code 为准（格式无关）
        combined = result.model_copy(update={"stderr": (result.stderr + "\n" + result.stdout).strip()})
        store = ArtifactStore.active()
        out_name = ctx.output_filename or f"output_{ctx.test_filename or 'run'}.json"
        await store.save(RepoName.TEST_OUTPUTS, Document(filename=out_name, content=combined.model_dump_json()))
        ok = result.return_code == 0
        return Message(content=("测试通过" if ok else f"测试失败:\n{combined.stderr[:3000]}"),
                       role="assistant", cause_by=self.name, sent_from="QA",
                       send_to={MESSAGE_ROUTE_TO_SELF} if ok else set(),
                       instruct_content={"output_filename": out_name, "ok": ok,
                                         "code_filename": ctx.code_filename,     # DebugError 修复回路透传
                                         "test_filename": ctx.test_filename},
                       instruct_schema="RunCodeOutput")
