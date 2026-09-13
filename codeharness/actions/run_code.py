"""RunCode：执行 pytest（源 actions/run_code.py 语义 → 第 8 步 sandbox.run_context）。"""
from codeharness.base.action import BaseAction
from codeharness.schema import Message, RunCodeContext
from codeharness.const import RepoName
from codeharness.document_store.artifact_store import ArtifactStore
from codeharness.tools.sandbox import run_context


class RunCode(BaseAction):
    async def run(self, msg: Message) -> Message:
        ctx = RunCodeContext(**(msg.instruct_content or {}))
        ctx.working_dir = ctx.working_dir or str(ArtifactStore.active().root)
        ctx.command = ctx.command or ["python", "-m", "pytest", "tests", "-x", "--tb=short"]
        result = await run_context(ctx)
        store = ArtifactStore.active()
        out_name = ctx.output_filename or f"output_{ctx.test_filename or 'run'}.json"
        await store.save(RepoName.TEST_OUTPUTS, Document(filename=out_name, content=result.model_dump_json()))
        ok = "OK" in result.stderr and "FAILED" not in result.stderr
        return Message(content=("测试通过" if ok else f"测试失败:\n{result.stderr[:3000]}"),
                       role="assistant", cause_by=self.name, sent_from="QA",
                       send_to={"<self>"} if ok else set(),
                       instruct_content={"output_filename": out_name, "ok": ok},
                       instruct_schema="RunCodeOutput")
