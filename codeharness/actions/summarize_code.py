"""SummarizeCode：汇总本轮代码并通知 QA 启动测试（源 actions/summarize_code.py 语义）。"""
from codeharness.base.action import BaseAction
from codeharness.schema import Message, Document
from codeharness.const import RepoName, DocName
from codeharness.document_store.artifact_store import ArtifactStore


class SummarizeCode(BaseAction):
    async def run(self, msg: Message) -> Message:
        store = ArtifactStore.active()
        files = store.all_files(RepoName.SRC)
        summary = await self._aask(f"用 5 句话总结以下代码变更（文件清单与要点）：\n{files}")
        await store.save(RepoName.DOCS, Document(filename=DocName.CODE_SUMMARY, content=summary))
        return Message(content=summary, role="assistant", cause_by=self.name, sent_from="Engineer")
